import json
import logging

from oboros import tome

logger = logging.getLogger("pal_mcp")


def strip_context_files(text: str) -> str:
    """Strip the === CONTEXT FILES === section from input text.

    The embedded file content/diffs are already represented by the files list
    and waste context when rendered in MCP responses.
    """
    marker = "\n=== CONTEXT FILES ==="
    idx = text.find(marker)
    if idx < 0:
        return text
    return text[:idx].rstrip()


def format_layer_markdown(
    heading: str,
    *,
    label: str | None = None,
    tool_name: str | None = None,
    model: str | None = None,
    timestamp: str | None = None,
    files: list[str] | None = None,
    input_text: str | None = None,
    output_text: str | None = None,
    include_file_content: bool = False,
) -> str:
    """Format a context layer or tool response as structured markdown.

    Shared by readnode (layer display) and content save (response persistence).
    When include_file_content is False (default), embedded file content/diffs
    are stripped from input_text since the file list is shown separately.
    """
    lines = [f"# {heading}", ""]
    if label:
        lines.append(f"**Label:** {label}")
    if tool_name:
        lines.append(f"**Tool:** {tool_name}")
    if model:
        lines.append(f"**Model:** {model}")
    if timestamp:
        lines.append(f"**Timestamp:** {timestamp}")
    if files:
        lines.append("**Files:**")
        for f in files:
            lines.append(f"- {f}")
    if input_text:
        display_input = input_text if include_file_content else strip_context_files(input_text)
        lines.extend(["", "## Input", "", display_input])
    if output_text:
        lines.extend(["", "## Output", "", output_text])
    return "\n".join(lines)


def _deep_parse_json_strings(obj):
    """Recursively parse string values that contain JSON dicts or lists.

    Ensures TOME sees structured data instead of stringified JSON blobs.
    Only replaces strings that decode to dict or list — scalar JSON values
    ("true", "123", "null") are left as-is.
    """
    if isinstance(obj, dict):
        return {k: _deep_parse_json_strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_parse_json_strings(v) for v in obj]
    if isinstance(obj, str):
        try:
            parsed = json.loads(obj)
            if isinstance(parsed, (dict, list)):
                return _deep_parse_json_strings(parsed)
        except (json.JSONDecodeError, ValueError):
            pass
    return obj


def render_markdown_output(data: dict) -> str:
    """Transform a tool output dict into TOME format (BFS-linearized markdown).

    Uses oboros.tome for token-optimized serialization. Multi-line text fields
    are extracted as markdown sections, scalar metadata stays in YAML frontmatter.
    String values containing serialized JSON dicts/lists are parsed before
    rendering so TOME can structure them properly.
    """
    return tome.dumps(_deep_parse_json_strings(data))
