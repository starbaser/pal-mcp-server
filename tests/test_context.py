"""
Tests for context silo tools — ctxinit, ctxstore, ctxquery, ctxlist, and context_registry.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from tools.context import CtxInitTool, CtxListTool, CtxQueryTool, CtxStoreRequest, CtxStoreTool
from tools.models import ToolModelCategory


class TestCtxInitTool:
    def setup_method(self):
        self.tool = CtxInitTool()

    def test_tool_metadata(self):
        assert self.tool.get_name() == "ctxinit"
        assert "create" in self.tool.get_description().lower()
        assert self.tool.requires_model() is False
        assert self.tool.get_model_category() is ToolModelCategory.FAST_RESPONSE

    def test_schema_structure(self):
        schema = self.tool.get_input_schema()
        props = schema["properties"]
        required = schema["required"]

        assert "store_name" in props
        assert "directory" in props
        assert "store_name" in required
        assert "directory" in required

    def test_annotations_not_read_only(self):
        annotations = self.tool.get_annotations()
        assert annotations["readOnlyHint"] is False

    async def test_create_store(self):
        new_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

        with patch("utils.conversation_memory.create_thread", return_value=new_uuid):
            with patch("utils.context_registry.get_store_entry", return_value=None):
                with patch("utils.context_registry.register_store") as mock_register:
                    result = await self.tool.execute({"store_name": "myproject", "directory": "/tmp/proj"})

        assert len(result) == 1
        payload = json.loads(result[0].text)
        assert payload["status"] == "success"
        assert "myproject" in payload["content"]
        mock_register.assert_called_once_with(
            store_id="myproject",
            thread_id=new_uuid,
            directory="/tmp/proj",
            entry_type="store",
        )

    async def test_collision_same_directory(self):
        with patch("utils.context_registry.get_store_entry", return_value={"directory": "/tmp/proj"}):
            result = await self.tool.execute({"store_name": "myproject", "directory": "/tmp/proj"})

        assert len(result) == 1
        payload = json.loads(result[0].text)
        assert payload["status"] == "error"
        assert "already exists" in payload["content"].lower()

    async def test_collision_different_directory_allowed(self):
        new_uuid = "ffffffff-0000-1111-2222-333333333333"

        with patch("utils.context_registry.get_store_entry", return_value={"directory": "/other/dir"}):
            with patch("utils.conversation_memory.create_thread", return_value=new_uuid):
                with patch("utils.context_registry.register_store"):
                    result = await self.tool.execute({"store_name": "myproject", "directory": "/tmp/proj"})

        assert len(result) == 1
        payload = json.loads(result[0].text)
        assert payload["status"] == "success"


class TestCtxStoreTool:
    def setup_method(self):
        self.tool = CtxStoreTool()

    def test_tool_metadata(self):
        assert self.tool.get_name() == "ctxstore"
        assert "layer" in self.tool.get_description().lower()
        assert self.tool.get_model_category() is ToolModelCategory.EXTENDED_REASONING

    def test_schema_structure(self):
        schema = self.tool.get_input_schema()
        props = schema["properties"]
        required = schema["required"]

        assert "prompt" in required
        assert "store_id" in required
        assert "directory" not in props
        assert "context_label" in props

    def test_ephemeral_false(self):
        assert self.tool.ephemeral is False

    async def test_store_requires_store_id(self):
        result = await self.tool.execute({"prompt": "test"})

        assert len(result) == 1
        payload = json.loads(result[0].text)
        assert payload["status"] == "error"
        assert "ctxinit" in payload["content"].lower()

    async def test_store_id_not_found(self):
        with patch("utils.context_registry.resolve_thread_id", return_value=None):
            result = await self.tool.execute({"store_id": "nonexistent", "prompt": "test"})

        assert len(result) == 1
        payload = json.loads(result[0].text)
        assert payload["status"] == "error"
        assert "not found" in payload["content"].lower()

    def test_default_thinking_mode(self):
        assert self.tool.get_default_thinking_mode() == "max"

    async def test_prepare_prompt_includes_header(self):
        request = CtxStoreRequest(prompt="ignored", store_id="myproject")

        with patch.object(self.tool, "handle_prompt_file_with_fallback", return_value="test content"):
            with patch.object(self.tool, "get_request_files", return_value=[]):
                prompt = await self.tool.prepare_prompt(request)

        assert "CONTEXT LAYER SUBMISSION" in prompt
        assert "test content" in prompt


class TestCtxQueryTool:
    def setup_method(self):
        self.tool = CtxQueryTool()

    def test_tool_metadata(self):
        assert self.tool.get_name() == "ctxquery"
        assert "query" in self.tool.get_description().lower()

    def test_schema_structure(self):
        schema = self.tool.get_input_schema()
        props = schema["properties"]
        required = schema["required"]

        assert "prompt" in required
        assert "store_id" in required
        assert "directory" not in props
        assert "absolute_file_paths" not in props
        assert "context_label" not in props

    def test_ephemeral_true(self):
        assert self.tool.ephemeral is True

    async def test_query_without_store_id_returns_error(self):
        result = await self.tool.execute({"prompt": "test"})

        assert len(result) == 1
        payload = json.loads(result[0].text)
        assert payload["status"] == "error"

    async def test_query_store_id_not_found(self):
        with patch("utils.context_registry.resolve_thread_id", return_value=None):
            result = await self.tool.execute({"store_id": "missing", "prompt": "test"})

        assert len(result) == 1
        payload = json.loads(result[0].text)
        assert payload["status"] == "error"


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
            {
                "store_id": "myproject",
                "entry_type": "store",
                "label": "first",
                "layer_count": 2,
                "follow_up_count": 0,
            },
            {
                "store_id": "myproject.Q0",
                "entry_type": "query",
                "label": None,
                "layer_count": 0,
                "follow_up_count": 1,
            },
        ]

        with patch("utils.context_registry.list_stores", return_value=stores):
            result = await self.tool.execute({})

        assert len(result) == 1
        payload = json.loads(result[0].text)
        assert payload["status"] == "success"
        assert "Found 2 node(s)" in payload["content"]
        assert payload["metadata"]["store_count"] == 2


class TestContextRegistry:
    def test_register_and_get_store_entry(self, tmp_path):
        reg_path = str(tmp_path / "context" / "stores.json")

        from utils import context_registry

        context_registry._REGISTRY_PATH = reg_path

        context_registry.register_store(
            store_id="myproject",
            thread_id="uuid-1234",
            directory="/tmp/proj",
            entry_type="store",
        )
        entry = context_registry.get_store_entry("myproject")

        assert entry is not None
        assert entry["store_id"] == "myproject"
        assert entry["thread_id"] == "uuid-1234"
        assert entry["directory"] == "/tmp/proj"
        assert entry["entry_type"] == "store"
        assert entry["layer_count"] == 0
        assert entry["follow_up_count"] == 0

    def test_resolve_thread_id(self, tmp_path):
        reg_path = str(tmp_path / "context" / "stores.json")

        from utils import context_registry

        context_registry._REGISTRY_PATH = reg_path

        context_registry.register_store(
            store_id="proj",
            thread_id="known-uuid",
            directory="/tmp/proj",
        )
        result = context_registry.resolve_thread_id("proj")

        assert result == "known-uuid"

    def test_resolve_thread_id_missing(self, tmp_path):
        reg_path = str(tmp_path / "context" / "stores.json")

        from utils import context_registry

        context_registry._REGISTRY_PATH = reg_path

        result = context_registry.resolve_thread_id("does-not-exist")

        assert result is None

    def test_get_next_query_index_empty(self, tmp_path):
        reg_path = str(tmp_path / "context" / "stores.json")

        from utils import context_registry

        context_registry._REGISTRY_PATH = reg_path

        context_registry.register_store(store_id="proj", thread_id="t1", directory="/tmp")
        result = context_registry.get_next_query_index("proj")

        assert result == 0

    def test_get_next_query_index_with_children(self, tmp_path):
        reg_path = str(tmp_path / "context" / "stores.json")

        from utils import context_registry

        context_registry._REGISTRY_PATH = reg_path

        context_registry.register_store(store_id="proj.L2", thread_id="t1", directory="/tmp")
        context_registry.register_store(
            store_id="proj.L2.Q0",
            thread_id="t2",
            directory="/tmp",
            entry_type="query",
            parent_store_id="proj.L2",
        )
        context_registry.register_store(
            store_id="proj.L2.Q1",
            thread_id="t3",
            directory="/tmp",
            entry_type="query",
            parent_store_id="proj.L2",
        )
        result = context_registry.get_next_query_index("proj.L2")

        assert result == 2

    def test_increment_layer_count(self, tmp_path):
        reg_path = str(tmp_path / "context" / "stores.json")

        from utils import context_registry

        context_registry._REGISTRY_PATH = reg_path

        context_registry.register_store(store_id="myproject", thread_id="uuid-abc", directory="/tmp")
        new_path = context_registry.increment_layer_count("myproject")

        assert new_path == "myproject.L1"
        new_entry = context_registry.get_store_entry("myproject.L1")
        assert new_entry is not None
        assert new_entry["thread_id"] == "uuid-abc"

    def test_increment_layer_count_stacked(self, tmp_path):
        """Incrementing a .L1 entry produces .L2 — layer number derived from path suffix."""
        reg_path = str(tmp_path / "context" / "stores.json")

        from utils import context_registry

        context_registry._REGISTRY_PATH = reg_path

        context_registry.register_store(store_id="myproject.L1", thread_id="uuid-def", directory="/tmp")
        new_path = context_registry.increment_layer_count("myproject.L1")

        assert new_path == "myproject.L2"
        new_entry = context_registry.get_store_entry("myproject.L2")
        assert new_entry is not None
        assert new_entry["thread_id"] == "uuid-def"

    def test_increment_follow_up_count(self, tmp_path):
        reg_path = str(tmp_path / "context" / "stores.json")

        from utils import context_registry

        context_registry._REGISTRY_PATH = reg_path

        context_registry.register_store(
            store_id="proj.L2.Q0",
            thread_id="uuid-q",
            directory="/tmp",
            entry_type="query",
        )
        new_path = context_registry.increment_follow_up_count("proj.L2.Q0")

        assert new_path == "proj.L2.Q0.1"
        new_entry = context_registry.get_store_entry("proj.L2.Q0.1")
        assert new_entry is not None
        assert new_entry["thread_id"] == "uuid-q"
        assert new_entry["entry_type"] == "query"

    def test_list_stores_by_directory(self, tmp_path):
        reg_path = str(tmp_path / "context" / "stores.json")

        from utils import context_registry

        context_registry._REGISTRY_PATH = reg_path

        context_registry.register_store(store_id="alpha", thread_id="t1", directory="/proj/alpha")
        context_registry.register_store(store_id="beta", thread_id="t2", directory="/proj/beta")
        context_registry.register_store(store_id="alpha2", thread_id="t3", directory="/proj/alpha")

        alpha = context_registry.list_stores("/proj/alpha")
        beta = context_registry.list_stores("/proj/beta")

        assert len(alpha) == 2
        assert len(beta) == 1
        alpha_ids = {e["store_id"] for e in alpha}
        assert alpha_ids == {"alpha", "alpha2"}

    def test_list_stores_all(self, tmp_path):
        reg_path = str(tmp_path / "context" / "stores.json")

        from utils import context_registry

        context_registry._REGISTRY_PATH = reg_path

        context_registry.register_store(store_id="one", thread_id="t1", directory="/proj/a")
        context_registry.register_store(store_id="two", thread_id="t2", directory="/proj/b")

        all_stores = context_registry.list_stores(None)

        assert len(all_stores) == 2
        ids = {e["store_id"] for e in all_stores}
        assert ids == {"one", "two"}

    def test_registry_file_creation(self, tmp_path):
        nonexistent = str(tmp_path / "new_dir" / "context" / "stores.json")

        from utils import context_registry

        context_registry._REGISTRY_PATH = nonexistent
        result = context_registry.load_registry()

        assert result == {}


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

        assert TOOLS["ctxquery"].ephemeral is True

    def test_ctxinit_not_in_ephemeral_tools(self):
        from server import TOOLS

        assert getattr(TOOLS["ctxinit"], "ephemeral", False) is False

    def test_ctxstore_has_ephemeral_false(self):
        from server import TOOLS

        assert TOOLS["ctxstore"].ephemeral is False


class TestContextStoreIdMapping:
    """store_id to continuation_id argument mapping via registry lookup."""

    def test_store_id_resolves_to_thread_uuid(self):
        tool = CtxStoreTool()
        args = {"store_id": "myproject", "prompt": "hello"}

        with patch("utils.context_registry.resolve_thread_id", return_value="real-uuid-5678"):
            tool._map_store_id(args)

        assert args["continuation_id"] == "real-uuid-5678"

    def test_store_id_not_found_sets_flag(self):
        tool = CtxStoreTool()
        args = {"store_id": "unknown-store", "prompt": "hello"}

        with patch("utils.context_registry.resolve_thread_id", return_value=None):
            tool._map_store_id(args)

        assert args.get("_store_id_not_found") is True

    def test_no_store_id_no_mapping(self):
        tool = CtxStoreTool()
        args = {"prompt": "hello"}
        tool._map_store_id(args)

        assert "continuation_id" not in args


class TestContextForkChain:
    """Fork chain creation and parent traversal using real thread storage."""

    async def test_fork_creates_parent_chain(self):
        from utils.conversation_memory import add_turn, create_thread, get_thread_chain

        parent_id = create_thread("ctxstore", {"prompt": "parent init"}, model_name="gemini-test")
        add_turn(parent_id, "user", "Store this context")
        add_turn(parent_id, "assistant", "Context stored", model_name="gemini-test", model_provider="google")

        fork_id = create_thread("ctxquery", {"prompt": "fork start"}, parent_thread_id=parent_id)

        chain = get_thread_chain(fork_id)

        assert len(chain) == 2
        assert chain[0].thread_id == parent_id
        assert chain[1].thread_id == fork_id

    async def test_fork_does_not_modify_parent(self):
        from utils.conversation_memory import add_turn, create_thread, get_thread

        parent_id = create_thread("ctxstore", {"prompt": "parent"}, model_name="gemini-test")
        add_turn(parent_id, "user", "Initial store")
        add_turn(parent_id, "assistant", "Acknowledged", model_name="gemini-test", model_provider="google")

        parent_before = get_thread(parent_id)
        assert parent_before is not None
        parent_turn_count = len(parent_before.turns)

        fork_id = create_thread("ctxquery", {"prompt": "fork"}, parent_thread_id=parent_id)
        add_turn(fork_id, "user", "Fork query 1")
        add_turn(fork_id, "assistant", "Fork answer 1", model_name="gemini-test", model_provider="google")
        add_turn(fork_id, "user", "Fork query 2")

        parent_after = get_thread(parent_id)
        assert parent_after is not None
        assert len(parent_after.turns) == parent_turn_count

    async def test_multi_level_fork_chain(self):
        from utils.conversation_memory import add_turn, create_thread, get_thread_chain

        id_a = create_thread("ctxstore", {"prompt": "root"}, model_name="test-model")
        add_turn(id_a, "assistant", "Root context", model_name="test-model", model_provider="custom")

        id_b = create_thread("ctxquery", {"prompt": "fork-b"}, parent_thread_id=id_a)
        add_turn(id_b, "assistant", "Fork B context", model_name="test-model", model_provider="custom")

        id_c = create_thread("ctxquery", {"prompt": "fork-c"}, parent_thread_id=id_b)

        chain = get_thread_chain(id_c)

        assert len(chain) == 3
        assert chain[0].thread_id == id_a
        assert chain[1].thread_id == id_b
        assert chain[2].thread_id == id_c


class TestToolForkRegistry:
    """Registry-level tests for tool fork path creation and indexing."""

    def test_get_next_tool_index_empty(self, tmp_path):
        from utils import context_registry

        context_registry._REGISTRY_PATH = str(tmp_path / "context" / "stores.json")

        context_registry.register_store(store_id="proj", thread_id="t1", directory="/tmp")
        result = context_registry.get_next_tool_index("proj", "thinkdeep")

        assert result == 0

    def test_get_next_tool_index_with_children(self, tmp_path):
        from utils import context_registry

        context_registry._REGISTRY_PATH = str(tmp_path / "context" / "stores.json")

        context_registry.register_store(store_id="proj", thread_id="t1", directory="/tmp")
        context_registry.register_store(
            store_id="proj.thinkdeep0",
            thread_id="t2",
            directory="/tmp",
            entry_type="tool",
            parent_store_id="proj",
            tool_name="thinkdeep",
        )
        context_registry.register_store(
            store_id="proj.thinkdeep1",
            thread_id="t3",
            directory="/tmp",
            entry_type="tool",
            parent_store_id="proj",
            tool_name="thinkdeep",
        )
        result = context_registry.get_next_tool_index("proj", "thinkdeep")

        assert result == 2

    def test_get_next_tool_index_different_tools_independent(self, tmp_path):
        from utils import context_registry

        context_registry._REGISTRY_PATH = str(tmp_path / "context" / "stores.json")

        context_registry.register_store(store_id="proj", thread_id="t1", directory="/tmp")
        context_registry.register_store(
            store_id="proj.thinkdeep0",
            thread_id="t2",
            directory="/tmp",
            entry_type="tool",
            parent_store_id="proj",
            tool_name="thinkdeep",
        )
        # analyze index should be independent of thinkdeep
        result = context_registry.get_next_tool_index("proj", "analyze")

        assert result == 0

    def test_register_store_with_tool_name(self, tmp_path):
        from utils import context_registry

        context_registry._REGISTRY_PATH = str(tmp_path / "context" / "stores.json")

        context_registry.register_store(
            store_id="proj.thinkdeep0",
            thread_id="t2",
            directory="/tmp",
            entry_type="tool",
            parent_store_id="proj",
            tool_name="thinkdeep",
        )
        entry = context_registry.get_store_entry("proj.thinkdeep0")

        assert entry is not None
        assert entry["entry_type"] == "tool"
        assert entry["tool_name"] == "thinkdeep"
        assert entry["parent_store_id"] == "proj"


class TestResolveStoreContinuation:
    """Tests for _resolve_store_continuation in server.py."""

    def test_returns_none_for_uuid(self, tmp_path):
        from server import _resolve_store_continuation
        from utils import context_registry

        context_registry._REGISTRY_PATH = str(tmp_path / "context" / "stores.json")

        args = {"continuation_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}
        result = _resolve_store_continuation("thinkdeep", args)

        assert result is None
        # continuation_id unchanged
        assert args["continuation_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def test_fork_from_store(self, tmp_path):
        from server import _resolve_store_continuation
        from utils import context_registry
        from utils.conversation_memory import create_thread

        context_registry._REGISTRY_PATH = str(tmp_path / "context" / "stores.json")

        parent_uuid = create_thread("ctxstore", {"prompt": "init"})
        context_registry.register_store(
            store_id="myproject", thread_id=parent_uuid, directory="/tmp/proj"
        )

        args = {"continuation_id": "myproject"}
        result = _resolve_store_continuation("thinkdeep", args)

        assert result == "myproject.thinkdeep0"
        # continuation_id should now be the new thread UUID, not "myproject"
        assert args["continuation_id"] != "myproject"
        assert args["continuation_id"] != parent_uuid

        # Verify registry entry was created
        entry = context_registry.get_store_entry("myproject.thinkdeep0")
        assert entry is not None
        assert entry["entry_type"] == "tool"
        assert entry["tool_name"] == "thinkdeep"
        assert entry["parent_store_id"] == "myproject"

    def test_continue_same_tool(self, tmp_path):
        from server import _resolve_store_continuation
        from utils import context_registry
        from utils.conversation_memory import create_thread

        context_registry._REGISTRY_PATH = str(tmp_path / "context" / "stores.json")

        parent_uuid = create_thread("ctxstore", {"prompt": "init"})
        fork_uuid = create_thread("thinkdeep", {"store_fork": "myproject"}, parent_thread_id=parent_uuid)

        context_registry.register_store(
            store_id="myproject", thread_id=parent_uuid, directory="/tmp/proj"
        )
        context_registry.register_store(
            store_id="myproject.thinkdeep0",
            thread_id=fork_uuid,
            directory="/tmp/proj",
            entry_type="tool",
            parent_store_id="myproject",
            tool_name="thinkdeep",
        )

        args = {"continuation_id": "myproject.thinkdeep0"}
        result = _resolve_store_continuation("thinkdeep", args)

        assert result == "myproject.thinkdeep0"
        assert args["continuation_id"] == fork_uuid

        # follow_up_count should have incremented
        entry = context_registry.get_store_entry("myproject.thinkdeep0")
        assert entry["follow_up_count"] == 1

    def test_refork_different_tool(self, tmp_path):
        from server import _resolve_store_continuation
        from utils import context_registry
        from utils.conversation_memory import create_thread

        context_registry._REGISTRY_PATH = str(tmp_path / "context" / "stores.json")

        parent_uuid = create_thread("ctxstore", {"prompt": "init"})
        fork_uuid = create_thread("thinkdeep", {"store_fork": "myproject"}, parent_thread_id=parent_uuid)

        context_registry.register_store(
            store_id="myproject", thread_id=parent_uuid, directory="/tmp/proj"
        )
        context_registry.register_store(
            store_id="myproject.thinkdeep0",
            thread_id=fork_uuid,
            directory="/tmp/proj",
            entry_type="tool",
            parent_store_id="myproject",
            tool_name="thinkdeep",
        )

        args = {"continuation_id": "myproject.thinkdeep0"}
        result = _resolve_store_continuation("analyze", args)

        assert result == "myproject.thinkdeep0.analyze0"
        # Should be a new UUID, not the thinkdeep fork's UUID
        assert args["continuation_id"] != fork_uuid
        assert args["continuation_id"] != parent_uuid

        entry = context_registry.get_store_entry("myproject.thinkdeep0.analyze0")
        assert entry is not None
        assert entry["entry_type"] == "tool"
        assert entry["tool_name"] == "analyze"
        assert entry["parent_store_id"] == "myproject.thinkdeep0"

    def test_fork_from_query(self, tmp_path):
        from server import _resolve_store_continuation
        from utils import context_registry
        from utils.conversation_memory import create_thread

        context_registry._REGISTRY_PATH = str(tmp_path / "context" / "stores.json")

        store_uuid = create_thread("ctxstore", {"prompt": "init"})
        query_uuid = create_thread("ctxquery", {"prompt": "ask"}, parent_thread_id=store_uuid)

        context_registry.register_store(
            store_id="myproject", thread_id=store_uuid, directory="/tmp/proj"
        )
        context_registry.register_store(
            store_id="myproject.Q0",
            thread_id=query_uuid,
            directory="/tmp/proj",
            entry_type="query",
            parent_store_id="myproject",
        )

        args = {"continuation_id": "myproject.Q0"}
        result = _resolve_store_continuation("thinkdeep", args)

        assert result == "myproject.Q0.thinkdeep0"

    def test_fork_from_layer(self, tmp_path):
        from server import _resolve_store_continuation
        from utils import context_registry
        from utils.conversation_memory import create_thread

        context_registry._REGISTRY_PATH = str(tmp_path / "context" / "stores.json")

        store_uuid = create_thread("ctxstore", {"prompt": "init"})

        context_registry.register_store(
            store_id="myproject", thread_id=store_uuid, directory="/tmp/proj"
        )
        context_registry.register_store(
            store_id="myproject.L1",
            thread_id=store_uuid,
            directory="/tmp/proj",
            entry_type="store",
            parent_store_id="myproject",
        )

        args = {"continuation_id": "myproject.L1"}
        result = _resolve_store_continuation("chat", args)

        assert result == "myproject.L1.chat0"

    def test_fork_index_increments(self, tmp_path):
        from server import _resolve_store_continuation
        from utils import context_registry
        from utils.conversation_memory import create_thread

        context_registry._REGISTRY_PATH = str(tmp_path / "context" / "stores.json")

        parent_uuid = create_thread("ctxstore", {"prompt": "init"})
        context_registry.register_store(
            store_id="myproject", thread_id=parent_uuid, directory="/tmp/proj"
        )

        # First fork
        args1 = {"continuation_id": "myproject"}
        result1 = _resolve_store_continuation("thinkdeep", args1)
        assert result1 == "myproject.thinkdeep0"

        # Second fork (new invocation from same store)
        args2 = {"continuation_id": "myproject"}
        result2 = _resolve_store_continuation("thinkdeep", args2)
        assert result2 == "myproject.thinkdeep1"


class TestInjectStorePathContinuation:
    """Tests for _inject_store_path_continuation response post-processing."""

    def test_uuid_replaced_in_response(self):
        from mcp.types import TextContent

        from server import _inject_store_path_continuation

        response_json = json.dumps({
            "status": "continuation_available",
            "content": "Analysis complete.",
            "continuation_offer": {
                "continuation_id": "some-uuid-value",
                "note": "Conversation active.",
                "context_window": 100000,
                "context_used": 5000,
                "context_remaining": 95000,
            },
        })
        items = [TextContent(type="text", text=response_json)]

        result = _inject_store_path_continuation(items, "myproject.thinkdeep0")

        parsed = json.loads(result[0].text)
        assert parsed["continuation_offer"]["continuation_id"] == "myproject.thinkdeep0"
        assert parsed["content"] == "Analysis complete."

    def test_non_json_passthrough(self):
        from mcp.types import TextContent

        from server import _inject_store_path_continuation

        items = [TextContent(type="text", text="not json")]
        result = _inject_store_path_continuation(items, "myproject.thinkdeep0")

        assert result[0].text == "not json"

    def test_no_continuation_offer_passthrough(self):
        from mcp.types import TextContent

        from server import _inject_store_path_continuation

        response_json = json.dumps({"status": "success", "content": "Done."})
        items = [TextContent(type="text", text=response_json)]

        result = _inject_store_path_continuation(items, "myproject.thinkdeep0")

        parsed = json.loads(result[0].text)
        assert "continuation_offer" not in parsed or parsed.get("continuation_offer") is None


if __name__ == "__main__":
    pytest.main([__file__])
