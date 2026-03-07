import json

import pytest
from mcp.types import ImageContent, TextContent

from utils.response_formatter import format_tool_result


def _wrap(data: dict) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(data))]


# ---------------------------------------------------------------------------
# SimpleTool output (ToolOutput-shaped)
# ---------------------------------------------------------------------------


def test_format_simple_tool_output_basic():
    data = {
        "status": "continuation_available",
        "content": "# Analysis\n\nHere is the analysis.",
        "content_type": "text",
        "metadata": {
            "tool_name": "chat",
            "model_used": "gemini-2.5-pro",
            "provider_used": "google",
            "conversation_ready": True,
        },
        "continuation_offer": {
            "continuation_id": "abc-uuid",
            "note": "Continue...",
            "remaining_turns": 9,
        },
    }
    result = format_tool_result(_wrap(data), "chat")

    assert len(result) == 1
    assert result[0].type == "text"
    text = result[0].text

    # Header
    assert "chat" in text
    assert "gemini-2.5-pro" in text
    assert "google" in text
    assert "━" in text

    # Body — newlines must be preserved unescaped
    assert "# Analysis\n\nHere is the analysis." in text

    # Footer
    assert "continuation_id: abc-uuid" in text
    assert "remaining_turns: 9" in text


def test_format_simple_tool_output_error():
    data = {"status": "error", "content": "Something went wrong", "metadata": {}}
    result = format_tool_result(_wrap(data), "chat")

    assert len(result) == 1
    text = result[0].text
    assert "ERROR" in text
    # model/provider absent — must not bleed a stale value in
    assert "gemini" not in text


def test_format_simple_tool_output_no_continuation():
    data = {
        "status": "success",
        "content": "Done.",
        "metadata": {"model_used": "gpt-4o", "provider_used": "openai"},
        "continuation_offer": None,
    }
    result = format_tool_result(_wrap(data), "analyze")

    text = result[0].text
    # Footer separator must not appear when there is no continuation
    assert "━" * 40 not in text


def test_format_simple_tool_output_no_content():
    # content must be a str for the simple-tool branch to activate; an empty
    # string is falsy, so _format_simple_tool_output substitutes "(no content)".
    data = {"status": "success", "content": "", "metadata": {}}
    result = format_tool_result(_wrap(data), "debug")

    assert "(no content)" in result[0].text


# ---------------------------------------------------------------------------
# WorkflowTool output
# ---------------------------------------------------------------------------


def test_format_workflow_output():
    data = {
        "status": "codereview_in_progress",
        "step_number": 1,
        "total_steps": 3,
        "next_step_required": True,
        "continuation_id": "wf-uuid",
        "model_used": "gemini-2.5-pro",
        "provider_used": "google",
        "step_guidance": "Please provide the file contents for review.",
        "required_actions": ["Review server.py", "Check error handling"],
        "codereview_status": {
            "files_checked": 0,
            "relevant_files": 3,
            "issues_found": 0,
        },
        "file_context": {"some": "data"},
    }
    result = format_tool_result(_wrap(data), "codereview")

    assert len(result) == 1
    text = result[0].text

    # Header
    assert "codereview" in text
    assert "[1/3]" in text
    assert "gemini-2.5-pro" in text
    assert "google" in text

    # Body
    assert "Status: codereview_in_progress" in text
    assert "Please provide the file contents for review." in text

    # Required actions — numbered
    assert "1. Review server.py" in text
    assert "2. Check error handling" in text

    # codereview_status flattened as key: value
    assert "files_checked: 0" in text
    assert "relevant_files: 3" in text
    assert "issues_found: 0" in text

    # file_context should appear in the remaining JSON dump (nothing silently dropped)
    assert "file_context" in text

    # Footer
    assert "continuation_id: wf-uuid" in text
    assert "next_step_required: true" in text


# ---------------------------------------------------------------------------
# Passthrough cases
# ---------------------------------------------------------------------------


def test_format_non_json_passthrough():
    raw = "Unknown tool: foo"
    item = TextContent(type="text", text=raw)
    result = format_tool_result([item], "unknown")

    assert len(result) == 1
    assert result[0].text == raw


def test_format_non_dict_json_passthrough():
    raw = "[1, 2, 3]"
    item = TextContent(type="text", text=raw)
    result = format_tool_result([item], "unknown")

    assert len(result) == 1
    assert result[0].text == raw


# ---------------------------------------------------------------------------
# Mixed content types
# ---------------------------------------------------------------------------


def test_format_image_content_skipped():
    tool_output_data = {
        "status": "success",
        "content": "Image generated.",
        "metadata": {"model_used": "dall-e-3", "provider_used": "openai"},
    }
    image = ImageContent(type="image", data="base64data", mimeType="image/png")
    text_item = TextContent(type="text", text=json.dumps(tool_output_data))
    result = format_tool_result([text_item, image], "imagegen")

    assert len(result) == 2
    # ImageContent passes through unchanged
    assert result[1].type == "image"
    assert result[1].data == "base64data"
    # TextContent is formatted
    assert result[0].type == "text"
    assert "dall-e-3" in result[0].text


# ---------------------------------------------------------------------------
# Generic / edge-case dict shapes
# ---------------------------------------------------------------------------


def test_format_generic_dict_pretty_print():
    data = {"key": "value", "nested": {"a": 1}}
    result = format_tool_result(_wrap(data), "unknown")

    assert len(result) == 1
    assert result[0].text == json.dumps(data, indent=2)


def test_format_dict_with_content_key():
    data = {"content": "Some text response", "other": 123}
    result = format_tool_result(_wrap(data), "chat")

    assert len(result) == 1
    # content string is unwrapped via the simple-tool path
    assert "Some text response" in result[0].text


def test_format_missing_metadata_fields():
    data = {"status": "success", "content": "Hello", "metadata": {}}
    result = format_tool_result(_wrap(data), "chat")

    text = result[0].text
    assert "chat" in text
    # No model/provider segment — header ends after the tool-name block
    assert "gemini" not in text
    assert "openai" not in text


def test_format_workflow_required_actions_dicts():
    data = {
        "status": "running",
        "step_number": 2,
        "total_steps": 4,
        "next_step_required": False,
        "continuation_id": "x",
        "model_used": "gpt-4o",
        "provider_used": "openai",
        "step_guidance": "Review the code.",
        "required_actions": [
            {"action": "Check file"},
            {"description": "Review code"},
        ],
    }
    result = format_tool_result(_wrap(data), "codereview")

    text = result[0].text
    assert "1. Check file" in text
    assert "2. Review code" in text


def test_format_unusual_status():
    data = {
        "status": "files_required_to_continue",
        "content": "Need files",
        "metadata": {},
    }
    result = format_tool_result(_wrap(data), "debug")

    text = result[0].text
    assert "status: files_required_to_continue" in text
