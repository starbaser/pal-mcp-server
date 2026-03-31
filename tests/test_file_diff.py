"""Tests for utils/file_diff.py — diff-based file deduplication."""

from __future__ import annotations

from utils.file_diff import (
    build_file_state_from_ancestry,
    compute_additions_only_diff,
    decide_file_representation,
    extract_file_blobs,
    extract_file_from_content_blob,
    find_base_layer_key,
    format_diff_block,
    format_file_block,
)
from utils.palstore import PalNode


def _make_node(
    files: list[str] | None = None,
    content: str = "",
    prompt: str = "p",
    response: str = "r",
) -> PalNode:
    return PalNode(
        timestamp="2026-01-01T00:00:00Z",
        files=files or [],
        content=content,
        prompt=prompt,
        response=response,
    )


def _make_content_blob(file_path: str, file_content: str, response: str = "ok") -> str:
    """Build a content blob with a single embedded file."""
    prompt = (
        f"=== CONTEXT LAYER SUBMISSION ===\n\nuser text"
        f"\n\n=== CONTEXT FILES ===\n"
        f"\n--- BEGIN FILE: {file_path} (Last modified: 2026-01-01 00:00:00 UTC) ---\n"
        f"{file_content}\n"
        f"--- END FILE: {file_path} ---\n"
        f"\n=== END CONTEXT FILES ==="
    )
    return f"{prompt}\n\n---\n\n{response}"


# ── extract_file_blobs ──


class TestExtractFileBlobs:
    def test_single_file(self):
        blob = (
            "--- BEGIN FILE: /a.py (Last modified: 2026-01-01 00:00:00 UTC) ---\n"
            "print('hello')\n"
            "--- END FILE: /a.py ---"
        )
        result = extract_file_blobs(blob)
        assert result == {"/a.py": "print('hello')"}

    def test_multiple_files(self):
        blob = (
            "--- BEGIN FILE: /a.py (Last modified: 2026-01-01 00:00:00 UTC) ---\n"
            "aaa\n"
            "--- END FILE: /a.py ---\n"
            "--- BEGIN FILE: /b.py (Last modified: 2026-01-01 00:00:00 UTC) ---\n"
            "bbb\n"
            "--- END FILE: /b.py ---"
        )
        result = extract_file_blobs(blob)
        assert result == {"/a.py": "aaa", "/b.py": "bbb"}

    def test_empty_blob(self):
        assert extract_file_blobs("") == {}

    def test_ignores_diff_markers(self):
        blob = "--- BEGIN DIFF: /a.py (changes since layer L1) ---\n" "+added\n" "--- END DIFF: /a.py ---"
        assert extract_file_blobs(blob) == {}


# ── extract_file_from_content_blob ──


class TestExtractFileFromContentBlob:
    def test_extracts_file(self):
        blob = _make_content_blob("/foo.py", "def foo():\n    pass")
        result = extract_file_from_content_blob(blob, "/foo.py")
        assert result == "def foo():\n    pass"

    def test_returns_none_for_missing_file(self):
        blob = _make_content_blob("/foo.py", "content")
        assert extract_file_from_content_blob(blob, "/bar.py") is None

    def test_returns_none_for_empty_blob(self):
        assert extract_file_from_content_blob("", "/foo.py") is None

    def test_returns_none_for_diff_marker(self):
        blob = "--- BEGIN DIFF: /a.py (changes since layer L1) ---\n" "+added\n" "--- END DIFF: /a.py ---"
        assert extract_file_from_content_blob(blob, "/a.py") is None


# ── compute_additions_only_diff ──


class TestComputeAdditionsOnlyDiff:
    def test_new_lines_added(self):
        old = "line1\nline2\n"
        new = "line1\nline2\nnew_line\n"
        result = compute_additions_only_diff(old, new)
        assert "+new_line" in result
        assert "@@" in result
        # No deletion lines
        assert not any(line.startswith("-") for line in result.splitlines() if not line.startswith("---"))

    def test_no_change(self):
        content = "same\ncontent\n"
        assert compute_additions_only_diff(content, content) == ""

    def test_pure_deletion(self):
        old = "line1\nline2\nline3\n"
        new = "line1\nline3\n"
        result = compute_additions_only_diff(old, new)
        # No additions, just a deletion — should be empty
        assert result == ""

    def test_mixed_changes(self):
        old = "aaa\nbbb\nccc\n"
        new = "aaa\nBBB\nccc\nnew\n"
        result = compute_additions_only_diff(old, new)
        assert "+BBB" in result
        assert "+new" in result
        # No - lines in the output
        lines = result.splitlines()
        for line in lines:
            assert not line.startswith("-")

    def test_empty_old(self):
        result = compute_additions_only_diff("", "new content\n")
        assert "+new content" in result

    def test_empty_new(self):
        result = compute_additions_only_diff("old content\n", "")
        assert result == ""


# ── format_diff_block ──


class TestFormatDiffBlock:
    def test_wraps_with_markers(self):
        result = format_diff_block("/a.py", "+new line", "L1")
        assert "--- BEGIN DIFF: /a.py (changes since layer L1) ---" in result
        assert "--- END DIFF: /a.py ---" in result
        assert "+new line" in result

    def test_empty_body_returns_empty(self):
        assert format_diff_block("/a.py", "", "L1") == ""


# ── format_file_block ──


class TestFormatFileBlock:
    def test_matches_expected_format(self):
        result = format_file_block("/a.py", "print('hi')", "2026-01-01 00:00:00 UTC")
        assert "--- BEGIN FILE: /a.py (Last modified: 2026-01-01 00:00:00 UTC) ---" in result
        assert "--- END FILE: /a.py ---" in result
        assert "print('hi')" in result


# ── decide_file_representation ──


class TestDecideFileRepresentation:
    def test_first_occurrence_full_file(self):
        result = decide_file_representation("/a.py", "content", None, None, "2026-01-01 00:00:00 UTC")
        assert "BEGIN FILE:" in result
        assert "content" in result

    def test_unchanged_omitted(self):
        result = decide_file_representation("/a.py", "same", "same", "L1", "2026-01-01 00:00:00 UTC")
        assert result == ""

    def test_small_addition_produces_diff(self):
        # Large file with a small addition — should produce a diff
        old = "line1\nline2\nline3\nline4\nline5\nline6\nline7\nline8\nline9\nline10\n"
        new = old + "new_line\n"
        result = decide_file_representation("/a.py", new, old, "L1", "2026-01-01 00:00:00 UTC")
        assert "BEGIN DIFF:" in result
        assert "+new_line" in result
        assert "BEGIN FILE:" not in result

    def test_large_change_produces_full_file(self):
        # Small old file, mostly new content — should produce full file
        old = "x\n"
        new = "a\nb\nc\nd\ne\nf\ng\nh\ni\nj\n"
        result = decide_file_representation("/a.py", new, old, "L1", "2026-01-01 00:00:00 UTC")
        assert "BEGIN FILE:" in result
        assert "BEGIN DIFF:" not in result

    def test_only_deletions_omitted(self):
        old = "line1\nline2\nline3\n"
        new = "line1\n"
        result = decide_file_representation("/a.py", new, old, "L1", "2026-01-01 00:00:00 UTC")
        assert result == ""

    def test_threshold_boundary_at_50_pct(self):
        # Craft content where additions are exactly at the boundary
        # 10 lines old, replace half with new content
        old_lines = [f"old_{i}" for i in range(10)]
        new_lines = [f"new_{i}" for i in range(10)]  # completely different
        old = "\n".join(old_lines) + "\n"
        new = "\n".join(new_lines) + "\n"
        result = decide_file_representation("/a.py", new, old, "L1", "2026-01-01 00:00:00 UTC")
        # All content is new additions, so ratio >= 50% → full file
        assert "BEGIN FILE:" in result


# ── build_file_state_from_ancestry ──


class TestBuildFileStateFromAncestry:
    def test_single_node_with_file(self):
        blob = _make_content_blob("/a.py", "content_a")
        node = _make_node(files=["/a.py"], content=blob)
        state = build_file_state_from_ancestry([node])
        assert state == {"/a.py": "content_a"}

    def test_later_node_overrides(self):
        blob1 = _make_content_blob("/a.py", "v1")
        blob2 = _make_content_blob("/a.py", "v2")
        n1 = _make_node(files=["/a.py"], content=blob1)
        n2 = _make_node(files=["/a.py"], content=blob2)
        state = build_file_state_from_ancestry([n1, n2])
        assert state["/a.py"] == "v2"

    def test_fork_nodes_skipped(self):
        fork = _make_node(files=[], content="")
        state = build_file_state_from_ancestry([fork])
        assert state == {}

    def test_empty_content_blob_skipped(self):
        node = _make_node(files=["/a.py"], content="")
        state = build_file_state_from_ancestry([node])
        assert state == {}

    def test_multiple_files_across_nodes(self):
        blob1 = _make_content_blob("/a.py", "a_content")
        blob2 = _make_content_blob("/b.py", "b_content")
        n1 = _make_node(files=["/a.py"], content=blob1)
        n2 = _make_node(files=["/b.py"], content=blob2)
        state = build_file_state_from_ancestry([n1, n2])
        assert state == {"/a.py": "a_content", "/b.py": "b_content"}


# ── find_base_layer_key ──


class TestFindBaseLayerKey:
    def test_finds_last_node_with_file(self):
        n1 = _make_node(files=["/a.py"])
        n2 = _make_node(files=["/a.py", "/b.py"])
        n3 = _make_node(files=["/b.py"])
        key = find_base_layer_key("/a.py", [n1, n2, n3], ["L1", "L2", "L3"])
        assert key == "L2"

    def test_returns_none_when_not_found(self):
        n1 = _make_node(files=["/b.py"])
        key = find_base_layer_key("/a.py", [n1], ["L1"])
        assert key is None

    def test_without_keys_uses_index(self):
        n1 = _make_node(files=["/a.py"])
        key = find_base_layer_key("/a.py", [n1])
        assert key == "prior-0"
