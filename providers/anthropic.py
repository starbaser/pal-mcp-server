"""Anthropic model provider implementation."""

import logging
from typing import TYPE_CHECKING, ClassVar, Optional

import anthropic

if TYPE_CHECKING:
    from tools.models import ToolModelCategory

from .base import ModelProvider
from .registries.anthropic import AnthropicModelRegistry
from .registry_provider_mixin import RegistryBackedProviderMixin
from .shared import ModelCapabilities, ModelResponse, ProviderType

logger = logging.getLogger(__name__)


class AnthropicSDKProviderMixin:
    """Shared Anthropic SDK logic for providers that speak the Anthropic Messages API."""

    THINKING_BUDGETS = {
        "minimal": 0.005,
        "low": 0.08,
        "medium": 0.33,
        "high": 0.67,
        "max": 1.0,
    }

    def _build_anthropic_kwargs(
        self,
        resolved_model: str,
        prompt: str,
        system_prompt: Optional[str],
        effective_temperature: Optional[float],
        max_output_tokens: Optional[int],
        capabilities: Optional[ModelCapabilities],
        thinking_mode: str = "medium",
    ) -> dict:
        msg_kwargs: dict = {
            "model": resolved_model,
            "max_tokens": max_output_tokens or (capabilities.max_output_tokens if capabilities else 8192),
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            msg_kwargs["system"] = system_prompt

        # Extended thinking
        if capabilities and capabilities.supports_extended_thinking and capabilities.max_thinking_tokens > 0:
            budget_pct = self.THINKING_BUDGETS.get(thinking_mode, self.THINKING_BUDGETS["medium"])
            budget_tokens = int(capabilities.max_thinking_tokens * budget_pct)
            if budget_tokens > 0:
                msg_kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget_tokens}
                # Anthropic API: temperature must be 1 when thinking is enabled
                msg_kwargs["temperature"] = 1.0
        elif effective_temperature is not None:
            msg_kwargs["temperature"] = effective_temperature

        return msg_kwargs

    def _create_message(self, msg_kwargs: dict):
        """Create a message using streaming to satisfy the SDK's long-request requirement."""
        with self.client.messages.stream(**msg_kwargs) as stream:
            return stream.get_final_message()

    @staticmethod
    def _extract_anthropic_response(response) -> tuple[str, dict]:
        """Extract text content and usage from an Anthropic response."""
        content = ""
        for block in response.content:
            if block.type == "text":
                content = block.text
                break
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
        }
        return content, usage


class AnthropicModelProvider(AnthropicSDKProviderMixin, RegistryBackedProviderMixin, ModelProvider):
    """Integration for Anthropic's Claude models via the Anthropic SDK."""

    FRIENDLY_NAME = "Anthropic"

    REGISTRY_CLASS = AnthropicModelRegistry
    MODEL_CAPABILITIES: ClassVar[dict[str, ModelCapabilities]] = {}

    PRIMARY_MODEL = "claude-sonnet-4-6"
    FALLBACK_MODEL = "claude-haiku-4-5-20251001"

    def __init__(self, api_key: str, **kwargs):
        self._base_url = kwargs.get("base_url", "https://api.anthropic.com")
        self._ensure_registry()
        super().__init__(api_key, **kwargs)
        self._client = None
        self._invalidate_capability_cache()

    @property
    def client(self):
        if self._client is None:
            self._client = anthropic.Anthropic(
                api_key=self.api_key,
                base_url=self._base_url,
            )
        return self._client

    def get_provider_type(self) -> ProviderType:
        return ProviderType.ANTHROPIC

    def generate_content(
        self,
        prompt: str,
        model_name: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.3,
        max_output_tokens: Optional[int] = None,
        thinking_mode: str = "medium",
        **kwargs,
    ) -> ModelResponse:
        if not self.validate_model_name(model_name):
            raise ValueError(f"Model '{model_name}' not available for Anthropic provider")

        capabilities: Optional[ModelCapabilities] = None
        try:
            capabilities = self.get_capabilities(model_name)
        except Exception:
            pass

        effective_temperature = temperature
        if capabilities:
            effective_temperature = capabilities.get_effective_temperature(temperature)

        resolved_model = self._resolve_model_name(model_name)

        msg_kwargs = self._build_anthropic_kwargs(
            resolved_model,
            prompt,
            system_prompt,
            effective_temperature,
            max_output_tokens,
            capabilities,
            thinking_mode,
        )

        max_retries = 4
        retry_delays = [1, 3, 5, 8]
        attempt_counter = {"value": 0}

        def _attempt() -> ModelResponse:
            attempt_counter["value"] += 1
            response = self._create_message(msg_kwargs)

            content, usage = self._extract_anthropic_response(response)
            return ModelResponse(
                content=content,
                usage=usage,
                model_name=resolved_model,
                friendly_name=self.FRIENDLY_NAME,
                provider=self.get_provider_type(),
                metadata={
                    "finish_reason": response.stop_reason,
                    "model": response.model,
                    "id": response.id,
                    "thinking_mode": (
                        thinking_mode if capabilities and capabilities.supports_extended_thinking else None
                    ),
                },
            )

        try:
            return self._run_with_retries(
                operation=_attempt,
                max_attempts=max_retries,
                delays=retry_delays,
                log_prefix=f"Anthropic API ({resolved_model})",
            )
        except Exception as exc:
            attempts = max(attempt_counter["value"], 1)
            raise RuntimeError(f"Anthropic API error for {resolved_model} after {attempts} attempts: {exc}") from exc

    def get_preferred_model(self, category: "ToolModelCategory", allowed_models: list[str]) -> Optional[str]:
        from tools.models import ToolModelCategory

        if not allowed_models:
            return None

        def find_first(preferences: list[str]) -> Optional[str]:
            for model in preferences:
                if model in allowed_models:
                    return model
            return None

        if category == ToolModelCategory.EXTENDED_REASONING:
            preferred = find_first(
                [
                    "claude-opus-4-6",
                    "claude-opus-4-5-20251101",
                    "claude-sonnet-4-6",
                    "claude-sonnet-4-5-20250929",
                ]
            )
            return preferred if preferred else allowed_models[0]
        elif category == ToolModelCategory.FAST_RESPONSE:
            preferred = find_first(
                [
                    "claude-haiku-4-5-20251001",
                    "claude-3-5-haiku-20241022",
                    "claude-sonnet-4-6",
                ]
            )
            return preferred if preferred else allowed_models[0]
        else:
            preferred = find_first(
                [
                    "claude-sonnet-4-6",
                    "claude-sonnet-4-5-20250929",
                    "claude-opus-4-6",
                    "claude-haiku-4-5-20251001",
                ]
            )
            return preferred if preferred else allowed_models[0]


AnthropicModelProvider._ensure_registry()
