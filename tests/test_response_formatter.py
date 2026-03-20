import json

from utils.response_formatter import render_markdown_output, _separate_fields


# ---------------------------------------------------------------------------
# _separate_fields
# ---------------------------------------------------------------------------


def test_separate_fields_multiline_extracted():
    data = {"status": "success", "content": "line1\nline2\nline3", "metadata": {"key": "val"}}
    inline, formatted = _separate_fields(data)

    assert "content" not in inline
    assert inline["status"] == "success"
    assert inline["metadata"] == {"key": "val"}
    assert len(formatted) == 1
    assert formatted[0] == ("content", "line1\nline2\nline3")


def test_separate_fields_single_line_stays():
    data = {"status": "success", "content": "short value"}
    inline, formatted = _separate_fields(data)

    assert inline["content"] == "short value"
    assert formatted == []


def test_separate_fields_none_dropped():
    data = {"status": "success", "page": None, "content": "ok"}
    inline, formatted = _separate_fields(data)

    assert "page" not in inline
    assert inline["status"] == "success"


def test_separate_fields_nested_extraction():
    data = {
        "model_response": {
            "stance": "agree",
            "verdict": "I agree.\n\nThe approach is sound.\n\nAdditions below.",
        }
    }
    inline, formatted = _separate_fields(data)

    assert inline["model_response"]["stance"] == "agree"
    assert "verdict" not in inline.get("model_response", {})
    assert len(formatted) == 1
    assert formatted[0][0] == "model_response.verdict"


# ---------------------------------------------------------------------------
# render_markdown_output — ToolOutput shape
# ---------------------------------------------------------------------------


def test_render_basic_tool_output():
    data = {
        "status": "success",
        "content": "## Heading\n\nSome analysis\n\nMore text",
        "content_type": "text",
        "metadata": {"store_id": "my-store", "total_pages": 5},
    }
    md = render_markdown_output(data)

    assert md.startswith("---\n")
    assert "json:" in md
    assert "store_id: my-store" in md
    assert "total_pages: 5" in md
    # Content extracted to section
    assert "# `content`" in md
    assert "## Heading\n\nSome analysis\n\nMore text" in md
    # Placeholder in front matter
    assert "\u2192 # `content`" in md


def test_render_scalar_only():
    data = {"status": "success", "content": "PAL v9.8.2", "content_type": "text"}
    md = render_markdown_output(data)

    assert md.startswith("---\n")
    # Short content stays inline
    assert "PAL v9.8.2" in md
    # No extracted sections
    assert "# `content`" not in md.split("---", 2)[-1]


def test_render_error():
    data = {"status": "error", "content": "Model not found"}
    md = render_markdown_output(data)

    assert "status: error" in md
    assert "Model not found" in md


def test_render_continuation_offer():
    data = {
        "status": "continuation_available",
        "content": "Hello!",
        "continuation_offer": {
            "continuation_id": "abc-123",
            "note": "Continue the conversation.",
            "context_window": 200000,
            "context_used": 5000,
        },
    }
    md = render_markdown_output(data)

    assert "continuation_id: abc-123" in md
    assert "context_window: 200000" in md


# ---------------------------------------------------------------------------
# render_markdown_output — workflow shape
# ---------------------------------------------------------------------------


def test_render_workflow_output():
    data = {
        "status": "analysis_complete",
        "step_number": 2,
        "total_steps": 2,
        "next_step_required": False,
        "content": "Expert analysis here.\n\nWith paragraphs.\n\nAnd more.",
        "complete_investigation": {
            "steps_taken": 2,
            "work_summary": "## Investigation\n\nChecked auth.\n\n## Findings\n\nBug found.",
        },
    }
    md = render_markdown_output(data)

    # Both multiline fields extracted
    assert "# `content`" in md
    assert "Expert analysis here." in md
    assert "# `complete_investigation.work_summary`" in md
    assert "Bug found." in md
    # Scalar nested fields in front matter
    assert "steps_taken: 2" in md


def test_render_empty_content():
    data = {"status": "success", "content": ""}
    md = render_markdown_output(data)

    assert "status: success" in md
