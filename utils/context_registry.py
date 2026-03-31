"""
Registry for tracking context trees, keyed by human-readable tree_path.

Persists to {PAL_STORAGE_DIR}/context/stores.json.
"""

import json
import os
import re
import tempfile
from datetime import datetime, timezone

from config import PAL_STORAGE_DIR

_REGISTRY_PATH = os.path.join(PAL_STORAGE_DIR, "context", "stores.json")
_ARMED_PATH = os.path.join(PAL_STORAGE_DIR, "context", "armed.json")


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


def get_tree_entry(tree_path: str) -> dict | None:
    """Look up a registry entry by its tree_path."""
    registry = load_registry()
    return registry.get(tree_path)


def resolve_thread_id(tree_path: str) -> str | None:
    """Return the internal thread UUID for a given tree_path."""
    entry = get_tree_entry(tree_path)
    if entry is None:
        return None
    return entry.get("thread_id")


def get_next_query_index(parent_tree_path: str) -> int:
    """Count direct Q-children of parent_tree_path to determine the next query index."""
    registry = load_registry()
    pattern = re.compile(r"^" + re.escape(parent_tree_path) + r"\.Q\d+$")
    count = 0
    for sid, entry in registry.items():
        if entry.get("parent_tree_path") == parent_tree_path and pattern.match(sid):
            count += 1
    return count


def get_next_tool_index(parent_tree_path: str, tool_name: str) -> int:
    """Count existing .<tool_name>\\d+ children to determine the next tool fork index."""
    registry = load_registry()
    pattern = re.compile(rf"^{re.escape(parent_tree_path)}\.{re.escape(tool_name)}(\d+)$")
    existing = [int(m.group(1)) for key in registry if (m := pattern.match(key))]
    return max(existing, default=-1) + 1


def register_tree(
    tree_path: str,
    thread_id: str,
    directory: str,
    label: str | None = None,
    model: str = "",
    parent_tree_path: str | None = None,
    tool_name: str | None = None,
) -> None:
    """Write a new entry keyed by tree_path."""
    registry = load_registry()
    registry[tree_path] = {
        "tree_path": tree_path,
        "thread_id": thread_id,
        "directory": directory,
        "label": label,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": model,
        "parent_tree_path": parent_tree_path,
        "tool_name": tool_name,
        "layer_count": 0,
        "follow_up_count": 0,
    }
    save_registry(registry)


def increment_layer_count(tree_path: str) -> str:
    """Increment layer_count on entry, register a new layered path, return it."""
    registry = load_registry()
    entry = registry.get(tree_path)
    if entry is None:
        raise KeyError(f"tree_path not found: {tree_path}")

    entry["layer_count"] = entry.get("layer_count", 0) + 1
    registry[tree_path] = entry
    save_registry(registry)

    # Derive next layer number from the path suffix, not the entry's layer_count
    match = re.search(r"\.L(\d+)$", tree_path)
    if match:
        base = tree_path[: match.start()]
        current_layer = int(match.group(1))
    else:
        base = tree_path
        current_layer = 0
    new_path = f"{base}.L{current_layer + 1}"

    register_tree(
        tree_path=new_path,
        thread_id=entry["thread_id"],
        directory=entry["directory"],
        label=entry.get("label"),
        model=entry.get("model", ""),
        parent_tree_path=tree_path,
    )
    return new_path


def increment_follow_up_count(tree_path: str) -> str:
    """Increment follow_up_count on entry, register a new follow-up path, return it."""
    registry = load_registry()
    entry = registry.get(tree_path)
    if entry is None:
        raise KeyError(f"tree_path not found: {tree_path}")

    entry["follow_up_count"] = entry.get("follow_up_count", 0) + 1
    new_count = entry["follow_up_count"]
    registry[tree_path] = entry
    save_registry(registry)

    new_path = f"{tree_path}.{new_count}"

    register_tree(
        tree_path=new_path,
        thread_id=entry["thread_id"],
        directory=entry["directory"],
        label=entry.get("label"),
        model=entry.get("model", ""),
        parent_tree_path=tree_path,
    )
    return new_path


def list_trees(directory: str | None = None) -> list[dict]:
    """Return all entries, optionally filtered by directory."""
    registry = load_registry()
    if directory is not None:
        return [e for e in registry.values() if e.get("directory") == directory]
    return list(registry.values())


def build_tree_listing(entries: list[dict]) -> list[dict]:
    """Build a tree from flat registry entries using parent_tree_path relationships.

    Returns root nodes, each augmented with a sorted ``children`` list.
    Orphans (parent not in the entry set) are promoted to roots.
    """
    by_id: dict[str, dict] = {}
    for entry in entries:
        node = {**entry, "children": []}
        by_id[node["tree_path"]] = node

    roots: list[dict] = []
    for node in by_id.values():
        parent_sid = node.get("parent_tree_path")
        parent = by_id.get(parent_sid) if parent_sid else None
        if parent is not None:
            parent["children"].append(node)
        else:
            roots.append(node)

    def _sort(nodes: list[dict]) -> None:
        nodes.sort(key=lambda n: n["tree_path"])
        for n in nodes:
            _sort(n["children"])

    _sort(roots)
    return roots


# ---------------------------------------------------------------------------
# Armed tree management — {PAL_STORAGE_DIR}/context/armed.json
# Maps directory paths to tree_paths for automatic SessionStart revival.
# ---------------------------------------------------------------------------


def load_armed() -> dict:
    """Read armed.json, returning {} if missing or corrupt."""
    if not os.path.exists(_ARMED_PATH):
        os.makedirs(os.path.dirname(_ARMED_PATH), exist_ok=True)
        return {}
    try:
        with open(_ARMED_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_armed(data: dict) -> None:
    """Atomically write armed state to disk."""
    dir_ = os.path.dirname(_ARMED_PATH)
    os.makedirs(dir_, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=dir_, delete=False, suffix=".tmp") as tmp:
        json.dump(data, tmp, indent=2)
        tmp_path = tmp.name
    os.rename(tmp_path, _ARMED_PATH)


def arm_tree(directory: str, tree_path: str) -> None:
    """Arm a directory for automatic context revival on SessionStart."""
    armed = load_armed()
    armed[directory] = tree_path
    save_armed(armed)


def disarm_tree(directory: str) -> None:
    """Remove the armed state for a directory."""
    armed = load_armed()
    armed.pop(directory, None)
    save_armed(armed)


def get_armed_tree(directory: str) -> str | None:
    """Return the armed tree_path for a directory, or None."""
    return load_armed().get(directory)


def list_armed_trees() -> dict:
    """Return the full directory-to-tree_path mapping."""
    return load_armed()
