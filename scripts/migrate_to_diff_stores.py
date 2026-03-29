#!/usr/bin/env python3
"""Migrate existing context stores to use diff-based file deduplication.

Walks each store's L-nodes in order, comparing file blobs between consecutive
layers. Duplicate or slightly-changed files are replaced with additions-only
diffs; unchanged files are stripped entirely. The net effect is a significant
reduction in stored token count.

Usage:
    python scripts/migrate_to_diff_stores.py [--dry-run] [--backup] [--store-id NAME] [--all]

Flags:
    --dry-run     Report token savings without modifying any store files.
    --backup      Copy original JSON to *.pre-dedup.json before rewriting.
    --store-id    Migrate only the named store.
    --all         Migrate all stores (default if no --store-id given).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys

# Ensure project root is on sys.path
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from utils.palstore import (  # noqa: E402
    PalNode,
    PalRoot,
    load_index,
    save_store,
)
from utils.file_diff import (  # noqa: E402
    decide_file_representation,
    extract_file_blobs,
)
from utils.token_utils import count_tokens  # noqa: E402

# Regex matching a full BEGIN FILE...END FILE block (non-greedy, DOTALL)
_FILE_BLOCK_SPAN_RE = re.compile(
    r"\n--- BEGIN FILE: (?P<path>.+?) \(Last modified:(?P<mtime>.*?)\) ---\n"
    r"(?P<content>.*?)\n"
    r"--- END FILE: .+? ---\n",
    re.DOTALL,
)


def _get_mtime_from_header(header_mtime: str) -> str:
    """Clean the mtime string extracted from a file header."""
    return header_mtime.strip()


def _sorted_l_keys(children: dict[str, PalNode]) -> list[str]:
    """Return L-node keys sorted numerically."""
    keys = [k for k in children if k[0] == "L" and k[1:].isdigit()]
    keys.sort(key=lambda k: int(k[1:]))
    return keys


def _migrate_node_content(
    node: PalNode,
    state: dict[str, str],
    layer_key: str,
    dry_run: bool,
) -> int:
    """Migrate a single node's content blob.

    Replaces duplicate BEGIN FILE blocks with diffs or omissions based on
    the running file state. Returns the token savings (original - new).
    """
    content = node.content
    if not content:
        return 0

    # Split into user_turn and response
    parts = content.split("\n\n---\n\n", 1)
    if len(parts) != 2:
        return 0
    user_turn, response = parts

    original_tokens = count_tokens(content)
    modified_turn = user_turn

    # Find all file blocks in the user turn and process in reverse order
    # (reverse so that span indices remain valid after replacements)
    matches = list(_FILE_BLOCK_SPAN_RE.finditer(user_turn))

    for match in reversed(matches):
        file_path = match.group("path")
        mtime = _get_mtime_from_header(match.group("mtime"))
        file_content = match.group("content")

        old_content = state.get(file_path)
        representation = decide_file_representation(file_path, file_content, old_content, layer_key, mtime)

        # Update running state with current file content
        state[file_path] = file_content

        # Replace the original block with the new representation
        modified_turn = modified_turn[: match.start()] + representation + modified_turn[match.end() :]

    new_content = f"{modified_turn}\n\n---\n\n{response}"
    new_tokens = count_tokens(new_content)
    savings = original_tokens - new_tokens

    if not dry_run and savings > 0:
        node.content = new_content

    return max(savings, 0)


def _migrate_children(
    children: dict[str, PalNode],
    state: dict[str, str],
    dry_run: bool,
    depth: int = 0,
) -> tuple[int, int]:
    """Recursively migrate L-nodes in a children dict.

    Returns (total_savings, nodes_modified).
    """
    total_savings = 0
    nodes_modified = 0

    # Process L-nodes in sorted order
    l_keys = _sorted_l_keys(children)

    for i, key in enumerate(l_keys):
        node = children[key]

        # For the first L-node at this level, just populate state (no diffing)
        if i == 0:
            blobs = extract_file_blobs(node.content) if node.content else {}
            for path, content in blobs.items():
                state[path] = content
        else:
            # Diff against accumulated state
            savings = _migrate_node_content(node, state, l_keys[i - 1], dry_run)
            if savings > 0:
                total_savings += savings
                nodes_modified += 1

        # Recurse into children (fork nodes may have nested L-layers)
        if node.children:
            child_state = dict(state)  # fork gets a snapshot of current state
            child_savings, child_modified = _migrate_children(node.children, child_state, dry_run, depth + 1)
            total_savings += child_savings
            nodes_modified += child_modified

    return total_savings, nodes_modified


def migrate_store(store: PalRoot, dry_run: bool = True) -> tuple[int, int]:
    """Migrate a single store to diff-based file representation.

    Returns (total_token_savings, nodes_modified).
    """
    state: dict[str, str] = {}
    return _migrate_children(store.children, state, dry_run)


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate context stores to diff-based file deduplication.")
    parser.add_argument("--dry-run", action="store_true", help="Report savings without modifying stores.")
    parser.add_argument("--backup", action="store_true", help="Copy original JSON to *.pre-dedup.json.")
    parser.add_argument("--store-id", type=str, help="Migrate only this store.")
    parser.add_argument("--all", action="store_true", default=True, help="Migrate all stores (default).")
    args = parser.parse_args()

    index = load_index()
    stores_to_migrate: list[tuple[str, str]] = []  # (store_id, encoded_dir)

    if args.store_id:
        encoded = index.get("stores", {}).get(args.store_id)
        if not encoded:
            print(f"Error: store '{args.store_id}' not found in index.")
            sys.exit(1)
        stores_to_migrate.append((args.store_id, encoded))
    else:
        for store_id, encoded in index.get("stores", {}).items():
            stores_to_migrate.append((store_id, encoded))

    if not stores_to_migrate:
        print("No stores found to migrate.")
        return

    grand_savings = 0
    grand_modified = 0

    for store_id, encoded_dir in stores_to_migrate:
        store_dir = get_store_dir_from_encoded(encoded_dir)
        store_path = os.path.join(store_dir, f"{store_id}.json")

        if not os.path.exists(store_path):
            print(f"  SKIP {store_id}: file not found at {store_path}")
            continue

        try:
            with open(store_path) as f:
                store = PalRoot.model_validate(json.load(f))
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"  SKIP {store_id}: invalid JSON ({exc})")
            continue

        savings, modified = migrate_store(store, dry_run=args.dry_run)
        grand_savings += savings
        grand_modified += modified

        if savings > 0:
            status = "DRY-RUN" if args.dry_run else "MIGRATED"
            print(f"  {status} {store_id}: {savings:,} tokens saved across {modified} node(s)")

            if not args.dry_run:
                if args.backup:
                    backup_path = store_path.replace(".json", ".pre-dedup.json")
                    shutil.copy2(store_path, backup_path)
                    print(f"    backup: {backup_path}")
                save_store(store)
        else:
            print(f"  OK {store_id}: no duplicate files found")

    print(f"\nTotal: {grand_savings:,} tokens saved across {grand_modified} node(s)")
    if args.dry_run:
        print("(dry-run mode — no files were modified)")


def get_store_dir_from_encoded(encoded_dir: str) -> str:
    """Get the store directory path from an encoded directory name."""
    from config import PAL_STORAGE_DIR

    return os.path.join(PAL_STORAGE_DIR, "context", encoded_dir)


if __name__ == "__main__":
    main()
