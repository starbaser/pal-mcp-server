import logging

from oboros import tome

logger = logging.getLogger("pal_mcp")


def format_layer_markdown(
    heading: str,
    *,
    label: str | None = None,
    entry_type: str | None = None,
    tool_name: str | None = None,
    model: str | None = None,
    timestamp: str | None = None,
    files: list[str] | None = None,
    input_text: str | None = None,
    output_text: str | None = None,
) -> str:
    """Format a context layer or tool response as structured markdown.

    Shared by readnode (layer display) and content save (response persistence).
    """
    lines = [f"# {heading}", ""]
    if label:
        lines.append(f"**Label:** {label}")
    if entry_type:
        lines.append(f"**Type:** {entry_type}")
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
        lines.extend(["", "## Input", "", input_text])
    if output_text:
        lines.extend(["", "## Output", "", output_text])
    return "\n".join(lines)


def render_markdown_output(data: dict) -> str:
    """Transform a tool output dict into TOME format (BFS-linearized markdown).

    Uses oboros.tome for token-optimized serialization. Multi-line text fields
    are extracted as markdown sections, scalar metadata stays in YAML frontmatter.
    """
    return tome.dumps(data)
