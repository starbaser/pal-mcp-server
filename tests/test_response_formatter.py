import json

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

    # Content extracted as formatted text with front matter
    assert "---\nkey: content\n---" in text
    assert "# Analysis\n\nHere is the analysis." in text

    # Continuation info in JSON blob
    assert '"continuation_id": "abc-uuid"' in text
    assert '"remaining_turns": 9' in text


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
    # No continuation_offer in output
    assert "continuation_offer" not in text


def test_format_simple_tool_output_no_content():
    data = {"status": "success", "content": "", "metadata": {}}
    result = format_tool_result(_wrap(data), "debug")

    text = result[0].text
    # Empty content stays inline in JSON blob
    assert '"content": ""' in text


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

    # All non-formatted fields in JSON blob
    assert '"status": "codereview_in_progress"' in text
    assert '"step_guidance": "Please provide the file contents for review."' in text
    assert '"Review server.py"' in text
    assert '"Check error handling"' in text
    assert '"files_checked": 0' in text
    assert '"relevant_files": 3' in text
    assert '"issues_found": 0' in text
    assert '"file_context"' in text
    assert '"continuation_id": "wf-uuid"' in text
    assert '"next_step_required": true' in text


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
    # Single-line content stays in JSON blob
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
    # Required actions in JSON blob
    assert '"Check file"' in text
    assert '"Review code"' in text


def test_format_unusual_status():
    data = {
        "status": "files_required_to_continue",
        "content": "Need files",
        "metadata": {},
    }
    result = format_tool_result(_wrap(data), "debug")

    text = result[0].text
    assert '"status": "files_required_to_continue"' in text


# ---------------------------------------------------------------------------
# Formatted text extraction (programmatic detection)
# ---------------------------------------------------------------------------


def test_format_multiline_next_steps():
    data = {
        "status": "analysis_complete",
        "content": "Analysis done.",
        "step_number": 3,
        "total_steps": 3,
        "next_step_required": False,
        "continuation_id": "x",
        "model_used": "gemini-2.5-pro",
        "provider_used": "google",
        "next_steps": "1. Review the findings\n2. Apply the fix\n3. Run tests again",
        "metadata": {"tool_name": "analyze"},
    }
    result = format_tool_result(_wrap(data), "analyze")
    text = result[0].text

    # next_steps extracted with front matter key
    assert "---\nkey: next_steps\n---" in text
    assert "1. Review the findings" in text
    assert "2. Apply the fix" in text


def test_format_nested_work_summary_extracted():
    data = {
        "status": "analysis_complete",
        "content": "Here is the expert analysis.\n\nWith multiple paragraphs.",
        "step_number": 2,
        "total_steps": 2,
        "next_step_required": False,
        "model_used": "gpt-4o",
        "provider_used": "openai",
        "complete_investigation": {
            "initial_request": "Debug the auth flow",
            "steps_taken": 2,
            "files_examined": ["server.py", "auth.py"],
            "work_summary": "## Investigation\n\nChecked auth flow.\n\n## Findings\n\nFound the bug in token refresh.",
        },
    }
    result = format_tool_result(_wrap(data), "debug")
    text = result[0].text

    # Nested work_summary extracted with dotted key in front matter
    assert "---\nkey: complete_investigation.work_summary\n---" in text
    assert "Found the bug in token refresh." in text
    # Scalar nested fields stay in JSON blob
    assert '"steps_taken": 2' in text


def test_format_consensus_verdict_extracted():
    data = {
        "status": "consulting_model",
        "step_number": 2,
        "total_steps": 4,
        "next_step_required": True,
        "continuation_id": "c-uuid",
        "model_used": "gemini-2.5-pro",
        "provider_used": "google",
        "content": "Consulting model 2.",
        "model_response": {
            "model": "gpt-4o",
            "stance": "agree",
            "status": "success",
            "verdict": "I agree with the analysis.\n\nThe approach is sound.\n\nHere are my additions.",
        },
    }
    result = format_tool_result(_wrap(data), "consensus")
    text = result[0].text

    # Nested verdict extracted with dotted key
    assert "---\nkey: model_response.verdict\n---" in text
    assert "I agree with the analysis." in text
    # Scalar fields from model_response stay in JSON blob
    assert '"stance": "agree"' in text


def test_format_single_line_stays_in_metadata():
    data = {
        "status": "success",
        "content": "Done.",
        "metadata": {},
        "some_extra_field": "short value",
    }
    result = format_tool_result(_wrap(data), "chat")
    text = result[0].text

    # Single-line extra field in JSON blob
    assert '"some_extra_field": "short value"' in text
    # No front matter section for it
    assert "key: some_extra_field" not in text


def test_format_body_text_step_guidance():
    data = {
        "status": "codereview_in_progress",
        "step_number": 1,
        "total_steps": 3,
        "next_step_required": True,
        "continuation_id": "g-uuid",
        "model_used": "gemini-2.5-pro",
        "provider_used": "google",
        "step_guidance": "Please provide file contents.",
    }
    result = format_tool_result(_wrap(data), "codereview")
    text = result[0].text

    # Single-line step_guidance stays in JSON blob (pure value detection)
    assert "Please provide file contents." in text
    assert '"step_guidance"' in text
