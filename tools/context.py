"""
Context silo tools — init, store, query, and list context stores.

Four tools: ctxinit, ctxstore, ctxquery, ctxlist.
"""

import logging
from typing import Any, Optional

from mcp.types import TextContent
from pydantic import Field

from config import TEMPERATURE_ANALYTICAL
from systemprompts import CONTEXT_PROMPT
from tools.shared.base_models import COMMON_FIELD_DESCRIPTIONS, ToolRequest

from .shared.base_tool import BaseTool
from .simple.base import SimpleTool

logger = logging.getLogger(__name__)

STORE_ID_DESCRIPTION = (
    "Path-based identifier for the context store. Returned by ctxinit or ctxstore. "
    "Pass back to continue storing or querying."
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CtxStoreRequest(ToolRequest):
    prompt: str = Field(...)
    absolute_file_paths: Optional[list[str]] = Field(default_factory=list)
    media: Optional[list[str]] = Field(default_factory=list)
    context_label: Optional[str] = Field(default=None)
    store_id: str = Field(...)


class CtxQueryRequest(ToolRequest):
    prompt: str = Field(...)
    store_id: str = Field(...)


# ---------------------------------------------------------------------------
# Shared base
# ---------------------------------------------------------------------------


class ContextBaseTool(SimpleTool):
    """Shared base for all context silo tools."""

    ephemeral: bool = False

    def get_model_category(self):
        from tools.models import ToolModelCategory

        return ToolModelCategory.EXTENDED_REASONING

    def get_default_temperature(self) -> float:
        return TEMPERATURE_ANALYTICAL

    def get_default_thinking_mode(self) -> str:
        return "max"

    def get_system_prompt(self) -> str:
        return CONTEXT_PROMPT

    def get_websearch_guidance(self) -> Optional[str]:
        return None

    def get_annotations(self) -> dict:
        return {"readOnlyHint": False}

    def _map_store_id(self, arguments: dict) -> dict:
        """Resolve path-based store_id to thread UUID via registry lookup."""
        store_id = arguments.get("store_id")
        if store_id:
            from utils.context_registry import resolve_thread_id

            thread_id = resolve_thread_id(store_id)
            if thread_id:
                arguments["continuation_id"] = thread_id
            else:
                arguments["_store_id_not_found"] = True
        return arguments


# ---------------------------------------------------------------------------
# ctxinit
# ---------------------------------------------------------------------------


class CtxInitTool(BaseTool):
    def get_name(self) -> str:
        return "ctxinit"

    def get_description(self) -> str:
        return "Create a named context store. Returns a store_id to use with ctxstore and ctxquery."

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "store_name": {
                    "type": "string",
                    "description": "Name for the context store. Used as the store_id path.",
                },
                "directory": {
                    "type": "string",
                    "description": "Absolute path to the project directory this store is associated with.",
                },
            },
            "required": ["store_name", "directory"],
            "additionalProperties": False,
        }

    def get_annotations(self) -> dict:
        return {"readOnlyHint": False}

    def get_system_prompt(self) -> str:
        return ""

    def get_request_model(self):
        return ToolRequest

    def requires_model(self) -> bool:
        return False

    def get_model_category(self):
        from tools.models import ToolModelCategory

        return ToolModelCategory.FAST_RESPONSE

    async def prepare_prompt(self, request: ToolRequest) -> str:
        return ""

    def format_response(self, response: str, request: ToolRequest, model_info: Optional[dict] = None) -> str:
        return response

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        from tools.models import ToolOutput
        from utils.context_registry import get_store_entry, register_store
        from utils.conversation_memory import create_thread

        store_name = arguments.get("store_name", "")
        directory = arguments.get("directory", "")

        existing = get_store_entry(store_name)
        if existing and existing.get("directory") == directory:
            error = ToolOutput(
                status="error",
                content=f'Store "{store_name}" already exists in this directory.',
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        thread_id = create_thread("ctxinit", {}, model_name=None)
        register_store(store_id=store_name, thread_id=thread_id, directory=directory, entry_type="store")

        tool_output = ToolOutput(
            status="success",
            content=(
                f"Context store created.\n\n"
                f"store_id: {store_name}\n"
                f"directory: {directory}\n\n"
                f'Use ctxstore(store_id="{store_name}", ...) to add context layers.'
            ),
            content_type="text",
            metadata={"store_id": store_name, "directory": directory},
        )
        return [TextContent(type="text", text=tool_output.model_dump_json())]


# ---------------------------------------------------------------------------
# ctxstore
# ---------------------------------------------------------------------------


class CtxStoreTool(ContextBaseTool):
    ephemeral: bool = False

    def get_name(self) -> str:
        return "ctxstore"

    def get_description(self) -> str:
        return (
            "Add context layers to an existing store. Each call appends a new layer. Requires a store_id from ctxinit."
        )

    def get_request_model(self):
        return CtxStoreRequest

    def get_tool_fields(self) -> dict[str, dict[str, Any]]:
        return {
            "prompt": {"type": "string", "description": "Content or context to store in this layer."},
            "absolute_file_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": COMMON_FIELD_DESCRIPTIONS["absolute_file_paths"],
            },
            "context_label": {
                "type": "string",
                "description": "Optional human-readable label for this context layer.",
            },
            "store_id": {"type": "string", "description": STORE_ID_DESCRIPTION},
        }

    def get_required_fields(self) -> list[str]:
        return ["prompt", "store_id"]

    def get_input_schema(self) -> dict[str, Any]:
        required_fields = ["prompt", "store_id"]
        if self.is_effective_auto_mode():
            required_fields.append("model")

        return {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Content or context to store in this layer."},
                "absolute_file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": COMMON_FIELD_DESCRIPTIONS["absolute_file_paths"],
                },
                "media": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": COMMON_FIELD_DESCRIPTIONS["media"],
                },
                "context_label": {
                    "type": "string",
                    "description": "Optional human-readable label for this context layer.",
                },
                "store_id": {"type": "string", "description": STORE_ID_DESCRIPTION},
                "model": self.get_model_field_schema(),
                "temperature": {
                    "type": "number",
                    "description": COMMON_FIELD_DESCRIPTIONS["temperature"],
                    "minimum": 0,
                    "maximum": 1,
                },
            },
            "required": required_fields,
            "additionalProperties": False,
        }

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        from tools.models import ToolOutput

        if not arguments.get("store_id"):
            error = ToolOutput(
                status="error",
                content="ctxstore requires a store_id. Use ctxinit to create a store first.",
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        self._map_store_id(arguments)

        if arguments.get("_store_id_not_found"):
            error = ToolOutput(
                status="error",
                content="Store not found. Use ctxinit to create a store first.",
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        arguments["thinking_mode"] = "max"
        self._store_id = arguments.get("store_id")

        return await super().execute(arguments)

    def _create_continuation_offer(self, request, model_info: Optional[dict] = None):
        continuation_data = super()._create_continuation_offer(request, model_info)
        if continuation_data:
            try:
                from utils.context_registry import increment_layer_count

                new_store_id = increment_layer_count(self._store_id)
                continuation_data["continuation_id"] = new_store_id
            except Exception as exc:
                logger.warning("Failed to increment layer count for store %s: %s", self._store_id, exc)
        return continuation_data

    async def prepare_prompt(self, request: CtxStoreRequest) -> str:
        user_content = self.handle_prompt_file_with_fallback(request)

        files = self.get_request_files(request)
        file_section = ""
        if files:
            file_content, processed = self._prepare_file_content_for_prompt(
                files,
                self.get_request_continuation_id(request),
                "Context files",
                model_context=getattr(self, "_model_context", None),
            )
            self._actually_processed_files = processed
            if file_content:
                file_section = f"\n\n=== CONTEXT FILES ===\n{file_content}\n=== END CONTEXT FILES ==="

        label_line = f"\n[Label: {request.context_label}]" if request.context_label else ""
        return f"=== CONTEXT LAYER SUBMISSION ==={label_line}\n\n{user_content}{file_section}"

    def format_response(self, response: str, request: CtxStoreRequest, model_info: Optional[dict] = None) -> str:
        return f"{response}\n\n---\n\nAGENT'S TURN: Context layer stored. Use the store_id to add more layers or query this silo."

    def _record_assistant_turn(
        self, continuation_id: str, response_text: str, request, model_info: Optional[dict]
    ) -> None:
        from utils.conversation_memory import add_turn

        model_provider = None
        model_name = None
        model_metadata: dict[str, Any] = {}

        if model_info:
            provider = model_info.get("provider")
            if provider:
                if isinstance(provider, str):
                    model_provider = provider
                else:
                    try:
                        model_provider = provider.get_provider_type().value
                    except AttributeError:
                        model_provider = str(provider)
            model_name = model_info.get("model_name")
            model_response = model_info.get("model_response")
            if model_response:
                model_metadata = {"usage": model_response.usage, "metadata": model_response.metadata}

        label = getattr(request, "context_label", None)
        if label:
            model_metadata["context_label"] = label

        add_turn(
            continuation_id,
            "assistant",
            response_text,
            files=self.get_request_files(request),
            media=self.get_request_media(request),
            tool_name=self.get_name(),
            model_provider=model_provider,
            model_name=model_name,
            model_metadata=model_metadata if model_metadata else None,
        )


# ---------------------------------------------------------------------------
# ctxquery
# ---------------------------------------------------------------------------


class CtxQueryTool(ContextBaseTool):
    ephemeral: bool = True

    def get_name(self) -> str:
        return "ctxquery"

    def get_description(self) -> str:
        return "Query a context store. Forks on store nodes, continues on query nodes. Returns a new store_id reflecting the operation."

    def get_request_model(self):
        return CtxQueryRequest

    def get_tool_fields(self) -> dict[str, dict[str, Any]]:
        return {
            "prompt": {"type": "string", "description": "Question or query to run against the context silo."},
            "store_id": {"type": "string", "description": STORE_ID_DESCRIPTION},
        }

    def get_required_fields(self) -> list[str]:
        return ["prompt", "store_id"]

    def get_input_schema(self) -> dict[str, Any]:
        required_fields = ["prompt", "store_id"]
        if self.is_effective_auto_mode():
            required_fields.append("model")

        return {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Question or query to run against the context silo."},
                "store_id": {"type": "string", "description": STORE_ID_DESCRIPTION},
                "model": self.get_model_field_schema(),
                "temperature": {
                    "type": "number",
                    "description": COMMON_FIELD_DESCRIPTIONS["temperature"],
                    "minimum": 0,
                    "maximum": 1,
                },
            },
            "required": required_fields,
            "additionalProperties": False,
        }

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        from tools.models import ToolOutput

        if not arguments.get("store_id"):
            error = ToolOutput(
                status="error",
                content="ctxquery requires a store_id. Create a store with ctxinit first.",
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        self._map_store_id(arguments)

        if arguments.get("_store_id_not_found"):
            error = ToolOutput(
                status="error",
                content="Store not found. Use ctxinit to create a store first.",
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        arguments["thinking_mode"] = "max"

        from utils.context_registry import get_store_entry

        self._store_id = arguments["store_id"]
        entry = get_store_entry(self._store_id)
        self._entry = entry

        if entry and entry.get("entry_type") == "store":
            return await self._execute_fork_path(arguments)
        else:
            return await self._execute_continue_path(arguments)

    async def _execute_fork_path(self, arguments: dict[str, Any]) -> list[TextContent]:
        from utils.context_registry import get_next_query_index, register_store
        from utils.conversation_memory import add_turn, create_thread

        parent_uuid = arguments["continuation_id"]
        prompt = arguments.get("prompt", "")
        entry = self._entry

        new_thread_id = create_thread("ctxquery", {}, parent_thread_id=parent_uuid)
        add_turn(new_thread_id, "user", prompt, tool_name="ctxquery")

        query_index = get_next_query_index(self._store_id)
        new_store_id = f"{self._store_id}.Q{query_index}"

        register_store(
            store_id=new_store_id,
            thread_id=new_thread_id,
            directory=entry["directory"],
            label=prompt[:80] if prompt else None,
            entry_type="query",
            parent_store_id=self._store_id,
        )

        arguments["continuation_id"] = new_thread_id
        self._new_store_id = new_store_id

        return await super().execute(arguments)

    async def _execute_continue_path(self, arguments: dict[str, Any]) -> list[TextContent]:
        from utils.context_registry import increment_follow_up_count
        from utils.conversation_memory import add_turn

        thread_uuid = arguments["continuation_id"]
        prompt = arguments.get("prompt", "")

        add_turn(thread_uuid, "user", prompt, tool_name="ctxquery")

        new_store_id = increment_follow_up_count(self._store_id)
        self._new_store_id = new_store_id

        return await super().execute(arguments)

    def _create_continuation_offer(self, request, model_info: Optional[dict] = None):
        new_id = getattr(self, "_new_store_id", None)
        if not new_id:
            return None
        context_window, context_used = self._get_context_token_info()
        return {
            "continuation_id": new_id,
            "context_window": context_window,
            "context_used": context_used,
            "note": f"Query recorded at {new_id}.",
        }

    def _record_assistant_turn(
        self, continuation_id: str, response_text: str, request, model_info: Optional[dict]
    ) -> None:
        from utils.conversation_memory import add_turn

        model_provider = None
        model_name = None
        model_metadata: dict[str, Any] = {}
        if model_info:
            provider = model_info.get("provider")
            if provider:
                if isinstance(provider, str):
                    model_provider = provider
                else:
                    try:
                        model_provider = provider.get_provider_type().value
                    except AttributeError:
                        model_provider = str(provider)
            model_name = model_info.get("model_name")
            model_response = model_info.get("model_response")
            if model_response:
                model_metadata = {"usage": model_response.usage, "metadata": model_response.metadata}
        add_turn(
            continuation_id,
            "assistant",
            response_text,
            tool_name=self.get_name(),
            model_provider=model_provider,
            model_name=model_name,
            model_metadata=model_metadata if model_metadata else None,
        )

    async def prepare_prompt(self, request: CtxQueryRequest) -> str:
        user_content = self.handle_prompt_file_with_fallback(request)
        return f"=== CONTEXT SILO QUERY ===\n\n{user_content}"

    def format_response(self, response: str, request: CtxQueryRequest, model_info: Optional[dict] = None) -> str:
        return f"{response}\n\n---\n\nAGENT'S TURN: Evaluate this response from the context silo alongside your own analysis."


# ---------------------------------------------------------------------------
# ctxlist
# ---------------------------------------------------------------------------


class CtxListTool(BaseTool):
    def get_name(self) -> str:
        return "ctxlist"

    def get_description(self) -> str:
        return "List context stores registered with PAL. Optionally filter by project directory."

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "Absolute path to filter stores by project directory. Omit to list all stores.",
                },
            },
            "required": [],
            "additionalProperties": False,
        }

    def get_annotations(self) -> Optional[dict[str, Any]]:
        return {"readOnlyHint": True}

    def get_system_prompt(self) -> str:
        return ""

    def get_request_model(self):
        return ToolRequest

    def requires_model(self) -> bool:
        return False

    def get_model_category(self):
        from tools.models import ToolModelCategory

        return ToolModelCategory.FAST_RESPONSE

    async def prepare_prompt(self, request: ToolRequest) -> str:
        return ""

    def format_response(self, response: str, request: ToolRequest, model_info: Optional[dict] = None) -> str:
        return response

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        from tools.models import ToolOutput
        from utils.context_registry import list_stores

        directory = arguments.get("directory")
        stores = list_stores(directory)

        if stores:
            content = f"Found {len(stores)} node(s):\n\n"
            for entry in stores:
                sid = entry.get("store_id", "?")
                etype = entry.get("entry_type", "?")
                label = entry.get("label") or "(none)"
                layers = entry.get("layer_count", 0)
                follow_ups = entry.get("follow_up_count", 0)

                line = f'- {sid}  [{etype}]  "{label}"'
                if etype == "store" and layers > 0:
                    line += f"  ({layers} layer{'s' if layers != 1 else ''})"
                if etype == "query" and follow_ups > 0:
                    line += f"  ({follow_ups} follow-up{'s' if follow_ups != 1 else ''})"
                content += line + "\n"
        else:
            scope = f" for directory '{directory}'" if directory else ""
            content = f"No context stores found{scope}."

        tool_output = ToolOutput(
            status="success",
            content=content.strip(),
            content_type="text",
            metadata={"store_count": len(stores), "directory_filter": directory},
        )
        return [TextContent(type="text", text=tool_output.model_dump_json())]
