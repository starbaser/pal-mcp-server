import json
import logging

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
