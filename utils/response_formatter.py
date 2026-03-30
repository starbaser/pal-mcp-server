import logging

import yaml

logger = logging.getLogger("pal_mcp")

_NEWLINE_THRESHOLD = 2


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

    Shared by palread (layer display) and content save (response persistence).
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


def _is_formatted_text(value: object) -> bool:
    """Return True if value is a string with >= _NEWLINE_THRESHOLD newlines."""
    return isinstance(value, str) and value.count("\n") >= _NEWLINE_THRESHOLD


def _separate_fields(
    data: dict,
    prefix: str = "",
    depth: int = 0,
    max_depth: int = 2,
) -> tuple[dict, list[tuple[str, str]]]:
    """Recursively separate formatted text from inline metadata.

    Returns:
        inline: dict preserving insertion order, with formatted-text values removed
        formatted: list of (dotted_key, text_value) in insertion order
    """
    inline = {}
    formatted = []

    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key

        if value is None:
            continue

        if _is_formatted_text(value):
            formatted.append((full_key, value))

        elif isinstance(value, dict) and depth < max_depth:
            nested_inline, nested_formatted = _separate_fields(
                value, prefix=full_key, depth=depth + 1, max_depth=max_depth
            )
            formatted.extend(nested_formatted)
            if nested_inline:
                inline[key] = nested_inline

        elif isinstance(value, list) and value:
            multiline = [v for v in value if _is_formatted_text(v)]
            if multiline:
                formatted.append((full_key, "\n\n".join(multiline)))
                rest = [v for v in value if not _is_formatted_text(v)]
                if rest:
                    inline[key] = rest
            else:
                inline[key] = value

        else:
            inline[key] = value

    return inline, formatted


# ---------------------------------------------------------------------------
# Markdown rendering (default MCP output format)
# ---------------------------------------------------------------------------


def render_markdown_output(data: dict) -> str:
    """Transform a tool output dict into a markdown document with YAML front matter.

    Multi-line text fields are extracted and rendered as markdown sections
    below the front matter. Scalar metadata stays in the front matter.
    """
    inline, formatted = _separate_fields(data)

    _inject_placeholders(inline, [key for key, _ in formatted])

    yaml_body = yaml.dump(
        inline,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    ).rstrip()
    parts = [f"---\n{yaml_body}\n---"]

    for dotted_key, text in formatted:
        parts.append(f"# `{dotted_key}`\n\n{text}")

    return "\n\n".join(parts)


def _inject_placeholders(inline: dict, dotted_keys: list[str]) -> None:
    """Walk *inline* and set placeholder values for keys that were extracted."""
    for dotted_key in dotted_keys:
        parts = dotted_key.split(".")
        target = inline
        for part in parts[:-1]:
            if part not in target or not isinstance(target[part], dict):
                target[part] = {}
            target = target[part]
        target[parts[-1]] = f"\u2192 # `{dotted_key}`"
