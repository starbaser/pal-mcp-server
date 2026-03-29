"""
Tests for palstore tools — palinit, palstore, palquery, pallist.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from tools.models import ToolModelCategory
from tools.palstore import PalInitTool, PalListTool, PalQueryTool, PalStoreRequest, PalStoreTool


class TestPalInitTool:
    def setup_method(self):
        self.tool = PalInitTool()

    def test_tool_metadata(self):
        assert self.tool.get_name() == "palinit"
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
        with patch("utils.palstore.resolve_store_location", return_value=None):
            with patch("utils.palstore.save_store") as mock_save:
                with patch("utils.palstore.update_index") as mock_update:
                    result = await self.tool.execute({"store_name": "myproject", "directory": "/tmp/proj"})

        assert len(result) == 1
        payload = json.loads(result[0].text)
        assert payload["status"] == "success"
        assert "myproject" in payload["content"]
        mock_save.assert_called_once()
        mock_update.assert_called_once_with("/tmp/proj", "myproject")

    async def test_collision_existing_store(self):
        with patch("utils.palstore.resolve_store_location", return_value=("/tmp/proj", "myproject")):
            result = await self.tool.execute({"store_name": "myproject", "directory": "/tmp/proj"})

        assert len(result) == 1
        payload = json.loads(result[0].text)
        assert payload["status"] == "error"
        assert "already exists" in payload["content"].lower()

    async def test_dots_in_name_rejected(self):
        result = await self.tool.execute({"store_name": "my.project", "directory": "/tmp/proj"})

        assert len(result) == 1
        payload = json.loads(result[0].text)
        assert payload["status"] == "error"
        assert "dot" in payload["content"].lower()


class TestPalStoreTool:
    def setup_method(self):
        self.tool = PalStoreTool()

    def test_tool_metadata(self):
        assert self.tool.get_name() == "palstore"
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

    async def test_store_requires_store_id(self):
        result = await self.tool.execute({"prompt": "test"})

        assert len(result) == 1
        payload = json.loads(result[0].text)
        assert payload["status"] == "error"
        assert "palinit" in payload["content"].lower()

    async def test_store_id_not_found(self):
        with patch("utils.palstore.resolve_store_location", return_value=None):
            result = await self.tool.execute({"store_id": "nonexistent", "prompt": "test"})

        assert len(result) == 1
        payload = json.loads(result[0].text)
        assert payload["status"] == "error"
        assert "not found" in payload["content"].lower()

    def test_default_thinking_mode(self):
        assert self.tool.get_default_thinking_mode() == "max"

    async def test_prepare_prompt_includes_header(self):
        request = PalStoreRequest(prompt="ignored", store_id="myproject")

        with patch.object(self.tool, "handle_prompt_file_with_fallback", return_value="test content"):
            with patch.object(self.tool, "get_request_files", return_value=[]):
                prompt = await self.tool.prepare_prompt(request)

        assert "CONTEXT LAYER SUBMISSION" in prompt
        assert "test content" in prompt


class TestPalQueryTool:
    def setup_method(self):
        self.tool = PalQueryTool()

    def test_tool_metadata(self):
        assert self.tool.get_name() == "palquery"
        assert "query" in self.tool.get_description().lower()

    def test_schema_structure(self):
        schema = self.tool.get_input_schema()
        props = schema["properties"]
        required = schema["required"]

        assert "prompt" in required
        assert "store_id" in required
        assert "directory" not in props
        assert "absolute_file_paths" in props
        assert "media" in props
        assert "context_label" not in props

    async def test_query_without_store_id_returns_error(self):
        result = await self.tool.execute({"prompt": "test"})

        assert len(result) == 1
        payload = json.loads(result[0].text)
        assert payload["status"] == "error"

    async def test_query_store_id_not_found(self):
        with patch("utils.palstore.resolve_store_location", return_value=None):
            result = await self.tool.execute({"store_id": "missing", "prompt": "test"})

        assert len(result) == 1
        payload = json.loads(result[0].text)
        assert payload["status"] == "error"


class TestPalListTool:
    def setup_method(self):
        self.tool = PalListTool()

    def test_tool_metadata(self):
        assert self.tool.get_name() == "pallist"

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
        with patch("utils.palstore.list_stores", return_value=[]):
            result = await self.tool.execute({})

        assert len(result) == 1
        payload = json.loads(result[0].text)
        assert payload["status"] == "success"
        assert "No context stores found" in payload["content"]

    async def test_execute_with_stores(self):
        from utils.palstore import PalRoot

        stores = [
            PalRoot(store_id="myproject", directory="/tmp/proj", created_at="2026-01-01T00:00:00Z"),
        ]

        with patch("utils.palstore.list_stores", return_value=stores):
            result = await self.tool.execute({})

        assert len(result) == 1
        payload = json.loads(result[0].text)
        assert payload["status"] == "success"
        assert "myproject" in payload["content"]


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

        thread_id = create_thread("palstore", {"prompt": "init"}, model_name="test-model")
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

        thread_id = create_thread("palstore", {"prompt": "init"}, model_name="test-model")
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

    def test_palquery_registered_in_tools(self):
        from server import TOOLS

        assert "palquery" in TOOLS

    def test_palstore_registered_in_tools(self):
        from server import TOOLS

        assert "palstore" in TOOLS


class TestContextForkChain:
    """Fork chain creation and parent traversal using real thread storage."""

    async def test_fork_creates_parent_chain(self):
        from utils.conversation_memory import add_turn, create_thread, get_thread_chain

        parent_id = create_thread("palstore", {"prompt": "parent init"}, model_name="gemini-test")
        add_turn(parent_id, "user", "Store this context")
        add_turn(parent_id, "assistant", "Context stored", model_name="gemini-test", model_provider="google")

        fork_id = create_thread("palquery", {"prompt": "fork start"}, parent_thread_id=parent_id)

        chain = get_thread_chain(fork_id)

        assert len(chain) == 2
        assert chain[0].thread_id == parent_id
        assert chain[1].thread_id == fork_id

    async def test_fork_does_not_modify_parent(self):
        from utils.conversation_memory import add_turn, create_thread, get_thread

        parent_id = create_thread("palstore", {"prompt": "parent"}, model_name="gemini-test")
        add_turn(parent_id, "user", "Initial store")
        add_turn(parent_id, "assistant", "Acknowledged", model_name="gemini-test", model_provider="google")

        parent_before = get_thread(parent_id)
        assert parent_before is not None
        parent_turn_count = len(parent_before.turns)

        fork_id = create_thread("palquery", {"prompt": "fork"}, parent_thread_id=parent_id)
        add_turn(fork_id, "user", "Fork query 1")
        add_turn(fork_id, "assistant", "Fork answer 1", model_name="gemini-test", model_provider="google")
        add_turn(fork_id, "user", "Fork query 2")

        parent_after = get_thread(parent_id)
        assert parent_after is not None
        assert len(parent_after.turns) == parent_turn_count

    async def test_multi_level_fork_chain(self):
        from utils.conversation_memory import add_turn, create_thread, get_thread_chain

        id_a = create_thread("palstore", {"prompt": "root"}, model_name="test-model")
        add_turn(id_a, "assistant", "Root context", model_name="test-model", model_provider="custom")

        id_b = create_thread("palquery", {"prompt": "fork-b"}, parent_thread_id=id_a)
        add_turn(id_b, "assistant", "Fork B context", model_name="test-model", model_provider="custom")

        id_c = create_thread("palquery", {"prompt": "fork-c"}, parent_thread_id=id_b)

        chain = get_thread_chain(id_c)

        assert len(chain) == 3
        assert chain[0].thread_id == id_a
        assert chain[1].thread_id == id_b
        assert chain[2].thread_id == id_c


class TestResolveStoreContinuation:
    """Tests for _resolve_store_continuation in server.py."""

    def _make_store(self, tmp_path, monkeypatch, store_id: str, directory: str):
        """Create, save, and index a PalRoot under tmp_path."""
        import os

        from utils import palstore
        from utils.palstore import PalRoot, save_store, update_index

        ctx_dir = str(tmp_path / "context")
        monkeypatch.setattr(palstore, "_CTX_DIR", ctx_dir)
        monkeypatch.setattr(palstore, "_INDEX_PATH", os.path.join(ctx_dir, "store-index.json"))
        monkeypatch.setattr(palstore, "_ARMED_PATH", os.path.join(ctx_dir, "armed.json"))

        from datetime import datetime, timezone

        store = PalRoot(
            store_id=store_id,
            directory=directory,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        save_store(store)
        update_index(directory, store_id)
        return store

    def test_returns_none_for_uuid(self, tmp_path, monkeypatch):
        import os

        from server import _resolve_store_continuation
        from utils import palstore

        ctx_dir = str(tmp_path / "context")
        monkeypatch.setattr(palstore, "_CTX_DIR", ctx_dir)
        monkeypatch.setattr(palstore, "_INDEX_PATH", os.path.join(ctx_dir, "store-index.json"))
        monkeypatch.setattr(palstore, "_ARMED_PATH", os.path.join(ctx_dir, "armed.json"))

        args = {"continuation_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}
        result = _resolve_store_continuation("thinkdeep", args)

        assert result is None
        assert args["continuation_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def test_fork_from_store(self, tmp_path, monkeypatch):
        from server import _resolve_store_continuation

        self._make_store(tmp_path, monkeypatch, "myproject", "/tmp/proj")

        args = {"continuation_id": "myproject"}
        result = _resolve_store_continuation("thinkdeep", args)

        # FORK mode: auto-fork F0 created under root, tool node under fork
        assert result == "myproject.F0.thinkdeep"
        # continuation_id is now a hydrated thread UUID, not the store path
        assert args["continuation_id"] != "myproject"

        bridge = args["_store_bridge"]
        assert bridge["tool_path"] == "myproject.F0.thinkdeep"
        assert bridge["tool_name"] == "thinkdeep"
        assert bridge["mode"] == "fork"
        assert bridge["store"].store_id == "myproject"

    def test_continue_same_tool(self, tmp_path, monkeypatch):
        from datetime import datetime, timezone

        from server import _resolve_store_continuation
        from utils.palstore import PalNode, save_store

        store = self._make_store(tmp_path, monkeypatch, "myproject", "/tmp/proj")

        # Simulate the structure a prior FORK call would have created:
        # myproject.F0 (fork) → myproject.F0.thinkdeep (tool)
        fork_node = PalNode(
            entry_type="fork",
            label="thinkdeep",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        thinkdeep_node = PalNode(
            entry_type="tool",
            tool_name="thinkdeep",
            timestamp=datetime.now(timezone.utc).isoformat(),
            prompt="initial prompt",
            response="initial response",
        )
        fork_node.children["thinkdeep"] = thinkdeep_node
        store.children["F0"] = fork_node
        save_store(store)

        args = {"continuation_id": "myproject.F0.thinkdeep"}
        result = _resolve_store_continuation("thinkdeep", args)

        # CONTINUE: same tool on same tool node — returns the same path
        assert result == "myproject.F0.thinkdeep"
        assert args["continuation_id"] != "myproject.F0.thinkdeep"

        bridge = args["_store_bridge"]
        assert bridge["tool_path"] == "myproject.F0.thinkdeep"
        assert bridge["tool_name"] == "thinkdeep"
        assert bridge["mode"] == "continue"

    def test_refork_different_tool(self, tmp_path, monkeypatch):
        from datetime import datetime, timezone

        from server import _resolve_store_continuation
        from utils.palstore import PalNode, save_store

        store = self._make_store(tmp_path, monkeypatch, "myproject", "/tmp/proj")

        # Simulate the structure from a prior FORK call:
        # myproject.F0 (fork) → myproject.F0.thinkdeep (tool)
        fork_node = PalNode(
            entry_type="fork",
            label="thinkdeep",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        thinkdeep_node = PalNode(
            entry_type="tool",
            tool_name="thinkdeep",
            timestamp=datetime.now(timezone.utc).isoformat(),
            prompt="initial prompt",
            response="initial response",
        )
        fork_node.children["thinkdeep"] = thinkdeep_node
        store.children["F0"] = fork_node
        save_store(store)

        args = {"continuation_id": "myproject.F0.thinkdeep"}
        result = _resolve_store_continuation("analyze", args)

        # FORK: analyze on a thinkdeep tool node creates F0 under thinkdeep, then analyze under that
        assert result == "myproject.F0.thinkdeep.F0.analyze"
        assert args["continuation_id"] != "myproject.F0.thinkdeep"

        bridge = args["_store_bridge"]
        assert bridge["tool_path"] == "myproject.F0.thinkdeep.F0.analyze"
        assert bridge["tool_name"] == "analyze"
        assert bridge["mode"] == "fork"

    def test_fork_from_query(self, tmp_path, monkeypatch):
        from datetime import datetime, timezone

        from server import _resolve_store_continuation
        from utils.palstore import PalNode, save_store

        store = self._make_store(tmp_path, monkeypatch, "myproject", "/tmp/proj")

        query_node = PalNode(
            entry_type="query",
            timestamp=datetime.now(timezone.utc).isoformat(),
            prompt="query prompt",
            response="query response",
        )
        store.children["Q0"] = query_node
        save_store(store)

        args = {"continuation_id": "myproject.Q0"}
        result = _resolve_store_continuation("thinkdeep", args)

        # FORK: auto-fork F0 under Q0, then thinkdeep tool node under the fork
        assert result == "myproject.Q0.F0.thinkdeep"

        bridge = args["_store_bridge"]
        assert bridge["tool_path"] == "myproject.Q0.F0.thinkdeep"
        assert bridge["mode"] == "fork"

    def test_fork_from_layer(self, tmp_path, monkeypatch):
        from datetime import datetime, timezone

        from server import _resolve_store_continuation
        from utils.palstore import PalNode, save_store

        store = self._make_store(tmp_path, monkeypatch, "myproject", "/tmp/proj")

        layer_node = PalNode(
            entry_type="store",
            timestamp=datetime.now(timezone.utc).isoformat(),
            prompt="layer prompt",
            response="layer response",
        )
        store.children["L1"] = layer_node
        save_store(store)

        args = {"continuation_id": "myproject.L1"}
        result = _resolve_store_continuation("chat", args)

        # FORK: auto-fork F0 under L1, then chat tool node under the fork
        assert result == "myproject.L1.F0.chat"

        bridge = args["_store_bridge"]
        assert bridge["tool_path"] == "myproject.L1.F0.chat"
        assert bridge["mode"] == "fork"

    def test_fork_index_increments(self, tmp_path, monkeypatch):
        from server import _resolve_store_continuation

        self._make_store(tmp_path, monkeypatch, "myproject", "/tmp/proj")

        # First fork — no children exist yet; _resolve_store_continuation saves to disk
        args1 = {"continuation_id": "myproject"}
        result1 = _resolve_store_continuation("thinkdeep", args1)
        assert result1 == "myproject.F0.thinkdeep"

        # Second fork: reload from disk so F0 is visible, then call again
        args2 = {"continuation_id": "myproject"}
        result2 = _resolve_store_continuation("thinkdeep", args2)
        assert result2 == "myproject.F1.thinkdeep"


class TestInjectStorePathContinuation:
    """Tests for _inject_store_path_continuation response post-processing."""

    def test_uuid_replaced_in_response(self):
        from mcp.types import TextContent

        from server import _inject_store_path_continuation

        response_json = json.dumps(
            {
                "status": "continuation_available",
                "content": "Analysis complete.",
                "continuation_offer": {
                    "continuation_id": "some-uuid-value",
                    "note": "Conversation active.",
                    "context_window": 100000,
                    "context_used": 5000,
                    "context_remaining": 95000,
                },
            }
        )
        items = [TextContent(type="text", text=response_json)]

        result = _inject_store_path_continuation(items, "myproject.F0.thinkdeep")

        parsed = json.loads(result[0].text)
        assert parsed["continuation_offer"]["continuation_id"] == "myproject.F0.thinkdeep"
        assert parsed["content"] == "Analysis complete."

    def test_bare_continuation_id_replaced_in_workflow_response(self):
        from mcp.types import TextContent

        from server import _inject_store_path_continuation

        response_json = json.dumps(
            {
                "status": "in_progress",
                "content": "Step 1 complete.",
                "continuation_id": "some-uuid-value",
                "step_number": 1,
                "total_steps": 3,
                "next_step_required": False,
            }
        )
        items = [TextContent(type="text", text=response_json)]

        result = _inject_store_path_continuation(items, "myproject.F0.analyze")

        parsed = json.loads(result[0].text)
        assert parsed["continuation_id"] == "myproject.F0.analyze"
        assert parsed["content"] == "Step 1 complete."

    def test_workflow_next_step_injects_guidance(self):
        from mcp.types import TextContent

        from server import _inject_store_path_continuation

        bridge = {"tool_name": "analyze", "tool_path": "myproject.F0.analyze", "mode": "fork"}
        response_json = json.dumps(
            {
                "status": "in_progress",
                "content": "Step 1.",
                "continuation_id": "some-uuid-value",
                "step_number": 1,
                "total_steps": 3,
                "next_step_required": True,
            }
        )
        items = [TextContent(type="text", text=response_json)]
        args = {"_store_bridge": bridge, "prompt": "test"}

        result = _inject_store_path_continuation(items, "myproject.F0.analyze", arguments=args)

        parsed = json.loads(result[0].text)
        assert "store_continuation_guidance" in parsed
        guidance = parsed["store_continuation_guidance"]
        assert "myproject.F0.analyze" in guidance
        assert "step_number=2" in guidance

    def test_completed_workflow_injects_chain_note(self):
        from mcp.types import TextContent

        from server import _inject_store_path_continuation

        bridge = {"tool_name": "planner", "tool_path": "myproject.F0.planner", "mode": "fork"}
        response_json = json.dumps(
            {
                "status": "complete",
                "content": "Plan complete.",
                "continuation_id": "some-uuid-value",
                "next_step_required": False,
            }
        )
        items = [TextContent(type="text", text=response_json)]
        args = {"_store_bridge": bridge, "prompt": "test"}

        result = _inject_store_path_continuation(items, "myproject.F0.planner", arguments=args)

        parsed = json.loads(result[0].text)
        assert "store_chain_note" in parsed
        assert "myproject.F0.planner" in parsed["store_chain_note"]
        assert "store_continuation_guidance" not in parsed

    def test_non_json_passthrough(self):
        from mcp.types import TextContent

        from server import _inject_store_path_continuation

        items = [TextContent(type="text", text="not json")]
        result = _inject_store_path_continuation(items, "myproject.F0.thinkdeep")

        assert result[0].text == "not json"

    def test_no_continuation_offer_passthrough(self):
        from mcp.types import TextContent

        from server import _inject_store_path_continuation

        response_json = json.dumps({"status": "success", "content": "Done."})
        items = [TextContent(type="text", text=response_json)]

        result = _inject_store_path_continuation(items, "myproject.F0.thinkdeep")

        parsed = json.loads(result[0].text)
        assert "continuation_offer" not in parsed or parsed.get("continuation_offer") is None
        assert "continuation_id" not in parsed or parsed.get("continuation_id") is None


class TestStrictHistoryEnforcement:
    """Context stores must never silently truncate conversation history."""

    def test_strict_mode_raises_on_truncation(self):
        """When strict=True, build_conversation_history raises ValueError if turns would be dropped."""
        from unittest.mock import MagicMock

        from utils.conversation_memory import ThreadContext, build_conversation_history

        # Create a thread with many large turns that will exceed any budget
        turns = []
        for i in range(50):
            from utils.conversation_memory import ConversationTurn

            turns.append(
                ConversationTurn(
                    role="user" if i % 2 == 0 else "assistant",
                    content="x" * 10000,  # ~2500 tokens per turn
                    timestamp="2026-01-01T00:00:00Z",
                )
            )

        context = ThreadContext(
            thread_id="test-strict",
            created_at="2026-01-01T00:00:00Z",
            last_updated_at="2026-01-01T00:00:00Z",
            tool_name="palstore",
            turns=turns,
            initial_context={},
        )

        # Create a model_context with a tiny window that can't fit all turns
        model_context = MagicMock()
        model_context.model_name = "tiny-model"
        model_context.capabilities.context_window = 8000
        model_context.calculate_token_allocation.return_value = MagicMock(
            history_tokens=2000,  # Only 2000 tokens for history
            file_tokens=1000,
            total_tokens=8000,
            content_tokens=4000,
        )
        model_context.estimate_tokens = lambda text: len(text) // 4

        with pytest.raises(ValueError, match="Context store history"):
            build_conversation_history(context, model_context, strict=True)

    def test_non_strict_mode_truncates_silently(self):
        """Default (strict=False) still truncates without error."""
        from unittest.mock import MagicMock

        from utils.conversation_memory import ConversationTurn, ThreadContext, build_conversation_history

        turns = []
        for i in range(50):
            turns.append(
                ConversationTurn(
                    role="user" if i % 2 == 0 else "assistant",
                    content="x" * 10000,
                    timestamp="2026-01-01T00:00:00Z",
                )
            )

        context = ThreadContext(
            thread_id="test-nonstrict",
            created_at="2026-01-01T00:00:00Z",
            last_updated_at="2026-01-01T00:00:00Z",
            tool_name="chat",
            turns=turns,
            initial_context={},
        )

        model_context = MagicMock()
        model_context.model_name = "tiny-model"
        model_context.capabilities.context_window = 8000
        model_context.calculate_token_allocation.return_value = MagicMock(
            history_tokens=2000,
            file_tokens=1000,
            total_tokens=8000,
            content_tokens=4000,
        )
        model_context.estimate_tokens = lambda text: len(text) // 4

        # Should NOT raise — truncates silently
        history, tokens = build_conversation_history(context, model_context, strict=False)
        assert "most recent turns" in history

    def test_strict_mode_passes_when_history_fits(self):
        """strict=True should not error when all turns fit within budget."""
        from unittest.mock import MagicMock

        from utils.conversation_memory import ConversationTurn, ThreadContext, build_conversation_history

        turns = [
            ConversationTurn(
                role="user",
                content="hello",
                timestamp="2026-01-01T00:00:00Z",
            ),
            ConversationTurn(
                role="assistant",
                content="hi there",
                timestamp="2026-01-01T00:00:01Z",
            ),
        ]

        context = ThreadContext(
            thread_id="test-fits",
            created_at="2026-01-01T00:00:00Z",
            last_updated_at="2026-01-01T00:00:01Z",
            tool_name="palstore",
            turns=turns,
            initial_context={},
        )

        model_context = MagicMock()
        model_context.model_name = "big-model"
        model_context.capabilities.context_window = 1000000
        model_context.calculate_token_allocation.return_value = MagicMock(
            history_tokens=500000,
            file_tokens=100000,
            total_tokens=1000000,
            content_tokens=800000,
        )
        model_context.estimate_tokens = lambda text: len(text) // 4

        # Should not raise
        history, tokens = build_conversation_history(context, model_context, strict=True)
        assert "most recent turns" not in history


if __name__ == "__main__":
    pytest.main([__file__])
