"""Model provider that routes generate_content() through CLI subprocesses via clink."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from dataclasses import replace
from typing import Any

from clink.agents import create_agent
from clink.constants import BUILTIN_PROMPTS_DIR, DEFAULT_TIMEOUT_SECONDS, PASSTHROUGH_ARGS
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


def _load_passthrough_prompt() -> str:
    """Load the base clink prompt which contains the read-only constraint."""
    path = BUILTIN_PROMPTS_DIR / "default.txt"
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        logger.warning("Failed to load passthrough prompt from %s: %s", path, exc)
        return ""


PASSTHROUGH_SYSTEM_PROMPT = _load_passthrough_prompt()

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
    """Routes model calls through configured CLI subprocesses.

    Models are declared in conf/cli_clients/*.json under the ``models`` key
    as a slug→real_model_name mapping.  Slugs follow the
    ``{cli}-cli-{variant}`` convention (e.g. ``gemini-cli-2.5-flash``,
    ``claude-cli-opus-4-6``) so they are unambiguous from native API
    model names and route correctly through the provider priority order.
    """

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
        try:
            self._registry.resolve_model_slug(model_name)
            return True
        except KeyError:
            return False

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    def get_all_model_capabilities(self) -> dict[str, ModelCapabilities]:
        """Expose clink models so they appear in available model listings."""
        result: dict[str, ModelCapabilities] = {}
        for slug in self._registry.list_model_slugs():
            try:
                result[slug] = self.get_capabilities(slug)
            except Exception:
                pass
        return result

    def get_capabilities(self, model_name: str) -> ModelCapabilities:
        if model_name in self._capabilities_cache:
            return self._capabilities_cache[model_name]

        try:
            _client, real_model = self._registry.resolve_model_slug(model_name)
        except KeyError:
            real_model = None

        caps = self._delegate_capabilities(real_model) if real_model else _FALLBACK_CAPABILITIES
        clink_caps = replace(
            caps,
            provider=ProviderType.CLINK,
            model_name=model_name,
            friendly_name=f"clink/{caps.friendly_name}",
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
        import time as _time

        client, real_model = self._registry.resolve_model_slug(model_name)
        session_id = kwargs.get("session_id")

        timeout = client.timeout_seconds or DEFAULT_TIMEOUT_SECONDS
        logger.info("clink dispatch: cli=%s model=%s prompt=%d chars timeout=%ds", client.name, model_name, len(prompt), timeout)
        _t0 = _time.monotonic()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                asyncio.run,
                self._async_generate(client, real_model, prompt, system_prompt, session_id=session_id),
            )
            try:
                agent_output = future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                _elapsed = _time.monotonic() - _t0
                logger.error("clink timeout: cli=%s model=%s after %.1fs", client.name, model_name, _elapsed)
                raise RuntimeError(f"CLI '{client.name}' timed out after {timeout}s for model '{model_name}'") from None

        _elapsed = _time.monotonic() - _t0
        _resp_len = len(agent_output.parsed.content) if agent_output.parsed.content else 0
        logger.info(
            "clink complete: cli=%s model=%s duration=%.1fs response=%d chars rc=%d",
            client.name, model_name, _elapsed, _resp_len, agent_output.returncode,
        )
        return self._to_model_response(agent_output.parsed, real_model or model_name, model_name)

    async def _async_generate(
        self,
        client: ResolvedCLIClient,
        real_model: str | None,
        prompt: str,
        system_prompt: str | None,
        session_id: str | None = None,
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
            f"{PASSTHROUGH_SYSTEM_PROMPT}\n\n{system_prompt}" if system_prompt else PASSTHROUGH_SYSTEM_PROMPT
        )

        return await agent.run(
            role=passthrough_role,
            prompt=composed_prompt,
            system_prompt=composed_system_prompt,
            files=[],
            images=[],
            model=real_model,
            session_id=session_id,
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
            friendly_name=f"clink/{model_used}",
            provider=ProviderType.CLINK,
            metadata=metadata,
        )
