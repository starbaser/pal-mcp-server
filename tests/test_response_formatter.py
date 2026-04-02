import json

from utils.response_formatter import _deep_parse_json_strings, render_markdown_output


def test_render_basic_tool_output():
    data = {
        "status": "success",
        "content": "## Heading\n\nSome analysis\n\nMore text",
        "content_type": "text",
        "metadata": {"tree_path": "my-store", "total_pages": 5},
    }
    md = render_markdown_output(data)

    assert "status: success" in md
    assert "tree_path: my-store" in md
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
        },
    }
    md = render_markdown_output(data)

    assert "continuation_id: abc-123" in md


def test_render_empty_content():
    data = {"status": "success", "content": ""}
    md = render_markdown_output(data)

    assert "status: success" in md


def test_deep_parse_json_strings_dict():
    inner = {"key": "value", "nested": [1, 2]}
    obj = {"content": json.dumps(inner), "status": "ok"}
    result = _deep_parse_json_strings(obj)
    assert result["content"] == inner
    assert result["status"] == "ok"


def test_deep_parse_json_strings_list():
    inner = [{"a": 1}, {"b": 2}]
    obj = {"items": json.dumps(inner)}
    result = _deep_parse_json_strings(obj)
    assert result["items"] == inner


def test_deep_parse_json_strings_nested():
    deep = {"level2": "data"}
    mid = {"level1": json.dumps(deep)}
    obj = {"content": json.dumps(mid)}
    result = _deep_parse_json_strings(obj)
    assert result["content"]["level1"] == deep


def test_deep_parse_json_strings_scalars_unchanged():
    obj = {"a": "true", "b": "123", "c": "hello", "d": "null"}
    result = _deep_parse_json_strings(obj)
    assert result == obj


def test_deep_parse_json_strings_non_json_unchanged():
    obj = {"content": "This is plain text\nwith newlines"}
    result = _deep_parse_json_strings(obj)
    assert result == obj


def test_render_embedded_json_content():
    inner = {"status": "analysis_complete", "findings": [{"id": 1}]}
    data = {"status": "success", "content": json.dumps(inner)}
    md = render_markdown_output(data)
    # The inner dict should be rendered as structured data, not a JSON string
    assert "analysis_complete" in md
    assert '"status"' not in md or "status: analysis_complete" in md
