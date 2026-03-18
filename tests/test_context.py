"""
Tests for context silo tools — ctxstore, ctxquery, ctxfork, ctxlist, and context_registry.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from tools.context import CtxForkTool, CtxListTool, CtxQueryTool, CtxStoreRequest, CtxStoreTool
from tools.models import ToolModelCategory


class TestCtxStoreTool:
    def setup_method(self):
        self.tool = CtxStoreTool()

    def test_tool_metadata(self):
        assert self.tool.get_name() == "ctxstore"
        assert "store" in self.tool.get_description().lower()
        assert self.tool.get_system_prompt()
        assert self.tool.get_default_temperature() > 0
        assert self.tool.get_model_category() is ToolModelCategory.EXTENDED_REASONING

    def test_schema_structure(self):
        schema = self.tool.get_input_schema()
        props = schema["properties"]

        assert "prompt" in props
        assert "store_id" in props
        assert "directory" in props
        assert "context_label" in props
        assert "absolute_file_paths" in props
        assert "media" in props
        assert "model" in props
        assert "temperature" in props

        assert "thinking_mode" not in props
        assert "prompt" in schema["required"]

    def test_request_model_validation(self):
        req = CtxStoreRequest(prompt="store this")
        assert req.prompt == "store this"

        with pytest.raises(ValidationError):
            CtxStoreRequest()

    def test_default_thinking_mode(self):
        assert self.tool.get_default_thinking_mode() == "max"

    def test_ephemeral_continuation_false(self):
        assert self.tool.ephemeral_continuation is False

    def test_annotations(self):
        annotations = self.tool.get_annotations()
        assert annotations["readOnlyHint"] is False

    async def test_first_store_requires_directory(self):
        result = await self.tool.execute({"prompt": "test content"})
        assert len(result) == 1
        payload = json.loads(result[0].text)
        assert payload["status"] == "error"
        assert "directory" in payload["content"].lower()

    async def test_prepare_prompt_includes_header(self):
        request = CtxStoreRequest(prompt="ignored", store_id="abc-123")

        with patch.object(self.tool, "handle_prompt_file_with_fallback", return_value="test content"):
            with patch.object(self.tool, "get_request_files", return_value=[]):
                prompt = await self.tool.prepare_prompt(request)

        assert "CONTEXT LAYER SUBMISSION" in prompt
        assert "test content" in prompt

    def test_format_response_appends_agent_turn(self):
        request = CtxStoreRequest(prompt="x", store_id="abc")
        result = self.tool.format_response("model answer", request)
        assert "model answer" in result
        assert "AGENT'S TURN:" in result

    def test_record_assistant_turn_injects_label(self):
        request = CtxStoreRequest(prompt="x", store_id="abc-123", context_label="my-label")

        with patch("utils.conversation_memory.add_turn") as mock_add_turn:
            self.tool._record_assistant_turn("abc-123", "response text", request, model_info=None)

        mock_add_turn.assert_called_once()
        call_kwargs = mock_add_turn.call_args.kwargs
        metadata = call_kwargs.get("model_metadata") or {}
        assert metadata.get("context_label") == "my-label"


class TestCtxQueryTool:
    def setup_method(self):
        self.tool = CtxQueryTool()

    def test_tool_metadata(self):
        assert self.tool.get_name() == "ctxquery"
        desc = self.tool.get_description().lower()
        assert "ephemeral" in desc or "read-only" in desc

    def test_schema_structure(self):
        schema = self.tool.get_input_schema()
        props = schema["properties"]
        required = schema["required"]

        assert "prompt" in required
        assert "store_id" in required
        assert "directory" not in props
        assert "absolute_file_paths" not in props
        assert "context_label" not in props

    def test_ephemeral_continuation_true(self):
        assert self.tool.ephemeral_continuation is True

    async def test_query_without_store_id_returns_error(self):
        result = await self.tool.execute({"prompt": "what is stored?"})
        assert len(result) == 1
        payload = json.loads(result[0].text)
        assert payload["status"] == "error"
        assert "store_id" in payload["content"].lower()

    def test_record_assistant_turn_is_noop(self):
        with patch("utils.conversation_memory.add_turn") as mock_add_turn:
            self.tool._record_assistant_turn("abc-123", "response", MagicMock(), model_info=None)
        mock_add_turn.assert_not_called()

    def test_create_continuation_offer_preserves_store_id(self):
        request = MagicMock()
        request.continuation_id = "silo-xyz-789"

        with patch.object(self.tool, "get_request_continuation_id", return_value="silo-xyz-789"):
            with patch.object(self.tool, "_get_context_token_info", return_value=(100000, 1000)):
                result = self.tool._create_continuation_offer(request)

        assert result is not None
        assert result["continuation_id"] == "silo-xyz-789"


class TestCtxForkTool:
    def setup_method(self):
        self.tool = CtxForkTool()

    def test_tool_metadata(self):
        assert self.tool.get_name() == "ctxfork"
        assert "fork" in self.tool.get_description().lower()

    def test_schema_structure(self):
        schema = self.tool.get_input_schema()
        required = schema["required"]

        assert "prompt" in required
        assert "store_id" in required

    def test_ephemeral_continuation_true(self):
        assert self.tool.ephemeral_continuation is True

    async def test_fork_without_store_id_returns_error(self):
        result = await self.tool.execute({"prompt": "branch this"})
        assert len(result) == 1
        payload = json.loads(result[0].text)
        assert payload["status"] == "error"
        assert "store_id" in payload["content"].lower()


class TestCtxListTool:
    def setup_method(self):
        self.tool = CtxListTool()

    def test_tool_metadata(self):
        assert self.tool.get_name() == "ctxlist"

    def test_requires_model_false(self):
        assert self.tool.requires_model() is False

    def test_schema_structure(self):
        schema = self.tool.get_input_schema()
        assert "directory" in schema["properties"]
        assert schema.get("required", []) == []

    def test_annotations_read_only(self):
        annotations = self.tool.get_annotations()
        assert annotations["readOnlyHint"] is True

    async def test_execute_empty_registry(self):
        with patch("utils.context_registry.list_stores", return_value=[]):
            result = await self.tool.execute({})

        assert len(result) == 1
        payload = json.loads(result[0].text)
        assert payload["status"] == "success"
        assert "No context stores found" in payload["content"]

    async def test_execute_with_stores(self):
        stores = [
            {"store_id": "abc-1", "label": "first", "model": "gpt-4", "turn_count": 3, "created_at": "2026-01-01"},
            {"store_id": "abc-2", "label": None, "model": "gemini-pro", "turn_count": 1, "created_at": "2026-01-02"},
        ]

        with patch("utils.context_registry.list_stores", return_value=stores):
            result = await self.tool.execute({})

        assert len(result) == 1
        payload = json.loads(result[0].text)
        assert payload["status"] == "success"
        assert "Found 2 store(s)" in payload["content"]
        assert payload["metadata"]["store_count"] == 2


class TestContextRegistry:
    def test_register_store(self, tmp_path):
        with patch("utils.context_registry._REGISTRY_PATH", str(tmp_path / "context" / "stores.json")):
            from utils import context_registry

            context_registry._REGISTRY_PATH = str(tmp_path / "context" / "stores.json")

            context_registry.register_store("/proj/alpha", "store-001", "alpha label", "gemini-pro")
            registry = context_registry.load_registry()

        assert "/proj/alpha" in registry
        entries = registry["/proj/alpha"]
        assert len(entries) == 1
        assert entries[0]["store_id"] == "store-001"
        assert entries[0]["label"] == "alpha label"

    def test_list_stores_by_directory(self, tmp_path):
        reg_path = str(tmp_path / "context" / "stores.json")

        with patch("utils.context_registry._REGISTRY_PATH", reg_path):
            from utils import context_registry

            context_registry._REGISTRY_PATH = reg_path

            context_registry.register_store("/proj/alpha", "store-a1", None, "gpt-4")
            context_registry.register_store("/proj/beta", "store-b1", None, "gpt-4")
            context_registry.register_store("/proj/alpha", "store-a2", None, "gpt-4")

            alpha_stores = context_registry.list_stores("/proj/alpha")
            beta_stores = context_registry.list_stores("/proj/beta")

        assert len(alpha_stores) == 2
        assert len(beta_stores) == 1
        assert alpha_stores[0]["store_id"] == "store-a1"
        assert beta_stores[0]["store_id"] == "store-b1"

    def test_list_stores_all(self, tmp_path):
        reg_path = str(tmp_path / "context" / "stores.json")

        with patch("utils.context_registry._REGISTRY_PATH", reg_path):
            from utils import context_registry

            context_registry._REGISTRY_PATH = reg_path

            context_registry.register_store("/proj/alpha", "store-a1", None, "gpt-4")
            context_registry.register_store("/proj/beta", "store-b1", None, "gpt-4")

            all_stores = context_registry.list_stores(None)

        assert len(all_stores) == 2
        ids = {s["store_id"] for s in all_stores}
        assert ids == {"store-a1", "store-b1"}

    def test_update_turn_count(self, tmp_path):
        reg_path = str(tmp_path / "context" / "stores.json")

        with patch("utils.context_registry._REGISTRY_PATH", reg_path):
            from utils import context_registry

            context_registry._REGISTRY_PATH = reg_path

            context_registry.register_store("/proj/alpha", "store-tc", None, "gpt-4")
            context_registry.update_store_turn_count("store-tc")
            context_registry.update_store_turn_count("store-tc")

            registry = context_registry.load_registry()

        entry = registry["/proj/alpha"][0]
        assert entry["turn_count"] == 2

    def test_get_store_directory(self, tmp_path):
        reg_path = str(tmp_path / "context" / "stores.json")

        with patch("utils.context_registry._REGISTRY_PATH", reg_path):
            from utils import context_registry

            context_registry._REGISTRY_PATH = reg_path

            context_registry.register_store("/proj/gamma", "store-dir", None, "gpt-4")
            directory = context_registry.get_store_directory("store-dir")
            missing = context_registry.get_store_directory("nonexistent")

        assert directory == "/proj/gamma"
        assert missing is None

    def test_registry_file_creation(self, tmp_path):
        nonexistent = str(tmp_path / "new_dir" / "context" / "stores.json")

        with patch("utils.context_registry._REGISTRY_PATH", nonexistent):
            from utils import context_registry

            context_registry._REGISTRY_PATH = nonexistent
            result = context_registry.load_registry()

        assert result == {}


if __name__ == "__main__":
    pytest.main([__file__])


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestContextEphemeralGuard:
    """Server-level ephemeral guard injection and reconstruct_thread_context skip."""

    def _mock_token_allocation(self):
        return MagicMock(
            total_tokens=200000,
            content_tokens=160000,
            response_tokens=40000,
            file_tokens=64000,
            history_tokens=64000,
        )

    async def test_ephemeral_skips_user_turn(self):
        """Ephemeral query must not record a new user turn in the thread."""
        from server import reconstruct_thread_context
        from utils.conversation_memory import add_turn, create_thread, get_thread

        thread_id = create_thread("ctxstore", {"prompt": "init"}, model_name="test-model")
        add_turn(thread_id, "assistant", "Stored initial context", model_name="test-model", model_provider="custom")

        arguments = {
            "continuation_id": thread_id,
            "prompt": "new query",
            "_ephemeral_query": True,
            "model": "test-model",
        }

        with patch("utils.model_context.ModelContext.calculate_token_allocation") as mock_calc:
            mock_calc.return_value = self._mock_token_allocation()
            with patch("utils.conversation_memory.build_conversation_history") as mock_build:
                mock_build.return_value = ("=== CONVERSATION HISTORY ===\nTest history", 1000)
                await reconstruct_thread_context(arguments)

        thread = get_thread(thread_id)
        assert thread is not None
        assert len(thread.turns) == 1

    async def test_non_ephemeral_records_user_turn(self):
        """Non-ephemeral continuation must add the new user prompt as a turn."""
        from server import reconstruct_thread_context
        from utils.conversation_memory import add_turn, create_thread, get_thread

        thread_id = create_thread("ctxstore", {"prompt": "init"}, model_name="test-model")
        add_turn(thread_id, "assistant", "Stored initial context", model_name="test-model", model_provider="custom")

        arguments = {
            "continuation_id": thread_id,
            "prompt": "follow-up query",
            "model": "test-model",
        }

        with patch("utils.model_context.ModelContext.calculate_token_allocation") as mock_calc:
            mock_calc.return_value = self._mock_token_allocation()
            with patch("utils.conversation_memory.build_conversation_history") as mock_build:
                mock_build.return_value = ("=== CONVERSATION HISTORY ===\nTest history", 1000)
                await reconstruct_thread_context(arguments)

        thread = get_thread(thread_id)
        assert thread is not None
        assert len(thread.turns) == 2
        assert thread.turns[1].role == "user"
        assert thread.turns[1].content == "follow-up query"

    def test_ctxquery_has_ephemeral_true(self):
        from server import TOOLS

        assert TOOLS["ctxquery"].ephemeral_continuation is True

    def test_ctxfork_has_ephemeral_true(self):
        from server import TOOLS

        assert TOOLS["ctxfork"].ephemeral_continuation is True

    def test_ctxstore_has_ephemeral_false(self):
        from server import TOOLS

        assert TOOLS["ctxstore"].ephemeral_continuation is False


class TestContextForkChain:
    """Fork chain creation and parent traversal using real thread storage."""

    async def test_fork_creates_parent_chain(self):
        """Fork thread must link to parent, and get_thread_chain returns both in order."""
        from utils.conversation_memory import add_turn, create_thread, get_thread_chain

        parent_id = create_thread("ctxstore", {"prompt": "parent init"}, model_name="gemini-test")
        add_turn(parent_id, "user", "Store this context")
        add_turn(parent_id, "assistant", "Context stored", model_name="gemini-test", model_provider="google")

        fork_id = create_thread("ctxfork", {"prompt": "fork start"}, parent_thread_id=parent_id)

        chain = get_thread_chain(fork_id)

        assert len(chain) == 2
        assert chain[0].thread_id == parent_id
        assert chain[1].thread_id == fork_id

    async def test_fork_does_not_modify_parent(self):
        """Adding turns to a fork must not affect the parent thread's turn count."""
        from utils.conversation_memory import add_turn, create_thread, get_thread

        parent_id = create_thread("ctxstore", {"prompt": "parent"}, model_name="gemini-test")
        add_turn(parent_id, "user", "Initial store")
        add_turn(parent_id, "assistant", "Acknowledged", model_name="gemini-test", model_provider="google")

        parent_before = get_thread(parent_id)
        assert parent_before is not None
        parent_turn_count = len(parent_before.turns)

        fork_id = create_thread("ctxfork", {"prompt": "fork"}, parent_thread_id=parent_id)
        add_turn(fork_id, "user", "Fork query 1")
        add_turn(fork_id, "assistant", "Fork answer 1", model_name="gemini-test", model_provider="google")
        add_turn(fork_id, "user", "Fork query 2")

        parent_after = get_thread(parent_id)
        assert parent_after is not None
        assert len(parent_after.turns) == parent_turn_count

    async def test_multi_level_fork_chain(self):
        """Three-level fork chain must be returned in chronological order A, B, C."""
        from utils.conversation_memory import add_turn, create_thread, get_thread_chain

        id_a = create_thread("ctxstore", {"prompt": "root"}, model_name="test-model")
        add_turn(id_a, "assistant", "Root context", model_name="test-model", model_provider="custom")

        id_b = create_thread("ctxfork", {"prompt": "fork-b"}, parent_thread_id=id_a)
        add_turn(id_b, "assistant", "Fork B context", model_name="test-model", model_provider="custom")

        id_c = create_thread("ctxfork", {"prompt": "fork-c"}, parent_thread_id=id_b)

        chain = get_thread_chain(id_c)

        assert len(chain) == 3
        assert chain[0].thread_id == id_a
        assert chain[1].thread_id == id_b
        assert chain[2].thread_id == id_c


class TestContextStoreIdMapping:
    """store_id to continuation_id argument mapping."""

    def test_store_id_maps_to_continuation_id(self):
        tool = CtxStoreTool()
        args = {"store_id": "test-uuid", "prompt": "hello"}
        tool._map_store_id(args)
        assert args["continuation_id"] == "test-uuid"

    def test_no_store_id_no_mapping(self):
        tool = CtxStoreTool()
        args = {"prompt": "hello"}
        tool._map_store_id(args)
        assert "continuation_id" not in args


class TestContextRegistryIntegration:
    """Registry integration with _create_continuation_offer overrides."""

    def test_store_registers_via_continuation_offer(self):
        """First ctxstore call must register the new store_id in the registry."""
        from tools.simple.base import SimpleTool

        tool = CtxStoreTool()
        tool._is_first_store = True
        tool._pending_directory = "/tmp/test-project"
        tool._pending_label = "test-label"
        tool._current_arguments = {"_resolved_model_name": "gemini-test"}

        mock_request = MagicMock()

        with patch.object(
            SimpleTool,
            "_create_continuation_offer",
            return_value={
                "continuation_id": "new-uuid-123",
                "context_window": 100000,
                "context_used": 5000,
                "note": "test",
            },
        ):
            with patch("utils.context_registry.register_store") as mock_register:
                tool._create_continuation_offer(mock_request, None)
                mock_register.assert_called_once_with("/tmp/test-project", "new-uuid-123", "test-label", "gemini-test")

    def test_store_updates_turn_count_on_continuation(self):
        """Subsequent ctxstore calls must increment the store's turn count."""
        from tools.simple.base import SimpleTool

        tool = CtxStoreTool()
        tool._is_first_store = False
        tool._pending_directory = None
        tool._pending_label = None
        tool._current_arguments = {}

        mock_request = MagicMock()

        with patch.object(
            SimpleTool,
            "_create_continuation_offer",
            return_value={
                "continuation_id": "existing-uuid-456",
                "context_window": 100000,
                "context_used": 8000,
                "note": "continued",
            },
        ):
            with patch("utils.context_registry.update_store_turn_count") as mock_update:
                tool._create_continuation_offer(mock_request, None)
                mock_update.assert_called_once_with("existing-uuid-456")

    async def test_ctxlist_with_real_registry(self, tmp_path):
        """CtxListTool.execute must reflect stores registered in a real registry file."""
        from utils import context_registry

        reg_path = str(tmp_path / "context" / "stores.json")
        context_registry._REGISTRY_PATH = reg_path

        try:
            context_registry.register_store("/tmp/project1", "store-aaa", "label-a", "gemini-pro")
            context_registry.register_store("/tmp/project1", "store-bbb", None, "gpt-4o")
            context_registry.register_store("/tmp/project2", "store-ccc", "label-c", "claude-3")

            with patch("utils.context_registry._REGISTRY_PATH", reg_path):
                result = await CtxListTool().execute({"directory": "/tmp/project1"})

            assert len(result) == 1
            payload = json.loads(result[0].text)
            assert payload["status"] == "success"
            assert payload["metadata"]["store_count"] == 2
        finally:
            context_registry._REGISTRY_PATH = __import__("os").path.join(
                __import__("config").PAL_STORAGE_DIR, "context", "stores.json"
            )
