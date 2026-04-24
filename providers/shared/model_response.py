"""Dataclass used to normalise provider SDK responses."""

from dataclasses import dataclass, field
from typing import Any

from .provider_type import ProviderType

__all__ = ["ModelResponse"]


@dataclass
class ModelResponse:
    """Portable representation of a provider completion."""

    content: str
    usage: dict[str, int] = field(default_factory=dict)
    model_name: str = ""
    friendly_name: str = ""
    provider: ProviderType = ProviderType.GOOGLE
    metadata: dict[str, Any] = field(default_factory=dict)
    generated_images: list[dict[str, str]] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        """Return the total token count if the provider reported usage data."""

        return self.usage.get("total_tokens", 0)

    @property
    def session_id(self) -> str | None:
        """Return CLI session_id if present (clink provider only)."""

        return self.metadata.get("session_id")
