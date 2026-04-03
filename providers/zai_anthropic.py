"""ZAI (GLM) model provider using the Anthropic-compatible endpoint at api.z.ai."""

import logging
from typing import TYPE_CHECKING, ClassVar, Optional

import anthropic

if TYPE_CHECKING:
    from tools.models import ToolModelCategory

from .base import ModelProvider
from .registries.zai import ZAIModelRegistry
from .registry_provider_mixin import RegistryBackedProviderMixin
from .shared import ModelCapabilities, ModelResponse, ProviderType

logger = logging.getLogger(__name__)


class ZAIAnthropicProvider(RegistryBackedProviderMixin, ModelProvider):
    """Integration for ZAI's GLM models via Anthropic-compatible API at api.z.ai/api/anthropic."""

    FRIENDLY_NAME = "ZAI"

    REGISTRY_CLASS = ZAIModelRegistry
    MODEL_CAPABILITIES: ClassVar[dict[str, ModelCapabilities]] = {}

    PRIMARY_MODEL = "glm-4.7"
    FALLBACK_MODEL = "glm-4.5-air"

    def __init__(self, api_key: str, **kwargs):
        self._base_url = kwargs.get("base_url", "https://api.z.ai/api/anthropic")
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
        return ProviderType.ZAI_ANTHROPIC

    def generate_content(
        self,
        prompt: str,
        model_name: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.3,
        max_output_tokens: Optional[int] = None,
        **kwargs,
    ) -> ModelResponse:
        if not self.validate_model_name(model_name):
            raise ValueError(f"Model '{model_name}' not available for ZAI provider")

        capabilities: Optional[ModelCapabilities] = None
        try:
            capabilities = self.get_capabilities(model_name)
        except Exception:
            pass

        effective_temperature = temperature
        if capabilities:
            effective_temperature = capabilities.get_effective_temperature(temperature)

        resolved_model = self._resolve_model_name(model_name)

        msg_kwargs: dict = {
            "model": resolved_model,
            "max_tokens": max_output_tokens or (capabilities.max_output_tokens if capabilities else 8192),
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            msg_kwargs["system"] = system_prompt
        if effective_temperature is not None:
            msg_kwargs["temperature"] = effective_temperature

        max_retries = 4
        retry_delays = [1, 3, 5, 8]
        attempt_counter = {"value": 0}

        def _attempt() -> ModelResponse:
            attempt_counter["value"] += 1
            response = self.client.messages.create(**msg_kwargs)

            content = response.content[0].text if response.content else ""
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
            }
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
                },
            )

        try:
            return self._run_with_retries(
                operation=_attempt,
                max_attempts=max_retries,
                delays=retry_delays,
                log_prefix=f"ZAI API ({resolved_model})",
            )
        except Exception as exc:
            attempts = max(attempt_counter["value"], 1)
            raise RuntimeError(f"ZAI API error for {resolved_model} after {attempts} attempts: {exc}") from exc

    def get_preferred_model(self, category: "ToolModelCategory", allowed_models: list[str]) -> Optional[str]:
        from tools.models import ToolModelCategory

        if not allowed_models:
            return None

        if category == ToolModelCategory.EXTENDED_REASONING:
            if self.PRIMARY_MODEL in allowed_models:
                return self.PRIMARY_MODEL
            if self.FALLBACK_MODEL in allowed_models:
                return self.FALLBACK_MODEL
            return allowed_models[0]
        elif category == ToolModelCategory.FAST_RESPONSE:
            if self.FALLBACK_MODEL in allowed_models:
                return self.FALLBACK_MODEL
            if self.PRIMARY_MODEL in allowed_models:
                return self.PRIMARY_MODEL
            return allowed_models[0]
        else:
            if self.PRIMARY_MODEL in allowed_models:
                return self.PRIMARY_MODEL
            if self.FALLBACK_MODEL in allowed_models:
                return self.FALLBACK_MODEL
            return allowed_models[0]


ZAIAnthropicProvider._ensure_registry()
