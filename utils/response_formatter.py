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
    prompt: str | None = None,
    response: str | None = None,
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
    if prompt:
        lines.extend(["", "## Prompt", "", prompt])
    if response:
        lines.extend(["", "## Response", "", response])
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


_POSTSCRIPT_KEYS = (
    "next_steps",
    "required_actions",
    "store_continuation_guidance",
    "store_chain_note",
    "saved_content_path",
    "completion_message",
)


def render_markdown_output(data: dict) -> str:
    """Transform a tool output dict into a markdown document with YAML front matter.

    Multi-line text fields are extracted from the JSON and rendered as markdown
    sections below the front matter.  Scalar metadata stays in the front matter
    under the ``json:`` key.

    Known directive keys (next_steps, required_actions, store_continuation_guidance,
    etc.) are extracted from the data and rendered as a postscript section at the
    bottom of the document so they are visible to agents.
    """
    # Extract postscript directives before field separation
    postscript = _extract_postscript(data)

    inline, formatted = _separate_fields(data)

    # Inject placeholder references for extracted keys
    _inject_placeholders(inline, [key for key, _ in formatted])

    # Build YAML front matter
    yaml_body = yaml.dump({"json": inline}, default_flow_style=False, sort_keys=False, allow_unicode=True).rstrip()
    parts = [f"---\n{yaml_body}\n---"]

    # Render extracted sections with dotted key path as heading
    for dotted_key, text in formatted:
        parts.append(f"# `{dotted_key}`\n\n{text}")

    # Render postscript directives at the bottom
    if postscript:
        parts.append("---\n\n" + "\n\n".join(postscript))

    return "\n\n".join(parts)


def _extract_postscript(data: dict) -> list[str]:
    """Pull known directive keys from data and format them as postscript lines.

    Removes matched keys from data so they don't appear in YAML front matter.
    """
    lines = []
    for key in _POSTSCRIPT_KEYS:
        value = data.pop(key, None)
        if value is None:
            continue
        if isinstance(value, list):
            items = "\n".join(f"- {item}" for item in value)
            lines.append(f"**{key}**:\n{items}")
        else:
            lines.append(f"**{key}**: {value}")
    return lines


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
