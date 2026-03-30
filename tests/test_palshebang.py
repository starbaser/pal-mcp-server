"""Unit tests for the palshebang #!/> file extraction feature.

Covers _GEN_BLOCK_RE, _resolve_gen_call_id, and _extract_gen_files from server.py.
"""

from __future__ import annotations

import json
import re

from mcp.types import TextContent

from server import _GEN_BLOCK_RE, _extract_gen_files, _resolve_gen_call_id

# ---------------------------------------------------------------------------
# _GEN_BLOCK_RE
# ---------------------------------------------------------------------------


class TestGenBlockRe:
    def test_matches_simple_block_with_lang(self):
        text = "```python\n#!/> main.py\nprint('hello')\n```\n"
        m = _GEN_BLOCK_RE.search(text)
        assert m is not None
        assert m.group("filename") == "main.py"
        assert m.group("lang") == "python"
        assert m.group("fence") == "```"
        assert m.group("body") == "print('hello')"

    def test_matches_bare_fence_no_lang(self):
        text = "```\n#!/> output.txt\nhello world\n```\n"
        m = _GEN_BLOCK_RE.search(text)
        assert m is not None
        assert m.group("filename") == "output.txt"
        assert m.group("lang") == ""

    def test_matches_four_backtick_fence(self):
        text = "````python\n#!/> app.py\nx = 1\n````\n"
        m = _GEN_BLOCK_RE.search(text)
        assert m is not None
        assert m.group("fence") == "````"
        assert m.group("filename") == "app.py"

    def test_no_match_without_shebang(self):
        text = "```python\nprint('hello')\n```\n"
        assert _GEN_BLOCK_RE.search(text) is None

    def test_no_match_shebang_not_first_line(self):
        text = "```python\nprint('hello')\n#!/> late.py\n```\n"
        assert _GEN_BLOCK_RE.search(text) is None

    def test_captures_multiline_body(self):
        text = "```python\n#!/> multi.py\ndef foo():\n    return 1\n```\n"
        m = _GEN_BLOCK_RE.search(text)
        assert m is not None
        assert "def foo():" in m.group("body")
        assert "return 1" in m.group("body")

    def test_handles_crlf_line_endings(self):
        text = "```python\r\n#!/> crlf.py\r\nvalue = 42\r\n```\r\n"
        m = _GEN_BLOCK_RE.search(text)
        assert m is not None
        assert m.group("filename") == "crlf.py"

    def test_matches_multiple_blocks_in_string(self):
        text = "```python\n#!/> first.py\nfirst\n```\n" "some prose\n" "```js\n#!/> second.js\nconsole.log()\n```\n"
        matches = list(_GEN_BLOCK_RE.finditer(text))
        assert len(matches) == 2
        assert matches[0].group("filename") == "first.py"
        assert matches[1].group("filename") == "second.js"

    def test_filename_whitespace_stripped(self):
        text = "```\n#!/>   padded.py   \ncontent\n```\n"
        m = _GEN_BLOCK_RE.search(text)
        assert m is not None
        # The regex captures to the end of line; strip() is applied in _extract_gen_files
        assert m.group("filename").strip() == "padded.py"

    def test_fence_must_match_opening(self):
        # Three-backtick open should not be closed by four-backtick close
        text = "```python\n#!/> bad.py\ncontent\n````\n"
        assert _GEN_BLOCK_RE.search(text) is None

    def test_lang_group_with_spaces(self):
        text = "```python  \n#!/> spaced.py\npass\n```\n"
        m = _GEN_BLOCK_RE.search(text)
        assert m is not None
        assert m.group("lang") == "python  "


# ---------------------------------------------------------------------------
# _resolve_gen_call_id
# ---------------------------------------------------------------------------


class TestResolveGenCallId:
    def test_uuid_with_dashes_truncated_to_12_hex(self):
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        result = _resolve_gen_call_id(uuid)
        # dashes stripped from the full UUID, then first 12 chars
        assert result == "550e8400e29b"
        assert len(result) == 12
        assert re.fullmatch(r"[0-9a-fA-F]{12}", result)

    def test_uuid_without_dashes(self):
        uuid = "550e8400e29b41d4a716446655440000"
        result = _resolve_gen_call_id(uuid)
        assert result == "550e8400e29b"

    def test_store_path_returns_last_segment(self):
        assert _resolve_gen_call_id("myproject.L3") == "L3"

    def test_store_path_deeply_nested(self):
        assert _resolve_gen_call_id("root.sub.L12") == "L12"

    def test_store_path_truncated_to_16_chars(self):
        long_segment = "A" * 20
        result = _resolve_gen_call_id(f"prefix.{long_segment}")
        assert result == "A" * 16

    def test_none_returns_timestamp_string(self):
        result = _resolve_gen_call_id(None)
        assert isinstance(result, str)
        assert len(result) == 15  # YYYYmmdd_HHMMSS
        assert re.fullmatch(r"\d{8}_\d{6}", result)

    def test_empty_string_returns_timestamp(self):
        result = _resolve_gen_call_id("")
        assert re.fullmatch(r"\d{8}_\d{6}", result)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(content: str, status: str = "success", continuation_id: str = "test-uuid") -> list[TextContent]:
    payload = json.dumps(
        {
            "status": status,
            "content": content,
            "continuation_offer": {"continuation_id": continuation_id},
        }
    )
    return [TextContent(type="text", text=payload)]


# ---------------------------------------------------------------------------
# _extract_gen_files
# ---------------------------------------------------------------------------


class TestExtractGenFiles:
    def test_no_shebang_passthrough(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.CODE_STORAGE_DIR", str(tmp_path))
        content = "Here is some text with a normal code block:\n```python\nprint('hi')\n```\n"
        result = _make_result(content)
        out = _extract_gen_files(result, "myproject.L1", str(tmp_path))
        assert out is result  # unchanged identity
        data = json.loads(out[0].text)
        assert "gen_files" not in data
        assert data["content"] == content

    def test_single_block_file_written(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.CODE_STORAGE_DIR", str(tmp_path))
        content = "Here is the file:\n```python\n#!/> main.py\nprint('hello')\n```\n"
        result = _make_result(content, continuation_id="myproject.L5")
        cwd = str(tmp_path / "project")
        out = _extract_gen_files(result, "myproject.L5", cwd)
        data = json.loads(out[0].text)

        assert "gen_files" in data
        assert len(data["gen_files"]) == 1
        file_path = data["gen_files"][0]
        assert file_path.endswith("main.py")
        assert "print('hello')" in open(file_path).read()

    def test_shebang_stripped_from_content(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.CODE_STORAGE_DIR", str(tmp_path))
        content = "Preamble:\n```python\n#!/> stripped.py\nvalue = 1\n```\nEpilogue\n"
        result = _make_result(content, continuation_id="myproject.L2")
        cwd = str(tmp_path / "proj")
        out = _extract_gen_files(result, "myproject.L2", cwd)
        data = json.loads(out[0].text)

        assert "#!/>" not in data["content"]
        assert "```python" in data["content"]
        assert "value = 1" in data["content"]

    def test_nested_path_creates_subdirectory(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.CODE_STORAGE_DIR", str(tmp_path))
        content = "```python\n#!/> src/utils/helpers.py\ndef helper(): pass\n```\n"
        result = _make_result(content, continuation_id="myproject.L7")
        cwd = str(tmp_path / "proj")
        out = _extract_gen_files(result, "myproject.L7", cwd)
        data = json.loads(out[0].text)

        assert len(data["gen_files"]) == 1
        file_path = data["gen_files"][0]
        assert file_path.endswith("helpers.py")
        assert "src/utils" in file_path or "src" + "/" + "utils" in file_path
        assert open(file_path).read().strip() == "def helper(): pass"

    def test_multiple_blocks_all_extracted(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.CODE_STORAGE_DIR", str(tmp_path))
        content = (
            "First file:\n```python\n#!/> alpha.py\nx = 1\n```\n"
            "Second file:\n```js\n#!/> beta.js\nconst y = 2;\n```\n"
        )
        result = _make_result(content, continuation_id="myproject.L9")
        cwd = str(tmp_path / "proj")
        out = _extract_gen_files(result, "myproject.L9", cwd)
        data = json.loads(out[0].text)

        assert len(data["gen_files"]) == 2
        names = {p.split("/")[-1] for p in data["gen_files"]}
        assert names == {"alpha.py", "beta.js"}

    def test_path_traversal_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.CODE_STORAGE_DIR", str(tmp_path))
        content = "```python\n#!/> ../../../etc/passwd\nevil\n```\n"
        result = _make_result(content, continuation_id="myproject.L1")
        cwd = str(tmp_path / "proj")
        out = _extract_gen_files(result, "myproject.L1", cwd)
        data = json.loads(out[0].text)

        assert "gen_files" not in data

    def test_absolute_path_filename_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.CODE_STORAGE_DIR", str(tmp_path))
        content = "```python\n#!/> /etc/passwd\nevil\n```\n"
        result = _make_result(content, continuation_id="myproject.L1")
        cwd = str(tmp_path / "proj")
        out = _extract_gen_files(result, "myproject.L1", cwd)
        data = json.loads(out[0].text)

        assert "gen_files" not in data

    def test_error_status_passthrough(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.CODE_STORAGE_DIR", str(tmp_path))
        content = "```python\n#!/> main.py\nprint('hi')\n```\n"
        result = _make_result(content, status="error")
        out = _extract_gen_files(result, "myproject.L1", str(tmp_path))
        assert out is result
        data = json.loads(out[0].text)
        assert "gen_files" not in data

    def test_empty_result_list_passthrough(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.CODE_STORAGE_DIR", str(tmp_path))
        out = _extract_gen_files([], None, str(tmp_path))
        assert out == []

    def test_empty_content_passthrough(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.CODE_STORAGE_DIR", str(tmp_path))
        payload = json.dumps({"status": "success", "content": ""})
        result = [TextContent(type="text", text=payload)]
        out = _extract_gen_files(result, None, str(tmp_path))
        assert out is result

    def test_non_json_result_passthrough(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.CODE_STORAGE_DIR", str(tmp_path))
        result = [TextContent(type="text", text="plain text, not json")]
        out = _extract_gen_files(result, None, str(tmp_path))
        assert out is result

    def test_body_content_preserved_exactly(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.CODE_STORAGE_DIR", str(tmp_path))
        body_lines = "line one\nline two\nline three"
        content = f"```\n#!/> exact.txt\n{body_lines}\n```\n"
        result = _make_result(content, continuation_id="myproject.L3")
        cwd = str(tmp_path / "proj")
        out = _extract_gen_files(result, "myproject.L3", cwd)
        data = json.loads(out[0].text)

        file_path = data["gen_files"][0]
        written = open(file_path).read()
        assert written.rstrip("\n") == body_lines

    def test_continuation_id_none_still_extracts(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.CODE_STORAGE_DIR", str(tmp_path))
        content = "```python\n#!/> nocontext.py\npass\n```\n"
        payload = json.dumps({"status": "success", "content": content})
        result = [TextContent(type="text", text=payload)]
        cwd = str(tmp_path / "proj")
        out = _extract_gen_files(result, None, cwd)
        data = json.loads(out[0].text)
        assert "gen_files" in data
        assert len(data["gen_files"]) == 1

    def test_uuid_continuation_id_used_as_dir_component(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.CODE_STORAGE_DIR", str(tmp_path))
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        content = "```python\n#!/> uuid_test.py\nx = 42\n```\n"
        result = _make_result(content, continuation_id=uuid)
        cwd = str(tmp_path / "proj")
        out = _extract_gen_files(result, uuid, cwd)
        data = json.loads(out[0].text)

        file_path = data["gen_files"][0]
        assert "550e8400e29b" in file_path

    def test_extra_result_items_preserved(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.CODE_STORAGE_DIR", str(tmp_path))
        content = "```python\n#!/> extra.py\npass\n```\n"
        payload = json.dumps({"status": "success", "content": content})
        second = TextContent(type="text", text="second item")
        result = [TextContent(type="text", text=payload), second]
        cwd = str(tmp_path / "proj")
        out = _extract_gen_files(result, "myproject.L1", cwd)
        assert len(out) == 2
        assert out[1] is second
