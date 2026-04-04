"""Enumeration describing which backend owns a given model."""

from enum import Enum

__all__ = ["ProviderType"]


class ProviderType(Enum):
    """Canonical identifiers for every supported provider backend."""

    GOOGLE = "google"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    AZURE = "azure"
    XAI = "xai"
    ZAI = "zai"
    OPENROUTER = "openrouter"
    CUSTOM = "custom"
    DIAL = "dial"
