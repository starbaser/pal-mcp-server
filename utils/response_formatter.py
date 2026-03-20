import json
import logging

import yaml
from mcp.types import TextContent

logger = logging.getLogger("pal_mcp")

_NEWLINE_THRESHOLD = 2


def format_tool_result(result: list, tool_name: str) -> list:
    """Format a list of MCP content items into human-readable text.

    Iterates over result items and reformats any TextContent items that contain
    recognizable JSON shapes. Non-TextContent items and items that fail parsing
    are returned unchanged.
    """
    formatted = []
    for item in result:
        if item.type != "text":
            formatted.append(item)
            continue

        try:
            data = json.loads(item.text)
            if not isinstance(data, dict):
                formatted.append(item)
                continue

            if (
                ("step_number" in data and "total_steps" in data)
                or ("status" in data and isinstance(data.get("content"), str))
                or isinstance(data.get("content"), str)
            ):
                text = _format_tool_output(data, tool_name)
            else:
                text = json.dumps(data, indent=2)

            formatted.append(TextContent(type="text", text=text))
        except Exception:
            logger.debug("response_formatter: could not reformat item for tool %r", tool_name, exc_info=True)
            formatted.append(item)

    return formatted


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


def _format_tool_output(data: dict, tool_name: str) -> str:
    """Unified formatter for all PAL tool output shapes.

    Renders: header, JSON blob of inline metadata, then formatted text
    sections in insertion order.
    """
    model, provider = _extract_model_provider(data)
    status = data.get("status", "")

    step_number = data.get("step_number")
    total_steps = data.get("total_steps")
    step_info = f" [{step_number}/{total_steps}]" if step_number and total_steps else None

    if status == "error":
        header = _format_header(tool_name, "ERROR", "")
    else:
        header = _format_header(tool_name, model, provider, step_info=step_info)

    lines = [header, ""]

    inline, formatted = _separate_fields(data)

    if inline:
        lines.append(json.dumps(inline, indent=2, ensure_ascii=False))
        lines.append("")

    for key, text in formatted:
        lines.append("---")
        lines.append(f"key: {key}")
        lines.append("---")
        lines.append("")
        lines.append(text)
        lines.append("")

    return "\n".join(lines)


def _extract_model_provider(data: dict) -> tuple[str, str]:
    """Extract model and provider from either top-level or nested metadata."""
    metadata = data.get("metadata") or {}
    model = metadata.get("model_used", "") or data.get("model_used", "")
    provider = metadata.get("provider_used", "") or data.get("provider_used", "")
    return model, provider


def _format_header(
    tool_name: str,
    model: str,
    provider: str,
    step_info: str | None = None,
) -> str:
    """Build a heavy-line header string for tool output."""
    name_part = f"━━━ {tool_name}"
    if step_info:
        name_part += step_info
    name_part += " ━━━"

    if model or provider:
        model_str = model if model else ""
        provider_str = f" ({provider})" if provider else ""
        return f"{name_part} {model_str}{provider_str} ━━━"

    return name_part


# ---------------------------------------------------------------------------
# Markdown rendering (default MCP output format)
# ---------------------------------------------------------------------------


def _make_section_title(dotted_key: str) -> str:
    """Derive a markdown section title from a dotted key path.

    ``content`` → ``Content``
    ``continuation_offer.note`` → ``Note``
    ``expert_analysis`` → ``Expert Analysis``
    """
    leaf = dotted_key.rsplit(".", 1)[-1]
    return leaf.replace("_", " ").title()


def render_markdown_output(data: dict) -> str:
    """Transform a tool output dict into a markdown document with YAML front matter.

    Multi-line text fields are extracted from the JSON and rendered as markdown
    sections below the front matter.  Scalar metadata stays in the front matter
    under the ``json:`` key.
    """
    inline, formatted = _separate_fields(data)

    # Build reference map: inject "→ ## Title" placeholders for extracted fields
    section_titles: list[tuple[str, str]] = []
    for dotted_key, _ in formatted:
        title = _make_section_title(dotted_key)
        section_titles.append((dotted_key, title))

    # Patch inline dict with placeholder references for extracted keys
    _inject_placeholders(inline, section_titles)

    # Build YAML front matter
    yaml_body = yaml.dump({"json": inline}, default_flow_style=False, sort_keys=False, allow_unicode=True).rstrip()
    parts = [f"---\n{yaml_body}\n---"]

    # Render extracted sections
    for (_dotted_key, text), (__, title) in zip(formatted, section_titles):
        parts.append(f"## {title}\n\n{text}")

    return "\n\n".join(parts)


def _inject_placeholders(inline: dict, section_titles: list[tuple[str, str]]) -> None:
    """Walk *inline* and set placeholder values for keys that were extracted.

    ``section_titles`` is a list of ``(dotted_key, title)`` pairs.  For a
    top-level key like ``content`` the inline dict gets
    ``inline["content"] = "→ ## Content"``.  For nested keys like
    ``continuation_offer.note`` we walk into ``inline["continuation_offer"]``
    and set ``["note"]``.
    """
    for dotted_key, title in section_titles:
        parts = dotted_key.split(".")
        target = inline
        for part in parts[:-1]:
            if part not in target or not isinstance(target[part], dict):
                target[part] = {}
            target = target[part]
        target[parts[-1]] = f"\u2192 ## {title}"
