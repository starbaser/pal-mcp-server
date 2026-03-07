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

            if "step_number" in data and "total_steps" in data:
                text = _format_workflow_output(data, tool_name)
            elif "status" in data and "content" in data and isinstance(data.get("content"), str):
                text = _format_simple_tool_output(data, tool_name)
            elif isinstance(data.get("content"), str):
                text = _format_simple_tool_output(data, tool_name)
            else:
                text = json.dumps(data, indent=2)

            formatted.append(TextContent(type="text", text=text))
        except Exception:
            logger.debug("response_formatter: could not reformat item for tool %r", tool_name, exc_info=True)
            formatted.append(item)

    return formatted


# ── Keys consumed by each formatter ──────────────────────────────────────────
# Any key NOT in these sets gets collected into the "remaining" JSON dump.

_SIMPLE_KNOWN_KEYS = frozenset({
    "status", "content", "content_type", "metadata",
    "continuation_offer",
})

_WORKFLOW_KNOWN_KEYS = frozenset({
    "step_number", "total_steps", "status", "content",
    "next_steps", "step_guidance", "required_actions",
    "continuation_id", "next_step_required", "metadata",
    "continuation_offer",
    # _status dicts are handled dynamically
})


def _extract_model_provider(data: dict) -> tuple[str, str]:
    """Extract model and provider from either top-level or nested metadata."""
    metadata = data.get("metadata") or {}
    model = metadata.get("model_used", "") or data.get("model_used", "")
    provider = metadata.get("provider_used", "") or data.get("provider_used", "")
    return model, provider


def _format_simple_tool_output(data: dict, tool_name: str) -> str:
    """Format a ToolOutput-shaped dict into a readable string.

    Renders: header, content body, continuation footer, and any remaining
    fields as a JSON dump so nothing is silently dropped.
    """
    model, provider = _extract_model_provider(data)
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
        if note := continuation_offer.get("note"):
            footer_pairs["note"] = note
        lines.append(_format_footer_from_pairs(footer_pairs))

    remaining = _collect_remaining(data, _SIMPLE_KNOWN_KEYS)
    if remaining:
        lines.append(_format_remaining_json(remaining))

    return "\n".join(lines)


def _format_workflow_output(data: dict, tool_name: str) -> str:
    """Format a WorkflowTool step dict into a readable string.

    Renders step progress, content, next_steps, required actions, tool-specific
    _status dicts, and a JSON dump of any remaining fields.
    """
    step_number = data.get("step_number", "?")
    total_steps = data.get("total_steps", "?")
    model, provider = _extract_model_provider(data)
    status = data.get("status", "")
    next_steps = data.get("next_steps", "") or data.get("step_guidance", "")
    required_actions = data.get("required_actions") or []

    step_info = f" [{step_number}/{total_steps}]"
    header = _format_header(tool_name, model, provider, step_info=step_info)

    lines = [header, ""]

    if status:
        lines.append(f"Status: {status}")
        lines.append("")

    content = data.get("content")
    if isinstance(content, str) and content:
        lines.append(content)
        lines.append("")

    if next_steps:
        lines.append(next_steps)
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

    # Render tool-specific *_status dicts inline
    status_keys = set()
    for k, v in data.items():
        if k.endswith("_status") and isinstance(v, dict):
            status_keys.add(k)
            for sk, sv in v.items():
                lines.append(f"{sk}: {sv}")
            lines.append("")

    # Footer: continuation info
    footer_pairs: dict[str, object] = {}
    if cid := data.get("continuation_id"):
        footer_pairs["continuation_id"] = cid
    if (nsr := data.get("next_step_required")) is not None:
        footer_pairs["next_step_required"] = str(nsr).lower()

    continuation_offer = data.get("continuation_offer")
    if continuation_offer and isinstance(continuation_offer, dict):
        if cid2 := continuation_offer.get("continuation_id"):
            footer_pairs.setdefault("continuation_id", cid2)
        if (remaining := continuation_offer.get("remaining_turns")) is not None:
            footer_pairs["remaining_turns"] = remaining

    lines.append(_format_footer_from_pairs(footer_pairs))

    # Dump everything else as JSON so nothing is silently lost
    skip_keys = _WORKFLOW_KNOWN_KEYS | status_keys
    remaining = _collect_remaining(data, skip_keys)
    if remaining:
        lines.append(_format_remaining_json(remaining))

    return "\n".join(lines)


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


def _format_footer_from_pairs(pairs: dict[str, object]) -> str:
    """Render a footer separator followed by key: value lines."""
    if not pairs:
        return ""

    lines = [_FOOTER_SEP]
    for k, v in pairs.items():
        lines.append(f"{k}: {v}")
    return "\n".join(lines)


def _collect_remaining(data: dict, known_keys: frozenset | set) -> dict:
    """Collect all keys from data that are not in known_keys."""
    return {k: v for k, v in data.items() if k not in known_keys and v is not None}


def _format_remaining_json(remaining: dict) -> str:
    """Format remaining fields as a compact JSON block."""
    return f"\n```json\n{json.dumps(remaining, indent=2, ensure_ascii=False)}\n```"
