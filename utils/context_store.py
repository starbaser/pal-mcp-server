"""
Tree-structured storage layer for context stores.

Replaces context_registry.py with a file-per-store layout where each store is a
self-contained JSON tree rooted at StoreRoot. Per-directory folders isolate stores
by project; a global index provides fast store_id lookup without scanning.

Storage layout:
    {PAL_STORAGE_DIR}/context/
    ├── store-index.json
    ├── armed.json
    └── -home-eigenmage-dev-projects-clearcode/
        ├── clearcode-history.json
        └── clearcode-imgui.json
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from typing import Literal

from pydantic import BaseModel

from config import PAL_STORAGE_DIR

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_CTX_DIR = os.path.join(PAL_STORAGE_DIR, "context")
_INDEX_PATH = os.path.join(_CTX_DIR, "store-index.json")
_ARMED_PATH = os.path.join(_CTX_DIR, "armed.json")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class StoreNode(BaseModel):
    """A single node in the context store tree."""

    entry_type: Literal["store", "query", "tool", "fork"]
    label: str | None = None
    timestamp: str  # ISO 8601
    model: str | None = None
    files: list[str] = []
    prompt: str = ""
    response: str = ""
    content: str = ""  # Full API exchange: assembled prompt (with files) + model response
    tool_name: str | None = None
    children: dict[str, StoreNode] = {}


class StoreRoot(BaseModel):
    """Root of a context store JSON file."""

    store_id: str
    directory: str
    label: str | None = None
    created_at: str
    children: dict[str, StoreNode] = {}


# ---------------------------------------------------------------------------
# Path utilities
# ---------------------------------------------------------------------------


def encode_directory(abs_path: str) -> str:
    """Encode absolute path to a safe directory name. Both / and . become -."""
    return abs_path.replace("/", "-").replace(".", "-")


def get_store_dir(directory: str) -> str:
    """Return the per-directory folder path inside the context store."""
    return os.path.join(_CTX_DIR, encode_directory(directory))


def get_store_path(directory: str, store_id: str) -> str:
    """Return the full path to a store's JSON file."""
    return os.path.join(get_store_dir(directory), f"{store_id}.json")


# ---------------------------------------------------------------------------
# Atomic write helper
# ---------------------------------------------------------------------------


def _atomic_write(path: str, data: dict | BaseModel) -> None:
    dir_ = os.path.dirname(path)
    os.makedirs(dir_, exist_ok=True)
    payload = data.model_dump() if isinstance(data, BaseModel) else data
    with tempfile.NamedTemporaryFile(mode="w", dir=dir_, delete=False, suffix=".tmp") as tmp:
        json.dump(payload, tmp, indent=2)
        tmp_path = tmp.name
    os.rename(tmp_path, path)


# ---------------------------------------------------------------------------
# Store load / save
# ---------------------------------------------------------------------------


def load_store(directory: str, store_id: str) -> StoreRoot | None:
    """Load a store JSON file. Return None if the file does not exist or is corrupt."""
    path = get_store_path(directory, store_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return StoreRoot.model_validate(json.load(f))
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def save_store(store: StoreRoot) -> None:
    """Atomically persist a StoreRoot to disk."""
    path = get_store_path(store.directory, store.store_id)
    _atomic_write(path, store)


def rename_store(directory: str, old_id: str, new_id: str) -> None:
    """Rename a root store: update store_id, rename file on disk, update index and armed state."""
    store = load_store(directory, old_id)
    if store is None:
        raise KeyError(f"Store not found: {old_id}")
    if "." in new_id:
        raise ValueError("Store names cannot contain dots.")

    store.store_id = new_id
    save_store(store)

    old_path = get_store_path(directory, old_id)
    if os.path.exists(old_path):
        os.remove(old_path)

    index = load_index()
    index["stores"].pop(old_id, None)
    encoded = encode_directory(directory)
    index["stores"][new_id] = encoded
    save_index(index)

    armed = load_armed()
    if armed.get(directory) == old_id:
        armed[directory] = new_id
        save_armed(armed)


# ---------------------------------------------------------------------------
# Tree navigation
# ---------------------------------------------------------------------------


def resolve_node(store: StoreRoot, store_id: str) -> StoreNode | None:
    """Walk a dot-delimited path to a node.

    The root store_id prefix is stripped before traversal. Returns None for
    a path that resolves to the root itself (no segments after root).
    """
    _, segments = parse_store_path(store_id)
    if not segments:
        return None

    current: dict[str, StoreNode] = store.children
    node: StoreNode | None = None
    for seg in segments:
        node = current.get(seg)
        if node is None:
            return None
        current = node.children
    return node


def add_child(store: StoreRoot, parent_path: str, child_key: str, node: StoreNode) -> str:
    """Insert a child node at parent_path and return the full path to the new node.

    When parent_path equals the store root id, the node is added directly to
    store.children. Otherwise the parent is resolved and the node is appended to
    its children dict.
    """
    if parent_path == store.store_id:
        store.children[child_key] = node
    else:
        parent = resolve_node(store, parent_path)
        if parent is None:
            raise KeyError(f"parent path not found in store: {parent_path}")
        parent.children[child_key] = node
    return f"{parent_path}.{child_key}"


def walk_ancestry(store: StoreRoot, store_id: str) -> list[StoreNode]:
    """Return nodes from the root down to (and including) the target.

    Fork nodes are included — they carry empty prompt/response so callers treat
    them as passthrough. Returns an empty list when store_id resolves to the root.
    """
    _, segments = parse_store_path(store_id)
    if not segments:
        return []

    ancestry: list[StoreNode] = []
    current: dict[str, StoreNode] = store.children
    for seg in segments:
        node = current.get(seg)
        if node is None:
            break
        ancestry.append(node)
        current = node.children
    return ancestry


def get_last_layer_path(store: StoreRoot) -> str | None:
    """Return dotted path to the highest-numbered L-child, or None if no layers exist."""
    count = sum(1 for k in store.children if k.startswith("L") and k[1:].isdigit())
    if not count:
        return None
    return f"{store.store_id}.L{count}"


def get_next_key(store: StoreRoot, parent_path: str, prefix: str) -> str:
    """Compute the next available child key for a given prefix at parent_path.

    Prefix rules:
      "L"           → L-children, start at L1, increment max
      "Q"           → Q-children, start at Q0, increment max
      "F"           → F-children, start at F0, increment max
      "" (empty)    → numeric follow-ups, start at 1
    """
    if parent_path == store.store_id:
        siblings = store.children
    else:
        parent = resolve_node(store, parent_path)
        siblings = parent.children if parent is not None else {}

    if prefix == "":
        # Numeric follow-ups
        nums = [int(k) for k in siblings if re.fullmatch(r"\d+", k)]
        return str(max(nums) + 1) if nums else "1"

    if prefix in ("L", "Q", "F"):
        start = 1 if prefix == "L" else 0
        pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
        indices = [int(m.group(1)) for k in siblings if (m := pattern.match(k))]
        return f"{prefix}{max(indices) + 1}" if indices else f"{prefix}{start}"

    raise ValueError(f"Unsupported prefix for get_next_key: {prefix!r}")


# ---------------------------------------------------------------------------
# Index operations
# ---------------------------------------------------------------------------


def load_index() -> dict:
    """Read store-index.json. Returns an empty structure on missing or corrupt file."""
    if not os.path.exists(_INDEX_PATH):
        os.makedirs(_CTX_DIR, exist_ok=True)
        return {"directories": {}, "stores": {}}
    try:
        with open(_INDEX_PATH) as f:
            data = json.load(f)
        data.setdefault("directories", {})
        data.setdefault("stores", {})
        return data
    except (json.JSONDecodeError, OSError):
        return {"directories": {}, "stores": {}}


def save_index(data: dict) -> None:
    """Atomically write the store index."""
    _atomic_write(_INDEX_PATH, data)


def update_index(directory: str, store_id: str) -> None:
    """Add or update an entry in the store index."""
    index = load_index()
    encoded = encode_directory(directory)
    index["directories"][directory] = encoded
    index["stores"][store_id] = encoded
    save_index(index)


def rebuild_index() -> dict:
    """Scan all per-directory folders and rebuild the store index from disk."""
    index: dict = {"directories": {}, "stores": {}}
    if not os.path.isdir(_CTX_DIR):
        return index

    for entry in os.scandir(_CTX_DIR):
        if not entry.is_dir():
            continue
        encoded = entry.name
        for file_entry in os.scandir(entry.path):
            if not file_entry.name.endswith(".json"):
                continue
            store_id = file_entry.name[:-5]
            try:
                with open(file_entry.path) as f:
                    data = json.load(f)
                directory = data.get("directory", "")
            except (json.JSONDecodeError, OSError):
                continue
            if directory:
                index["directories"][directory] = encoded
            index["stores"][store_id] = encoded

    save_index(index)
    return index


def resolve_store_location(store_id: str) -> tuple[str, str] | None:
    """Look up a store_id in the index and return (directory, root_store_id).

    Parses the root segment from store_id, resolves via index, falls back to
    scanning if not found. Returns None when the store does not exist.
    """
    root, _ = parse_store_path(store_id)
    index = load_index()
    encoded = index["stores"].get(root)

    if encoded:
        # Reverse-lookup directory from encoded
        directory = next((d for d, e in index["directories"].items() if e == encoded), None)
        if directory and os.path.exists(get_store_path(directory, root)):
            return directory, root

    # Fall back to scanning all per-directory folders
    if not os.path.isdir(_CTX_DIR):
        return None
    for entry in os.scandir(_CTX_DIR):
        if not entry.is_dir():
            continue
        candidate = os.path.join(entry.path, f"{root}.json")
        if os.path.exists(candidate):
            try:
                with open(candidate) as f:
                    data = json.load(f)
                directory = data.get("directory", "")
                if directory:
                    update_index(directory, root)
                    return directory, root
            except (json.JSONDecodeError, OSError):
                continue
    return None


# ---------------------------------------------------------------------------
# Parse helper
# ---------------------------------------------------------------------------


def parse_store_path(store_id: str) -> tuple[str, list[str]]:
    """Split a dotted store_id into (root, [segments]).

    Looks up the root in the index first for an exact match. Fallback: first
    dot-split segment is taken as the root.
    """
    if "." not in store_id:
        return store_id, []

    index = load_index()
    parts = store_id.split(".")
    # Greedily find the longest root that exists in the index
    for i in range(len(parts), 0, -1):
        candidate = ".".join(parts[:i])
        if candidate in index["stores"]:
            return candidate, parts[i:]

    # Default: first segment is root
    return parts[0], parts[1:]


# ---------------------------------------------------------------------------
# Armed store management
# ---------------------------------------------------------------------------


def load_armed() -> dict:
    """Read armed.json. Returns {} on missing or corrupt file."""
    if not os.path.exists(_ARMED_PATH):
        os.makedirs(_CTX_DIR, exist_ok=True)
        return {}
    try:
        with open(_ARMED_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_armed(data: dict) -> None:
    """Atomically write armed state to disk."""
    _atomic_write(_ARMED_PATH, data)


def arm_store(directory: str, store_id: str) -> None:
    """Arm a directory for automatic context revival on SessionStart."""
    armed = load_armed()
    armed[directory] = store_id
    save_armed(armed)


def disarm_store(directory: str) -> None:
    """Remove the armed state for a directory."""
    armed = load_armed()
    armed.pop(directory, None)
    save_armed(armed)


def get_armed_store(directory: str) -> str | None:
    """Return the armed store_id for a directory, or None."""
    return load_armed().get(directory)


def list_armed_stores() -> dict:
    """Return the full directory-to-store_id mapping."""
    return load_armed()


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def list_stores(directory: str | None = None) -> list[StoreRoot]:
    """Return loaded StoreRoot objects, optionally filtered to a single directory."""
    if directory is not None:
        folder = get_store_dir(directory)
        if not os.path.isdir(folder):
            return []
        roots: list[StoreRoot] = []
        for entry in os.scandir(folder):
            if entry.name.endswith(".json"):
                store_id = entry.name[:-5]
                store = load_store(directory, store_id)
                if store is not None:
                    roots.append(store)
        return roots

    results: list[StoreRoot] = []
    if not os.path.isdir(_CTX_DIR):
        return results
    for dir_entry in os.scandir(_CTX_DIR):
        if not dir_entry.is_dir():
            continue
        for file_entry in os.scandir(dir_entry.path):
            if not file_entry.name.endswith(".json"):
                continue
            try:
                with open(file_entry.path) as f:
                    store = StoreRoot.model_validate(json.load(f))
                results.append(store)
            except (json.JSONDecodeError, OSError, ValueError):
                continue
    return results


def list_all_directories() -> list[str]:
    """Return all directories that have at least one store on disk."""
    index = load_index()
    dirs: set[str] = set(index["directories"].keys())

    # Also scan for directories not yet in the index
    if os.path.isdir(_CTX_DIR):
        for entry in os.scandir(_CTX_DIR):
            if entry.is_dir():
                for file_entry in os.scandir(entry.path):
                    if file_entry.name.endswith(".json"):
                        try:
                            with open(file_entry.path) as f:
                                data = json.load(f)
                            directory = data.get("directory", "")
                            if directory:
                                dirs.add(directory)
                        except (json.JSONDecodeError, OSError):
                            continue

    return sorted(dirs)
