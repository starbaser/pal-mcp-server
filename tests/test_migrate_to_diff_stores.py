"""Tests for the context store diff migration script."""

from __future__ import annotations

from utils.context_store import StoreNode, StoreRoot


def _make_content_blob(files: dict[str, str], prompt: str = "user text", response: str = "ok") -> str:
    """Build a content blob with embedded file blocks."""
    file_section = ""
    if files:
        parts = []
        for path, content in files.items():
            parts.append(
                f"\n--- BEGIN FILE: {path} (Last modified: 2026-01-01 00:00:00 UTC) ---\n"
                f"{content}\n"
                f"--- END FILE: {path} ---\n"
            )
        file_section = f"\n\n=== CONTEXT FILES ===\n{''.join(parts)}\n=== END CONTEXT FILES ==="

    full_prompt = f"=== CONTEXT LAYER SUBMISSION ===\n\n{prompt}{file_section}"
    return f"{full_prompt}\n\n---\n\n{response}"


def _make_store(layers: list[dict]) -> StoreRoot:
    """Build a store with L-nodes from a list of layer specs.

    Each spec: {"files": {path: content}, "prompt": str, "response": str}
    """
    children = {}
    for i, spec in enumerate(layers, 1):
        files_dict = spec.get("files", {})
        content_blob = _make_content_blob(
            files_dict,
            spec.get("prompt", f"layer {i} prompt"),
            spec.get("response", f"layer {i} response"),
        )
        children[f"L{i}"] = StoreNode(
            entry_type="store",
            timestamp=f"2026-01-0{i}T00:00:00Z",
            files=list(files_dict.keys()),
            prompt=spec.get("prompt", f"layer {i} prompt"),
            response=spec.get("response", f"layer {i} response"),
            content=content_blob,
        )
    return StoreRoot(
        store_id="test-store",
        directory="/tmp/test",
        created_at="2026-01-01T00:00:00Z",
        children=children,
    )


class TestMigrateStore:
    def test_duplicate_file_replaced_with_diff_or_omitted(self):
        """L2 has exact same file as L1 — should be stripped from L2's content."""
        from scripts.migrate_to_diff_stores import migrate_store

        store = _make_store(
            [
                {"files": {"/a.py": "line1\nline2\nline3\n"}},
                {"files": {"/a.py": "line1\nline2\nline3\n"}},
            ]
        )

        savings, modified = migrate_store(store, dry_run=False)
        assert savings > 0
        assert modified == 1

        # L2's content should no longer have BEGIN FILE for /a.py
        l2_content = store.children["L2"].content
        assert "BEGIN FILE: /a.py" not in l2_content

    def test_small_change_produces_diff(self):
        """L2 has a small addition — should produce a DIFF marker."""
        from scripts.migrate_to_diff_stores import migrate_store

        old = "line1\nline2\nline3\nline4\nline5\nline6\nline7\nline8\nline9\nline10\n"
        new = old + "added_line\n"

        store = _make_store(
            [
                {"files": {"/a.py": old}},
                {"files": {"/a.py": new}},
            ]
        )

        migrate_store(store, dry_run=False)
        l2_content = store.children["L2"].content
        assert "BEGIN DIFF: /a.py" in l2_content
        assert "+added_line" in l2_content

    def test_first_layer_unchanged(self):
        """L1 should never be modified (no prior state to diff against)."""
        from scripts.migrate_to_diff_stores import migrate_store

        store = _make_store(
            [
                {"files": {"/a.py": "content"}},
                {"files": {"/a.py": "content"}},
            ]
        )
        original_l1 = store.children["L1"].content

        migrate_store(store, dry_run=False)
        assert store.children["L1"].content == original_l1

    def test_dry_run_no_writes(self):
        """Dry run should report savings but not modify content."""
        from scripts.migrate_to_diff_stores import migrate_store

        store = _make_store(
            [
                {"files": {"/a.py": "same\n"}},
                {"files": {"/a.py": "same\n"}},
            ]
        )
        original_l2 = store.children["L2"].content

        savings, _ = migrate_store(store, dry_run=True)
        assert savings > 0
        assert store.children["L2"].content == original_l2

    def test_no_files_no_savings(self):
        """Store with no file attachments should have zero savings."""
        from scripts.migrate_to_diff_stores import migrate_store

        store = _make_store(
            [
                {"files": {}, "prompt": "just text"},
                {"files": {}, "prompt": "more text"},
            ]
        )
        savings, modified = migrate_store(store, dry_run=True)
        assert savings == 0
        assert modified == 0

    def test_different_files_no_savings(self):
        """L1 and L2 have different files — no dedup possible."""
        from scripts.migrate_to_diff_stores import migrate_store

        store = _make_store(
            [
                {"files": {"/a.py": "aaa\n"}},
                {"files": {"/b.py": "bbb\n"}},
            ]
        )
        savings, _ = migrate_store(store, dry_run=True)
        assert savings == 0

    def test_response_preserved(self):
        """Migration should not corrupt the response portion."""
        from scripts.migrate_to_diff_stores import migrate_store

        store = _make_store(
            [
                {"files": {"/a.py": "content\n"}, "response": "important response"},
                {"files": {"/a.py": "content\n"}, "response": "also important"},
            ]
        )
        migrate_store(store, dry_run=False)
        assert "also important" in store.children["L2"].content
