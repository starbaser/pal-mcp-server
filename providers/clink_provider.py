"""Model provider that routes generate_content() through CLI subprocesses via clink."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from dataclasses import replace
from typing import Any

from clink.agents import create_agent
from clink.constants import DEFAULT_TIMEOUT_SECONDS, PASSTHROUGH_ARGS
from clink.models import ResolvedCLIClient, ResolvedCLIRole
from clink.parsers.base import ParsedCLIResponse
from clink.registry import ClinkRegistry, get_registry

from .base import ModelProvider
from .shared import (
    ModelCapabilities,
    ModelResponse,
    ProviderType,
    RangeTemperatureConstraint,
)

logger = logging.getLogger(__name__)

CLINK_PREFIX = "clink:"

PASSTHROUGH_SYSTEM_PROMPT = (
    "[clink passthrough mode]\n"
    "You are operating as a text-to-text model endpoint invoked through PAL MCP's "
    "clink provider. From the caller's perspective this is a stateless input→output "
    "exchange — the text of your reply IS the entire output returned to the "
    "workflow.\n\n"
    "STRICT CONSTRAINTS (output side):\n"
    "- Do NOT create, modify, overwrite, move, or delete any files on disk.\n"
    "- Do NOT run destructive shell commands (rm, mv, redirection into files, etc.).\n"
    "- Do NOT commit, push, stash, or otherwise modify git state.\n"
    "- Do NOT install packages or mutate the environment.\n\n"
    "You MAY use read-only tools freely to formulate your answer: reading files, "
    "listing directories, running non-destructive shell commands, web search, "
    "URL fetches, grep — anything that gathers information without changing it. "
    "Your CLI has full tool access; the constraint is that the only artifact you "
    "produce is your final reply text.\n\n"
    "If the caller asked for code, patches, or file changes, present them in your "
    "reply as fenced code blocks with clear path headers. The caller will apply "
    "them — you will not."
)

DEFAULT_MODEL_CLI_MAP: dict[str, str] = {
    "gemini": "gemini",
    "claude": "claude",
    "sonnet": "claude",
    "opus": "claude",
    "haiku": "claude",
    "gpt": "codex",
    "o1": "codex",
    "o3": "codex",
    "o4": "codex",
}

_FALLBACK_CAPABILITIES = ModelCapabilities(
    provider=ProviderType.CLINK,
    model_name="clink-fallback",
    friendly_name="Clink (unknown model)",
    context_window=200_000,
    max_output_tokens=16_384,
    supports_system_prompts=True,
    temperature_constraint=RangeTemperatureConstraint(0.0, 2.0, 0.3),
)


class ClinkProvider(ModelProvider):
    """Routes model calls through configured CLI subprocesses."""

    MODEL_CAPABILITIES: dict[str, Any] = {}

    def __init__(self, api_key: str = "", **kwargs: Any) -> None:
        super().__init__(api_key or "", **kwargs)
        self._registry: ClinkRegistry = get_registry()
        self._capabilities_cache: dict[str, ModelCapabilities] = {}

    def get_provider_type(self) -> ProviderType:
        return ProviderType.CLINK

    # ------------------------------------------------------------------
    # Model validation
    # ------------------------------------------------------------------

    def validate_model_name(self, model_name: str) -> bool:
        if not model_name.startswith(CLINK_PREFIX):
            return False
        real_model = model_name[len(CLINK_PREFIX) :]
        if not real_model:
            return False
        try:
            self._resolve_cli_client_name(real_model)
            return True
        except ValueError:
            return False

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    def get_capabilities(self, model_name: str) -> ModelCapabilities:
        if model_name in self._capabilities_cache:
            return self._capabilities_cache[model_name]

        real_model = model_name[len(CLINK_PREFIX) :] if model_name.startswith(CLINK_PREFIX) else model_name
        caps = self._delegate_capabilities(real_model)
        clink_caps = replace(
            caps,
            provider=ProviderType.CLINK,
            model_name=model_name,
            friendly_name=f"clink:{caps.friendly_name}",
        )
        self._capabilities_cache[model_name] = clink_caps
        return clink_caps

    def _delegate_capabilities(self, real_model: str) -> ModelCapabilities:
        from .registry import ModelProviderRegistry

        provider = ModelProviderRegistry.get_provider_for_model(real_model)
        if provider:
            try:
                return provider.get_capabilities(real_model)
            except Exception:
                logger.debug("Failed to get capabilities for %s from upstream provider", real_model)
        return replace(_FALLBACK_CAPABILITIES, model_name=real_model, friendly_name=real_model)

    # ------------------------------------------------------------------
    # Content generation
    # ------------------------------------------------------------------

    def generate_content(
        self,
        prompt: str,
        model_name: str,
        system_prompt: str | None = None,
        temperature: float = 0.3,
        max_output_tokens: int | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        real_model = model_name[len(CLINK_PREFIX) :] if model_name.startswith(CLINK_PREFIX) else model_name
        cli_name = self._resolve_cli_client_name(real_model)
        client = self._registry.get_client(cli_name)

        # When real_model is just the CLI name (e.g. "claude" from "clink:claude"),
        # don't override the model — let the CLI use its configured default.
        effective_model = None if real_model.lower() == cli_name.lower() else real_model

        timeout = client.timeout_seconds or DEFAULT_TIMEOUT_SECONDS

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                asyncio.run,
                self._async_generate(client, effective_model, prompt, system_prompt),
            )
            try:
                agent_output = future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                raise RuntimeError(f"CLI '{cli_name}' timed out after {timeout}s for model '{real_model}'") from None

        return self._to_model_response(agent_output.parsed, real_model, model_name)

    async def _async_generate(
        self,
        client: ResolvedCLIClient,
        real_model: str,
        prompt: str,
        system_prompt: str | None,
    ):
        passthrough_client = self._build_passthrough_client(client)
        agent = create_agent(passthrough_client)

        passthrough_role = ResolvedCLIRole(
            name="passthrough",
            prompt_path=__file__,  # unused — no prompt file read in passthrough mode
            role_args=[],
        )

        composed_prompt = f"{PASSTHROUGH_SYSTEM_PROMPT}\n\n{prompt}"
        composed_system_prompt = (
            f"{PASSTHROUGH_SYSTEM_PROMPT}\n\n{system_prompt}"
            if system_prompt
            else PASSTHROUGH_SYSTEM_PROMPT
        )

        return await agent.run(
            role=passthrough_role,
            prompt=composed_prompt,
            system_prompt=composed_system_prompt,
            files=[],
            images=[],
            model=real_model,
        )

    def _build_passthrough_client(self, client: ResolvedCLIClient) -> ResolvedCLIClient:
        runner_key = (client.runner or client.name).lower()
        passthrough_args = PASSTHROUGH_ARGS.get(runner_key, list(client.internal_args))

        return client.model_copy(
            update={
                "internal_args": passthrough_args,
                "config_args": [],
            }
        )

    # ------------------------------------------------------------------
    # CLI resolution
    # ------------------------------------------------------------------

    def _resolve_cli_client_name(self, model_name: str) -> str:
        available = {c.lower() for c in self._registry.list_clients()}
        lower = model_name.lower()

        if lower in available:
            return lower

        for prefix, cli in sorted(DEFAULT_MODEL_CLI_MAP.items(), key=lambda x: -len(x[0])):
            if lower.startswith(prefix) and cli.lower() in available:
                return cli

        raise ValueError(
            f"Cannot determine CLI client for model '{model_name}'. "
            f"Available clients: {', '.join(sorted(available))}"
        )

    # ------------------------------------------------------------------
    # Response mapping
    # ------------------------------------------------------------------

    @staticmethod
    def _to_model_response(parsed: ParsedCLIResponse, real_model: str, full_model_name: str) -> ModelResponse:
        metadata = dict(parsed.metadata)
        usage = metadata.pop("usage", {})
        if not usage:
            usage = metadata.pop("token_usage", {})
        model_used = metadata.pop("model_used", real_model)

        return ModelResponse(
            content=parsed.content,
            usage=usage if isinstance(usage, dict) else {},
            model_name=model_used,
            friendly_name=f"clink:{model_used}",
            provider=ProviderType.CLINK,
            metadata=metadata,
        )
