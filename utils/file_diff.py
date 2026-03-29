"""Diff-based file deduplication for context store ancestry traversal.

When layers share files across ancestry chains, this module computes
additions-only diffs to avoid embedding duplicate file content. Files
unchanged between layers are omitted; files with <50% token change
are represented as additions-only diffs; files with >=50% change are
re-embedded in full.
"""

from __future__ import annotations

import difflib
import re
from typing import TYPE_CHECKING

from utils.token_utils import count_tokens

if TYPE_CHECKING:
    from utils.palstore import PalNode

# Regex to extract all BEGIN FILE blocks from a content blob
_FILE_BLOCK_RE = re.compile(
    r"--- BEGIN FILE: (?P<path>.+?) \(Last modified:.*?\) ---\n" r"(?P<content>.*?)\n" r"--- END FILE: .+? ---",
    re.DOTALL,
)

# Regex to extract a single BEGIN FILE block by path
_FILE_BLOCK_SINGLE_TMPL = (
    r"--- BEGIN FILE: {escaped_path} \(Last modified:.*?\) ---\n" r"(.*?)\n" r"--- END FILE: {escaped_path} ---"
)

# Binary extensions that should always be embedded in full
_BINARY_EXTENSIONS: set[str] | None = None


def _get_binary_extensions() -> set[str]:
    global _BINARY_EXTENSIONS
    if _BINARY_EXTENSIONS is None:
        try:
            from utils.file_types import BINARY_EXTENSIONS

            _BINARY_EXTENSIONS = BINARY_EXTENSIONS
        except ImportError:
            _BINARY_EXTENSIONS = set()
    return _BINARY_EXTENSIONS


def extract_file_blobs(content_blob: str) -> dict[str, str]:
    """Parse all BEGIN FILE blocks from a content blob.

    Returns a dict mapping file paths to their raw content (without markers).
    Only handles BEGIN FILE markers; BEGIN DIFF markers are skipped.
    """
    result: dict[str, str] = {}
    for match in _FILE_BLOCK_RE.finditer(content_blob):
        result[match.group("path")] = match.group("content")
    return result


def extract_file_from_content_blob(content_blob: str, file_path: str) -> str | None:
    """Extract a single file's content from a content blob.

    Handles BEGIN FILE markers and returns the raw content.
    Returns None for BEGIN DIFF markers or if the file is not found.
    """
    if not content_blob:
        return None
    escaped = re.escape(file_path)
    pattern = _FILE_BLOCK_SINGLE_TMPL.format(escaped_path=escaped)
    match = re.search(pattern, content_blob, re.DOTALL)
    if match:
        return match.group(1)
    return None


def compute_additions_only_diff(old_content: str, new_content: str) -> str:
    """Compute a unified diff keeping only @@ headers and + lines.

    Returns the filtered diff body (without BEGIN/END markers).
    Returns empty string if there are no additions.
    """
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)

    diff = difflib.unified_diff(old_lines, new_lines, n=0)

    filtered: list[str] = []
    has_additions = False
    for line in diff:
        # Skip the --- / +++ header lines
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("@@") or line.startswith("+"):
            filtered.append(line)
            if line.startswith("+"):
                has_additions = True

    if not has_additions:
        return ""

    # Join and strip trailing whitespace
    return "".join(filtered).rstrip("\n")


def format_diff_block(file_path: str, diff_body: str, base_layer_key: str) -> str:
    """Wrap a diff body in BEGIN DIFF / END DIFF markers.

    Returns empty string if diff_body is empty.
    """
    if not diff_body:
        return ""
    return (
        f"\n--- BEGIN DIFF: {file_path} (changes since layer {base_layer_key}) ---\n"
        f"{diff_body}\n"
        f"--- END DIFF: {file_path} ---\n"
    )


def format_file_block(file_path: str, content: str, modified_at: str) -> str:
    """Format a full file block using BEGIN FILE / END FILE markers.

    Matches the format produced by read_file_content() in file_utils.py.
    """
    return (
        f"\n--- BEGIN FILE: {file_path} (Last modified: {modified_at}) ---\n"
        f"{content}\n"
        f"--- END FILE: {file_path} ---\n"
    )


def decide_file_representation(
    file_path: str,
    new_content: str,
    old_content: str | None,
    base_layer_key: str | None,
    modified_at: str,
    threshold: float = 0.50,
) -> str:
    """Decide how to represent a file: full, diff, or omit.

    Returns the formatted block string (may be empty for omitted files).

    Args:
        file_path: Absolute path to the file.
        new_content: Current file content (raw text, no markers).
        old_content: Previous version from prior layer, or None if first occurrence.
        base_layer_key: Key of the ancestor node that had the prior version (e.g. "L1").
        modified_at: Formatted modification timestamp for full-file headers.
        threshold: Token change ratio at or above which the full file is included.
    """
    import os

    # First occurrence: always full file
    if old_content is None:
        return format_file_block(file_path, new_content, modified_at)

    # Unchanged: omit entirely
    if old_content == new_content:
        return ""

    # Binary files: always full
    ext = os.path.splitext(file_path)[1].lower()
    if ext in _get_binary_extensions():
        return format_file_block(file_path, new_content, modified_at)

    # Compute additions-only diff
    diff_body = compute_additions_only_diff(old_content, new_content)

    if not diff_body:
        # Only deletions, no additions — omit
        return ""

    # Extract raw addition text for token counting (strip + prefixes and @@ headers)
    addition_lines = [line[1:] for line in diff_body.splitlines() if line.startswith("+")]
    additions_text = "\n".join(addition_lines)
    additions_tokens = count_tokens(additions_text)
    new_tokens = count_tokens(new_content)

    if new_tokens == 0 or (additions_tokens / new_tokens) >= threshold:
        return format_file_block(file_path, new_content, modified_at)

    return format_diff_block(file_path, diff_body, base_layer_key or "unknown")


def build_file_state_from_ancestry(ancestors: list[PalNode]) -> dict[str, str]:
    """Build a map of file paths to their latest content from ancestor nodes.

    Walks ancestors in order (oldest to newest), extracting file content
    from each node's content blob. Last-write wins, producing the most
    recent version of each file as seen across the ancestry chain.
    """
    state: dict[str, str] = {}
    for node in ancestors:
        files = getattr(node, "files", None) or []
        content_blob = getattr(node, "content", "") or ""
        if not files or not content_blob:
            continue
        for file_path in files:
            extracted = extract_file_from_content_blob(content_blob, file_path)
            if extracted is not None:
                state[file_path] = extracted
    return state


def find_base_layer_key(
    file_path: str,
    ancestors: list[PalNode],
    ancestor_keys: list[str] | None = None,
) -> str | None:
    """Find the key of the most recent ancestor node that contained a file.

    Returns the key string (e.g. "L2") or None if the file was not found.
    If ancestor_keys is not provided, returns a generic "prior" label.
    """
    last_key: str | None = None
    for i, node in enumerate(ancestors):
        files = getattr(node, "files", None) or []
        if file_path in files:
            last_key = ancestor_keys[i] if ancestor_keys else f"prior-{i}"
    return last_key
