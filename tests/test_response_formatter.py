from utils.response_formatter import render_markdown_output


def test_render_basic_tool_output():
    data = {
        "status": "success",
        "content": "## Heading\n\nSome analysis\n\nMore text",
        "content_type": "text",
        "metadata": {"store_id": "my-store", "total_pages": 5},
    }
    md = render_markdown_output(data)

    assert "status: success" in md
    assert "store_id: my-store" in md
    assert "total_pages: 5" in md
    assert "## Heading" in md
    assert "Some analysis" in md


def test_render_scalar_only():
    data = {"status": "success", "content": "PAL v9.8.2", "content_type": "text"}
    md = render_markdown_output(data)

    assert "status: success" in md
    assert "PAL v9.8.2" in md


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


def test_render_empty_content():
    data = {"status": "success", "content": ""}
    md = render_markdown_output(data)

    assert "status: success" in md
