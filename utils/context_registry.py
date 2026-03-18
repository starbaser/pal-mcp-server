"""
Registry for tracking context stores organized by directory.

Persists to {PAL_STORAGE_DIR}/context/stores.json.
"""

import json
import os
import tempfile
from datetime import datetime, timezone

from config import PAL_STORAGE_DIR

_REGISTRY_PATH = os.path.join(PAL_STORAGE_DIR, "context", "stores.json")


def load_registry() -> dict:
    """Read the registry JSON file, creating it (and parent dirs) if missing."""
    if not os.path.exists(_REGISTRY_PATH):
        os.makedirs(os.path.dirname(_REGISTRY_PATH), exist_ok=True)
        return {}
    try:
        with open(_REGISTRY_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_registry(data: dict) -> None:
    """Atomically write registry data to disk."""
    dir_ = os.path.dirname(_REGISTRY_PATH)
    os.makedirs(dir_, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=dir_, delete=False, suffix=".tmp") as tmp:
        json.dump(data, tmp, indent=2)
        tmp_path = tmp.name
    os.rename(tmp_path, _REGISTRY_PATH)


def register_store(
    directory: str,
    store_id: str,
    label: str | None,
    model: str,
    parent_store_id: str | None = None,
) -> None:
    """Add a new store entry under the given directory."""
    registry = load_registry()
    entry = {
        "store_id": store_id,
        "label": label,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": model,
        "turn_count": 0,
        "parent_store_id": parent_store_id,
    }
    registry.setdefault(directory, []).append(entry)
    save_registry(registry)


def update_store_turn_count(store_id: str) -> None:
    """Increment turn_count for the store matching store_id, wherever it lives."""
    registry = load_registry()
    for entries in registry.values():
        for entry in entries:
            if entry.get("store_id") == store_id:
                entry["turn_count"] = entry.get("turn_count", 0) + 1
                save_registry(registry)
                return


def list_stores(directory: str | None = None) -> list[dict]:
    """Return store entries for a specific directory, or all entries if directory is None."""
    registry = load_registry()
    if directory is not None:
        return list(registry.get(directory, []))
    return [entry for entries in registry.values() for entry in entries]


def get_store_directory(store_id: str) -> str | None:
    """Return the directory a store_id belongs to, or None if not found."""
    registry = load_registry()
    for directory, entries in registry.items():
        for entry in entries:
            if entry.get("store_id") == store_id:
                return directory
    return None
