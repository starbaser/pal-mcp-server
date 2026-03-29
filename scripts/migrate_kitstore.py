"""
Migration script: fix 3 L-under-L violations in the kitstore context store.

Violations fixed:
  1. kitstore.L7.F0.L1.L1 -> kitstore.L7.F0.L2   (fix deepest first)
  2. kitstore.L7.L1        -> kitstore.L8           (promote to root)
  3. kitstore.L8.L1        -> kitstore.L9           (post-fix #2 cleanup)
"""

import shutil
import sys

sys.path.insert(0, "/home/eigenmage/dev/opt/pal-mcp-server")

from utils.context_store import load_store, resolve_store_location, save_store


def max_l_index(children: dict) -> int:
    indices = [int(k[1:]) for k in children if k.startswith("L") and k[1:].isdigit()]
    return max(indices) if indices else -1


def main() -> None:
    location = resolve_store_location("kitstore")
    if location is None:
        print("ERROR: could not locate 'kitstore' in context store index")
        sys.exit(1)

    directory, store_id = location
    print(f"Store located: directory={directory!r}, store_id={store_id!r}")

    store = load_store(directory, store_id)
    if store is None:
        print("ERROR: failed to load store")
        sys.exit(1)

    # Back up store file before any modification
    from utils.context_store import get_store_path

    store_path = get_store_path(directory, store_id)
    bak_path = store_path + ".bak"
    shutil.copy2(store_path, bak_path)
    print(f"Backup written: {bak_path}")

    # -------------------------------------------------------------------------
    # Fix #3: kitstore.L7.F0.L1.L1 -> kitstore.L7.F0.L2
    # -------------------------------------------------------------------------
    l7 = store.children.get("L7")
    if l7 is None:
        print("ERROR: L7 not found at root")
        sys.exit(1)

    f0 = l7.children.get("F0")
    if f0 is None:
        print("ERROR: L7.F0 not found")
        sys.exit(1)

    l1_under_f0 = f0.children.get("L1")
    if l1_under_f0 is None:
        print("ERROR: L7.F0.L1 not found")
        sys.exit(1)

    nested_l1 = l1_under_f0.children.get("L1")
    if nested_l1 is None:
        print("ERROR: L7.F0.L1.L1 not found")
        sys.exit(1)

    # Compute target key: max L-index at F0 level, +1
    next_f0_l = max_l_index(f0.children) + 1
    new_key_f0 = f"L{next_f0_l}"
    print(f"Fix #3: kitstore.L7.F0.L1.L1 -> kitstore.L7.F0.{new_key_f0}")

    # Move nested L1 up to F0, then clean it off L1
    f0.children[new_key_f0] = nested_l1
    del l1_under_f0.children["L1"]
    print(f"  Done. L7.F0.{new_key_f0} has {len(nested_l1.children)} children")

    # -------------------------------------------------------------------------
    # Fix #1: kitstore.L7.L1 -> kitstore.L8  (promote to root)
    # -------------------------------------------------------------------------
    l7_l1 = l7.children.get("L1")
    if l7_l1 is None:
        print("ERROR: L7.L1 not found")
        sys.exit(1)

    next_root_l = max_l_index(store.children) + 1
    new_root_key = f"L{next_root_l}"
    print(f"Fix #1: kitstore.L7.L1 -> kitstore.{new_root_key}")

    store.children[new_root_key] = l7_l1
    del l7.children["L1"]
    print(f"  Done. {new_root_key} has {len(l7_l1.children)} children")

    # -------------------------------------------------------------------------
    # Fix #2: kitstore.L8.L1 -> kitstore.L9  (after fix #1, L8 is the promoted node)
    # -------------------------------------------------------------------------
    promoted_node = store.children[new_root_key]
    nested_l1_in_promoted = promoted_node.children.get("L1")
    if nested_l1_in_promoted is None:
        print(f"INFO: No L1 child found in {new_root_key} — fix #2 is a no-op")
    else:
        next_root_l2 = max_l_index(store.children) + 1
        new_root_key2 = f"L{next_root_l2}"
        print(f"Fix #2: kitstore.{new_root_key}.L1 -> kitstore.{new_root_key2}")

        store.children[new_root_key2] = nested_l1_in_promoted
        del promoted_node.children["L1"]
        print(f"  Done. {new_root_key2} has {len(nested_l1_in_promoted.children)} children")

    # -------------------------------------------------------------------------
    # Save
    # -------------------------------------------------------------------------
    save_store(store)
    print(f"\nStore saved: {store_path}")

    # Summary
    root_l_keys = sorted(
        [k for k in store.children if k.startswith("L") and k[1:].isdigit()],
        key=lambda k: int(k[1:]),
    )
    print(f"\nRoot L-nodes after migration: {root_l_keys}")
    print("Migration complete.")


if __name__ == "__main__":
    main()
