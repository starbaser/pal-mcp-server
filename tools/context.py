"""
Context store tools — init, store, query, fork, list, read, arm, filelist, and fileread context stores.
"""

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

from mcp.types import TextContent
from pydantic import Field

from systemprompts import CONTEXT_PROMPT
from tools.shared.base_models import COMMON_FIELD_DESCRIPTIONS, ToolRequest

from .shared.base_tool import BaseTool
from .simple.base import SimpleTool

logger = logging.getLogger(__name__)

STORE_ID_DESCRIPTION = (
    "Dot-path identifier for a context store node. Root stores use a plain name (e.g. 'myproject'). "
    "Child nodes extend the path with dot notation (e.g. 'myproject.L1', 'myproject.L1.Q1'). "
    "Use ctxlist to discover existing store_ids. Returned by ctxinit, ctxstore, ctxquery, and ctxfork."
)


def _truncate_label(text: str, max_len: int = 80) -> str:
    """Truncate label at a word boundary with ellipsis. Collapses newlines to spaces."""
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    truncated = text[:max_len].rsplit(" ", 1)[0]
    return truncated + "…"


def _natural_sort_key(key: str) -> tuple:
    """Sort key that handles mixed alpha-numeric keys naturally: L1, L2, ..., L10."""
    import re

    parts = re.split(r"(\d+)", key)
    return tuple(int(p) if p.isdigit() else p for p in parts)


def _render_store_tree(store, root_path: str = "", indent: int = 0) -> list[str]:
    """Render a store's node dict as indented markdown link lines."""
    from utils.context_store import StoreNode

    lines: list[str] = []

    def _render_nodes(nodes: dict[str, StoreNode], parent_path: str, depth: int) -> None:
        p = "  " * depth
        for key, node in sorted(nodes.items(), key=lambda kv: _natural_sort_key(kv[0])):
            full_path = f"{parent_path}.{key}"
            etype = node.entry_type
            ts = (node.timestamp or "")[:10]

            if etype == "store":
                if node.label and node.label.startswith(f"{key}:"):
                    label_part = node.label
                elif node.label:
                    label_part = f"{key}. {node.label}"
                else:
                    label_part = key
                prompt_part = f'  "{_truncate_label(node.prompt, 60)}"' if node.prompt else ""
                date_part = f" — {ts}" if ts else ""
                files_part = ""
                if node.files:
                    basenames = ", ".join(os.path.basename(f) for f in node.files)
                    files_part = f" — {basenames}"
                lines.append(f"{p}- [{label_part}](#{full_path}){prompt_part}{date_part}{files_part}")
            elif etype == "query":
                display_key = f".{key}" if key.isdigit() else key
                prompt_part = f'  "{_truncate_label(node.prompt, 60)}"' if node.prompt else ""
                date_part = f" — {ts}" if ts else ""
                lines.append(f"{p}- [{display_key}](#{full_path}){prompt_part}{date_part}")
            elif etype == "fork":
                if node.label and node.label.startswith(f"{key}:"):
                    label_part = node.label
                elif node.label:
                    label_part = f"{key}. {node.label}"
                else:
                    label_part = key
                date_part = f" — {ts}" if ts else ""
                lines.append(f"{p}- [{label_part}](#{full_path}){date_part}")
            elif etype == "tool":
                tool_name = node.tool_name or key
                lines.append(f"{p}- [.{tool_name}](#{full_path})")
            else:
                lines.append(f"{p}- [.{key}](#{full_path})")

            if node.children:
                _render_nodes(node.children, full_path, depth + 1)

    if isinstance(store, dict):
        _render_nodes(store, root_path, indent)
    else:
        _render_nodes(store.children, store.store_id, indent)

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
    absolute_file_paths: Optional[list[str]] = Field(default_factory=list)
    media: Optional[list[str]] = Field(default_factory=list)
    store_id: str = Field(...)


# ---------------------------------------------------------------------------
# Shared base
# ---------------------------------------------------------------------------


class ContextBaseTool(SimpleTool):
    """Shared base for context store tools that call external models."""

    def get_model_category(self):
        from tools.models import ToolModelCategory

        return ToolModelCategory.EXTENDED_REASONING

    def get_default_temperature(self) -> float:
        return 0.0

    def get_default_thinking_mode(self) -> str:
        return "max"

    def get_system_prompt(self) -> str:
        return CONTEXT_PROMPT

    def get_websearch_guidance(self) -> Optional[str]:
        return None

    def get_annotations(self) -> dict:
        return {"readOnlyHint": False, "openWorldHint": False}

    def _resolve_store(self, store_id: str):
        """Load store for a given store_id path. Returns (store, root_store_id).

        Raises KeyError if store not found.
        """
        from utils.context_store import load_store, resolve_store_location

        location = resolve_store_location(store_id)
        if location is None:
            raise KeyError(f'Store "{store_id}" not found.')
        directory, root_id = location
        store = load_store(directory, root_id)
        if store is None:
            raise KeyError(f'Store file not found: "{root_id}".')
        return store, root_id


# ---------------------------------------------------------------------------
# ctxinit
# ---------------------------------------------------------------------------


class CtxInitTool(BaseTool):
    def get_name(self) -> str:
        return "ctxinit"

    def get_description(self) -> str:
        return (
            "Create a new named context store and register it to a project directory. "
            "Run ctxlist first to check for an existing store before creating a new one; "
            "use ctxstore to add context layers after creation."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "store_name": {
                    "type": "string",
                    "description": (
                        "Unique name for the context store (e.g. 'myproject'). "
                        "No dots allowed — dots are reserved for dot-path child notation. "
                        "Becomes the root store_id."
                    ),
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
        return {"readOnlyHint": False, "openWorldHint": False}

    def get_system_prompt(self) -> str:
        return ""

    def get_request_model(self):
        return ToolRequest

    def requires_model(self) -> bool:
        return False

    def get_model_category(self):
        from tools.models import ToolModelCategory

        return ToolModelCategory.FAST_RESPONSE

    async def prepare_prompt(self, _request: ToolRequest) -> str:
        return ""

    def format_response(self, response: str, _request: ToolRequest, _model_info: Optional[dict] = None) -> str:
        return response

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        from tools.models import ToolOutput
        from utils.context_store import StoreRoot, save_store, update_index

        store_name = arguments.get("store_name", "")
        directory = arguments.get("directory", "")

        if "." in store_name:
            error = ToolOutput(
                status="error",
                content="store_name must not contain dots. Dots are reserved for path notation.",
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        from utils.context_store import resolve_store_location

        existing = resolve_store_location(store_name)
        if existing is not None:
            error = ToolOutput(
                status="error",
                content=f'Store "{store_name}" already exists.',
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        store = StoreRoot(
            store_id=store_name,
            directory=directory,
            created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        save_store(store)
        update_index(directory, store_name)

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
    def get_name(self) -> str:
        return "ctxstore"

    def get_description(self) -> str:
        return (
            "Add a context layer to an existing store, embedding files and prose for an external model to process. "
            "Each call appends a new numbered layer (L1, L2, …); seed 4–6 key files per layer for best results. "
            "Use ctxinit to create a store first, then ctxquery to retrieve stored context."
        )

    def get_request_model(self):
        return CtxStoreRequest

    def get_tool_fields(self) -> dict[str, dict[str, Any]]:
        return {
            "prompt": {
                "type": "string",
                "description": "Prose context or instructions for the external model to process and store.",
            },
            "absolute_file_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": COMMON_FIELD_DESCRIPTIONS["absolute_file_paths"],
            },
            "context_label": {
                "type": "string",
                "description": "Short human-readable label for this layer (e.g. 'session 3 conversation').",
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
                "prompt": {
                    "type": "string",
                    "description": "Prose context or instructions for the external model to process and store.",
                },
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
                    "description": "Short human-readable label for this layer (e.g. 'session 3 conversation').",
                },
                "store_id": {"type": "string", "description": STORE_ID_DESCRIPTION},
                "model": self.get_model_field_schema(),
                "temperature": {
                    "type": "number",
                    "description": COMMON_FIELD_DESCRIPTIONS["temperature"],
                    "minimum": 0,
                    "maximum": 1,
                },
                "thinking_mode": {
                    "type": "string",
                    "enum": ["minimal", "low", "medium", "high", "max"],
                    "description": COMMON_FIELD_DESCRIPTIONS["thinking_mode"],
                    "default": "max",
                },
            },
            "required": required_fields,
            "additionalProperties": False,
        }

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        from tools.models import ToolOutput

        store_id = arguments.get("store_id", "")
        if not store_id:
            error = ToolOutput(
                status="error",
                content="ctxstore requires a store_id. Use ctxinit to create a store first.",
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        try:
            store, _ = self._resolve_store(store_id)
        except KeyError as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        from utils.context_builder import build_context_from_ancestry
        from utils.context_store import walk_ancestry

        ancestors = walk_ancestry(store, store_id)
        self._injected_history = build_context_from_ancestry(ancestors)
        self._store = store
        self._store_id = store_id

        # Estimate context usage for continuation_offer reporting
        arguments["_context_used"] = len(self._injected_history) // 4 if self._injected_history else 0

        arguments.pop("continuation_id", None)
        arguments["thinking_mode"] = "max"
        arguments["temperature"] = 0

        return await super().execute(arguments)

    async def prepare_prompt(self, request: CtxStoreRequest) -> str:
        user_content = self.handle_prompt_file_with_fallback(request)

        files = self.get_request_files(request)
        file_section = ""
        if files:
            file_content, processed = self._prepare_file_content_for_prompt(
                files,
                None,
                "Context files",
                model_context=getattr(self, "_model_context", None),
            )
            self._actually_processed_files = processed
            if file_content:
                file_section = f"\n\n=== CONTEXT FILES ===\n{file_content}\n=== END CONTEXT FILES ==="

        label_line = f"\n[Label: {request.context_label}]" if request.context_label else ""
        base = f"=== CONTEXT LAYER SUBMISSION ==={label_line}\n\n{user_content}{file_section}"

        injected = getattr(self, "_injected_history", "")
        full_prompt = f"{injected}\n\n{base}" if injected else base
        self._last_full_prompt = full_prompt
        return full_prompt

    def format_response(self, response: str, _request: CtxStoreRequest, _model_info: Optional[dict] = None) -> str:
        self._last_raw_response = response
        return f"{response}\n\n---\n\nAGENT'S TURN: Context layer stored. Use the store_id to add more layers or query this store."

    def _create_continuation_offer(self, _request, _model_info: Optional[dict] = None):
        from utils.context_store import get_next_key

        store = getattr(self, "_store", None)
        store_id = getattr(self, "_store_id", None)
        if store is None or store_id is None:
            return None

        self._next_key = get_next_key(store, store_id, "L")
        root_id = store.store_id
        if store_id == root_id:
            self._new_store_path = f"{root_id}.{self._next_key}"
        else:
            self._new_store_path = f"{store_id}.{self._next_key}"

        context_window, context_used = self._get_context_token_info()
        return {
            "continuation_id": self._new_store_path,
            "context_window": context_window,
            "context_used": context_used,
            "note": f"Layer stored at {self._new_store_path}.",
        }

    def _record_assistant_turn(
        self, _continuation_id: str, response_text: str, request, model_info: Optional[dict]
    ) -> None:
        from utils.context_store import StoreNode, add_child, save_store

        store = getattr(self, "_store", None)
        store_id = getattr(self, "_store_id", None)
        next_key = getattr(self, "_next_key", None)
        if store is None or store_id is None or next_key is None:
            logger.warning("ctxstore: missing store state in _record_assistant_turn, skipping write")
            return

        raw = getattr(self, "_last_raw_response", response_text)
        full_prompt = getattr(self, "_last_full_prompt", "")
        model_name = model_info.get("model_name") if model_info else None

        # Build content blob: full API exchange (prompt with files + model response)
        content = f"{full_prompt}\n\n---\n\n{raw}" if full_prompt else raw

        node = StoreNode(
            entry_type="store",
            label=getattr(request, "context_label", None),
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            model=model_name,
            files=self.get_request_files(request),
            prompt=self.get_request_prompt(request),
            response=raw,
            content=content,
        )

        add_child(store, store_id, next_key, node)
        save_store(store)


# ---------------------------------------------------------------------------
# ctxquery
# ---------------------------------------------------------------------------


class CtxQueryTool(ContextBaseTool):
    def get_name(self) -> str:
        return "ctxquery"

    def get_description(self) -> str:
        return (
            "Ask a question against a context store — sends all stored layers to an external model for analysis. "
            "Use for targeted questions, not context revival. Each query is recorded as a child node. "
            "For context revival, prefer ctxread (metadata + summary) + ctxfilelist → ctxfileread (selective file content)."
        )

    def get_request_model(self):
        return CtxQueryRequest

    def get_tool_fields(self) -> dict[str, dict[str, Any]]:
        return {
            "prompt": {
                "type": "string",
                "description": "Question or instruction to run against the context store.",
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
                "prompt": {
                    "type": "string",
                    "description": "Question or instruction to run against the context store.",
                },
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
                "store_id": {"type": "string", "description": STORE_ID_DESCRIPTION},
                "model": self.get_model_field_schema(),
                "temperature": {
                    "type": "number",
                    "description": COMMON_FIELD_DESCRIPTIONS["temperature"],
                    "minimum": 0,
                    "maximum": 1,
                },
                "thinking_mode": {
                    "type": "string",
                    "enum": ["minimal", "low", "medium", "high", "max"],
                    "description": COMMON_FIELD_DESCRIPTIONS["thinking_mode"],
                    "default": "max",
                },
            },
            "required": required_fields,
            "additionalProperties": False,
        }

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        from tools.models import ToolOutput

        store_id = arguments.get("store_id", "")
        if not store_id:
            error = ToolOutput(
                status="error",
                content="ctxquery requires a store_id. Create a store with ctxinit first.",
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        try:
            store, _ = self._resolve_store(store_id)
        except KeyError as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        from utils.context_store import resolve_node, resolve_root_alias

        try:
            store_id = resolve_root_alias(store, store_id)
        except KeyError as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        node = resolve_node(store, store_id)

        if node is not None and node.entry_type in ("store", "fork", "query", "tool"):
            self._parent_path = store_id
            self._child_prefix = "Q"
            self._child_entry_type = "query"
        else:
            error = ToolOutput(
                status="error",
                content=f"Cannot resolve store path: {store_id}",
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        from utils.context_builder import build_context_from_ancestry
        from utils.context_store import walk_ancestry

        ancestors = walk_ancestry(store, store_id)

        # Root-level query: walk_ancestry returns [] because there's no path to traverse.
        # Collect all L-children (layers) as the context — this is "query the whole store".
        if not ancestors and store_id == store.store_id:
            layers = [(k, v) for k, v in store.children.items() if k.startswith("L") and k[1:].isdigit()]
            layers.sort(key=lambda kv: int(kv[0][1:]))
            ancestors = [node for _, node in layers]

        self._injected_history = build_context_from_ancestry(ancestors)
        self._store = store
        self._store_id = store_id

        arguments["_context_used"] = len(self._injected_history) // 4 if self._injected_history else 0

        arguments.pop("continuation_id", None)
        arguments["thinking_mode"] = "max"
        arguments["temperature"] = 0

        return await super().execute(arguments)

    async def prepare_prompt(self, request: CtxQueryRequest) -> str:
        user_content = self.handle_prompt_file_with_fallback(request)

        files = self.get_request_files(request)
        file_section = ""
        if files:
            file_content, processed = self._prepare_file_content_for_prompt(
                files,
                None,
                "Context files",
                model_context=getattr(self, "_model_context", None),
            )
            self._actually_processed_files = processed
            if file_content:
                file_section = f"\n\n=== CONTEXT FILES ===\n{file_content}\n=== END CONTEXT FILES ==="

        injected = getattr(self, "_injected_history", "")
        base = f"=== CONTEXT STORE QUERY ===\n\n{user_content}{file_section}"

        full_prompt = f"{injected}\n\n{base}" if injected else base
        self._last_full_prompt = full_prompt
        return full_prompt

    def format_response(self, response: str, _request: CtxQueryRequest, _model_info: Optional[dict] = None) -> str:
        self._last_raw_response = response
        return f"{response}\n\n---\n\nAGENT'S TURN: Evaluate this response from the context store alongside your own analysis."

    def _create_continuation_offer(self, _request, _model_info: Optional[dict] = None):
        from utils.context_store import get_next_key

        store = getattr(self, "_store", None)
        parent_path = getattr(self, "_parent_path", None)
        child_prefix = getattr(self, "_child_prefix", "Q")
        if store is None or parent_path is None:
            return None

        self._next_key = get_next_key(store, parent_path, child_prefix)
        self._new_store_path = f"{parent_path}.{self._next_key}"

        context_window, context_used = self._get_context_token_info()
        return {
            "continuation_id": self._new_store_path,
            "context_window": context_window,
            "context_used": context_used,
            "note": f"Query recorded at {self._new_store_path}.",
        }

    def _record_assistant_turn(
        self, _continuation_id: str, response_text: str, request, model_info: Optional[dict]
    ) -> None:
        from utils.context_store import StoreNode, add_child, save_store

        store = getattr(self, "_store", None)
        parent_path = getattr(self, "_parent_path", None)
        next_key = getattr(self, "_next_key", None)
        child_entry_type = getattr(self, "_child_entry_type", "query")

        if store is None or parent_path is None or next_key is None:
            logger.warning("ctxquery: missing store state in _record_assistant_turn, skipping write")
            return

        raw = getattr(self, "_last_raw_response", response_text)
        full_prompt = getattr(self, "_last_full_prompt", "")
        model_name = model_info.get("model_name") if model_info else None
        prompt = self.get_request_prompt(request)

        content = f"{full_prompt}\n\n---\n\n{raw}" if full_prompt else raw

        node = StoreNode(
            entry_type=child_entry_type,
            label=_truncate_label(prompt) if prompt else None,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            model=model_name,
            files=self.get_request_files(request),
            prompt=prompt,
            response=raw,
            content=content,
        )

        add_child(store, parent_path, next_key, node)
        save_store(store)


# ---------------------------------------------------------------------------
# ctxfork
# ---------------------------------------------------------------------------


class CtxForkTool(BaseTool):
    def get_name(self) -> str:
        return "ctxfork"

    def get_description(self) -> str:
        return (
            "Create a fork point in a context store, branching from any node to explore alternatives "
            "without disrupting the main lineage. Returns a new store_id for the fork branch. "
            "Use ctxlist to find the store_id to fork from; use ctxstore or ctxquery with the returned fork store_id."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "store_id": {
                    "type": "string",
                    "description": STORE_ID_DESCRIPTION,
                },
                "label": {
                    "type": "string",
                    "description": "Optional short label for the fork point (e.g. 'alt-approach-A').",
                },
            },
            "required": ["store_id"],
            "additionalProperties": False,
        }

    def get_annotations(self) -> dict:
        return {"readOnlyHint": False, "openWorldHint": False}

    def get_system_prompt(self) -> str:
        return ""

    def get_request_model(self):
        return ToolRequest

    def requires_model(self) -> bool:
        return False

    def get_model_category(self):
        from tools.models import ToolModelCategory

        return ToolModelCategory.FAST_RESPONSE

    async def prepare_prompt(self, _request: ToolRequest) -> str:
        return ""

    def format_response(self, response: str, _request: ToolRequest, _model_info: Optional[dict] = None) -> str:
        return response

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        from tools.models import ToolOutput
        from utils.context_store import (
            StoreNode,
            add_child,
            get_next_key,
            load_store,
            resolve_store_location,
            save_store,
        )

        store_id = arguments.get("store_id", "")
        label = arguments.get("label")

        location = resolve_store_location(store_id)
        if location is None:
            error = ToolOutput(status="error", content=f'Store "{store_id}" not found.', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        directory, root_id = location
        store = load_store(directory, root_id)
        if store is None:
            error = ToolOutput(status="error", content=f'Store file not found: "{root_id}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        from utils.context_store import resolve_root_alias

        try:
            store_id = resolve_root_alias(store, store_id)
        except KeyError as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        next_key = get_next_key(store, store_id, "F")
        fork_node = StoreNode(
            entry_type="fork",
            label=label,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        new_path = add_child(store, store_id, next_key, fork_node)
        save_store(store)

        tool_output = ToolOutput(
            status="success",
            content=(
                f"Fork created.\n\n"
                f"store_id: {new_path}\n\n"
                f"Use this store_id to build a new context branch from this point."
            ),
            content_type="text",
            metadata={"store_id": new_path, "parent_store_id": store_id},
        )
        return [TextContent(type="text", text=tool_output.model_dump_json())]


# ---------------------------------------------------------------------------
# ctxlist
# ---------------------------------------------------------------------------


class CtxListTool(BaseTool):
    def get_name(self) -> str:
        return "ctxlist"

    def get_description(self) -> str:
        return (
            "List context stores and their full node trees, returning store_ids needed for ctxstore, ctxquery, "
            "ctxread, ctxfork, and ctxarm. Filter by directory to scope to a project, "
            "or pass store_id to drill into a specific subtree."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": (
                        "Absolute path to filter stores by project directory. "
                        "Omit to list all stores across all projects."
                    ),
                },
                "store_id": {
                    "type": "string",
                    "description": (
                        "Show only the subtree rooted at this node (e.g. 'myproject' or 'myproject.L2'). "
                        "Use ctxread for the full content of a single node."
                    ),
                },
            },
            "required": [],
            "additionalProperties": False,
        }

    def get_annotations(self) -> dict:
        return {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False}

    def get_system_prompt(self) -> str:
        return ""

    def get_request_model(self):
        return ToolRequest

    def requires_model(self) -> bool:
        return False

    def get_model_category(self):
        from tools.models import ToolModelCategory

        return ToolModelCategory.FAST_RESPONSE

    async def prepare_prompt(self, _request: ToolRequest) -> str:
        return ""

    def format_response(self, response: str, _request: ToolRequest, _model_info: Optional[dict] = None) -> str:
        return response

    def _count_l_children(self, children: dict) -> int:
        return sum(1 for k in children if k.startswith("L") and k[1:].isdigit())

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        from tools.models import ToolOutput
        from utils.context_store import list_stores, load_store, resolve_node, resolve_store_location

        directory = arguments.get("directory")
        store_id = arguments.get("store_id")

        if store_id:
            location = resolve_store_location(store_id)
            if location is None:
                error = ToolOutput(status="error", content=f'Store "{store_id}" not found.', content_type="text")
                return [TextContent(type="text", text=error.model_dump_json())]

            dir_, root_id = location
            store = load_store(dir_, root_id)
            if store is None:
                error = ToolOutput(status="error", content=f'Store file not found: "{root_id}".', content_type="text")
                return [TextContent(type="text", text=error.model_dump_json())]

            node = resolve_node(store, store_id)
            if node is None and store_id == store.store_id:
                n_layers = self._count_l_children(store.children)
                lines = [f"{store.store_id}  ({n_layers} layers)"]
                lines.extend(_render_store_tree(store, indent=1))
            elif node is not None:
                lines = [f"{store_id}"]
                lines.extend(_render_store_tree(node.children, root_path=store_id, indent=1))
            else:
                lines = [f"Node not found: {store_id}"]

            content = "\n".join(lines)
        else:
            stores = list_stores(directory)
            if stores:
                lines = []
                for store in stores:
                    n_layers = self._count_l_children(store.children)
                    lines.append(f"{store.store_id}  ({n_layers} layers)")
                    lines.extend(_render_store_tree(store, indent=1))
                content = "\n".join(lines)
            else:
                scope = f" for directory '{directory}'" if directory else ""
                content = f"No context stores found{scope}."

        tool_output = ToolOutput(
            status="success",
            content=content,
            content_type="text",
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
            "Read a context store node's metadata, prompt, and response — the primary tool for context revival. "
            "Returns label, model, timestamp, file list, prompt text, and response text. "
            "After reading, use ctxfilelist to see attached files, then ctxfileread to selectively load relevant ones."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "store_id": {
                    "type": "string",
                    "description": STORE_ID_DESCRIPTION,
                },
            },
            "required": ["store_id"],
            "additionalProperties": False,
        }

    def get_annotations(self) -> dict:
        return {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False}

    def get_system_prompt(self) -> str:
        return ""

    def get_request_model(self):
        return ToolRequest

    def requires_model(self) -> bool:
        return False

    def get_model_category(self):
        from tools.models import ToolModelCategory

        return ToolModelCategory.FAST_RESPONSE

    async def prepare_prompt(self, _request: ToolRequest) -> str:
        return ""

    def format_response(self, response: str, _request: ToolRequest, _model_info: Optional[dict] = None) -> str:
        return response

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        from tools.models import ToolOutput
        from utils.context_store import load_store, resolve_node, resolve_store_location

        store_id = arguments.get("store_id", "")

        location = resolve_store_location(store_id)
        if location is None:
            error = ToolOutput(status="error", content=f'Store "{store_id}" not found.', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        directory, root_id = location
        store = load_store(directory, root_id)
        if store is None:
            error = ToolOutput(status="error", content=f'Store file not found: "{root_id}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        from utils.context_store import resolve_root_alias

        try:
            store_id = resolve_root_alias(store, store_id)
        except KeyError as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        node = resolve_node(store, store_id)

        if node is None:
            error = ToolOutput(
                status="error",
                content=f"Node not found: {store_id}",
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]
        else:
            lines = [f"# {store_id}", ""]
            if node.label:
                lines.append(f"**Label:** {node.label}")
            if node.entry_type:
                lines.append(f"**Type:** {node.entry_type}")
            if node.model:
                lines.append(f"**Model:** {node.model}")
            if node.timestamp:
                lines.append(f"**Timestamp:** {node.timestamp}")
            if node.files:
                lines.append("**Files:**")
                for f in node.files:
                    lines.append(f"- {f}")
            if node.prompt:
                lines.extend(["", "## Prompt", "", node.prompt])
            if node.response:
                lines.extend(["", "## Response", "", node.response])
            content = "\n".join(lines)

        tool_output = ToolOutput(
            status="success",
            content=content,
            content_type="text",
            metadata={"store_id": store_id},
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
            "Arm a context store for automatic revival at every session start, "
            "so the SessionStart hook runs ctxlist and ctxquery to restore project context without manual steps. "
            "Pass disarm=true to remove the armed state; use ctxlist to find the store_id to arm."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "store_id": {
                    "type": "string",
                    "description": (
                        "Root store_id to arm for auto-revival (e.g. 'myproject'). "
                        "Must be an existing root store — use ctxlist to confirm."
                    ),
                },
                "directory": {
                    "type": "string",
                    "description": (
                        "Absolute path to the project directory. "
                        "Must match the directory the store was registered to at ctxinit time."
                    ),
                },
                "disarm": {
                    "type": "boolean",
                    "description": "Set true to remove the armed state for this directory. Defaults to false.",
                    "default": False,
                },
            },
            "required": ["store_id", "directory"],
            "additionalProperties": False,
        }

    def get_annotations(self) -> dict:
        return {"readOnlyHint": False, "idempotentHint": True, "openWorldHint": False}

    def get_system_prompt(self) -> str:
        return ""

    def get_request_model(self):
        return ToolRequest

    def requires_model(self) -> bool:
        return False

    def get_model_category(self):
        from tools.models import ToolModelCategory

        return ToolModelCategory.FAST_RESPONSE

    async def prepare_prompt(self, _request: ToolRequest) -> str:
        return ""

    def format_response(self, response: str, _request: ToolRequest, _model_info: Optional[dict] = None) -> str:
        return response

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        from tools.models import ToolOutput
        from utils.context_store import arm_store, disarm_store, load_store, resolve_store_location

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

        location = resolve_store_location(store_id)
        if location is None:
            tool_output = ToolOutput(
                status="error",
                content=f'Store "{store_id}" not found. Use ctxinit to create it first.',
                content_type="text",
            )
            return [TextContent(type="text", text=tool_output.model_dump_json())]

        store_directory, root_id = location
        store = load_store(store_directory, root_id)
        if store is None:
            tool_output = ToolOutput(
                status="error",
                content=f'Store file not found for "{store_id}".',
                content_type="text",
            )
            return [TextContent(type="text", text=tool_output.model_dump_json())]

        if store.directory != directory:
            tool_output = ToolOutput(
                status="error",
                content=(
                    f'Store "{store_id}" is registered to "{store.directory}", '
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


# ---------------------------------------------------------------------------
# ctxrename
# ---------------------------------------------------------------------------


class CtxRenameTool(BaseTool):
    def get_name(self) -> str:
        return "ctxrename"

    def get_description(self) -> str:
        return (
            "Rename a root context store, updating the store file, index, and any armed state atomically. "
            "Use ctxlist to confirm the current store_id before renaming."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "store_id": {
                    "type": "string",
                    "description": "Current root store_id to rename (e.g. 'myproject'). Use ctxlist to confirm it exists.",
                },
                "new_name": {
                    "type": "string",
                    "description": "New name for the store (e.g. 'myproject-v2'). No dots allowed.",
                },
                "directory": {
                    "type": "string",
                    "description": "Absolute path to the project directory the store is associated with.",
                },
            },
            "required": ["store_id", "new_name", "directory"],
            "additionalProperties": False,
        }

    def get_annotations(self) -> dict:
        return {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False}

    def get_system_prompt(self) -> str:
        return ""

    def get_request_model(self):
        return ToolRequest

    def requires_model(self) -> bool:
        return False

    def get_model_category(self):
        from tools.models import ToolModelCategory

        return ToolModelCategory.FAST_RESPONSE

    async def prepare_prompt(self, _request: ToolRequest) -> str:
        return ""

    def format_response(self, response: str, _request: ToolRequest, _model_info: Optional[dict] = None) -> str:
        return response

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        from tools.models import ToolOutput
        from utils.context_store import rename_store

        store_id = arguments.get("store_id", "")
        new_name = arguments.get("new_name", "")
        directory = arguments.get("directory", "")

        if not store_id or not new_name or not directory:
            tool_output = ToolOutput(
                status="error",
                content="store_id, new_name, and directory are all required.",
                content_type="text",
            )
            return [TextContent(type="text", text=tool_output.model_dump_json())]

        try:
            rename_store(directory, store_id, new_name)
        except KeyError as e:
            tool_output = ToolOutput(status="error", content=str(e), content_type="text")
            return [TextContent(type="text", text=tool_output.model_dump_json())]
        except ValueError as e:
            tool_output = ToolOutput(status="error", content=str(e), content_type="text")
            return [TextContent(type="text", text=tool_output.model_dump_json())]

        tool_output = ToolOutput(
            status="success",
            content=(f"Store renamed.\n\nold store_id: {store_id}\nnew store_id: {new_name}\ndirectory: {directory}"),
            content_type="text",
            metadata={"store_id": new_name, "old_store_id": store_id, "directory": directory},
        )
        return [TextContent(type="text", text=tool_output.model_dump_json())]


# ---------------------------------------------------------------------------
# ctxexport
# ---------------------------------------------------------------------------


class CtxExportTool(BaseTool):
    def get_name(self) -> str:
        return "ctxexport"

    def get_description(self) -> str:
        return (
            "Export an entire context store to a self-contained markdown file. "
            "Serializes all layers, metadata, prompts, responses, and file references "
            "into a single portable document suitable for archival, handoffs, or direct file reads."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "store_id": {
                    "type": "string",
                    "description": STORE_ID_DESCRIPTION,
                },
                "output_path": {
                    "type": "string",
                    "description": (
                        "Absolute path for the output markdown file. "
                        "Parent directory must exist. Example: '/home/user/.claude/handoffs/myproject-export.md'"
                    ),
                },
            },
            "required": ["store_id", "output_path"],
            "additionalProperties": False,
        }

    def get_annotations(self) -> dict:
        return {"readOnlyHint": False, "idempotentHint": True, "openWorldHint": False}

    def get_system_prompt(self) -> str:
        return ""

    def get_request_model(self):
        return ToolRequest

    def requires_model(self) -> bool:
        return False

    def get_model_category(self):
        from tools.models import ToolModelCategory

        return ToolModelCategory.FAST_RESPONSE

    async def prepare_prompt(self, _request: ToolRequest) -> str:
        return ""

    def format_response(self, response: str, _request: ToolRequest, _model_info: Optional[dict] = None) -> str:
        return response

    def _render_node(self, path: str, node, depth: int) -> list[str]:
        """Render a single node as markdown sections."""
        from utils.context_store import StoreNode

        node: StoreNode
        heading = "#" * min(depth, 6)
        lines = [f"{heading} {path}", ""]

        meta_parts = []
        if node.label:
            meta_parts.append(f"**Label:** {node.label}")
        if node.entry_type:
            meta_parts.append(f"**Type:** {node.entry_type}")
        if node.model:
            meta_parts.append(f"**Model:** {node.model}")
        if node.timestamp:
            meta_parts.append(f"**Timestamp:** {node.timestamp}")
        if node.tool_name:
            meta_parts.append(f"**Tool:** {node.tool_name}")
        if meta_parts:
            lines.extend(meta_parts)
            lines.append("")

        if node.files:
            lines.append("**Attached files:**")
            for f in node.files:
                lines.append(f"- `{f}`")
            lines.append("")

        if node.prompt:
            lines.extend(["### Prompt", "", node.prompt, ""])
        if node.response:
            lines.extend(["### Response", "", node.response, ""])

        return lines

    def _walk_tree(self, nodes: dict, parent_path: str, depth: int) -> list[str]:
        """Recursively render all nodes depth-first."""
        lines: list[str] = []
        for key in sorted(nodes, key=_natural_sort_key):
            node = nodes[key]
            full_path = f"{parent_path}.{key}"
            lines.extend(self._render_node(full_path, node, depth))
            if node.children:
                lines.extend(self._walk_tree(node.children, full_path, depth + 1))
        return lines

    def _build_toc(self, nodes: dict, parent_path: str, indent: int = 0) -> list[str]:
        """Build a flat table of contents with anchor links."""
        lines: list[str] = []
        pad = "  " * indent
        for key in sorted(nodes, key=_natural_sort_key):
            node = nodes[key]
            full_path = f"{parent_path}.{key}"
            anchor = full_path.lower().replace(".", "")
            ts = (node.timestamp or "")[:10]
            date_part = f" — {ts}" if ts else ""
            lines.append(f"{pad}- [{full_path}](#{anchor}){date_part}")
            if node.children:
                lines.extend(self._build_toc(node.children, full_path, indent + 1))
        return lines

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        from tools.models import ToolOutput
        from utils.context_store import load_store, resolve_store_location

        store_id = arguments.get("store_id", "")
        output_path = arguments.get("output_path", "")

        if not output_path:
            error = ToolOutput(status="error", content="output_path is required.", content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        parent_dir = os.path.dirname(output_path)
        if parent_dir and not os.path.isdir(parent_dir):
            error = ToolOutput(
                status="error",
                content=f"Parent directory does not exist: {parent_dir}",
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        location = resolve_store_location(store_id)
        if location is None:
            error = ToolOutput(status="error", content=f'Store "{store_id}" not found.', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        directory, root_id = location
        store = load_store(directory, root_id)
        if store is None:
            error = ToolOutput(status="error", content=f'Store file not found: "{root_id}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        # Build the markdown document
        doc: list[str] = []

        # Header
        doc.append(f"# Context Store Export: {store.store_id}")
        doc.append("")

        # Store metadata
        doc.append(f"**Directory:** `{store.directory}`")
        doc.append(f"**Created:** {store.created_at}")
        if store.label:
            doc.append(f"**Label:** {store.label}")
        n_layers = sum(1 for k in store.children if k.startswith("L") and k[1:].isdigit())
        doc.append(f"**Layers:** {n_layers}")
        doc.append(f"**Exported:** {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
        doc.append("")

        # Table of contents
        if store.children:
            doc.append("## Table of Contents")
            doc.append("")
            doc.extend(self._build_toc(store.children, store.store_id))
            doc.append("")

        # Separator
        doc.append("---")
        doc.append("")

        # All nodes
        doc.extend(self._walk_tree(store.children, store.store_id, depth=2))

        content = "\n".join(doc)

        try:
            with open(output_path, "w") as f:
                f.write(content)
        except OSError as e:
            error = ToolOutput(status="error", content=f"Failed to write file: {e}", content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        tool_output = ToolOutput(
            status="success",
            content=f"Exported store '{store.store_id}' ({n_layers} layers) to:\n{output_path}",
            content_type="text",
            metadata={"store_id": store_id, "output_path": output_path, "layers": n_layers},
        )
        return [TextContent(type="text", text=tool_output.model_dump_json())]


# ---------------------------------------------------------------------------
# ctxfilelist
# ---------------------------------------------------------------------------


class CtxFileListTool(BaseTool):
    def get_name(self) -> str:
        return "ctxfilelist"

    def get_description(self) -> str:
        return (
            "List all files attached across a context store tree, grouped by node. "
            "Shows which files were seeded into each layer with timestamps and labels. "
            "Part of the context revival flow: ctxread → ctxfilelist → ctxfileread on context-relevant files."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "store_id": {
                    "type": "string",
                    "description": STORE_ID_DESCRIPTION,
                },
            },
            "required": ["store_id"],
            "additionalProperties": False,
        }

    def get_annotations(self) -> dict:
        return {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False}

    def get_system_prompt(self) -> str:
        return ""

    def get_request_model(self):
        return ToolRequest

    def requires_model(self) -> bool:
        return False

    def get_model_category(self):
        from tools.models import ToolModelCategory

        return ToolModelCategory.FAST_RESPONSE

    async def prepare_prompt(self, _request: ToolRequest) -> str:
        return ""

    def format_response(self, response: str, _request: ToolRequest, _model_info: Optional[dict] = None) -> str:
        return response

    def _collect_files(self, nodes: dict, parent_path: str) -> list[tuple[str, str, str, list[str]]]:
        """Recursively collect (node_path, timestamp, label, files) for nodes with files."""
        results: list[tuple[str, str, str, list[str]]] = []
        for key in sorted(nodes, key=_natural_sort_key):
            node = nodes[key]
            full_path = f"{parent_path}.{key}"
            if node.files:
                results.append((full_path, node.timestamp or "", node.label or "", list(node.files)))
            if node.children:
                results.extend(self._collect_files(node.children, full_path))
        return results

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        from tools.models import ToolOutput
        from utils.context_store import load_store, resolve_node, resolve_store_location

        store_id = arguments.get("store_id", "")

        location = resolve_store_location(store_id)
        if location is None:
            error = ToolOutput(status="error", content=f'Store "{store_id}" not found.', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        directory, root_id = location
        store = load_store(directory, root_id)
        if store is None:
            error = ToolOutput(status="error", content=f'Store file not found: "{root_id}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        node = resolve_node(store, store_id)
        if node is None and store_id == store.store_id:
            collected = self._collect_files(store.children, store.store_id)
        elif node is not None:
            collected = self._collect_files(node.children, store_id)
            if node.files:
                collected.insert(0, (store_id, node.timestamp or "", node.label or "", list(node.files)))
        else:
            error = ToolOutput(status="error", content=f"Node not found: {store_id}", content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        if not collected:
            content = f"No files attached to any node in '{store_id}'."
        else:
            total_files = sum(len(files) for _, _, _, files in collected)
            lines = [f"**{total_files} files across {len(collected)} nodes in '{store_id}':**", ""]
            for node_path, timestamp, label, files in collected:
                date = timestamp[:10] if timestamp else "unknown"
                label_part = f' — "{_truncate_label(label, 60)}"' if label else ""
                lines.append(f"**{node_path}**{label_part} — {date}")
                for f in files:
                    lines.append(f"  {f}")
                lines.append("")
            content = "\n".join(lines)

        tool_output = ToolOutput(status="success", content=content, content_type="text")
        return [TextContent(type="text", text=tool_output.model_dump_json())]


# ---------------------------------------------------------------------------
# ctxfileread
# ---------------------------------------------------------------------------


class CtxFileReadTool(BaseTool):
    def get_name(self) -> str:
        return "ctxfileread"

    def get_description(self) -> str:
        return (
            "Read a specific file from a context store node's stored content blob (falls back to disk). "
            "Use after ctxfilelist to selectively load only the files relevant to the current task. "
            "This targeted approach avoids the token cost of ctxquery, which sends all layers to an external model."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "store_id": {
                    "type": "string",
                    "description": STORE_ID_DESCRIPTION,
                },
                "file_path": {
                    "type": "string",
                    "description": "Absolute path of the file to read (must match a path in the node's files list).",
                },
            },
            "required": ["store_id", "file_path"],
            "additionalProperties": False,
        }

    def get_annotations(self) -> dict:
        return {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False}

    def get_system_prompt(self) -> str:
        return ""

    def get_request_model(self):
        return ToolRequest

    def requires_model(self) -> bool:
        return False

    def get_model_category(self):
        from tools.models import ToolModelCategory

        return ToolModelCategory.FAST_RESPONSE

    async def prepare_prompt(self, _request: ToolRequest) -> str:
        return ""

    def format_response(self, response: str, _request: ToolRequest, _model_info: Optional[dict] = None) -> str:
        return response

    def _find_nodes_with_file(self, nodes: dict, parent_path: str, file_path: str) -> list[tuple[str, str, "Any"]]:
        """Recursively find nodes referencing file_path. Returns (node_path, timestamp, node)."""
        from utils.context_store import StoreNode

        results: list[tuple[str, str, StoreNode]] = []
        for key in sorted(nodes, key=_natural_sort_key):
            node = nodes[key]
            full_path = f"{parent_path}.{key}"
            if node.files and file_path in node.files:
                results.append((full_path, node.timestamp or "", node))
            if node.children:
                results.extend(self._find_nodes_with_file(node.children, full_path, file_path))
        return results

    def _extract_file_from_blob(self, content_blob: str, file_path: str) -> Optional[str]:
        """Extract a single file's content from the node content blob."""
        pattern = (
            r"--- BEGIN FILE: "
            + re.escape(file_path)
            + r" \(Last modified:.*?\) ---\n"
            + r"(.*?)\n"
            + r"--- END FILE: "
            + re.escape(file_path)
            + r" ---"
        )
        match = re.search(pattern, content_blob, re.DOTALL)
        if match:
            return match.group(1)
        return None

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        from tools.models import ToolOutput
        from utils.context_store import load_store, resolve_node, resolve_store_location

        store_id = arguments.get("store_id", "")
        file_path = arguments.get("file_path", "")

        if not file_path:
            error = ToolOutput(status="error", content="file_path is required.", content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        location = resolve_store_location(store_id)
        if location is None:
            error = ToolOutput(status="error", content=f'Store "{store_id}" not found.', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        directory, root_id = location
        store = load_store(directory, root_id)
        if store is None:
            error = ToolOutput(status="error", content=f'Store file not found: "{root_id}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        # Search from root or subtree
        node = resolve_node(store, store_id)
        if node is None and store_id == store.store_id:
            matches = self._find_nodes_with_file(store.children, store.store_id, file_path)
        elif node is not None:
            matches = self._find_nodes_with_file(node.children, store_id, file_path)
            if node.files and file_path in node.files:
                matches.insert(0, (store_id, node.timestamp or "", node))
        else:
            error = ToolOutput(status="error", content=f"Node not found: {store_id}", content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        if not matches:
            error = ToolOutput(
                status="error",
                content=f"File '{file_path}' not found in any node under '{store_id}'. Use ctxfilelist to discover attached files.",
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        # Use most recent node
        matches.sort(key=lambda m: m[1], reverse=True)
        node_path, timestamp, target_node = matches[0]

        # Try extracting from content blob
        extracted = None
        if target_node.content:
            extracted = self._extract_file_from_blob(target_node.content, file_path)

        source = "store"
        if extracted is None:
            # Fall back to reading from disk
            try:
                with open(file_path, encoding="utf-8", errors="replace") as f:
                    extracted = f.read()
                source = "disk"
            except OSError as e:
                error = ToolOutput(
                    status="error",
                    content=f"File content not in store blob and cannot read from disk: {e}",
                    content_type="text",
                )
                return [TextContent(type="text", text=error.model_dump_json())]

        header = f"# {os.path.basename(file_path)}\n\n"
        header += f"**Source:** {source} (node {node_path}, {timestamp[:10] if timestamp else 'unknown'})\n"
        header += f"**Path:** {file_path}\n"
        if len(matches) > 1:
            other_nodes = ", ".join(m[0] for m in matches[1:])
            header += f"**Also in:** {other_nodes}\n"
        header += "\n---\n\n"

        content = header + extracted

        tool_output = ToolOutput(
            status="success",
            content=content,
            content_type="text",
            metadata={"store_id": store_id, "file_path": file_path, "source": source, "node_path": node_path},
        )
        return [TextContent(type="text", text=tool_output.model_dump_json())]
