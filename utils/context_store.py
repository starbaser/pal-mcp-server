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
from datetime import datetime, timezone
from typing import Callable, Literal

from pydantic import BaseModel

from config import PAL_STORAGE_DIR

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_CTX_DIR = os.path.join(PAL_STORAGE_DIR, "context")
_INDEX_PATH = os.path.join(_CTX_DIR, "store-index.json")
_ARMED_PATH = os.path.join(_CTX_DIR, "armed.json")


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------


def _categorize_key(key: str) -> str:
    """Categorize a node key: 'L', 'Q', 'F', 'numeric', or 'tool'."""
    if key.startswith("L") and key[1:].isdigit():
        return "L"
    if key.startswith("Q") and key[1:].isdigit():
        return "Q"
    if key.startswith("F") and key[1:].isdigit():
        return "F"
    if key.isdigit():
        return "numeric"
    return "tool"


VALID_CHILD_KEYS: dict[str, set[str]] = {
    "root": {"L", "F"},
    "store": {"Q", "F"},
    "query": {"Q", "numeric", "F"},
    "fork": {"L", "Q", "F", "tool"},
    "tool": {"F", "numeric"},
}


def _is_l_node_key(key: str) -> bool:
    """Return True if key is an L-node key (e.g. 'L1', 'L14')."""
    return key.startswith("L") and key[1:].isdigit()


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

    Raises ValueError when the child key category violates the tree structure
    rules defined in VALID_CHILD_KEYS.
    """
    child_cat = _categorize_key(child_key)
    if parent_path == store.store_id:
        parent_type = "root"
    else:
        parent = resolve_node(store, parent_path)
        if parent is None:
            raise KeyError(f"parent path not found in store: {parent_path}")
        parent_type = parent.entry_type

    allowed = VALID_CHILD_KEYS.get(parent_type, set())
    if child_cat not in allowed:
        raise ValueError(
            f"Invalid tree structure: {child_cat}-node '{child_key}' "
            f"cannot be a child of {parent_type}-node at '{parent_path}'"
        )

    if parent_path == store.store_id:
        store.children[child_key] = node
    else:
        resolve_node(store, parent_path).children[child_key] = node
    return f"{parent_path}.{child_key}"


def walk_ancestry(store: StoreRoot, store_id: str) -> list[StoreNode]:
    """Return nodes from the root down to (and including) the target.

    Layers are cumulative: when the target segment at any level is an L-node,
    all numerically preceding L-siblings at that level are included in order.
    For example, walking to ``L14.F0`` returns ``[L0, L1, ..., L14, F0]``.

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
        if _is_l_node_key(seg):
            target_idx = int(seg[1:])
            preceding = [(int(k[1:]), v) for k, v in current.items() if _is_l_node_key(k) and int(k[1:]) < target_idx]
            preceding.sort(key=lambda kv: kv[0])
            ancestry.extend(v for _, v in preceding)
        ancestry.append(node)
        current = node.children
    return ancestry


def walk_range(store: StoreRoot, start_path: str, end_path: str) -> list[StoreNode]:
    """Return the ancestor chain from start_path through end_path (inclusive).

    Uses the same cumulative L-node logic as walk_ancestry. The start node must
    appear in the ancestry of end_path — either as a direct tree ancestor or as
    a preceding L-sibling at the same level. Raises ValueError when start is not
    reachable from end's ancestry.
    """
    start_node = resolve_node(store, start_path)
    if start_node is None:
        raise ValueError(f"Start node not found: {start_path}")

    end_node = resolve_node(store, end_path)
    if end_node is None:
        raise ValueError(f"End node not found: {end_path}")

    ancestry = walk_ancestry(store, end_path)

    start_idx = None
    for i, node in enumerate(ancestry):
        if node is start_node:
            start_idx = i
            break

    if start_idx is None:
        raise ValueError(f"Start node '{start_path}' is not an ancestor of end node '{end_path}'")

    return ancestry[start_idx:]


def get_last_layer_path(store: StoreRoot, parent_path: str | None = None) -> str | None:
    """Return dotted path to the highest-numbered L-child, or None if no layers exist."""
    if parent_path is None:
        parent_path = store.store_id

    if parent_path == store.store_id:
        container = store.children
    else:
        parent_node = resolve_node(store, parent_path)
        container = parent_node.children if parent_node is not None else {}

    indices = [int(k[1:]) for k in container if _is_l_node_key(k)]
    if not indices:
        return None
    return f"{parent_path}.L{max(indices)}"


def resolve_root_alias(store: StoreRoot, store_id: str) -> str:
    """Resolve root store_id alias to the current top layer path.

    Root store_id is shorthand for the latest L-child. Returns store_id
    unchanged if it's not the root, or if the root has no layers yet.
    """
    if store_id != store.store_id:
        return store_id
    last = get_last_layer_path(store)
    if last is None:
        return store_id
    return last


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


def resolve_layer_insertion_point(store: StoreRoot, store_id: str) -> str:
    """Return the parent path where the next L-node should be inserted.

    When store_id points directly to an L-node (e.g. 'myproject.L7'), the next
    L-node is a sibling — return L7's parent path. For root, fork, query, or tool
    nodes, the next L-node is a child — return store_id itself.
    """
    _, segments = parse_store_path(store_id)
    if not segments:
        return store_id

    last_seg = segments[-1]
    if _is_l_node_key(last_seg):
        if len(segments) == 1:
            return store.store_id
        return f"{store.store_id}.{'.'.join(segments[:-1])}"
    return store_id


# ---------------------------------------------------------------------------
# N-ary tree manipulation primitives
# ---------------------------------------------------------------------------


def detach_node(store: StoreRoot, node_path: str) -> StoreNode:
    """Remove a node from its parent and return it. Siblings are not renumbered.

    The detached node retains its full subtree intact. Raises ValueError if
    node_path resolves to the root (no segments). Raises KeyError if not found.
    """
    _, segments = parse_store_path(node_path)
    if not segments:
        raise ValueError(f"Cannot detach root store: {node_path}")

    target_key = segments[-1]

    if len(segments) == 1:
        parent_container = store.children
    else:
        parent_path_str = f"{store.store_id}.{'.'.join(segments[:-1])}"
        parent_node = resolve_node(store, parent_path_str)
        if parent_node is None:
            raise KeyError(f"Parent path not found: {parent_path_str}")
        parent_container = parent_node.children

    if target_key not in parent_container:
        raise KeyError(f"Node not found: {node_path}")

    return parent_container.pop(target_key)


def move_node(store: StoreRoot, source_path: str, dest_parent: str, dest_key: str) -> str:
    """Detach a node and reattach at a new location. Returns the new dot-path.

    If add_child raises (validation failure), the node is reinserted at its
    original location and the exception is re-raised.
    """
    _, src_segments = parse_store_path(source_path)
    if not src_segments:
        raise ValueError(f"Cannot move root store: {source_path}")

    original_key = src_segments[-1]
    if len(src_segments) == 1:
        original_parent_container = store.children
    else:
        orig_parent_path = f"{store.store_id}.{'.'.join(src_segments[:-1])}"
        orig_parent_node = resolve_node(store, orig_parent_path)
        if orig_parent_node is None:
            raise KeyError(f"Source parent not found: {orig_parent_path}")
        original_parent_container = orig_parent_node.children

    node = detach_node(store, source_path)
    try:
        return add_child(store, dest_parent, dest_key, node)
    except Exception:
        original_parent_container[original_key] = node
        raise


def copy_node(store: StoreRoot, source_path: str, dest_parent: str, dest_key: str) -> str:
    """Deep copy a node to a new location. Returns the new dot-path.

    The original node is unchanged. Raises KeyError if source_path is not found.
    Raises ValueError if the destination is structurally invalid.
    """
    source = resolve_node(store, source_path)
    if source is None:
        raise KeyError(f"Source node not found: {source_path}")
    clone = source.model_copy(deep=True)
    return add_child(store, dest_parent, dest_key, clone)


def fold_range(store: StoreRoot, start_path: str, end_path: str) -> StoreNode:
    """Aggregate a range of ancestor nodes into a single new StoreNode.

    Does NOT insert the result — returns the folded node for the caller to place.
    Fork nodes in the range are skipped when collecting files (they carry no
    prompt/response content). The returned node has entry_type="store".
    """
    from utils.context_builder import build_context_from_ancestry

    nodes = walk_range(store, start_path, end_path)
    content = build_context_from_ancestry(nodes)

    seen: set[str] = set()
    files: list[str] = []
    for node in nodes:
        if node.entry_type == "fork":
            continue
        for f in node.files:
            if f not in seen:
                seen.add(f)
                files.append(f)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return StoreNode(entry_type="store", content=content, files=files, timestamp=timestamp, prompt="", response="")


def find_ancestor(
    store: StoreRoot, node_path: str, predicate: Callable[[str, StoreNode], bool]
) -> tuple[str, StoreNode] | None:
    """Walk from root toward node_path, returning the deepest ancestor matching predicate.

    The predicate receives (segment_key, node) for each ancestor in the path.
    Returns (path, node) for the deepest match, or None if no match is found.
    The target node itself is not checked — only its ancestors.
    """
    _, segments = parse_store_path(node_path)
    if not segments:
        return None

    last_match: tuple[str, StoreNode] | None = None
    current: dict[str, StoreNode] = store.children

    for i, seg in enumerate(segments[:-1]):
        node = current.get(seg)
        if node is None:
            break
        current_path = f"{store.store_id}.{'.'.join(segments[:i + 1])}"
        if predicate(seg, node):
            last_match = (current_path, node)
        current = node.children

    return last_match


def is_l_ancestor(key: str, node: StoreNode) -> bool:  # noqa: ARG001
    """Predicate: True when the ancestor key is an L-node key."""
    return _is_l_node_key(key)


def is_fork_ancestor(key: str, node: StoreNode) -> bool:  # noqa: ARG001
    """Predicate: True when the ancestor node is a fork."""
    return node.entry_type == "fork"


def collect_subtree_files(node: StoreNode) -> list[str]:
    """Recursively gather all unique file paths from a node and its descendants.

    Preserves insertion order. DFS traversal.
    """
    seen: set[str] = set()
    result: list[str] = []

    def _collect(n: StoreNode) -> None:
        for f in n.files:
            if f not in seen:
                seen.add(f)
                result.append(f)
        for child in n.children.values():
            _collect(child)

    _collect(node)
    return result


def _get_key_prefix(key: str) -> str:
    """Extract the alpha prefix from a node key. Returns '' for pure-numeric keys."""
    m = re.match(r"^([A-Za-z]+)", key)
    return m.group(1) if m else ""


def _get_key_index(key: str) -> int:
    """Extract the trailing integer from a node key."""
    m = re.search(r"(\d+)$", key)
    if m is None:
        raise ValueError(f"Key has no trailing integer: {key!r}")
    return int(m.group(1))


def delete_node_with_shift(store: StoreRoot, node_path: str) -> dict:
    """Delete a node and shift subsequent same-prefix siblings down by one.

    Accepts a full dot-path like 'myproject.L5' or 'myproject.L2.F1'.
    Returns a dict with:
      - deleted: the full path that was removed
      - shifted: list of (old_path, new_path) tuples
      - had_children: bool

    Raises KeyError if the node does not exist.
    Raises ValueError if the path resolves to a root (no segments).
    """
    _, segments = parse_store_path(node_path)
    if not segments:
        raise ValueError(f"Cannot delete root store: {node_path}")

    target_key = segments[-1]

    if len(segments) == 1:
        parent_container = store.children
        parent_path = store.store_id
    else:
        parent_path_str = f"{store.store_id}.{'.'.join(segments[:-1])}"
        parent_node = resolve_node(store, parent_path_str)
        if parent_node is None:
            raise KeyError(f"Parent path not found: {parent_path_str}")
        parent_container = parent_node.children
        parent_path = parent_path_str

    if target_key not in parent_container:
        raise KeyError(f"Node not found: {node_path}")

    had_children = bool(parent_container[target_key].children)

    prefix = _get_key_prefix(target_key)
    target_index = _get_key_index(target_key)

    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$") if prefix else re.compile(r"^(\d+)$")
    higher_keys = sorted(
        [k for k in parent_container if pattern.match(k) and _get_key_index(k) > target_index],
        key=_get_key_index,
    )

    del parent_container[target_key]

    shifted: list[tuple[str, str]] = []
    for old_key in higher_keys:
        old_index = _get_key_index(old_key)
        new_key = f"{prefix}{old_index - 1}"
        node = parent_container.pop(old_key)
        parent_container[new_key] = node
        shifted.append((f"{parent_path}.{old_key}", f"{parent_path}.{new_key}"))

    return {"deleted": node_path, "shifted": shifted, "had_children": had_children}


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
