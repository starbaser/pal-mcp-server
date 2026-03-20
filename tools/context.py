"""
Context silo tools — init, store, query, list, read, and arm context stores.
"""

import logging
import os
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


def _truncate_label(text: str, max_len: int = 80) -> str:
    """Truncate label at a word boundary with ellipsis."""
    if len(text) <= max_len:
        return text
    truncated = text[:max_len].rsplit(" ", 1)[0]
    return truncated + "…"


def _render_tree(nodes: list[dict], indent: int = 0) -> list[str]:
    """Recursively render tree nodes as indented dash lines."""
    lines: list[str] = []
    prefix = "  " * indent + "- "
    for node in nodes:
        sid = node.get("store_id", "?")
        etype = node.get("entry_type", "?")
        label = _truncate_label(node.get("label") or "(none)")

        suffix = ""
        if etype == "store" and node.get("layer_count", 0) > 0:
            n = node["layer_count"]
            suffix = f"  ({n} layer{'s' if n != 1 else ''})"
        elif etype == "query" and node.get("follow_up_count", 0) > 0:
            n = node["follow_up_count"]
            suffix = f"  ({n} follow-up{'s' if n != 1 else ''})"
        elif etype == "tool" and node.get("tool_name"):
            suffix = f"  (tool: {node['tool_name']})"

        lines.append(f'{prefix}{sid}  [{etype}]  "{label}"{suffix}')
        lines.extend(_render_tree(node.get("children", []), indent + 1))
    return lines


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
            label=_truncate_label(prompt) if prompt else None,
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


# ---------------------------------------------------------------------------
# ctxtree
# ---------------------------------------------------------------------------


class CtxTreeTool(BaseTool):
    def get_name(self) -> str:
        return "ctxtree"

    def get_description(self) -> str:
        return "Show context stores as an indented tree, grouped by parent-child relationships."

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "Absolute path to filter stores by project directory. Omit to show all stores.",
                },
            },
            "required": [],
            "additionalProperties": False,
        }

    def get_annotations(self) -> dict:
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
        from utils.context_registry import build_store_tree, list_stores

        directory = arguments.get("directory")
        stores = list_stores(directory)

        if stores:
            roots = build_store_tree(stores)
            content = f"Found {len(stores)} node(s):\n\n" + "\n".join(_render_tree(roots))
        else:
            scope = f" for directory '{directory}'" if directory else ""
            content = f"No context stores found{scope}."

        tool_output = ToolOutput(
            status="success",
            content=content,
            content_type="text",
            metadata={"store_count": len(stores), "directory_filter": directory},
        )
        return [TextContent(type="text", text=tool_output.model_dump_json())]


# ---------------------------------------------------------------------------
# ctxread
# ---------------------------------------------------------------------------


class CtxReadTool(BaseTool):
    def get_name(self) -> str:
        return "ctxread"

    def get_description(self) -> str:
        return (
            "Read the content of a context store node. Without a page number, returns a table of contents. "
            "With a page number, returns the full prompt and response for that layer."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "store_id": {
                    "type": "string",
                    "description": "The store_id to read. Must exist in the context registry.",
                },
                "page": {
                    "type": "integer",
                    "description": "1-indexed page number. Each page is one prompt+response pair. Omit for table of contents.",
                    "minimum": 1,
                },
            },
            "required": ["store_id"],
            "additionalProperties": False,
        }

    def get_annotations(self) -> dict:
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

    def _get_page_label(self, page_turns: list, entry_label: str | None) -> str:
        """Extract the label for a page from the assistant turn's metadata, falling back to entry label."""
        if len(page_turns) >= 2:
            meta = page_turns[1].model_metadata or {}
            label = meta.get("context_label")
            if label:
                return label
        return entry_label or "(none)"

    def _get_page_date(self, page_turns: list) -> str:
        """Extract a short date string from the user turn's timestamp."""
        if page_turns:
            ts = page_turns[0].timestamp or ""
            return ts[:10] if len(ts) >= 10 else ts
        return "unknown"

    def _get_page_model(self, page_turns: list) -> str:
        """Get the model name from the assistant turn."""
        if len(page_turns) >= 2:
            return page_turns[1].model_name or "unknown"
        return "unknown"

    def _get_page_files(self, page_turns: list) -> list[str]:
        """Get file paths from the user turn."""
        if page_turns and page_turns[0].files:
            return page_turns[0].files
        return []

    def _render_toc(self, store_id: str, entry: dict, pages: list) -> str:
        """Render the table of contents in markdown TOC format."""
        total = len(pages)
        etype = entry.get("entry_type", "?")
        directory = entry.get("directory", "")

        lines = [
            f"## {store_id}",
            "",
            f"**Type:** {etype} | **Pages:** {total}  ",
            f"**Directory:** {directory}",
            "",
            "## Table of Contents",
            "",
        ]

        for i, page_turns in enumerate(pages, 1):
            label = _truncate_label(self._get_page_label(page_turns, entry.get("label")))
            date = self._get_page_date(page_turns)
            model = self._get_page_model(page_turns)
            files = self._get_page_files(page_turns)

            anchor = f"{store_id}.p{i}"
            lines.append(f"- [{i}. {label}](#{anchor}) — {date}, {model}")
            if files:
                basenames = ", ".join(os.path.basename(f) for f in files)
                lines.append(f"  - Files: {basenames}")

        return "\n".join(lines)

    def _render_page(self, store_id: str, page_num: int, total: int, page_turns: list, entry_label: str | None) -> str:
        """Render a single page with full prompt and response in markdown format."""
        label = self._get_page_label(page_turns, entry_label)
        model = self._get_page_model(page_turns)
        date = self._get_page_date(page_turns)
        files = self._get_page_files(page_turns)

        lines = [
            f"# Page {page_num} of {total} — {store_id}",
            "",
            f"**Label:** {label}",
            f"**Model:** {model}",
            f"**Timestamp:** {date}",
        ]

        if files:
            lines.append("")
            lines.append("**Files:**")
            for f in files:
                lines.append(f"- {f}")

        user_turn = page_turns[0]
        lines.append("")
        lines.append("## Prompt")
        lines.append("")
        lines.append(user_turn.content)

        if len(page_turns) >= 2:
            lines.append("")
            lines.append("## Response")
            lines.append("")
            lines.append(page_turns[1].content)
        else:
            lines.append("")
            lines.append("*(response pending or incomplete)*")

        return "\n".join(lines)

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        from tools.models import ToolOutput
        from utils.context_registry import get_store_entry, resolve_thread_id
        from utils.conversation_memory import get_thread

        store_id = arguments.get("store_id", "")
        page = arguments.get("page")

        thread_id = resolve_thread_id(store_id)
        if not thread_id:
            error = ToolOutput(status="error", content=f'Store "{store_id}" not found.', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        entry = get_store_entry(store_id) or {}

        thread = get_thread(thread_id)
        if not thread:
            error = ToolOutput(
                status="error",
                content=(
                    f'Thread data unavailable for "{store_id}". '
                    "The store exists in the registry but the thread was not found in memory."
                ),
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        if not thread.turns:
            error = ToolOutput(
                status="error",
                content=f'Store "{store_id}" exists but has no recorded turns.',
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        # Group turns into pages (each user+assistant pair = 1 page)
        pages: list[list] = []
        for i in range(0, len(thread.turns), 2):
            pages.append(thread.turns[i : i + 2])
        total_pages = len(pages)

        if page is None:
            content = self._render_toc(store_id, entry, pages)
        else:
            if page < 1 or page > total_pages:
                error = ToolOutput(
                    status="error",
                    content=f"Page {page} does not exist. Store has {total_pages} page(s).",
                    content_type="text",
                )
                return [TextContent(type="text", text=error.model_dump_json())]
            content = self._render_page(store_id, page, total_pages, pages[page - 1], entry.get("label"))

        tool_output = ToolOutput(
            status="success",
            content=content,
            content_type="text",
            metadata={"store_id": store_id, "total_pages": total_pages, "page": page},
        )
        return [TextContent(type="text", text=tool_output.model_dump_json())]


# ---------------------------------------------------------------------------
# ctxarm
# ---------------------------------------------------------------------------


class CtxArmTool(BaseTool):
    def get_name(self) -> str:
        return "ctxarm"

    def get_description(self) -> str:
        return (
            "Arm a context store for automatic revival at every session start. "
            "On each new Claude session for this directory, revival fires automatically "
            "via SessionStart hook, running ctxlist and ctxquery to restore project context. "
            "Pass disarm=true to remove the armed state."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "store_id": {
                    "type": "string",
                    "description": "The store_id to arm for auto-revival. Must exist in the context registry.",
                },
                "directory": {
                    "type": "string",
                    "description": "Absolute path to the project directory. Must match the store's registered directory.",
                },
                "disarm": {
                    "type": "boolean",
                    "description": "If true, removes the armed state for this directory.",
                    "default": False,
                },
            },
            "required": ["store_id", "directory"],
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
        from utils.context_registry import arm_store, disarm_store, get_store_entry

        store_id = arguments.get("store_id", "")
        directory = arguments.get("directory", "")
        disarm = arguments.get("disarm", False)

        if disarm:
            disarm_store(directory)
            tool_output = ToolOutput(
                status="success",
                content=f"Disarmed: '{directory}'. Revival will no longer fire on SessionStart.",
                content_type="text",
                metadata={"store_id": store_id, "directory": directory, "armed": False},
            )
            return [TextContent(type="text", text=tool_output.model_dump_json())]

        entry = get_store_entry(store_id)
        if not entry:
            tool_output = ToolOutput(
                status="error",
                content=f'Store "{store_id}" not found. Use ctxinit to create it first.',
                content_type="text",
            )
            return [TextContent(type="text", text=tool_output.model_dump_json())]

        if entry.get("directory") != directory:
            tool_output = ToolOutput(
                status="error",
                content=(
                    f'Store "{store_id}" is registered to "{entry.get("directory")}", '
                    f'not "{directory}". Use the correct directory.'
                ),
                content_type="text",
            )
            return [TextContent(type="text", text=tool_output.model_dump_json())]

        arm_store(directory, store_id)
        tool_output = ToolOutput(
            status="success",
            content=(
                f"Armed: store '{store_id}' will auto-revive on every SessionStart for '{directory}'.\n\n"
                f"Revival sequence fires automatically — no further action needed.\n"
                f'To disarm: ctxarm(store_id="{store_id}", directory="{directory}", disarm=true)'
            ),
            content_type="text",
            metadata={"store_id": store_id, "directory": directory, "armed": True},
        )
        return [TextContent(type="text", text=tool_output.model_dump_json())]
