"""ZAI (GLM) model provider implementation."""

import logging
from typing import TYPE_CHECKING, ClassVar, Optional

if TYPE_CHECKING:
    from tools.models import ToolModelCategory

from .openai_compatible import OpenAICompatibleProvider
from .registries.zai import ZAIModelRegistry
from .registry_provider_mixin import RegistryBackedProviderMixin
from .shared import ModelCapabilities, ProviderType

logger = logging.getLogger(__name__)


class ZAIModelProvider(RegistryBackedProviderMixin, OpenAICompatibleProvider):
    """Integration for ZAI's GLM models exposed over an OpenAI-style API.

    Publishes capability metadata for GLM-4.7 and related models,
    maps tool-category preferences to the appropriate GLM model.
    """

    FRIENDLY_NAME = "ZAI"

    REGISTRY_CLASS = ZAIModelRegistry
    MODEL_CAPABILITIES: ClassVar[dict[str, ModelCapabilities]] = {}

    # Canonical model identifiers used for category routing
    PRIMARY_MODEL = "glm-4.7"
    FALLBACK_MODEL = "glm-4.5-air"

    def __init__(self, api_key: str, **kwargs):
        """Initialize ZAI provider with API key."""
        # Set ZAI base URL for coding plan endpoint
        kwargs.setdefault("base_url", "https://api.z.ai/api/coding/paas/v4/")
        self._ensure_registry()
        super().__init__(api_key, **kwargs)
        self._invalidate_capability_cache()

    def get_provider_type(self) -> ProviderType:
        """Get the provider type."""
        return ProviderType.ZAI

    def get_preferred_model(self, category: "ToolModelCategory", allowed_models: list[str]) -> Optional[str]:
        """Get ZAI's preferred model for a given category from allowed models.

        Args:
            category: The tool category requiring a model
            allowed_models: Pre-filtered list of models allowed by restrictions

        Returns:
            Preferred model name or None
        """
        from tools.models import ToolModelCategory

        if not allowed_models:
            return None

        if category == ToolModelCategory.EXTENDED_REASONING:
            # Prefer GLM-4.7 for advanced reasoning tasks
            if self.PRIMARY_MODEL in allowed_models:
                return self.PRIMARY_MODEL
            if self.FALLBACK_MODEL in allowed_models:
                return self.FALLBACK_MODEL
            return allowed_models[0]

        elif category == ToolModelCategory.FAST_RESPONSE:
            # Prefer GLM-4.5 Air for speed
            if self.FALLBACK_MODEL in allowed_models:
                return self.FALLBACK_MODEL
            if self.PRIMARY_MODEL in allowed_models:
                return self.PRIMARY_MODEL
            return allowed_models[0]

        else:  # BALANCED or default
            # Prefer GLM-4.7 for balanced use
            if self.PRIMARY_MODEL in allowed_models:
                return self.PRIMARY_MODEL
            if self.FALLBACK_MODEL in allowed_models:
                return self.FALLBACK_MODEL
            return allowed_models[0]


# Load registry data at import time
ZAIModelProvider._ensure_registry()
