"""
One-shot migration script: fix 7 structural violations in live context store JSON files.

Violations fixed:
  L-under-L (3):  Promote misplaced L-child nodes to root-level siblings
  Tool-under-Q (4): Wrap direct Q-child tool nodes in F-fork wrappers
"""

import re
import shutil
import sys

sys.path.insert(0, ".")

from utils.context_store import StoreNode, load_store, resolve_store_location, get_store_path, save_store


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------


def _is_l_key(key: str) -> bool:
    return key.startswith("L") and key[1:].isdigit()


def _next_root_l_key(store) -> str:
    """Return the next available L-key at store root, one above the current max."""
    indices = [int(k[1:]) for k in store.children if _is_l_key(k)]
    next_idx = max(indices) + 1 if indices else 1
    return f"L{next_idx}"


def _next_f_key(q_node: StoreNode) -> str:
    """Return the next available F-key in a Q-node's children."""
    pattern = re.compile(r"^F(\d+)$")
    indices = [int(m.group(1)) for k in q_node.children if (m := pattern.match(k))]
    return f"F{max(indices) + 1}" if indices else "F0"


def _tool_name_from_key(key: str) -> str:
    """Strip trailing digit(s) from a key like 'thinkdeep1' → 'thinkdeep'."""
    return re.sub(r"\d+$", "", key)


# ---------------------------------------------------------------------------
# Backup helper
# ---------------------------------------------------------------------------


def backup_store(directory: str, store_id: str) -> str:
    src = get_store_path(directory, store_id)
    dst = src + ".bak"
    shutil.copy2(src, dst)
    return dst


# ---------------------------------------------------------------------------
# Fix: L-under-L → promote to root sibling
# ---------------------------------------------------------------------------


def fix_l_under_l(store_name: str, parent_l_key: str) -> None:
    loc = resolve_store_location(store_name)
    if loc is None:
        print(f"  ERROR: store '{store_name}' not found")
        return

    directory, root_id = loc
    store = load_store(directory, root_id)

    parent_node = store.children.get(parent_l_key)
    if parent_node is None:
        print(f"  ERROR: {store_name}.{parent_l_key} not found")
        return

    l1_node = parent_node.children.get("L1")
    if l1_node is None:
        print(f"  ERROR: {store_name}.{parent_l_key}.L1 not found")
        return

    new_key = _next_root_l_key(store)

    bak = backup_store(directory, root_id)
    print(f"  backed up → {bak}")

    # Move node: remove from parent, add to store root
    del parent_node.children["L1"]
    store.children[new_key] = l1_node

    save_store(store)
    print(f"  {store_name}.{parent_l_key}.L1  →  {store_name}.{new_key}  (label: {l1_node.label!r})")


# ---------------------------------------------------------------------------
# Fix: tool-under-Q → wrap in F-fork
# ---------------------------------------------------------------------------


def fix_tool_under_q(store_name: str, l_key: str, q_key: str, tool_key: str) -> None:
    loc = resolve_store_location(store_name)
    if loc is None:
        print(f"  ERROR: store '{store_name}' not found")
        return

    directory, root_id = loc
    store = load_store(directory, root_id)

    l_node = store.children.get(l_key)
    if l_node is None:
        print(f"  ERROR: {store_name}.{l_key} not found")
        return
    q_node = l_node.children.get(q_key)
    if q_node is None:
        print(f"  ERROR: {store_name}.{l_key}.{q_key} not found")
        return
    tool_node = q_node.children.get(tool_key)
    if tool_node is None:
        print(f"  ERROR: {store_name}.{l_key}.{q_key}.{tool_key} not found")
        return

    canonical_tool_name = _tool_name_from_key(tool_key)
    f_key = _next_f_key(q_node)

    bak = backup_store(directory, root_id)
    print(f"  backed up → {bak}")

    fork_node = StoreNode(
        entry_type="fork",
        timestamp=tool_node.timestamp,
        label=canonical_tool_name,
    )
    fork_node.children[canonical_tool_name] = tool_node

    del q_node.children[tool_key]
    q_node.children[f_key] = fork_node

    save_store(store)
    print(
        f"  {store_name}.{l_key}.{q_key}.{tool_key}"
        f"  →  {store_name}.{l_key}.{q_key}.{f_key}.{canonical_tool_name}"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print("=" * 60)
    print("Context Store Migration — 7 structural violations")
    print("=" * 60)

    # --- L-under-L fixes ---

    print()
    print("[1/3] lspdna.L19.L1 → root sibling")
    fix_l_under_l("lspdna", "L19")

    print()
    print("[2/3] lspdna.L21.L1 → root sibling")
    fix_l_under_l("lspdna", "L21")

    print()
    print("[3/3] clark.L19.L1 → root sibling")
    fix_l_under_l("clark", "L19")

    # --- Tool-under-Q fixes ---

    print()
    print("[4/7] clearcode-legacy.L24.Q7.thinkdeep1 → F-fork wrapper")
    fix_tool_under_q("clearcode-legacy", "L24", "Q7", "thinkdeep1")

    print()
    print("[5/7] clearcode-legacy.L30.Q13.planner0 → F-fork wrapper")
    fix_tool_under_q("clearcode-legacy", "L30", "Q13", "planner0")

    print()
    print("[6/7] clearcode-legacy.L31.Q18.analyze0 → F-fork wrapper")
    fix_tool_under_q("clearcode-legacy", "L31", "Q18", "analyze0")

    print()
    print("[7/7] clearcode-legacy.L31.Q18.thinkdeep0 → F-fork wrapper")
    fix_tool_under_q("clearcode-legacy", "L31", "Q18", "thinkdeep0")

    # --- Sanity verification ---

    print()
    print("=" * 60)
    print("Sanity check — verify stores load and violations are gone")
    print("=" * 60)

    errors = []

    for store_name, parent_key in [("lspdna", "L19"), ("lspdna", "L21"), ("clark", "L19")]:
        loc = resolve_store_location(store_name)
        store = load_store(loc[0], loc[1])
        parent = store.children.get(parent_key)
        if parent and "L1" in parent.children:
            errors.append(f"  FAIL: {store_name}.{parent_key}.L1 still present")
        else:
            print(f"  OK: {store_name}.{parent_key} has no L1 child")

    cl_loc = resolve_store_location("clearcode-legacy")
    cl_store = load_store(cl_loc[0], cl_loc[1])

    for path_keys, old_tool_key in [
        (("L24", "Q7"), "thinkdeep1"),
        (("L30", "Q13"), "planner0"),
        (("L31", "Q18"), "analyze0"),
        (("L31", "Q18"), "thinkdeep0"),
    ]:
        l_key, q_key = path_keys
        q_node = cl_store.children[l_key].children[q_key]
        if old_tool_key in q_node.children:
            errors.append(f"  FAIL: clearcode-legacy.{l_key}.{q_key}.{old_tool_key} still present")
        else:
            # Check that an F-fork wrapping the right tool_name exists
            canonical = _tool_name_from_key(old_tool_key)
            found_fork = any(
                k.startswith("F") and canonical in q_node.children[k].children
                for k in q_node.children
                if k.startswith("F")
            )
            if found_fork:
                print(f"  OK: clearcode-legacy.{l_key}.{q_key} has {canonical} wrapped in fork")
            else:
                errors.append(f"  FAIL: fork for {canonical} not found in clearcode-legacy.{l_key}.{q_key}")

    if errors:
        print()
        print("ERRORS:")
        for e in errors:
            print(e)
        sys.exit(1)
    else:
        print()
        print("All 7 violations fixed successfully.")


if __name__ == "__main__":
    main()
