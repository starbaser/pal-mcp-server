#!/usr/bin/env python3
"""Migrate old context stores (stores.json + threads) to new tree-based format.

Non-destructive: old files are never deleted. Safe to run multiple times — an
existing new-format store file is overwritten with an identical result.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import CONVERSATION_STORAGE_DIR, PAL_STORAGE_DIR
from utils.context_store import StoreNode, StoreRoot, save_store, update_index

_OLD_REGISTRY_PATH = os.path.join(PAL_STORAGE_DIR, "context", "stores.json")
_THREADS_DIR = CONVERSATION_STORAGE_DIR

# ---------------------------------------------------------------------------
# Summary counters
# ---------------------------------------------------------------------------

_stats: dict[str, int] = {
    "directories": 0,
    "stores_created": 0,
    "nodes_migrated": 0,
    "threads_missing": 0,
    "turns_skipped": 0,
    "errors": 0,
}


def _err(msg: str) -> None:
    print(f"  [ERROR] {msg}", file=sys.stderr)
    _stats["errors"] += 1


# ---------------------------------------------------------------------------
# Thread file loading
# ---------------------------------------------------------------------------


def load_thread_file(thread_id: str) -> list[dict] | None:
    """Load the turns list from a thread file. Returns None on any failure."""
    path = os.path.join(_THREADS_DIR, f"thread_{thread_id}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            outer = json.load(f)
        inner_str = outer.get("value")
        if not isinstance(inner_str, str):
            return None
        inner = json.loads(inner_str)
        turns = inner.get("turns")
        if not isinstance(turns, list):
            return None
        return turns
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def _extract_turn_pair(turns: list[dict], pair_index: int) -> tuple[str, str, str, str, str] | None:
    """Extract (prompt, response, label, model, timestamp) for turn pair at pair_index.

    Pair 0 is turns[0:2], pair 1 is turns[2:4], etc.  A pair is valid only if
    it contains at least one assistant turn with non-empty content.  Returns
    None for incomplete or empty pairs.
    """
    base = pair_index * 2
    if base >= len(turns):
        return None

    user_turn = turns[base] if base < len(turns) else None
    asst_turn = turns[base + 1] if base + 1 < len(turns) else None

    prompt = (user_turn.get("content") or "").strip() if user_turn else ""
    response = (asst_turn.get("content") or "").strip() if asst_turn else ""

    if not response:
        _stats["turns_skipped"] += 1
        return None

    label = ""
    model = ""
    timestamp = ""
    if asst_turn:
        meta = asst_turn.get("model_metadata") or {}
        label = meta.get("context_label") or asst_turn.get("tool_name") or ""
        model = asst_turn.get("model_name") or ""
    if user_turn:
        timestamp = user_turn.get("timestamp") or ""

    return prompt, response, label, model, timestamp


def _natural_key(key: str) -> tuple:
    """Sort key for natural ordering: L1, L2, ..., L10, Q0, Q1, ..."""
    parts = re.split(r"(\d+)", key)
    return tuple(int(p) if p.isdigit() else p for p in parts)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _entry_timestamp(entry: dict) -> str:
    return entry.get("created_at") or _now_iso()


# ---------------------------------------------------------------------------
# Node builders
# ---------------------------------------------------------------------------


def _build_layer_nodes(root_entry: dict) -> dict[str, StoreNode]:
    """Build the L-keyed layer nodes for a root store entry.

    The root entry and all its .LN siblings share the same thread_id and
    accumulate turns sequentially.  Pair 0 → L1, pair 1 → L2, etc.
    """
    thread_id = root_entry["thread_id"]
    turns = load_thread_file(thread_id)
    if turns is None:
        _stats["threads_missing"] += 1
        return {}

    nodes: dict[str, StoreNode] = {}
    pair_count = (len(turns) + 1) // 2  # ceil division

    for pair_idx in range(0, pair_count):
        pair = _extract_turn_pair(turns, pair_idx)
        if pair is None:
            continue
        prompt, response, label, model, timestamp = pair
        key = f"L{pair_idx + 1}"
        nodes[key] = StoreNode(
            entry_type="store",
            label=label or None,
            timestamp=timestamp or _entry_timestamp(root_entry),
            model=model or None,
            prompt=prompt,
            response=response,
        )
        _stats["nodes_migrated"] += 1

    return nodes


def _build_query_node(q_entry: dict, all_entries: dict[str, dict]) -> StoreNode | None:
    """Build a query StoreNode with optional numeric follow-up children.

    Q entries that share a thread_id with their parent have their turns
    interleaved in the same thread file; those with a distinct thread_id
    use their own file.  In both cases the first turn pair forms the
    Q-node's own prompt/response, and additional pairs become numbered
    children (key "1", "2", ...).
    """
    thread_id = q_entry["thread_id"]
    turns = load_thread_file(thread_id)
    if turns is None:
        _stats["threads_missing"] += 1
        return StoreNode(
            entry_type="query",
            label=q_entry.get("label") or "⚠ thread data missing",
            timestamp=_entry_timestamp(q_entry),
            prompt="",
            response="",
        )

    # Find the right starting pair.  When the Q thread is the same as the
    # parent root thread we need to skip the root's own pairs.  However
    # from the registry there is no reliable turn-offset field, so we use
    # the simpler heuristic: always read from pair 0 of the thread.  This
    # is correct for entries whose thread_id differs from the root (the
    # majority).  Root-sharing threads (L-layers) are handled by
    # _build_layer_nodes instead, so by the time we're here thread_id is
    # always a dedicated Q/tool thread.
    first_pair = _extract_turn_pair(turns, 0)
    if first_pair is None:
        _stats["turns_skipped"] += 1
        return None

    prompt, response, label, model, timestamp = first_pair
    effective_label = q_entry.get("label") or label or None

    # Build numeric follow-up children from additional pairs in the same thread
    children: dict[str, StoreNode] = {}
    pair_count = (len(turns) + 1) // 2
    for pair_idx in range(1, pair_count):
        pair = _extract_turn_pair(turns, pair_idx)
        if pair is None:
            continue
        fp, fr, fl, fm, fts = pair
        key = str(pair_idx)
        children[key] = StoreNode(
            entry_type="query",
            label=fl or None,
            timestamp=fts or _entry_timestamp(q_entry),
            model=fm or None,
            prompt=fp,
            response=fr,
        )
        _stats["nodes_migrated"] += 1

    # Also attach direct numeric-key children from registry (e.g. lspdna.Q1.1)
    q_id = q_entry["store_id"]
    for other_id, other_entry in all_entries.items():
        if other_entry.get("parent_store_id") != q_id:
            continue
        suffix = other_id[len(q_id) + 1:]
        if not suffix.isdigit():
            continue
        # Registry numeric follow-up children share the Q thread; their
        # content is already captured by the pair loop above via the same
        # thread_id.  Only add them if not already present.
        if suffix not in children:
            fu_turns = load_thread_file(other_entry["thread_id"])
            if fu_turns:
                fu_pair = _extract_turn_pair(fu_turns, 0)
                if fu_pair:
                    fp, fr, fl, fm, fts = fu_pair
                    children[suffix] = StoreNode(
                        entry_type="query",
                        label=fl or None,
                        timestamp=fts or _entry_timestamp(other_entry),
                        model=fm or None,
                        prompt=fp,
                        response=fr,
                    )
                    _stats["nodes_migrated"] += 1

    node = StoreNode(
        entry_type="query",
        label=effective_label,
        timestamp=timestamp or _entry_timestamp(q_entry),
        model=model or None,
        prompt=prompt,
        response=response,
        children=children,
    )
    _stats["nodes_migrated"] += 1
    return node


def _build_tool_node(tool_entry: dict) -> StoreNode | None:
    """Build a tool StoreNode from the first turn pair of its thread."""
    thread_id = tool_entry["thread_id"]
    turns = load_thread_file(thread_id)
    if turns is None:
        _stats["threads_missing"] += 1
        return StoreNode(
            entry_type="tool",
            label="⚠ thread data missing",
            timestamp=_entry_timestamp(tool_entry),
            tool_name=tool_entry.get("tool_name"),
            prompt="",
            response="",
        )

    first_pair = _extract_turn_pair(turns, 0)
    if first_pair is None:
        _stats["turns_skipped"] += 1
        return None

    prompt, response, label, model, timestamp = first_pair
    node = StoreNode(
        entry_type="tool",
        label=label or tool_entry.get("label") or None,
        timestamp=timestamp or _entry_timestamp(tool_entry),
        model=model or None,
        tool_name=tool_entry.get("tool_name"),
        prompt=prompt,
        response=response,
    )
    _stats["nodes_migrated"] += 1
    return node


# ---------------------------------------------------------------------------
# Tree builder for one root store
# ---------------------------------------------------------------------------


def _build_children(
    parent_id: str,
    all_entries: dict[str, dict],
) -> dict[str, StoreNode]:
    """Recursively build the children dict for a node at parent_id."""
    children: dict[str, StoreNode] = {}

    # Collect direct children only (immediate children whose store_id is
    # exactly parent_id + "." + one segment).
    for sid, entry in all_entries.items():
        if entry.get("parent_store_id") != parent_id:
            continue

        suffix = sid[len(parent_id) + 1:]
        # Skip if there are more dots — those are handled by the recursive call
        # on the intermediate node.
        if "." in suffix:
            continue

        entry_type = entry.get("entry_type", "store")
        key = suffix

        if entry_type == "store":
            # L-layer siblings: skipped here — they are encoded as child nodes
            # on the root by _build_layer_nodes rather than via children recursion.
            continue

        if entry_type == "query":
            # Numeric follow-up children (e.g. Q1.1) are embedded inside the Q
            # node by _build_query_node; skip them at this level.
            if key.isdigit():
                continue
            node = _build_query_node(entry, all_entries)
            if node is None:
                continue
            # Attach tool grandchildren onto the Q node
            node.children.update(_build_children(sid, all_entries))
            children[key] = node

        elif entry_type == "tool":
            node = _build_tool_node(entry)
            if node is None:
                continue
            children[key] = node

    return children


def build_tree_for_root(root_entry: dict, all_entries: dict[str, dict]) -> StoreRoot:
    """Construct a StoreRoot with a fully populated node tree."""
    thread_id = root_entry["thread_id"]
    turns = load_thread_file(thread_id)

    root_label: str | None = root_entry.get("label") or None

    if turns is not None:
        pair0 = _extract_turn_pair(turns, 0)
        if pair0:
            _, _, extracted_label, _, _ = pair0
            if not root_label and extracted_label:
                root_label = extracted_label
    else:
        _stats["threads_missing"] += 1

    # L-layer nodes from subsequent pairs in the shared thread
    layer_nodes = _build_layer_nodes(root_entry) if turns is not None else {}

    # Q and tool children from registry
    descendant_children = _build_children(root_entry["store_id"], all_entries)

    # Attach Q/tool children to the latest L-layer whose timestamp precedes
    # the child's timestamp, so queries sit next to the layer they were made
    # against rather than all clustering on the root.
    l_keys_sorted = sorted(
        ((k, v) for k, v in layer_nodes.items()),
        key=lambda kv: _natural_key(kv[0]),
    )

    root_children: dict[str, StoreNode] = {}
    for key in sorted(layer_nodes.keys(), key=_natural_key):
        root_children[key] = layer_nodes[key]

    for q_key, q_node in sorted(descendant_children.items(), key=lambda kv: _natural_key(kv[0])):
        q_ts = q_node.timestamp or ""
        # Find the latest L-layer created before this Q
        target_l_key = None
        for l_key, l_node in l_keys_sorted:
            if (l_node.timestamp or "") <= q_ts:
                target_l_key = l_key
            else:
                break
        if target_l_key and target_l_key in root_children:
            root_children[target_l_key].children[q_key] = q_node
        else:
            root_children[q_key] = q_node

    store = StoreRoot(
        store_id=root_entry["store_id"],
        directory=root_entry["directory"],
        label=root_label,
        created_at=_entry_timestamp(root_entry),
        children=root_children,
    )

    _stats["stores_created"] += 1
    return store


# ---------------------------------------------------------------------------
# Top-level registry loading
# ---------------------------------------------------------------------------


def load_old_registry() -> dict[str, dict]:
    """Read stores.json. Returns {} if missing or corrupt."""
    if not os.path.exists(_OLD_REGISTRY_PATH):
        return {}
    try:
        with open(_OLD_REGISTRY_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------------------
# Main migration
# ---------------------------------------------------------------------------


def migrate(dry_run: bool = False) -> None:
    registry = load_old_registry()
    if not registry:
        print("Nothing to migrate: stores.json is missing or empty.")
        return

    # Identify root entries (no parent, entry_type == "store", no .LN suffix)
    # L-layer siblings (e.g. "foo.L1") share their root thread so they are
    # folded into the root tree rather than treated as independent roots.
    root_entries: list[dict] = []
    for sid, entry in registry.items():
        if entry.get("parent_store_id") is not None:
            continue
        if re.search(r"\.L\d+$", sid):
            continue
        root_entries.append(entry)

    # Group by directory for summary output
    by_dir: dict[str, list[dict]] = {}
    for entry in root_entries:
        d = entry.get("directory", "")
        by_dir.setdefault(d, []).append(entry)

    print(f"Found {len(root_entries)} root store(s) across {len(by_dir)} director(y/ies).")
    if dry_run:
        print("[dry-run] No files will be written.\n")

    for directory, roots in sorted(by_dir.items()):
        print(f"\nDirectory: {directory}")
        _stats["directories"] += 1

        for root_entry in roots:
            sid = root_entry["store_id"]
            print(f"  Migrating: {sid} ...", end="", flush=True)
            try:
                store = build_tree_for_root(root_entry, registry)
            except Exception as exc:
                print(f" FAILED: {exc}")
                _err(f"build_tree_for_root({sid}): {exc}")
                continue

            if dry_run:
                child_count = _count_nodes(store)
                print(f" [dry-run] {child_count} node(s) would be written")
            else:
                try:
                    save_store(store)
                    update_index(store.directory, store.store_id)
                    child_count = _count_nodes(store)
                    print(f" ok ({child_count} node(s))")
                except Exception as exc:
                    print(f" FAILED: {exc}")
                    _err(f"save_store({sid}): {exc}")

    print()
    print("=" * 60)
    print("Migration summary")
    print("=" * 60)
    print(f"  Directories processed : {_stats['directories']}")
    print(f"  Stores created        : {_stats['stores_created']}")
    print(f"  Nodes migrated        : {_stats['nodes_migrated']}")
    print(f"  Missing thread files  : {_stats['threads_missing']}")
    print(f"  Incomplete turn pairs : {_stats['turns_skipped']}")
    print(f"  Errors                : {_stats['errors']}")
    if dry_run:
        print("\n[dry-run] No changes written to disk.")


def _count_nodes(store: StoreRoot) -> int:
    """Count total StoreNode instances recursively in a StoreRoot."""

    def _recurse(children: dict) -> int:
        total = 0
        for node in children.values():
            total += 1 + _recurse(node.children)
        return total

    return _recurse(store.children)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate context stores to tree format")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be migrated without writing")
    args = parser.parse_args()
    migrate(dry_run=args.dry_run)
