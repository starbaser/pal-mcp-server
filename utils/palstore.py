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
    """Root of a PALTree JSON file.

    tree_path is canonical: '{directory}:{tree_name}'
    e.g. '/home/eigenmage/dev/projects/clearcode:clearcode-legacy'
    """

    tree_path: str
    label: str | None = None
    created_at: str
    children: dict[str, PalNode] = {}

    @property
    def directory(self) -> str:
        """Project directory extracted from canonical tree_path."""
        return self.tree_path.rsplit(":", 1)[0]

    @property
    def tree_name(self) -> str:
        """Tree name extracted from canonical tree_path."""
        return self.tree_path.rsplit(":", 1)[1]

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy(cls, data):
        """Migrate legacy formats to canonical '{directory}:{tree_name}' tree_path."""
        if isinstance(data, dict):
            if "store_id" in data and "tree_path" not in data:
                data["tree_path"] = data.pop("store_id")

            directory = data.pop("directory", None)
            tp = data.get("tree_path", "")

            if tp and ":" not in tp:
                if directory:
                    data["tree_path"] = f"{directory}:{tp}"

            final_tp = data.get("tree_path", "")
            if ":" in final_tp:
                tree_name = final_tp.rsplit(":", 1)[1]
                if "." in tree_name:
                    raise ValueError(f"tree_name cannot contain periods: {tree_name!r}")
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
        """Serialize to dict for inclusion in ToolOutput metadata.

        traversal_order is compact: ['tree_name:L1-L2-L3-L12.F0-L12.Q0']
        One entry per tree. Nodes joined by '-', segments within by '.'.
        """
        tree_chains: dict[str, list[str]] = {}
        for full_path in self.nodes_visited:
            if ":" in full_path:
                after_colon = full_path.rsplit(":", 1)[1]
                if "." in after_colon:
                    tree_name, node_path = after_colon.split(".", 1)
                else:
                    tree_name, node_path = after_colon, ""
            else:
                parts = full_path.split(".", 1)
                tree_name = parts[0]
                node_path = parts[1] if len(parts) > 1 else ""
            if tree_name not in tree_chains:
                tree_chains[tree_name] = []
            if node_path:
                tree_chains[tree_name].append(node_path)

        traversal_order = [f"{name}:{'-'.join(nodes)}" if nodes else name for name, nodes in tree_chains.items()]

        return {
            "traversal_type": self.traversal_type,
            "traversal_order": traversal_order,
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


def get_tree_file_path(canonical: str) -> str:
    """Return the full path to a tree's JSON file from a canonical tree_path."""
    directory, tree_name = canonical.rsplit(":", 1)
    return os.path.join(get_tree_dir(directory), f"{tree_name}.json")


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


def load_tree(canonical: str) -> PalRoot | None:
    """Load a PALTree JSON file from a canonical tree_path. Return None if not found or corrupt."""
    path = get_tree_file_path(canonical)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return PalRoot.model_validate(json.load(f))
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def save_tree(tree: PalRoot) -> None:
    """Atomically persist a PalRoot to disk."""
    path = get_tree_file_path(tree.tree_path)
    _atomic_write(path, tree)


def rename_tree(canonical: str, new_name: str) -> None:
    """Rename a root PALTree: update tree_path, rename file on disk, update index and armed state."""
    tree = load_tree(canonical)
    if tree is None:
        raise KeyError(f"PALTree not found: {canonical}")
    if "." in new_name:
        raise ValueError("Tree names cannot contain dots.")

    directory = tree.directory
    new_canonical = f"{directory}:{new_name}"
    tree.tree_path = new_canonical
    save_tree(tree)

    old_path = get_tree_file_path(canonical)
    if os.path.exists(old_path):
        os.remove(old_path)

    index = load_index()
    index["trees"].pop(canonical, None)
    encoded = encode_directory(directory)
    index["trees"][new_canonical] = encoded
    save_index(index)

    armed = load_armed()
    if armed.get(directory) == canonical:
        armed[directory] = new_canonical
        save_armed(armed)


# ---------------------------------------------------------------------------
# Tree navigation
# ---------------------------------------------------------------------------


def resolve_node(tree: PalRoot, node_path: str) -> PalNode | None:
    """Walk a dot-delimited node_path to a PalNode within a tree.

    node_path is pure segments (e.g. 'L1.Q0'), no tree prefix.
    Returns None for empty node_path (root) or not found.
    """
    if not node_path:
        return None

    segments = node_path.split(".")
    current: dict[str, PalNode] = tree.children
    node: PalNode | None = None
    for seg in segments:
        node = current.get(seg)
        if node is None:
            return None
        current = node.children
    return node


def resolve_palnode(tree: PalRoot, combined_path: str) -> PalNode | None:
    """DEPRECATED: Use resolve_node(tree, node_path) instead.

    Accepts a combined path (tree_name.L1.Q0) and strips the tree prefix.
    Kept for backward compatibility during migration.
    """
    _, segments = parse_tree_path(combined_path)
    if not segments:
        return None
    return resolve_node(tree, ".".join(segments))


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


def add_palnode(tree: PalRoot, parent_node_path: str, child_key: str, node: PalNode) -> str:
    """Insert a child node and return the full canonical path to the new node.

    parent_node_path is pure node segments (e.g. 'L1', 'L1.Q0'). Empty string
    means the tree root. Validates the insertion against the PALTree grammar.
    """
    child_type = _classify_key(child_key)

    if not parent_node_path:
        parent_type = "ρ"
    else:
        segments = parent_node_path.split(".")
        parent_type = _classify_key(segments[-1])

    allowed = _VALID_CHILDREN.get(parent_type, set())
    if child_type not in allowed:
        raise ValueError(
            f"PALTree grammar violation: {parent_type}→{child_type} "
            f"(key '{child_key}' cannot be a child of '{parent_node_path or 'root'}'). "
            f"Allowed child types for {parent_type}: {sorted(allowed)}"
        )

    if not parent_node_path:
        tree.children[child_key] = node
        return f"{tree.tree_path}.{child_key}"
    else:
        parent = resolve_node(tree, parent_node_path)
        if parent is None:
            raise KeyError(f"parent path not found in tree: {parent_node_path}")
        parent.children[child_key] = node
        return f"{tree.tree_path}.{parent_node_path}.{child_key}"


def walk_palnode_ancestry(tree: PalRoot, node_path: str) -> list[PalNode]:
    """Return nodes from the root down to (and including) the target.

    node_path is pure segments (e.g. 'L1.Q0'). Returns an empty list for
    empty node_path (root). Delegates to iter_ancestry().
    """
    return [node for _, node in iter_ancestry(tree, node_path)]


def walk_palnode_range(tree: PalRoot, start_node_path: str, end_node_path: str) -> list[PalNode]:
    """Return the ancestor chain from start through end (inclusive).

    Both paths are pure node segments. The start must be an ancestor of end.
    Delegates to iter_range().
    """
    return [node for _, node in iter_range(tree, start_node_path, end_node_path)]


# ---------------------------------------------------------------------------
# Generator-based traversal primitives
# ---------------------------------------------------------------------------


def iter_ancestry(tree: PalRoot, node_path: str) -> Generator[tuple[str, PalNode], None, None]:
    """Yield (full_path, node) pairs from root down to the target node.

    node_path is pure segments (e.g. 'L1.Q0'). full_path includes the
    canonical tree prefix for display/logging.
    """
    if not node_path:
        return
    segments = node_path.split(".")
    current: dict[str, PalNode] = tree.children
    for i, seg in enumerate(segments):
        node = current.get(seg)
        if node is None:
            return
        full_path = f"{tree.tree_path}.{'.'.join(segments[: i + 1])}"
        yield full_path, node
        current = node.children


def iter_context(tree: PalRoot, node_path: str) -> Generator[tuple[str, PalNode], None, None]:
    """Yield (full_path, node) pairs for model context building.

    Strata-aware: if node_path targets an L-node (or descends from one),
    walks all L-strata from L1 to the target via calc_traversal.
    Empty node_path defaults to a full strata walk through the last layer.
    Non-L paths fall back to iter_ancestry.

    This is the correct traversal for model-calling tools (addtreelayer,
    querynode) where the model needs full strata context.
    """
    if not node_path:
        # Default: walk all layers (L1 through last)
        l_keys = sorted(
            [k for k in tree.children if re.match(r"^L\d+$", k)],
            key=lambda k: int(k[1:]),
        )
        if not l_keys:
            return
        node_path = l_keys[-1]

    first_seg = node_path.split(".")[0]
    is_l_rooted = bool(re.match(r"^L\d*$", first_seg))

    if is_l_rooted:
        # Strata walk: L1 through the target node
        yield from calc_traversal(tree, "", node_path)
    else:
        yield from iter_ancestry(tree, node_path)


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


def iter_range(tree: PalRoot, start_node_path: str, end_node_path: str) -> Generator[tuple[str, PalNode], None, None]:
    """Yield (full_path, node) pairs from start through end in ancestry.

    Both are pure node segments. start must be an ancestor of end.
    """
    start_node = resolve_node(tree, start_node_path)
    if start_node is None:
        raise ValueError(f"Start node not found: {start_node_path}")
    end_node = resolve_node(tree, end_node_path)
    if end_node is None:
        raise ValueError(f"End node not found: {end_node_path}")

    ancestry_pairs = list(iter_ancestry(tree, end_node_path))

    start_idx = None
    for i, (_path, node) in enumerate(ancestry_pairs):
        if node is start_node:
            start_idx = i
            break

    if start_idx is None:
        raise ValueError(f"Start node '{start_node_path}' is not an ancestor of end node '{end_node_path}'")

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


# ---------------------------------------------------------------------------
# Strata traversal — L-nodes as concentric tree rings
# ---------------------------------------------------------------------------


def _parse_node_point(node_path: str, tree: PalRoot) -> tuple[int, list[str]]:
    """Parse a node_path into (layer_number, sublayer_segments).

    'L24.Q7.F0.thinkdeep' → (24, ['Q7', 'F0', 'thinkdeep'])
    'L3'                   → (3, [])
    'L'                    → (max_layer, [])   # shorthand for last layer
    ''                     → (1, [])           # empty = first layer
    """
    if not node_path:
        # Empty means first layer
        l_keys = sorted(
            [k for k in tree.children if re.match(r"^L\d+$", k)],
            key=lambda k: int(k[1:]),
        )
        if not l_keys:
            raise ValueError("Tree has no L-nodes")
        return int(l_keys[0][1:]), []

    parts = node_path.split(".")
    layer_key = parts[0]
    sublayer = parts[1:]

    if layer_key == "L":
        # Shorthand: last layer
        l_keys = sorted(
            [k for k in tree.children if re.match(r"^L\d+$", k)],
            key=lambda k: int(k[1:]),
        )
        if not l_keys:
            raise ValueError("Tree has no L-nodes")
        return int(l_keys[-1][1:]), sublayer

    m = re.match(r"^L(\d+)$", layer_key)
    if not m:
        raise ValueError(f"node_path must start with an L-key, got: {layer_key!r}")

    return int(m.group(1)), sublayer


def calc_traversal(
    tree: PalRoot,
    node_a: str,
    node_b: str,
) -> Generator[tuple[str, PalNode], None, None]:
    """Yield (full_path, node) pairs for a strata traversal between two nodes.

    L-nodes at root level are concentric strata (tree rings). This generator
    walks through consecutive layers in chronological order, expanding both
    endpoints inward through their sublayer paths.

    Algorithm:
    1. Parse both nodes into (layer_num, sublayer_path)
    2. Sort chronologically (lower layer first)
    3. For each layer in the range:
       - At the earlier endpoint's layer: yield L-node, then expand sublayer
       - At intermediate layers: yield L-node only (cross-section)
       - At the later endpoint's layer: yield L-node, then expand sublayer
    """
    layer_a, sublayer_a = _parse_node_point(node_a, tree)
    layer_b, sublayer_b = _parse_node_point(node_b, tree)

    # Sort chronologically — earlier layer first
    if layer_a <= layer_b:
        earlier_layer, earlier_sub = layer_a, sublayer_a
        later_layer, later_sub = layer_b, sublayer_b
    else:
        earlier_layer, earlier_sub = layer_b, sublayer_b
        later_layer, later_sub = layer_a, sublayer_a

    # Collect all L-keys at root level, sorted
    l_keys = sorted(
        [(int(k[1:]), k) for k in tree.children if re.match(r"^L\d+$", k)],
        key=lambda t: t[0],
    )

    for layer_num, l_key in l_keys:
        if layer_num < earlier_layer or layer_num > later_layer:
            continue

        l_node = tree.children[l_key]
        l_path = f"{tree.tree_path}.{l_key}"
        yield l_path, l_node

        # Expand sublayer at endpoints
        sublayer: list[str] | None = None
        if layer_num == earlier_layer and earlier_sub:
            sublayer = earlier_sub
        elif layer_num == later_layer and later_sub:
            sublayer = later_sub

        if sublayer:
            current_node = l_node
            current_path = l_path
            for seg in sublayer:
                child = current_node.children.get(seg)
                if child is None:
                    break
                current_path = f"{current_path}.{seg}"
                yield current_path, child
                current_node = child


def get_next_key(tree: PalRoot, parent_node_path: str, prefix: str = "") -> str:
    """Compute the next available child key for a given prefix.

    parent_node_path is pure segments ('' for root).
    Any string prefix is accepted. Keys are auto-incremented from 0.
    """
    if not parent_node_path:
        siblings = tree.children
    else:
        parent = resolve_node(tree, parent_node_path)
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

    node_path is pure segments (e.g. 'L1.Q0'). Raises ValueError if empty.
    """
    if not node_path:
        raise ValueError("Cannot detach root tree")

    segments = node_path.split(".")
    target_key = segments[-1]

    if len(segments) == 1:
        parent_container = tree.children
    else:
        parent_node_path = ".".join(segments[:-1])
        parent_node = resolve_node(tree, parent_node_path)
        if parent_node is None:
            raise KeyError(f"Parent path not found: {parent_node_path}")
        parent_container = parent_node.children

    if target_key not in parent_container:
        raise KeyError(f"Node not found: {node_path}")

    return parent_container.pop(target_key)


def move_palnode(tree: PalRoot, source_node_path: str, dest_node_path: str, dest_key: str) -> str:
    """Detach a node and reattach at a new location. Returns the new full canonical path.

    Both paths are pure node segments. dest_node_path is '' for root.
    Rollback on failure.
    """
    if not source_node_path:
        raise ValueError("Cannot move root tree")

    segments = source_node_path.split(".")
    original_key = segments[-1]
    if len(segments) == 1:
        original_parent_container = tree.children
    else:
        parent_node_path = ".".join(segments[:-1])
        orig_parent_node = resolve_node(tree, parent_node_path)
        if orig_parent_node is None:
            raise KeyError(f"Source parent not found: {parent_node_path}")
        original_parent_container = orig_parent_node.children

    node = detach_palnode(tree, source_node_path)
    try:
        return add_palnode(tree, dest_node_path, dest_key, node)
    except Exception:
        original_parent_container[original_key] = node
        raise


def copy_palnode(tree: PalRoot, source_node_path: str, dest_node_path: str, dest_key: str) -> str:
    """Deep copy a node to a new location. Returns the new full canonical path.

    Both paths are pure node segments. dest_node_path is '' for root.
    """
    source = resolve_node(tree, source_node_path)
    if source is None:
        raise KeyError(f"Source node not found: {source_node_path}")
    clone = source.model_copy(deep=True)
    return add_palnode(tree, dest_node_path, dest_key, clone)


def fold_palnode_range(tree: PalRoot, start_node_path: str, end_node_path: str) -> tuple[PalNode, TraversalLog]:
    """Aggregate a range of ancestor nodes into a single new PalNode.

    Both paths are pure node segments. Does NOT insert the result.
    """
    from utils.palstore_builder import build_context_from_ancestry

    nodes, tlog = collect_traversal(iter_range(tree, start_node_path, end_node_path), "range")
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

    node_path is pure segments. The predicate receives (segment_key, node).
    Returns (full_path, node) for the deepest match, or None. Target itself is not checked.
    """
    if not node_path:
        return None

    segments = node_path.split(".")
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

    node_path is pure segments (e.g. 'L5', 'L2.F1').
    Returns a dict with:
      - deleted: the node_path that was removed
      - shifted: list of (old_node_path, new_node_path) tuples
      - had_children: bool
    """
    if not node_path:
        raise ValueError("Cannot delete root tree")

    segments = node_path.split(".")
    target_key = segments[-1]

    if len(segments) == 1:
        parent_container = tree.children
        parent_path = ""
    else:
        parent_node_path = ".".join(segments[:-1])
        parent_node = resolve_node(tree, parent_node_path)
        if parent_node is None:
            raise KeyError(f"Parent path not found: {parent_node_path}")
        parent_container = parent_node.children
        parent_path = parent_node_path

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
        old_np = f"{parent_path}.{old_key}" if parent_path else old_key
        new_np = f"{parent_path}.{new_key}" if parent_path else new_key
        shifted.append((old_np, new_np))

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


def update_index(canonical: str) -> None:
    """Add or update an entry in the tree index from a canonical tree_path."""
    directory, _ = canonical.rsplit(":", 1)
    index = load_index()
    encoded = encode_directory(directory)
    index["directories"][directory] = encoded
    index["trees"][canonical] = encoded
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
            try:
                with open(file_entry.path) as f:
                    data = json.load(f)
                tree = PalRoot.model_validate(data)
                canonical = tree.tree_path
                directory = tree.directory
            except (json.JSONDecodeError, OSError, ValueError):
                continue
            if directory:
                index["directories"][directory] = encoded
            index["trees"][canonical] = encoded

    save_index(index)
    return index


def resolve_tree_path(raw: str) -> str:
    """Resolve a raw tree identifier to a canonical tree_path.

    Accepts:
      '/abs/path:name'  -> canonical, returned as-is (validated to exist)
      'name'            -> shorthand, resolved via index scan (must be unique)

    Raises KeyError if not found. Raises ValueError if ambiguous.
    """
    if ":" in raw:
        if os.path.exists(get_tree_file_path(raw)):
            return raw
        raise KeyError(f"PALTree not found: {raw}")

    # Shorthand: scan index for matching tree_name
    index = load_index()
    matches = []
    for key in index["trees"]:
        if ":" in key:
            tree_name = key.rsplit(":", 1)[1]
            if tree_name == raw:
                matches.append(key)
        elif key == raw:
            # Legacy index entry (pre-migration) — resolve by loading the tree file
            encoded = index["trees"][key]
            candidate = os.path.join(_CTX_DIR, encoded, f"{raw}.json")
            if os.path.exists(candidate):
                try:
                    with open(candidate) as f:
                        data = json.load(f)
                    tree = PalRoot.model_validate(data)
                    # Migrate index entry
                    index["trees"].pop(key, None)
                    index["trees"][tree.tree_path] = encoded
                    save_index(index)
                    return tree.tree_path
                except (json.JSONDecodeError, OSError, ValueError):
                    pass

    if len(matches) == 1:
        return matches[0]
    elif len(matches) > 1:
        raise ValueError(f"Ambiguous tree name '{raw}' matches {len(matches)} trees: " + ", ".join(matches))

    # Fall back to scanning all per-directory folders
    if not os.path.isdir(_CTX_DIR):
        raise KeyError(f"PALTree not found: {raw}")

    for entry in os.scandir(_CTX_DIR):
        if not entry.is_dir():
            continue
        candidate = os.path.join(entry.path, f"{raw}.json")
        if os.path.exists(candidate):
            try:
                with open(candidate) as f:
                    data = json.load(f)
                tree = PalRoot.model_validate(data)
                update_index(tree.tree_path)
                return tree.tree_path
            except (json.JSONDecodeError, OSError, ValueError):
                continue

    raise KeyError(f"PALTree not found: {raw}")


def resolve_tree_location(tree_path: str) -> tuple[str, str] | None:
    """DEPRECATED: Use resolve_tree_path() instead.

    Returns (directory, tree_name) for backward compatibility, or None if not found.
    """
    try:
        canonical = resolve_tree_path(tree_path)
        directory, tree_name = canonical.rsplit(":", 1)
        return directory, tree_name
    except (KeyError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Parse helper
# ---------------------------------------------------------------------------


def parse_tree_path(raw: str) -> tuple[str, list[str]]:
    """Split a combined path into (canonical_tree_path, node_segments).

    Accepts:
      '/abs/path:name.L1.Q0' -> ('/abs/path:name', ['L1', 'Q0'])
      '/abs/path:name'       -> ('/abs/path:name', [])
      'name.L1.Q0'           -> (resolve_tree_path('name'), ['L1', 'Q0'])
      'name'                 -> (resolve_tree_path('name'), [])

    Since periods are forbidden in tree_name, the first '.' after the
    tree identifier always starts the node segments.
    """
    if not raw:
        return "", []

    if ":" in raw:
        colon_idx = raw.rindex(":")
        after_colon = raw[colon_idx + 1 :]
        if "." in after_colon:
            dot_idx = after_colon.index(".")
            tree_path = raw[: colon_idx + 1 + dot_idx]
            node_str = after_colon[dot_idx + 1 :]
            return tree_path, node_str.split(".") if node_str else []
        else:
            return raw, []
    else:
        if "." in raw:
            dot_idx = raw.index(".")
            tree_name = raw[:dot_idx]
            node_str = raw[dot_idx + 1 :]
            canonical = resolve_tree_path(tree_name)
            return canonical, node_str.split(".") if node_str else []
        else:
            canonical = resolve_tree_path(raw)
            return canonical, []


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
                try:
                    with open(entry.path) as f:
                        tree = PalRoot.model_validate(json.load(f))
                    roots.append(tree)
                except (json.JSONDecodeError, OSError, ValueError):
                    continue
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
                            tree = PalRoot.model_validate(data)
                            directory = tree.directory
                            if directory:
                                dirs.add(directory)
                        except (json.JSONDecodeError, OSError, ValueError):
                            continue

    return sorted(dirs)
