"""
Tree-structured storage layer for PALTrees.

Replaces context_registry.py with a file-per-tree layout where each tree is a
self-contained JSON file rooted at PalRoot. Per-directory folders isolate trees
by project; a global index provides fast tree_path lookup without scanning.

Storage layout:
    {PAL_STORAGE_DIR}/context/
    ├── tree-index.json
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
from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, model_validator

from config import PAL_STORAGE_DIR

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_CTX_DIR = os.path.join(PAL_STORAGE_DIR, "context")
_INDEX_PATH = os.path.join(_CTX_DIR, "tree-index.json")
_ARMED_PATH = os.path.join(_CTX_DIR, "armed.json")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class PalNode(BaseModel):
    """A single node in a PALTree."""

    model_config = ConfigDict(extra="ignore")

    label: str | None = None
    timestamp: str = ""  # ISO 8601
    model: str | None = None
    tool_name: str | None = None
    files: list[str] = []
    input: str = ""  # render_markdown_output(input_dict) — tool call data
    output: str = ""  # render_markdown_output(output_dict) — tool response
    metadata: dict[str, Any] = {}  # workflow state, model info, arbitrary tool metadata
    children: dict[str, PalNode] = {}

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_fields(cls, data):
        """Transparently migrate legacy prompt/response/content fields to input/output."""
        if isinstance(data, dict):
            if "input" not in data:
                content = data.get("content", "")
                if content:
                    user_portion = content.split("\n\n---\n\n", 1)[0]
                    for marker in ["=== CONTEXT LAYER SUBMISSION ===", "=== PALTREE QUERY ==="]:
                        idx = user_portion.rfind(marker)
                        if idx >= 0:
                            data["input"] = user_portion[idx:]
                            break
                    else:
                        data["input"] = data.get("prompt", "")
                else:
                    data["input"] = data.get("prompt", "")
            if "output" not in data:
                data["output"] = data.get("response", "")
            for field in ("prompt", "response", "content"):
                data.pop(field, None)
        return data


class PalRoot(BaseModel):
    """Root of a PALTree JSON file."""

    tree_path: str
    directory: str
    label: str | None = None
    created_at: str
    children: dict[str, PalNode] = {}

    @model_validator(mode="before")
    @classmethod
    def _migrate_store_id(cls, data):
        if isinstance(data, dict) and "store_id" in data and "tree_path" not in data:
            data["tree_path"] = data.pop("store_id")
        return data


# ---------------------------------------------------------------------------
# Traversal log
# ---------------------------------------------------------------------------


@dataclass
class TraversalLog:
    """Records nodes visited during a PALTree traversal.

    Accumulates paths, content size, and token counts as nodes are visited.
    Every PALTree tool operation produces a TraversalLog included in the result.
    """

    traversal_type: str = ""
    nodes_visited: list[str] = field(default_factory=list)
    total_content_chars: int = 0
    total_tokens: int = 0

    def record(self, path: str, node: PalNode) -> None:
        """Record a visited node, accumulating content stats."""
        from utils.token_utils import count_tokens

        self.nodes_visited.append(path)
        content = (node.input or "") + (node.output or "")
        chars = len(content)
        self.total_content_chars += chars
        self.total_tokens += count_tokens(content) if content else 0

    def to_dict(self) -> dict:
        """Serialize to dict for inclusion in ToolOutput metadata."""
        return {
            "traversal_type": self.traversal_type,
            "traversal_order": self.nodes_visited,
            "node_count": len(self.nodes_visited),
            "content_chars": self.total_content_chars,
            "tokens": self.total_tokens,
        }


# ---------------------------------------------------------------------------
# Sort utility (shared by generators and tool rendering)
# ---------------------------------------------------------------------------


def _natural_sort_key(key: str) -> tuple:
    """Sort key that handles mixed alpha-numeric keys naturally: 0, 1, ..., 10."""
    parts = re.split(r"(\d+)", key)
    return tuple(int(p) if p.isdigit() else p for p in parts)


# ---------------------------------------------------------------------------
# Path utilities
# ---------------------------------------------------------------------------


def encode_directory(abs_path: str) -> str:
    """Encode absolute path to a safe directory name. Both / and . become -."""
    return abs_path.replace("/", "-").replace(".", "-")


def get_tree_dir(directory: str) -> str:
    """Return the per-directory folder path inside the PALTree storage root."""
    return os.path.join(_CTX_DIR, encode_directory(directory))


def get_tree_file_path(directory: str, tree_path: str) -> str:
    """Return the full path to a tree's JSON file."""
    return os.path.join(get_tree_dir(directory), f"{tree_path}.json")


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
# Tree load / save
# ---------------------------------------------------------------------------


def load_tree(directory: str, tree_path: str) -> PalRoot | None:
    """Load a PALTree JSON file. Return None if the file does not exist or is corrupt."""
    path = get_tree_file_path(directory, tree_path)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return PalRoot.model_validate(json.load(f))
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def save_tree(tree: PalRoot) -> None:
    """Atomically persist a PalRoot to disk."""
    path = get_tree_file_path(tree.directory, tree.tree_path)
    _atomic_write(path, tree)


def rename_tree(directory: str, old_id: str, new_id: str) -> None:
    """Rename a root PALTree: update tree_path, rename file on disk, update index and armed state."""
    tree = load_tree(directory, old_id)
    if tree is None:
        raise KeyError(f"PALTree not found: {old_id}")
    if "." in new_id:
        raise ValueError("Tree names cannot contain dots.")

    tree.tree_path = new_id
    save_tree(tree)

    old_path = get_tree_file_path(directory, old_id)
    if os.path.exists(old_path):
        os.remove(old_path)

    index = load_index()
    index["trees"].pop(old_id, None)
    encoded = encode_directory(directory)
    index["trees"][new_id] = encoded
    save_index(index)

    armed = load_armed()
    if armed.get(directory) == old_id:
        armed[directory] = new_id
        save_armed(armed)


# ---------------------------------------------------------------------------
# Tree navigation
# ---------------------------------------------------------------------------


def resolve_palnode(tree: PalRoot, tree_path: str) -> PalNode | None:
    """Walk a dot-delimited tree_path to a node.

    The root tree_path prefix is stripped before traversal. Returns None for
    a path that resolves to the root itself (no segments after root).
    """
    _, segments = parse_tree_path(tree_path)
    if not segments:
        return None

    current: dict[str, PalNode] = tree.children
    node: PalNode | None = None
    for seg in segments:
        node = current.get(seg)
        if node is None:
            return None
        current = node.children
    return node


def _classify_key(key: str) -> str:
    """Classify a node key into its type: L, Q, F, C, N, or X (unknown)."""
    for prefix in ("L", "Q", "F", "C"):
        if key.startswith(prefix) and key[len(prefix) :].isdigit():
            return prefix
    if key.isdigit():
        return "N"
    return "X"


# Valid parent→child transitions (the PALTree grammar)
#   ρ → L only
#   L → Q, F, C  (not L)
#   Q → Q, F, C  (Q→Q is intentional: follow-up queries nest for ancestry context)
#   F → L, Q, F, C
#   C → (leaf, no children)
_VALID_CHILDREN: dict[str, set[str]] = {
    "ρ": {"L"},
    "L": {"Q", "F", "C", "N"},
    "Q": {"Q", "F", "C", "N"},
    "F": {"L", "Q", "F", "C", "N"},
    "C": set(),
    "N": set(),
    "X": {"L", "Q", "F", "C", "N", "X"},
}


def add_palnode(tree: PalRoot, parent_path: str, child_key: str, node: PalNode) -> str:
    """Insert a child node at parent_path and return the full path to the new node.

    Validates the insertion against the PALTree grammar before inserting.
    When parent_path equals the tree root id, the node is added directly to
    tree.children. Otherwise the parent is resolved and the node is appended to
    its children dict.
    """
    child_type = _classify_key(child_key)

    if parent_path == tree.tree_path:
        parent_type = "ρ"
    else:
        _, segments = parse_tree_path(parent_path)
        parent_type = _classify_key(segments[-1]) if segments else "ρ"

    allowed = _VALID_CHILDREN.get(parent_type, set())
    if child_type not in allowed:
        raise ValueError(
            f"PALTree grammar violation: {parent_type}→{child_type} "
            f"(key '{child_key}' cannot be a child of '{parent_path}'). "
            f"Allowed child types for {parent_type}: {sorted(allowed)}"
        )

    if parent_path == tree.tree_path:
        tree.children[child_key] = node
    else:
        parent = resolve_palnode(tree, parent_path)
        if parent is None:
            raise KeyError(f"parent path not found in tree: {parent_path}")
        parent.children[child_key] = node
    return f"{parent_path}.{child_key}"


def walk_palnode_ancestry(tree: PalRoot, tree_path: str) -> list[PalNode]:
    """Return nodes from the root down to (and including) the target.

    Pure parent-chain traversal: only direct ancestors are included.
    Returns an empty list when tree_path resolves to the root.
    Delegates to iter_ancestry().
    """
    return [node for _, node in iter_ancestry(tree, tree_path)]


def walk_palnode_range(tree: PalRoot, start_path: str, end_path: str) -> list[PalNode]:
    """Return the ancestor chain from start_path through end_path (inclusive).

    The start node must appear in the ancestry of end_path as a direct tree
    ancestor. Raises ValueError when start is not reachable from end's ancestry.
    Delegates to iter_range().
    """
    return [node for _, node in iter_range(tree, start_path, end_path)]


# ---------------------------------------------------------------------------
# Generator-based traversal primitives
# ---------------------------------------------------------------------------


def iter_ancestry(tree: PalRoot, tree_path: str) -> Generator[tuple[str, PalNode], None, None]:
    """Yield (full_path, node) pairs from root down to the target node.

    The shared traversal primitive for ancestry walks. Consumers use
    collect_traversal() or iterate directly.
    """
    _, segments = parse_tree_path(tree_path)
    if not segments:
        return
    current: dict[str, PalNode] = tree.children
    for i, seg in enumerate(segments):
        node = current.get(seg)
        if node is None:
            return
        full_path = f"{tree.tree_path}.{'.'.join(segments[: i + 1])}"
        yield full_path, node
        current = node.children


def iter_dfs(root_path: str, children: dict[str, PalNode]) -> Generator[tuple[str, PalNode], None, None]:
    """Yield (full_path, node) pairs in depth-first order over a children dict.

    The shared traversal primitive for subtree walks. Depth relative to
    root_path is computable as: full_path.count('.') - root_path.count('.') - 1
    """
    for key in sorted(children, key=_natural_sort_key):
        node = children[key]
        full_path = f"{root_path}.{key}"
        yield full_path, node
        if node.children:
            yield from iter_dfs(full_path, node.children)


def iter_range(tree: PalRoot, start_path: str, end_path: str) -> Generator[tuple[str, PalNode], None, None]:
    """Yield (full_path, node) pairs from start_path through end_path in ancestry.

    start_path must be an ancestor of end_path. Raises ValueError otherwise.
    """
    start_node = resolve_palnode(tree, start_path)
    if start_node is None:
        raise ValueError(f"Start node not found: {start_path}")
    end_node = resolve_palnode(tree, end_path)
    if end_node is None:
        raise ValueError(f"End node not found: {end_path}")

    ancestry_pairs = list(iter_ancestry(tree, end_path))

    start_idx = None
    for i, (_path, node) in enumerate(ancestry_pairs):
        if node is start_node:
            start_idx = i
            break

    if start_idx is None:
        raise ValueError(f"Start node '{start_path}' is not an ancestor of end node '{end_path}'")

    yield from ancestry_pairs[start_idx:]


def collect_traversal(
    gen: Generator[tuple[str, PalNode], None, None],
    traversal_type: str = "",
) -> tuple[list[PalNode], TraversalLog]:
    """Consume a traversal generator, returning (node_list, traversal_log).

    The convenience wrapper that pairs any generator with a TraversalLog.
    """
    tlog = TraversalLog(traversal_type=traversal_type)
    nodes: list[PalNode] = []
    for path, node in gen:
        tlog.record(path, node)
        nodes.append(node)
    return nodes, tlog


def get_next_key(tree: PalRoot, parent_path: str, prefix: str = "") -> str:
    """Compute the next available child key for a given prefix at parent_path.

    Any string prefix is accepted. Keys are auto-incremented from 0.
    Empty prefix produces pure numeric keys (0, 1, 2, ...).
    """
    if parent_path == tree.tree_path:
        siblings = tree.children
    else:
        parent = resolve_palnode(tree, parent_path)
        siblings = parent.children if parent is not None else {}

    if prefix:
        pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
        indices = [int(m.group(1)) for k in siblings if (m := pattern.match(k))]
    else:
        indices = [int(k) for k in siblings if re.fullmatch(r"\d+", k)]

    return f"{prefix}{max(indices) + 1}" if indices else f"{prefix}0"


# ---------------------------------------------------------------------------
# N-ary tree manipulation primitives
# ---------------------------------------------------------------------------


def detach_palnode(tree: PalRoot, node_path: str) -> PalNode:
    """Remove a node from its parent and return it. Siblings are not renumbered.

    The detached node retains its full subtree intact. Raises ValueError if
    node_path resolves to the root (no segments). Raises KeyError if not found.
    """
    _, segments = parse_tree_path(node_path)
    if not segments:
        raise ValueError(f"Cannot detach root tree: {node_path}")

    target_key = segments[-1]

    if len(segments) == 1:
        parent_container = tree.children
    else:
        parent_path_str = f"{tree.tree_path}.{'.'.join(segments[:-1])}"
        parent_node = resolve_palnode(tree, parent_path_str)
        if parent_node is None:
            raise KeyError(f"Parent path not found: {parent_path_str}")
        parent_container = parent_node.children

    if target_key not in parent_container:
        raise KeyError(f"Node not found: {node_path}")

    return parent_container.pop(target_key)


def move_palnode(tree: PalRoot, source_path: str, dest_parent: str, dest_key: str) -> str:
    """Detach a node and reattach at a new location. Returns the new dot-path.

    If add_palnode raises (validation failure), the node is reinserted at its
    original location and the exception is re-raised.
    """
    _, src_segments = parse_tree_path(source_path)
    if not src_segments:
        raise ValueError(f"Cannot move root tree: {source_path}")

    original_key = src_segments[-1]
    if len(src_segments) == 1:
        original_parent_container = tree.children
    else:
        orig_parent_path = f"{tree.tree_path}.{'.'.join(src_segments[:-1])}"
        orig_parent_node = resolve_palnode(tree, orig_parent_path)
        if orig_parent_node is None:
            raise KeyError(f"Source parent not found: {orig_parent_path}")
        original_parent_container = orig_parent_node.children

    node = detach_palnode(tree, source_path)
    try:
        return add_palnode(tree, dest_parent, dest_key, node)
    except Exception:
        original_parent_container[original_key] = node
        raise


def copy_palnode(tree: PalRoot, source_path: str, dest_parent: str, dest_key: str) -> str:
    """Deep copy a node to a new location. Returns the new dot-path.

    The original node is unchanged. Raises KeyError if source_path is not found.
    Raises ValueError if the destination is structurally invalid.
    """
    source = resolve_palnode(tree, source_path)
    if source is None:
        raise KeyError(f"Source node not found: {source_path}")
    clone = source.model_copy(deep=True)
    return add_palnode(tree, dest_parent, dest_key, clone)


def fold_palnode_range(tree: PalRoot, start_path: str, end_path: str) -> tuple[PalNode, TraversalLog]:
    """Aggregate a range of ancestor nodes into a single new PalNode.

    Does NOT insert the result — returns (folded_node, traversal_log).
    """
    from utils.palstore_builder import build_context_from_ancestry

    nodes, tlog = collect_traversal(iter_range(tree, start_path, end_path), "range")
    folded_history = build_context_from_ancestry(nodes)

    seen: set[str] = set()
    files: list[str] = []
    for node in nodes:
        for f in node.files:
            if f not in seen:
                seen.add(f)
                files.append(f)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return PalNode(input=folded_history, files=files, timestamp=timestamp), tlog


def find_palnode_ancestor(
    tree: PalRoot, node_path: str, predicate: Callable[[str, PalNode], bool]
) -> tuple[str, PalNode] | None:
    """Walk from root toward node_path, returning the deepest ancestor matching predicate.

    The predicate receives (segment_key, node) for each ancestor in the path.
    Returns (path, node) for the deepest match, or None if no match is found.
    The target node itself is not checked — only its ancestors.
    """
    _, segments = parse_tree_path(node_path)
    if not segments:
        return None

    last_match: tuple[str, PalNode] | None = None
    current: dict[str, PalNode] = tree.children

    for i, seg in enumerate(segments[:-1]):
        node = current.get(seg)
        if node is None:
            break
        current_path = f"{tree.tree_path}.{'.'.join(segments[: i + 1])}"
        if predicate(seg, node):
            last_match = (current_path, node)
        current = node.children

    return last_match


def collect_palnode_files(node: PalNode) -> list[str]:
    """Recursively gather all unique file paths from a node and its descendants.

    Preserves insertion order. Uses iter_dfs for consistent traversal.
    """
    seen: set[str] = set()
    result: list[str] = []

    for f in node.files:
        if f not in seen:
            seen.add(f)
            result.append(f)
    for _, child in iter_dfs("", node.children):
        for f in child.files:
            if f not in seen:
                seen.add(f)
                result.append(f)

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


def delete_palnode_with_shift(tree: PalRoot, node_path: str) -> dict:
    """Delete a node and shift subsequent same-prefix siblings down by one.

    Accepts a full dot-path like 'myproject.L5' or 'myproject.L2.F1'.
    Returns a dict with:
      - deleted: the full path that was removed
      - shifted: list of (old_path, new_path) tuples
      - had_children: bool

    Raises KeyError if the node does not exist.
    Raises ValueError if the path resolves to a root (no segments).
    """
    _, segments = parse_tree_path(node_path)
    if not segments:
        raise ValueError(f"Cannot delete root tree: {node_path}")

    target_key = segments[-1]

    if len(segments) == 1:
        parent_container = tree.children
        parent_path = tree.tree_path
    else:
        parent_path_str = f"{tree.tree_path}.{'.'.join(segments[:-1])}"
        parent_node = resolve_palnode(tree, parent_path_str)
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
    """Read tree-index.json. Returns an empty structure on missing or corrupt file."""
    if not os.path.exists(_INDEX_PATH):
        os.makedirs(_CTX_DIR, exist_ok=True)
        return {"directories": {}, "trees": {}}
    try:
        with open(_INDEX_PATH) as f:
            data = json.load(f)
        data.setdefault("directories", {})
        data.setdefault("trees", {})
        return data
    except (json.JSONDecodeError, OSError):
        return {"directories": {}, "trees": {}}


def save_index(data: dict) -> None:
    """Atomically write the tree index."""
    _atomic_write(_INDEX_PATH, data)


def update_index(directory: str, tree_path: str) -> None:
    """Add or update an entry in the tree index."""
    index = load_index()
    encoded = encode_directory(directory)
    index["directories"][directory] = encoded
    index["trees"][tree_path] = encoded
    save_index(index)


def rebuild_index() -> dict:
    """Scan all per-directory folders and rebuild the tree index from disk."""
    index: dict = {"directories": {}, "trees": {}}
    if not os.path.isdir(_CTX_DIR):
        return index

    for entry in os.scandir(_CTX_DIR):
        if not entry.is_dir():
            continue
        encoded = entry.name
        for file_entry in os.scandir(entry.path):
            if not file_entry.name.endswith(".json"):
                continue
            tree_path = file_entry.name[:-5]
            try:
                with open(file_entry.path) as f:
                    data = json.load(f)
                directory = data.get("directory", "")
            except (json.JSONDecodeError, OSError):
                continue
            if directory:
                index["directories"][directory] = encoded
            index["trees"][tree_path] = encoded

    save_index(index)
    return index


def resolve_tree_location(tree_path: str) -> tuple[str, str] | None:
    """Look up a tree_path in the index and return (directory, root_tree_path).

    Parses the root segment from tree_path, resolves via index, falls back to
    scanning if not found. Returns None when the PALTree does not exist.
    """
    root, _ = parse_tree_path(tree_path)
    index = load_index()
    encoded = index["trees"].get(root)

    if encoded:
        # Reverse-lookup directory from encoded
        directory = next((d for d, e in index["directories"].items() if e == encoded), None)
        if directory and os.path.exists(get_tree_file_path(directory, root)):
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


def parse_tree_path(tree_path: str) -> tuple[str, list[str]]:
    """Split a dotted tree_path into (root, [segments]).

    Looks up the root in the index first for an exact match. Fallback: first
    dot-split segment is taken as the root.
    """
    if "." not in tree_path:
        return tree_path, []

    index = load_index()
    parts = tree_path.split(".")
    # Greedily find the longest root that exists in the index
    for i in range(len(parts), 0, -1):
        candidate = ".".join(parts[:i])
        if candidate in index["trees"]:
            return candidate, parts[i:]

    # Default: first segment is root
    return parts[0], parts[1:]


# ---------------------------------------------------------------------------
# Armed tree management
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


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def list_trees(directory: str | None = None) -> list[PalRoot]:
    """Return loaded PalRoot objects, optionally filtered to a single directory."""
    if directory is not None:
        folder = get_tree_dir(directory)
        if not os.path.isdir(folder):
            return []
        roots: list[PalRoot] = []
        for entry in os.scandir(folder):
            if entry.name.endswith(".json"):
                tree_path = entry.name[:-5]
                tree = load_tree(directory, tree_path)
                if tree is not None:
                    roots.append(tree)
        return roots

    results: list[PalRoot] = []
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
                    tree = PalRoot.model_validate(json.load(f))
                results.append(tree)
            except (json.JSONDecodeError, OSError, ValueError):
                continue
    return results


def list_all_directories() -> list[str]:
    """Return all directories that have at least one PALTree on disk."""
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
