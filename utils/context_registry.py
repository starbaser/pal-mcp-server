"""
Registry for tracking context stores, keyed by human-readable store_id path.

Persists to {PAL_STORAGE_DIR}/context/stores.json.
"""

import json
import os
import re
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


def get_store_entry(store_id: str) -> dict | None:
    """Look up a registry entry by its store_id path."""
    registry = load_registry()
    return registry.get(store_id)


def resolve_thread_id(store_id: str) -> str | None:
    """Return the internal thread UUID for a given store_id path."""
    entry = get_store_entry(store_id)
    if entry is None:
        return None
    return entry.get("thread_id")


def get_next_query_index(parent_store_id: str) -> int:
    """Count direct Q-children of parent_store_id to determine the next query index."""
    registry = load_registry()
    pattern = re.compile(r"^" + re.escape(parent_store_id) + r"\.Q\d+$")
    count = 0
    for sid, entry in registry.items():
        if (
            entry.get("parent_store_id") == parent_store_id
            and entry.get("entry_type") == "query"
            and pattern.match(sid)
        ):
            count += 1
    return count


def get_next_tool_index(parent_store_id: str, tool_name: str) -> int:
    """Count existing .<tool_name>\\d+ children to determine the next tool fork index."""
    registry = load_registry()
    pattern = re.compile(rf"^{re.escape(parent_store_id)}\.{re.escape(tool_name)}(\d+)$")
    existing = [int(m.group(1)) for key in registry if (m := pattern.match(key))]
    return max(existing, default=-1) + 1


def register_store(
    store_id: str,
    thread_id: str,
    directory: str,
    label: str | None = None,
    model: str = "",
    entry_type: str = "store",
    parent_store_id: str | None = None,
    tool_name: str | None = None,
) -> None:
    """Write a new entry keyed by store_id path."""
    registry = load_registry()
    registry[store_id] = {
        "store_id": store_id,
        "thread_id": thread_id,
        "directory": directory,
        "label": label,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": model,
        "entry_type": entry_type,
        "parent_store_id": parent_store_id,
        "tool_name": tool_name,
        "layer_count": 0,
        "follow_up_count": 0,
    }
    save_registry(registry)


def increment_layer_count(store_id: str) -> str:
    """Increment layer_count on entry, register a new layered path, return it."""
    registry = load_registry()
    entry = registry.get(store_id)
    if entry is None:
        raise KeyError(f"store_id not found: {store_id}")

    entry["layer_count"] = entry.get("layer_count", 0) + 1
    registry[store_id] = entry
    save_registry(registry)

    # Derive next layer number from the path suffix, not the entry's layer_count
    match = re.search(r"\.L(\d+)$", store_id)
    if match:
        base = store_id[: match.start()]
        current_layer = int(match.group(1))
    else:
        base = store_id
        current_layer = 0
    new_path = f"{base}.L{current_layer + 1}"

    register_store(
        store_id=new_path,
        thread_id=entry["thread_id"],
        directory=entry["directory"],
        label=entry.get("label"),
        model=entry.get("model", ""),
        entry_type="store",
        parent_store_id=store_id,
    )
    return new_path


def increment_follow_up_count(store_id: str) -> str:
    """Increment follow_up_count on entry, register a new follow-up path, return it."""
    registry = load_registry()
    entry = registry.get(store_id)
    if entry is None:
        raise KeyError(f"store_id not found: {store_id}")

    entry["follow_up_count"] = entry.get("follow_up_count", 0) + 1
    new_count = entry["follow_up_count"]
    registry[store_id] = entry
    save_registry(registry)

    new_path = f"{store_id}.{new_count}"

    register_store(
        store_id=new_path,
        thread_id=entry["thread_id"],
        directory=entry["directory"],
        label=entry.get("label"),
        model=entry.get("model", ""),
        entry_type="query",
        parent_store_id=store_id,
    )
    return new_path


def list_stores(directory: str | None = None) -> list[dict]:
    """Return all entries, optionally filtered by directory."""
    registry = load_registry()
    if directory is not None:
        return [e for e in registry.values() if e.get("directory") == directory]
    return list(registry.values())
