"""Tests for CLI session resume: verify no redundant history is sent when --resume is active.

When a CLI session is resumed via session_id, the CLI already has all prior
conversation turns. PAL must only send the new user message — no conversation
history reconstruction, no file re-embedding.
"""

from unittest.mock import MagicMock, patch

import pytest

from providers.clink_provider import ClinkProvider, _escape_at_directives
from utils.conversation_memory import add_turn, create_thread, set_thread_session_id
from utils.storage_backend import get_storage_backend


@pytest.fixture(autouse=True)
def _clean_storage():
    """Clear storage before/after each test."""
    storage = get_storage_backend()
    cache = getattr(storage, "_cache", None) or getattr(storage, "_store", None)
    if cache is not None:
        cache.clear()
    yield
    if cache is not None:
        cache.clear()


class TestSimpleToolClinkSessionResume:
    """SimpleTool must skip history reconstruction when a clink session is active."""

    def _make_tool_and_context(self, *, is_clink: bool, session_id: str | None = None):
        """Set up a ChatTool with appropriate model context."""
        from tools.chat import ChatTool

        tool = ChatTool()
        model_context = MagicMock()
        if is_clink:
            model_context.provider = MagicMock(spec=ClinkProvider)
            model_context.provider.get_provider_type.return_value = MagicMock(value="clink")
            model_context.model_name = "gemini-cli-3.1-pro-preview"
        else:
            model_context.provider = MagicMock()
            model_context.provider.get_provider_type.return_value = MagicMock(value="google")
            model_context.model_name = "gemini-2.5-flash"
        model_context.capabilities = MagicMock()
        model_context.capabilities.context_window = 1_048_576
        model_context.capabilities.max_output_tokens = 16_384
        model_context.calculate_token_allocation.return_value = MagicMock(
            file_tokens=100_000, history_tokens=100_000, prompt_tokens=100_000,
        )
        tool._model_context = model_context
        tool._current_model_name = model_context.model_name
        args = {"_model_context": model_context, "_resolved_model_name": model_context.model_name}
        if session_id:
            args["_clink_session_id"] = session_id
        tool._current_arguments = args
        return tool

    @pytest.mark.asyncio
    async def test_clink_session_skips_build_conversation_history(self):
        """When _clink_session_id is present, build_conversation_history must NOT be called."""
        thread_id = create_thread("chat", {"prompt": "hello"}, model_name="gemini-cli-3.1-pro-preview")
        add_turn(thread_id, "assistant", "prior response " * 500, tool_name="chat")
        set_thread_session_id(thread_id, "gemini-session-123")

        tool = self._make_tool_and_context(is_clink=True, session_id="gemini-session-123")
        captured_prompt = {}

        def capture_generate(prompt, **kwargs):
            captured_prompt["value"] = prompt
            resp = MagicMock()
            resp.content = "reply"
            resp.usage = {}
            resp.session_id = "gemini-session-123"
            resp.model_name = "gemini-cli-3.1-pro-preview"
            resp.metadata = {}
            return resp

        tool._current_arguments["_model_context"].provider.generate_content = capture_generate

        with patch("utils.conversation_memory.build_conversation_history") as mock_build:
            mock_build.return_value = ("HUGE HISTORY SHOULD NOT BE USED " * 1000, 50000)

            await tool.execute({
                "prompt": "new message only",
                "model": "gemini-cli-3.1-pro-preview",
                "continuation_id": thread_id,
                "working_directory_absolute_path": "/tmp",
                "_clink_session_id": "gemini-session-123",
                "_model_context": tool._current_arguments["_model_context"],
                "_resolved_model_name": "gemini-cli-3.1-pro-preview",
            })

            mock_build.assert_not_called()
            assert "HUGE HISTORY" not in captured_prompt.get("value", "")

    @pytest.mark.asyncio
    async def test_clink_session_still_records_user_turn(self):
        """Even when skipping history, the user's new message should be recorded in the thread."""
        thread_id = create_thread("chat", {"prompt": "hello"}, model_name="gemini-cli-3.1-pro-preview")
        set_thread_session_id(thread_id, "session-abc")

        from utils.conversation_memory import get_thread

        initial_turns = len(get_thread(thread_id).turns)

        tool = self._make_tool_and_context(is_clink=True, session_id="session-abc")

        def mock_generate(prompt, **kwargs):
            resp = MagicMock()
            resp.content = "reply"
            resp.usage = {}
            resp.session_id = "session-abc"
            resp.model_name = "gemini-cli-3.1-pro-preview"
            resp.metadata = {}
            return resp

        tool._current_arguments["_model_context"].provider.generate_content = mock_generate

        await tool.execute({
            "prompt": "new turn content",
            "model": "gemini-cli-3.1-pro-preview",
            "continuation_id": thread_id,
            "working_directory_absolute_path": "/tmp",
            "_clink_session_id": "session-abc",
            "_model_context": tool._current_arguments["_model_context"],
            "_resolved_model_name": "gemini-cli-3.1-pro-preview",
        })

        thread = get_thread(thread_id)
        # Should have initial turns + user turn + assistant turn
        assert len(thread.turns) > initial_turns
        user_turns = [t for t in thread.turns if t.role == "user"]
        assert len(user_turns) >= 1


class TestWorkflowToolClinkSessionResume:
    """WorkflowTool must skip file re-embedding when a clink session is active."""

    @pytest.mark.asyncio
    async def test_clink_session_skips_file_embedding(self):
        from tools.analyze import AnalyzeTool

        tool = AnalyzeTool()
        model_context = MagicMock()
        model_context.provider = MagicMock(spec=ClinkProvider)
        model_context.model_name = "gemini-cli-3.1-pro-preview"
        model_context.capabilities = MagicMock()
        tool._model_context = model_context
        tool._current_model_name = "gemini-cli-3.1-pro-preview"

        tool.consolidated_findings.relevant_files = ["/home/test/big_file.py"]
        tool.consolidated_findings.findings = ["Finding 1"]
        tool.consolidated_findings.media = []
        tool.work_history = [{"step": "test", "step_number": 1, "findings": "test"}]

        mock_resp = MagicMock()
        mock_resp.content = '{"status": "analysis_complete"}'
        mock_resp.usage = {}
        mock_resp.session_id = "test-session"
        mock_resp.model_name = "gemini-cli-3.1-pro-preview"
        mock_resp.metadata = {}
        model_context.provider.generate_content = MagicMock(return_value=mock_resp)

        with patch.object(tool, "_prepare_files_for_expert_analysis") as mock_files:
            mock_files.return_value = "EMBEDDED FILES SHOULD NOT APPEAR"

            await tool._call_expert_analysis(
                {"_clink_session_id": "test-session-xyz"},
                MagicMock(thinking_mode=None, temperature=None),
            )

            mock_files.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_clink_session_embeds_files(self):
        from tools.analyze import AnalyzeTool

        tool = AnalyzeTool()
        model_context = MagicMock()
        model_context.provider = MagicMock()
        model_context.model_name = "gemini-2.5-flash"
        model_context.capabilities = MagicMock()
        tool._model_context = model_context
        tool._current_model_name = "gemini-2.5-flash"

        tool.consolidated_findings.relevant_files = ["/home/test/file.py"]
        tool.consolidated_findings.findings = ["Finding"]
        tool.consolidated_findings.media = []
        tool.work_history = [{"step": "test", "step_number": 1, "findings": "test"}]

        mock_resp = MagicMock()
        mock_resp.content = '{"status": "analysis_complete"}'
        mock_resp.usage = {}
        mock_resp.session_id = None
        mock_resp.model_name = "gemini-2.5-flash"
        mock_resp.metadata = {}
        model_context.provider.generate_content = MagicMock(return_value=mock_resp)

        with patch.object(tool, "_prepare_files_for_expert_analysis") as mock_files:
            mock_files.return_value = "file content"

            await tool._call_expert_analysis(
                {},
                MagicMock(thinking_mode=None, temperature=None),
            )

            mock_files.assert_called_once()


class TestAtDirectiveEscaping:
    """Verify @path patterns are escaped before sending to CLI runners."""

    def test_escapes_relative_at_path(self):
        result = _escape_at_directives("@.kitstore/foo/bar.rs")
        assert result.startswith("\u200b@")

    def test_escapes_absolute_at_path(self):
        result = _escape_at_directives("@/home/user/file.txt")
        assert result.startswith("\u200b@")

    def test_escapes_tilde_at_path(self):
        result = _escape_at_directives("@~/.config/foo")
        assert result.startswith("\u200b@")

    def test_escapes_directory_at_path(self):
        result = _escape_at_directives("from @apidevtools/parser")
        assert "\u200b@apidevtools" in result

    def test_preserves_email_addresses(self):
        assert _escape_at_directives("email@example.com") == "email@example.com"

    def test_preserves_plain_text(self):
        assert _escape_at_directives("no at signs here") == "no at signs here"

    def test_preserves_at_without_path(self):
        assert _escape_at_directives("@Dockerfile") == "@Dockerfile"
