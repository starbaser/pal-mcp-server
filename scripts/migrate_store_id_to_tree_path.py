"""
One-shot migration: rename 'store_id' → 'tree_path' in all PALTree JSON files.

The PalRoot model field was renamed from store_id to tree_path.
A model_validator provides backwards-compat on load, but this script
rewrites the JSON files so they use the canonical field name.
"""

import json
import os
import sys

sys.path.insert(0, ".")

from config import PAL_STORAGE_DIR

_CTX_DIR = os.path.join(PAL_STORAGE_DIR, "context")


def migrate_file(path: str) -> bool:
    """Rename 'store_id' to 'tree_path' in a single JSON file. Returns True if modified."""
    with open(path, encoding="utf-8") as f:
        raw = f.read()

    if '"store_id"' not in raw:
        return False

    data = json.loads(raw)
    if "store_id" in data and "tree_path" not in data:
        data["tree_path"] = data.pop("store_id")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
        return True

    return False


def main():
    if not os.path.isdir(_CTX_DIR):
        print(f"No context directory found at {_CTX_DIR}")
        return

    migrated = 0
    skipped = 0
    errors = 0

    for dir_entry in os.scandir(_CTX_DIR):
        if not dir_entry.is_dir():
            continue
        for file_entry in os.scandir(dir_entry.path):
            if not file_entry.name.endswith(".json"):
                continue
            try:
                if migrate_file(file_entry.path):
                    print(f"  migrated: {file_entry.path}")
                    migrated += 1
                else:
                    skipped += 1
            except Exception as e:
                print(f"  ERROR: {file_entry.path}: {e}")
                errors += 1

    print(f"\nDone. migrated={migrated} skipped={skipped} errors={errors}")


if __name__ == "__main__":
    main()
