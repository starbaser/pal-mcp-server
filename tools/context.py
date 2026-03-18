"""
Context silo tools — store, query, fork, and list context stores.

Four tools share ContextBaseTool as their base:
- ctxstore  : Persist context layers into a named silo (thread-backed)
- ctxquery  : Ephemeral read-only query against an existing silo
- ctxfork   : Fork a silo checkpoint into a new independent thread
- ctxlist   : List known stores (no model required)
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
    "Identifier for the context store. Returned by ctxstore on first call. "
    "Pass back to continue storing, querying, or forking."
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CtxStoreRequest(ToolRequest):
    prompt: str = Field(...)
    absolute_file_paths: Optional[list[str]] = Field(default_factory=list)
    media: Optional[list[str]] = Field(default_factory=list)
    directory: Optional[str] = Field(default=None)
    context_label: Optional[str] = Field(default=None)
    store_id: Optional[str] = Field(default=None)


class CtxQueryRequest(ToolRequest):
    prompt: str = Field(...)
    store_id: str = Field(...)


class CtxForkRequest(ToolRequest):
    prompt: str = Field(...)
    store_id: str = Field(...)
    absolute_file_paths: Optional[list[str]] = Field(default_factory=list)
    media: Optional[list[str]] = Field(default_factory=list)
    context_label: Optional[str] = Field(default=None)


# ---------------------------------------------------------------------------
# Shared base
# ---------------------------------------------------------------------------


class ContextBaseTool(SimpleTool):
    """Shared base for all context silo tools."""

    ephemeral_continuation: bool = False

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
        """Map store_id to continuation_id for thread system compatibility."""
        if "store_id" in arguments:
            arguments["continuation_id"] = arguments["store_id"]
        return arguments


# ---------------------------------------------------------------------------
# ctxstore
# ---------------------------------------------------------------------------


class CtxStoreTool(ContextBaseTool):
    ephemeral_continuation: bool = False

    def __init__(self) -> None:
        super().__init__()
        self._pending_directory: Optional[str] = None
        self._pending_label: Optional[str] = None

    def get_name(self) -> str:
        return "ctxstore"

    def get_description(self) -> str:
        return (
            "Store context layers into a named silo. Each call adds a new layer to the silo. "
            "Returns a store_id to use with ctxquery, ctxfork, and subsequent ctxstore calls."
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
            "directory": {
                "type": "string",
                "description": "Absolute path to the project directory. Required on first store call.",
            },
            "context_label": {
                "type": "string",
                "description": "Optional human-readable label for this context layer.",
            },
            "store_id": {"type": "string", "description": STORE_ID_DESCRIPTION},
        }

    def get_required_fields(self) -> list[str]:
        return ["prompt"]

    def get_input_schema(self) -> dict[str, Any]:
        required_fields = ["prompt"]
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
                "directory": {
                    "type": "string",
                    "description": "Absolute path to the project directory. Required on first store call.",
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

        self._map_store_id(arguments)
        arguments["thinking_mode"] = "max"

        if not arguments.get("continuation_id") and not arguments.get("directory"):
            error = ToolOutput(
                status="error",
                content="First ctxstore call requires 'directory' to associate this store with a project.",
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        self._pending_directory = arguments.get("directory")
        self._pending_label = arguments.get("context_label")
        self._is_first_store = not arguments.get("continuation_id")

        return await super().execute(arguments)

    def _create_continuation_offer(self, request, model_info: Optional[dict] = None):
        continuation_data = super()._create_continuation_offer(request, model_info)
        if continuation_data and self._is_first_store:
            new_id = continuation_data.get("continuation_id")
            directory = self._pending_directory
            label = self._pending_label
            model = (
                self._current_arguments.get("_resolved_model_name") or self._current_arguments.get("model") or ""
                if hasattr(self, "_current_arguments") and self._current_arguments
                else ""
            )
            if directory and new_id:
                try:
                    from utils.context_registry import register_store

                    register_store(directory, new_id, label, model)
                except Exception as exc:
                    logger.warning("Failed to register new context store: %s", exc)
        elif continuation_data and not self._is_first_store:
            cid = continuation_data.get("continuation_id")
            if cid:
                try:
                    from utils.context_registry import update_store_turn_count

                    update_store_turn_count(cid)
                except Exception as exc:
                    logger.warning("Failed to update store turn count: %s", exc)
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
    ephemeral_continuation: bool = True

    def get_name(self) -> str:
        return "ctxquery"

    def get_description(self) -> str:
        return (
            "Ephemeral read-only query against an existing context silo. "
            "The query does not write to the silo — the store_id checkpoint is preserved unchanged."
        )

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

        self._map_store_id(arguments)
        arguments["thinking_mode"] = "max"

        if not arguments.get("continuation_id"):
            error = ToolOutput(
                status="error",
                content="ctxquery requires a store_id. Create a store with ctxstore first.",
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        return await super().execute(arguments)

    async def prepare_prompt(self, request: CtxQueryRequest) -> str:
        user_content = self.handle_prompt_file_with_fallback(request)
        return f"=== CONTEXT SILO QUERY (READ-ONLY) ===\n\n{user_content}"

    def format_response(self, response: str, request: CtxQueryRequest, model_info: Optional[dict] = None) -> str:
        return f"{response}\n\n---\n\nAGENT'S TURN: Evaluate this response from the context silo alongside your own analysis."

    def _record_assistant_turn(
        self, continuation_id: str, response_text: str, request, model_info: Optional[dict]
    ) -> None:
        return

    def _create_continuation_offer(self, request, model_info: Optional[dict] = None):
        store_id = self.get_request_continuation_id(request)
        if not store_id:
            return None

        context_window, context_used = self._get_context_token_info()

        return {
            "continuation_id": store_id,
            "context_window": context_window,
            "context_used": context_used,
            "note": "Silo checkpoint preserved.",
        }


# ---------------------------------------------------------------------------
# ctxfork
# ---------------------------------------------------------------------------


class CtxForkTool(ContextBaseTool):
    ephemeral_continuation: bool = True

    def __init__(self) -> None:
        super().__init__()
        self._fork_store_id: Optional[str] = None
        self._parent_store_id: Optional[str] = None

    def get_name(self) -> str:
        return "ctxfork"

    def get_description(self) -> str:
        return (
            "Fork an existing context silo checkpoint into a new independent thread. "
            "The parent silo is not modified. Returns a new store_id for the fork."
        )

    def get_request_model(self):
        return CtxForkRequest

    def get_tool_fields(self) -> dict[str, dict[str, Any]]:
        return {
            "prompt": {"type": "string", "description": "Query or instruction to start the fork with."},
            "store_id": {"type": "string", "description": STORE_ID_DESCRIPTION},
            "absolute_file_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": COMMON_FIELD_DESCRIPTIONS["absolute_file_paths"],
            },
            "context_label": {
                "type": "string",
                "description": "Optional label for the forked silo.",
            },
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
                "prompt": {"type": "string", "description": "Query or instruction to start the fork with."},
                "store_id": {"type": "string", "description": STORE_ID_DESCRIPTION},
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
                    "description": "Optional label for the forked silo.",
                },
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

        self._map_store_id(arguments)
        arguments["thinking_mode"] = "max"

        if not arguments.get("continuation_id"):
            error = ToolOutput(
                status="error",
                content="ctxfork requires a store_id. Create a store with ctxstore first.",
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        return await self._handle_fork(arguments)

    async def _handle_fork(self, arguments: dict[str, Any]) -> list[TextContent]:
        from utils.context_registry import get_store_directory, register_store
        from utils.conversation_memory import add_turn, create_thread

        parent_store_id = arguments.get("continuation_id")
        user_prompt = arguments.get("prompt", "")
        user_files = arguments.get("absolute_file_paths") or []
        resolved_model = arguments.get("_resolved_model_name") or arguments.get("model") or ""
        label = arguments.get("context_label")

        initial_request_dict = {k: v for k, v in arguments.items() if k not in ("continuation_id", "store_id")}
        new_thread_id = create_thread(
            tool_name=self.get_name(),
            initial_request=initial_request_dict,
            parent_thread_id=parent_store_id,
            model_name=resolved_model,
        )

        add_turn(new_thread_id, "user", user_prompt, files=user_files, tool_name=self.get_name())

        arguments["continuation_id"] = new_thread_id
        self._fork_store_id = new_thread_id
        self._parent_store_id = parent_store_id

        directory = get_store_directory(parent_store_id)
        if directory:
            try:
                register_store(directory, new_thread_id, label, resolved_model, parent_store_id=parent_store_id)
            except Exception as exc:
                logger.warning("Failed to register forked store: %s", exc)

        return await super().execute(arguments)

    async def prepare_prompt(self, request: CtxForkRequest) -> str:
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

        return f"=== CONTEXT SILO FORK — BRANCHED QUERY ===\n\n{user_content}{file_section}"

    def format_response(self, response: str, request: CtxForkRequest, model_info: Optional[dict] = None) -> str:
        return (
            f"{response}\n\n---\n\nAGENT'S TURN: This is a forked silo branch. "
            "Use the new store_id to continue building this fork independently. "
            "The parent silo remains unchanged and accessible via its original store_id."
        )

    def _create_continuation_offer(self, request, model_info: Optional[dict] = None):
        fork_id = self._fork_store_id
        if not fork_id:
            return None

        context_window, context_used = self._get_context_token_info()

        return {
            "continuation_id": fork_id,
            "context_window": context_window,
            "context_used": context_used,
            "note": f"Forked silo created. Parent store preserved at {self._parent_store_id}.",
            "parent_store_id": self._parent_store_id,
        }

    def _create_continuation_offer_response(
        self, content: str, continuation_data: dict, request, model_info: Optional[dict] = None
    ):
        from tools.models import ContinuationOffer, ToolOutput

        try:
            ctx_window = continuation_data.get("context_window", 0)
            ctx_used = continuation_data.get("context_used", 0)
            continuation_offer = ContinuationOffer(
                continuation_id=continuation_data["continuation_id"],
                note=continuation_data["note"],
                context_window=ctx_window,
                context_used=ctx_used,
                context_remaining=max(0, ctx_window - ctx_used),
            )

            metadata: dict[str, Any] = {"tool_name": self.get_name(), "conversation_ready": True}
            parent = continuation_data.get("parent_store_id")
            if parent:
                metadata["parent_store_id"] = parent

            if model_info:
                model_name = model_info.get("model_name")
                if model_name:
                    metadata["model_used"] = model_name
                provider = model_info.get("provider")
                if provider:
                    if isinstance(provider, str):
                        metadata["provider_used"] = provider
                    else:
                        try:
                            metadata["provider_used"] = provider.get_provider_type().value
                        except AttributeError:
                            metadata["provider_used"] = str(provider)

            return ToolOutput(
                status="continuation_available",
                content=content,
                content_type="text",
                continuation_offer=continuation_offer,
                metadata=metadata,
            )
        except Exception:
            return ToolOutput(status="success", content=content, content_type="text")


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
            content = f"Found {len(stores)} store(s):\n\n"
            for entry in stores:
                content += f"- store_id: {entry.get('store_id')}\n"
                content += f"  label: {entry.get('label') or '(none)'}\n"
                content += f"  model: {entry.get('model') or '(unknown)'}\n"
                content += f"  turns: {entry.get('turn_count', 0)}\n"
                content += f"  created: {entry.get('created_at', '?')}\n"
                parent = entry.get("parent_store_id")
                if parent:
                    content += f"  parent: {parent}\n"
                content += "\n"
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
