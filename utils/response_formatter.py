import json
import logging

from mcp.types import TextContent


logger = logging.getLogger("pal_mcp")

_FOOTER_WIDTH = 40
_FOOTER_SEP = "━" * _FOOTER_WIDTH


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

            if "status" in data and "content" in data and isinstance(data.get("content"), str):
                text = _format_simple_tool_output(data, tool_name)
            elif "step_number" in data and "total_steps" in data:
                text = _format_workflow_output(data, tool_name)
            elif isinstance(data.get("content"), str):
                text = _format_simple_tool_output(data, tool_name)
            else:
                text = json.dumps(data, indent=2)

            formatted.append(TextContent(type="text", text=text))
        except Exception:
            logger.debug("response_formatter: could not reformat item for tool %r", tool_name, exc_info=True)
            formatted.append(item)

    return formatted


def _format_simple_tool_output(data: dict, tool_name: str) -> str:
    """Format a ToolOutput-shaped dict into a readable string.

    Produces a header line, the content body, and an optional footer with
    continuation info when a continuation_offer is present.
    """
    metadata = data.get("metadata") or {}
    model = metadata.get("model_used", "")
    provider = metadata.get("provider_used", "")
    status = data.get("status", "")

    if status == "error":
        header = _format_header(tool_name, "ERROR", "")
    else:
        header = _format_header(tool_name, model, provider)

    content = data.get("content") or "(no content)"

    lines = [header, "", content]

    if status not in ("success", "continuation_available", "error") and status:
        lines.append(f"\nstatus: {status}")

    continuation_offer = data.get("continuation_offer")
    if continuation_offer:
        footer_pairs: dict[str, object] = {}
        if cid := continuation_offer.get("continuation_id"):
            footer_pairs["continuation_id"] = cid
        if (remaining := continuation_offer.get("remaining_turns")) is not None:
            footer_pairs["remaining_turns"] = remaining
        lines.append(_format_footer_from_pairs(footer_pairs))

    return "\n".join(lines)


def _format_workflow_output(data: dict, tool_name: str) -> str:
    """Format a WorkflowTool step dict into a readable string.

    Renders step progress, guidance, required actions, and tool-specific status
    dicts extracted by scanning for keys ending in '_status'.
    """
    step_number = data.get("step_number", "?")
    total_steps = data.get("total_steps", "?")
    model = data.get("model_used", "")
    provider = data.get("provider_used", "")
    status = data.get("status", "")
    step_guidance = data.get("step_guidance", "")
    required_actions = data.get("required_actions") or []

    step_info = f" [{step_number}/{total_steps}]"
    header = _format_header(tool_name, model, provider, step_info=step_info)

    lines = [header, ""]

    if status:
        lines.append(f"Status: {status}")
        lines.append("")

    if step_guidance:
        lines.append(step_guidance)
        lines.append("")

    if required_actions:
        lines.append("Required actions:")
        for i, action in enumerate(required_actions, start=1):
            if isinstance(action, dict):
                label = action.get("action") or action.get("description") or str(action)
            else:
                label = str(action)
            lines.append(f"  {i}. {label}")
        lines.append("")

    status_dicts: list[tuple[str, dict]] = [
        (k, v) for k, v in data.items() if k.endswith("_status") and isinstance(v, dict)
    ]
    for _, status_dict in status_dicts:
        for k, v in status_dict.items():
            lines.append(f"{k}: {v}")
        lines.append("")

    footer_pairs: dict[str, object] = {}
    if cid := data.get("continuation_id"):
        footer_pairs["continuation_id"] = cid
    if (nsr := data.get("next_step_required")) is not None:
        footer_pairs["next_step_required"] = str(nsr).lower()

    lines.append(_format_footer_from_pairs(footer_pairs))

    return "\n".join(lines)


def _format_header(
    tool_name: str,
    model: str,
    provider: str,
    step_info: str | None = None,
) -> str:
    """Build a heavy-line header string for tool output.

    Includes step_info after the tool name when provided. Omits the model/provider
    segment when both are absent.
    """
    name_part = f"━━━ {tool_name}"
    if step_info:
        name_part += step_info
    name_part += " ━━━"

    if model or provider:
        model_str = model if model else ""
        provider_str = f" ({provider})" if provider else ""
        return f"{name_part} {model_str}{provider_str} ━━━"

    return name_part



def _format_footer_from_pairs(pairs: dict[str, object]) -> str:
    """Render a footer separator followed by key: value lines.

    Returns an empty string when pairs is empty.
    """
    if not pairs:
        return ""

    lines = [_FOOTER_SEP]
    for k, v in pairs.items():
        lines.append(f"{k}: {v}")
    return "\n".join(lines)
