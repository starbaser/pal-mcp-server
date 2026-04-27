"""Fetch, cache, and expose the litellm model-pricing JSON as PAL ModelCapabilities."""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

from config import (
    EXTERNAL_MODELS_DISABLED,
    LITELLM_MODEL_DATA_CACHE_TTL,
    LITELLM_MODEL_DATA_DIR,
    LITELLM_MODEL_DATA_URL,
)

if TYPE_CHECKING:
    from providers.shared import ModelCapabilities, ProviderType

logger = logging.getLogger(__name__)

_CACHE_FILENAME = "litellm_models.json"

# litellm_provider string → PAL ProviderType.value string.
# Resolved to actual ProviderType enums at call time to avoid circular imports.
_LITELLM_PROVIDER_MAP: dict[str, str] = {
    "gemini": "google",
    "anthropic": "anthropic",
    "openai": "openai",
    "xai": "xai",
    "zai": "zai",
    "deepseek": "deepseek",
}

# litellm provider prefixes stripped from model keys to produce bare PAL model names.
_PREFIX_STRIP: dict[str, str] = {
    "gemini": "gemini/",
    "xai": "xai/",
    "zai": "zai/",
    "deepseek": "deepseek/",
}

# Singleton — loaded once per process, reused across all registries.
_loaded_data: dict[str, dict] | None = None


def _cache_path() -> Path:
    return Path(LITELLM_MODEL_DATA_DIR) / _CACHE_FILENAME


def _fetch_and_cache() -> dict:
    """Download the JSON from the configured URL and write it to the cache directory."""
    cache = _cache_path()
    cache.parent.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(LITELLM_MODEL_DATA_URL, headers={"User-Agent": "pal-mcp-server"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
        data = json.loads(raw)
        cache.write_bytes(raw)
        logger.info("Fetched litellm model data (%d entries)", len(data))
        return data
    except Exception as exc:
        logger.warning("Failed to fetch litellm model data: %s", exc)
        return {}


def _load_cache() -> dict:
    """Read the locally cached JSON if it exists and hasn't expired."""
    cache = _cache_path()
    if not cache.exists():
        return {}
    age = time.time() - cache.stat().st_mtime
    if age > LITELLM_MODEL_DATA_CACHE_TTL:
        return {}
    try:
        return json.loads(cache.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_litellm_data() -> dict[str, dict]:
    """Return the full litellm model dict (cached singleton)."""
    global _loaded_data
    if _loaded_data is not None:
        return _loaded_data

    if EXTERNAL_MODELS_DISABLED:
        _loaded_data = {}
        return _loaded_data

    data = _load_cache() or _fetch_and_cache()
    _loaded_data = data
    return _loaded_data


def reset_cache() -> None:
    """Force a re-fetch on next access (for tests)."""
    global _loaded_data
    _loaded_data = None


def _strip_provider_prefix(key: str, litellm_provider: str) -> str:
    """Remove the litellm routing prefix (e.g. 'gemini/') from a model key."""
    prefix = _PREFIX_STRIP.get(litellm_provider)
    if prefix and key.startswith(prefix):
        return key[len(prefix) :]
    return key


def _derive_friendly_name(model_name: str) -> str:
    """Generate a human-readable name from a bare model identifier."""
    return model_name.replace("-", " ").replace("_", " ").title()


def _make_capabilities(key: str, entry: dict[str, Any], provider: ProviderType) -> ModelCapabilities | None:
    """Convert a single litellm JSON entry into a PAL ModelCapabilities.

    Returns None for non-chat models or entries missing essential data.
    Provider-related imports are resolved here to avoid circular imports at module level.
    """
    if entry.get("mode") != "chat":
        return None

    litellm_provider = entry.get("litellm_provider", "")
    model_name = _strip_provider_prefix(key, litellm_provider)

    if model_name.startswith("ft:"):
        return None

    from providers.shared import ModelCapabilities as MC
    from providers.shared.temperature import RangeTemperatureConstraint

    context_window = entry.get("max_input_tokens") or entry.get("max_tokens") or 0
    max_output = entry.get("max_output_tokens") or 0
    supports_reasoning = bool(entry.get("supports_reasoning"))

    return MC(
        provider=provider,
        model_name=model_name,
        friendly_name=_derive_friendly_name(model_name),
        intelligence_score=10,
        context_window=int(context_window),
        max_output_tokens=int(max_output),
        max_thinking_tokens=0,
        supports_extended_thinking=supports_reasoning,
        supports_system_prompts=bool(entry.get("supports_system_messages", True)),
        supports_streaming=bool(entry.get("supports_native_streaming", True)),
        supports_function_calling=bool(entry.get("supports_function_calling")),
        supports_images=bool(entry.get("supports_vision") or entry.get("supports_image_input")),
        supports_video=bool(entry.get("supports_video_input")),
        supports_audio=bool(entry.get("supports_audio_input")),
        supports_json_mode=bool(entry.get("supports_response_schema")),
        supports_temperature=True,
        temperature_constraint=RangeTemperatureConstraint(0.0, 2.0, 0.3),
    )


def get_models_for_provider(provider: ProviderType) -> dict[str, ModelCapabilities]:
    """Return litellm-sourced ModelCapabilities for a single PAL provider.

    Keys are bare model names (provider prefix stripped).
    """
    data = load_litellm_data()
    if not data:
        return {}

    # Reverse lookup: which litellm_provider strings map to this PAL provider?
    litellm_providers = {lp for lp, pv in _LITELLM_PROVIDER_MAP.items() if pv == provider.value}
    if not litellm_providers:
        return {}

    result: dict[str, ModelCapabilities] = {}
    for key, entry in data.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("litellm_provider") not in litellm_providers:
            continue
        cap = _make_capabilities(key, entry, provider)
        if cap and cap.model_name not in result:
            result[cap.model_name] = cap

    return result
