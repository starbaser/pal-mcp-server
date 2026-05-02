"""
PALTree and PALNode tools — init, add, query, fork, list, read, filelist, and fileread PALTrees.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from mcp.types import TextContent
from pydantic import Field

from systemprompts import CONTEXT_PROMPT
from tools.shared.base_models import COMMON_FIELD_DESCRIPTIONS, ToolRequest

from .shared.base_tool import BaseTool
from .simple.base import SimpleTool

logger = logging.getLogger(__name__)

TREE_PATH_DESCRIPTION = (
    "Identifies a PALTree. Canonical: '/abs/path:tree-name'. "
    "Shorthand: 'tree-name' (resolved if unique). Use treelist to discover trees."
)

NODE_PATH_DESCRIPTION = (
    "Dot-path to a node within the tree (e.g. 'L0', 'L0.Q0', 'L3.F1.Q2'). "
    "Use 'L' (no number) as shorthand for the last layer."
)


def _truncate_label(text: str, max_len: int = 80) -> str:
    """Truncate label at a word boundary with ellipsis. Collapses newlines to spaces."""
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    truncated = text[:max_len].rsplit(" ", 1)[0]
    return truncated + "…"


def _render_tree(tree, root_path: str = "", indent: int = 0, tlog=None) -> list[str]:
    """Render a tree's node dict as indented markdown link lines.

    Uses iter_dfs from utils.palstore for consistent traversal.
    Optionally populates a TraversalLog.
    """
    from utils.palstore import iter_dfs

    if isinstance(tree, dict):
        children = tree
        base_path = root_path
    else:
        children = tree.children
        base_path = tree.tree_path

    lines: list[str] = []
    for full_path, node in iter_dfs(base_path, children):
        if tlog is not None:
            tlog.record(full_path, node)
        depth = full_path.count(".") - base_path.count(".") - 1
        p = "  " * (depth + indent)
        key = full_path.rsplit(".", 1)[-1]
        ts = (node.timestamp or "")[:10]
        if node.label:
            label_part = node.label if node.label.startswith(f"{key}:") else f"{key}. {node.label}"
        else:
            label_part = key
        date_part = f" — {ts}" if ts else ""
        files_part = ""
        if node.files:
            basenames = ", ".join(os.path.basename(f) for f in node.files)
            files_part = f" — {basenames}"
        lines.append(f"{p}- [{label_part}](#{full_path}){date_part}{files_part}")

    return lines


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class PalGrowLayerRequest(ToolRequest):
    prompt: str = Field(...)
    absolute_file_paths: Optional[list[str]] = Field(default_factory=list)
    media: Optional[list[str]] = Field(default_factory=list)
    context_label: Optional[str] = Field(default=None)
    tree_path: str = Field(...)


class PalQueryRequest(ToolRequest):
    prompt: str = Field(...)
    absolute_file_paths: Optional[list[str]] = Field(default_factory=list)
    media: Optional[list[str]] = Field(default_factory=list)
    tree_path: str = Field(...)


# ---------------------------------------------------------------------------
# Shared base
# ---------------------------------------------------------------------------


_PALTREE_DEFAULT_MODEL = "deepseek-v4-pro"
_PALTREE_ALLOWED_MODELS = {"deepseek-v4-pro", "deepseek-v4-flash"}


class PalTreeBaseTool(SimpleTool):
    """Shared base for PALTree and PALNode tools that call external models."""

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

    def get_default_model(self) -> str:
        return _PALTREE_DEFAULT_MODEL

    def get_allowed_models(self) -> set[str] | None:
        return _PALTREE_ALLOWED_MODELS

    def get_model_field_schema(self) -> dict[str, Any]:
        allowed = sorted(_PALTREE_ALLOWED_MODELS)
        return {
            "type": "string",
            "description": (
                f"The default model is '{_PALTREE_DEFAULT_MODEL}'. "
                f"Allowed models for tree operations: {', '.join(allowed)}."
            ),
        }

    def get_annotations(self) -> dict:
        return {"readOnlyHint": False, "openWorldHint": False}

    def _parse_response(self, raw_text, request, model_info=None):
        """Override to inject traversal data into ToolOutput metadata."""
        from tools.models import ToolOutput

        tool_output = super()._parse_response(raw_text, request, model_info)
        tlog = getattr(self, "_tlog", None)
        if tlog and isinstance(tool_output, ToolOutput):
            if tool_output.metadata is None:
                tool_output.metadata = {}
            tool_output.metadata.update(tlog.to_dict())
        return tool_output

    def _resolve_tree(self, tree_path: str):
        """Load tree for a given tree_path. Returns (tree, canonical).

        tree_path can be canonical, shorthand, or combined form with node segments.
        Raises KeyError if not found.
        """
        from utils.palstore import load_tree, parse_tree_path

        canonical, _ = parse_tree_path(tree_path)
        tree = load_tree(canonical)
        if tree is None:
            raise KeyError(f'PALTree file not found: "{canonical}".')
        return tree, canonical


# ---------------------------------------------------------------------------
# newtree
# ---------------------------------------------------------------------------


class PalInitTool(BaseTool):
    def get_name(self) -> str:
        return "newtree"

    def get_description(self) -> str:
        return (
            "Create a new named PALTree and register it to a project directory.\n"
            "Run treelist first to check for an existing tree before creating a new one;\n"
            "use growlayer to add nodes after creation."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tree_path": {
                    "type": "string",
                    "description": (
                        "Canonical tree_path: '/abs/path/to/project:tree-name'. "
                        "Colon separates directory from tree name. Periods forbidden in tree name."
                    ),
                },
            },
            "required": ["tree_path"],
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
        from utils.palstore import PalRoot, TraversalLog, save_tree, update_index

        tree_path = arguments.get("tree_path", "")

        if ":" not in tree_path:
            error = ToolOutput(
                status="error",
                content="tree_path must be canonical: '/abs/path:tree-name' (colon required).",
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        directory, tree_name = tree_path.rsplit(":", 1)

        if "." in tree_name:
            error = ToolOutput(
                status="error",
                content="tree_name must not contain dots. Dots are reserved for node path notation.",
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        from utils.palstore import get_tree_file_path

        if os.path.exists(get_tree_file_path(tree_path)):
            error = ToolOutput(
                status="error",
                content=f'PALTree "{tree_name}" already exists at {directory}.',
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        tree = PalRoot(
            tree_path=tree_path,
            created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        save_tree(tree)
        update_index(tree_path)

        tlog = TraversalLog(traversal_type="none")
        tool_output = ToolOutput(
            status="success",
            content=(
                f"PALTree created.\n\n"
                f"tree_path: {tree_path}\n"
                f"directory: {directory}\n\n"
                f'Use growlayer(tree_path="{tree_name}", ...) to add context layers.'
            ),
            content_type="text",
            metadata={"tree_path": tree_path, "directory": directory, **tlog.to_dict()},
        )
        return [TextContent(type="text", text=tool_output.model_dump_json())]


# ---------------------------------------------------------------------------
# growlayer
# ---------------------------------------------------------------------------


class PalGrowLayerTool(PalTreeBaseTool):
    def get_name(self) -> str:
        return "growlayer"

    def get_description(self) -> str:
        return (
            "Grow a PALTree by adding a new context layer, embedding files and prose for an external model to synthesize.\n"
            "Each call appends a new numbered layer; seed 4-6 key files per layer for best results.\n"
            "Use newtree to create a tree first, then querynode to retrieve stored context."
        )

    def get_request_model(self):
        return PalGrowLayerRequest

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
            "tree_path": {"type": "string", "description": TREE_PATH_DESCRIPTION},
        }

    def get_required_fields(self) -> list[str]:
        return ["prompt", "tree_path"]

    def get_input_schema(self) -> dict[str, Any]:
        required_fields = ["prompt", "tree_path"]
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
                "tree_path": {"type": "string", "description": TREE_PATH_DESCRIPTION},
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

        tree_path = arguments.get("tree_path", "")
        if not tree_path:
            error = ToolOutput(
                status="error",
                content="growlayer requires a tree_path. Use newtree to create a tree first.",
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        try:
            tree, canonical = self._resolve_tree(tree_path)
        except KeyError as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        # Extract node_path from combined form if present
        from utils.palstore import parse_tree_path

        _, node_segments = parse_tree_path(tree_path)
        node_path = ".".join(node_segments)

        from utils.file_diff import build_file_state_from_ancestry
        from utils.palstore import collect_traversal, iter_context
        from utils.palstore_builder import build_context_from_ancestry

        ancestors, tlog = collect_traversal(iter_context(tree, node_path), "strata")
        self._tlog = tlog
        self._ancestors = ancestors
        self._prior_file_state = build_file_state_from_ancestry(ancestors)
        self._injected_history = build_context_from_ancestry(ancestors)
        self._tree = tree
        self._node_path = node_path

        arguments["_context_used"] = tlog.total_tokens

        arguments.pop("continuation_id", None)
        arguments["thinking_mode"] = "max"
        arguments["temperature"] = 0

        return await super().execute(arguments)

    async def prepare_prompt(self, request: PalGrowLayerRequest) -> str:
        user_content = self.handle_prompt_file_with_fallback(request)

        files = self.get_request_files(request)
        file_section = ""
        if files:
            file_section = self._build_diff_aware_file_section(files)

        label_line = f"\n[Label: {request.context_label}]" if request.context_label else ""
        base = f"=== CONTEXT LAYER SUBMISSION ==={label_line}\n\n{user_content}{file_section}"
        self._last_base_prompt = base

        injected = getattr(self, "_injected_history", "")
        full_prompt = f"{injected}\n\n{base}" if injected else base
        self._last_full_prompt = full_prompt
        return full_prompt

    def _build_diff_aware_file_section(self, files: list[str]) -> str:
        """Build the file section using diffs against prior ancestry state."""
        import logging
        import os
        from datetime import datetime, timezone

        from utils.file_diff import decide_file_representation, find_base_layer_key

        prior_state = getattr(self, "_prior_file_state", {})
        ancestors = getattr(self, "_ancestors", [])

        file_parts: list[str] = []
        actually_processed: list[str] = []

        for file_path in files:
            try:
                with open(file_path, encoding="utf-8", errors="replace") as f:
                    raw_content = f.read()
            except OSError:
                logging.getLogger(__name__).warning(f"[CTX] Cannot read file: {file_path}")
                continue

            try:
                mtime = os.stat(file_path).st_mtime
                modified_at = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")
            except OSError:
                modified_at = "unknown"

            old_content = prior_state.get(file_path)
            base_key = find_base_layer_key(file_path, ancestors)

            representation = decide_file_representation(file_path, raw_content, old_content, base_key, modified_at)
            if representation:
                file_parts.append(representation)
                actually_processed.append(file_path)

        self._actually_processed_files = actually_processed
        if not file_parts:
            return ""
        return f"\n\n=== CONTEXT FILES ===\n{''.join(file_parts)}\n=== END CONTEXT FILES ==="

    def format_response(self, response: str, _request: PalGrowLayerRequest, _model_info: Optional[dict] = None) -> str:
        self._last_raw_response = response
        return f"{response}\n\n---\n\nAGENT'S TURN: Context layer stored. Use the tree_path to add more layers or query this tree."

    def _create_continuation_offer(self, _request, _model_info: Optional[dict] = None):
        import re

        from utils.palstore import get_next_key

        tree = getattr(self, "_tree", None)
        node_path = getattr(self, "_node_path", None)
        if tree is None or node_path is None:
            return None

        # Walk up past L-prefixed ancestors to prevent L->L nesting
        parts = node_path.split(".") if node_path else []
        while parts and re.match(r"^L\d+$", parts[-1]):
            parts.pop()
        insertion_parent = ".".join(parts)

        self._insertion_parent = insertion_parent
        self._next_key = get_next_key(tree, insertion_parent, "L")
        new_node_path = f"{insertion_parent}.{self._next_key}" if insertion_parent else self._next_key
        self._new_node_path = new_node_path
        continuation_id = f"{tree.tree_name}.{new_node_path}"

        return {
            "continuation_id": continuation_id,
            "note": f"Layer stored at {tree.tree_name}.{new_node_path}.",
        }

    def _record_assistant_turn(
        self, _continuation_id: str, response_text: str, request, model_info: Optional[dict]
    ) -> None:
        from utils.palstore import PalNode, add_palnode, save_tree
        from utils.response_formatter import render_markdown_output

        tree = getattr(self, "_tree", None)
        insertion_parent = getattr(self, "_insertion_parent", None)
        next_key = getattr(self, "_next_key", None)
        if tree is None or insertion_parent is None or next_key is None:
            logger.warning("growlayer: missing tree state in _record_assistant_turn, skipping write")
            return

        raw = getattr(self, "_last_raw_response", response_text)
        model_name = model_info.get("model_name") if model_info else None
        provider_obj = model_info.get("provider") if model_info else None
        provider = str(provider_obj) if provider_obj and not isinstance(provider_obj, str) else provider_obj

        input_dict = {
            "tool_name": self.get_name(),
            "model": model_name,
            "node_path": getattr(self, "_node_path", None),
            "context_label": getattr(request, "context_label", None),
            "prompt": getattr(self, "_last_base_prompt", ""),
        }
        output_dict = {
            "status": "success",
            "content": raw,
            "metadata": {"model_used": model_name, "provider_used": provider},
        }

        node = PalNode(
            label=getattr(request, "context_label", None),
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            model=model_name,
            files=self.get_request_files(request),
            input=render_markdown_output(input_dict),
            output=render_markdown_output(output_dict),
        )

        add_palnode(tree, insertion_parent, next_key, node)
        save_tree(tree)


# ---------------------------------------------------------------------------
# upsertnode
# ---------------------------------------------------------------------------


class PalUpsertTool(BaseTool):
    """Update fields on an existing PALNode, or insert a new child node — without calling a model."""

    def get_name(self) -> str:
        return "upsertnode"

    def get_description(self) -> str:
        return (
            "Update fields on an existing PALNode, or insert a new child node — without calling a model.\n"
            "Two modes:\n"
            "  Update mode (default): target an existing node by tree_path, update its fields.\n"
            "  Insert mode (insert=true): target an existing node, add a NEW child node under it.\n"
            "Fields: label, input, output, files, metadata."
        )

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

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tree_path": {"type": "string", "description": TREE_PATH_DESCRIPTION},
                "insert": {
                    "type": "boolean",
                    "description": (
                        "False (default): update fields on the node at tree_path. "
                        "True: insert a NEW child node under tree_path."
                    ),
                },
                "label": {"type": "string", "description": "Human-readable label for the node."},
                "input": {"type": "string", "description": "Input text to store on the node."},
                "output": {"type": "string", "description": "Output text to store on the node."},
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Absolute file paths to attach to the node. Replaces existing list.",
                },
                "metadata": {
                    "type": "object",
                    "description": "Key-value metadata to merge into the node (existing keys preserved unless overwritten).",
                },
                "child_key": {
                    "type": "string",
                    "description": (
                        "Insert mode only. Explicit key for the new child (e.g. '0', '3'). "
                        "If omitted, auto-generated using child_prefix."
                    ),
                },
                "child_prefix": {
                    "type": "string",
                    "description": (
                        "Insert mode only. Prefix string for auto-key generation when child_key is absent. "
                        "Defaults to empty string (purely numeric keys)."
                    ),
                },
            },
            "required": ["tree_path"],
            "additionalProperties": False,
        }

    async def prepare_prompt(self, _request) -> str:
        return ""

    def format_response(self, response, _request, _model_info=None):
        return response

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        from tools.models import ToolOutput
        from utils.palstore import load_tree, parse_tree_path

        tree_path = arguments.get("tree_path", "")
        if not tree_path:
            error = ToolOutput(status="error", content="tree_path is required.", content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        try:
            canonical, node_segments = parse_tree_path(tree_path)
        except (KeyError, ValueError) as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        tree = load_tree(canonical)
        if tree is None:
            error = ToolOutput(status="error", content=f'PALTree file not found: "{canonical}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        node_path = ".".join(node_segments)
        insert_mode = arguments.get("insert", False)

        if insert_mode:
            return await self._handle_insert(tree, node_path, arguments)
        else:
            return await self._handle_update(tree, node_path, arguments)

    async def _handle_update(self, tree, node_path: str, arguments: dict[str, Any]) -> list[TextContent]:
        from tools.models import ToolOutput
        from utils.palstore import resolve_node, save_tree

        if not node_path:
            error = ToolOutput(
                status="error", content="Cannot upsert the root node. Target a child node.", content_type="text"
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        node = resolve_node(tree, node_path)
        if node is None:
            error = ToolOutput(status="error", content=f'Node not found: "{node_path}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        updated_fields = []
        if "label" in arguments:
            node.label = arguments["label"]
            updated_fields.append("label")
        if "input" in arguments:
            node.input = arguments["input"]
            updated_fields.append("input")
        if "output" in arguments:
            node.output = arguments["output"]
            updated_fields.append("output")
        if "files" in arguments:
            node.files = arguments["files"]
            updated_fields.append("files")
        if "metadata" in arguments:
            node.metadata.update(arguments["metadata"])
            updated_fields.append("metadata")

        if not updated_fields:
            error = ToolOutput(status="error", content="No fields provided to update.", content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        save_tree(tree)

        from utils.palstore import TraversalLog

        tlog = TraversalLog(traversal_type="resolve")
        full_path = f"{tree.tree_path}.{node_path}"
        tlog.record(full_path, node)
        result = ToolOutput(
            status="success",
            content=f"Updated {', '.join(updated_fields)} on {node_path}.",
            content_type="text",
            metadata={
                "tree_path": tree.tree_path,
                "node_path": node_path,
                "updated_fields": updated_fields,
                **tlog.to_dict(),
            },
        )
        return [TextContent(type="text", text=result.model_dump_json())]

    async def _handle_insert(self, tree, node_path: str, arguments: dict[str, Any]) -> list[TextContent]:
        from tools.models import ToolOutput
        from utils.palstore import PalNode, add_palnode, get_next_key, resolve_node, save_tree

        # Verify parent exists (root or node)
        if node_path:
            parent = resolve_node(tree, node_path)
            if parent is None:
                error = ToolOutput(
                    status="error", content=f'Parent node not found: "{node_path}".', content_type="text"
                )
                return [TextContent(type="text", text=error.model_dump_json())]

        # Determine child key
        child_key = arguments.get("child_key")
        if not child_key:
            child_prefix = arguments.get("child_prefix", "")
            child_key = get_next_key(tree, node_path, child_prefix)

        # Check for duplicate key
        if not node_path:
            siblings = tree.children
        else:
            parent_node = resolve_node(tree, node_path)
            siblings = parent_node.children if parent_node else {}
        if child_key in siblings:
            error = ToolOutput(
                status="error",
                content=f'Key "{child_key}" already exists under "{node_path or "root"}". Use update mode or choose a different key.',
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        node = PalNode(
            label=arguments.get("label"),
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            files=arguments.get("files", []),
            input=arguments.get("input", ""),
            output=arguments.get("output", ""),
            metadata=arguments.get("metadata", {}),
        )

        try:
            full_path = add_palnode(tree, node_path, child_key, node)
        except ValueError as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        save_tree(tree)

        from utils.palstore import TraversalLog

        tlog = TraversalLog(traversal_type="resolve")
        tlog.record(full_path, node)
        new_node_path = f"{node_path}.{child_key}" if node_path else child_key
        result = ToolOutput(
            status="success",
            content=f"Inserted node at {new_node_path}.",
            content_type="text",
            metadata={
                "tree_path": tree.tree_path,
                "node_path": new_node_path,
                "child_key": child_key,
                **tlog.to_dict(),
            },
        )
        return [TextContent(type="text", text=result.model_dump_json())]


# ---------------------------------------------------------------------------
# querynode
# ---------------------------------------------------------------------------


class PalQueryTool(PalTreeBaseTool):
    def get_name(self) -> str:
        return "querynode"

    def get_description(self) -> str:
        return (
            "Ask a question against a PALTree — sends all stored nodes to an external model for analysis.\n"
            "Use for targeted questions, not context revival. Each query is recorded as a child node.\n"
            "For context revival, prefer readnode (metadata + summary) + listnodefiles → readnodefile (selective file content)."
        )

    def get_request_model(self):
        return PalQueryRequest

    def get_tool_fields(self) -> dict[str, dict[str, Any]]:
        return {
            "prompt": {
                "type": "string",
                "description": "Question or instruction to run against the PALTree.",
            },
            "tree_path": {"type": "string", "description": TREE_PATH_DESCRIPTION},
        }

    def get_required_fields(self) -> list[str]:
        return ["prompt", "tree_path"]

    def get_input_schema(self) -> dict[str, Any]:
        required_fields = ["prompt", "tree_path"]
        if self.is_effective_auto_mode():
            required_fields.append("model")

        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Question or instruction to run against the PALTree.",
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
                "tree_path": {"type": "string", "description": TREE_PATH_DESCRIPTION},
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

        tree_path = arguments.get("tree_path", "")
        if not tree_path:
            error = ToolOutput(
                status="error",
                content="querynode requires a tree_path. Create a tree with newtree first.",
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        try:
            tree, canonical = self._resolve_tree(tree_path)
        except KeyError as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        from utils.palstore import collect_traversal, parse_tree_path, resolve_node
        from utils.palstore_builder import build_context_from_ancestry

        _, node_segments = parse_tree_path(tree_path)
        node_path = ".".join(node_segments)

        if node_path:
            node = resolve_node(tree, node_path)
            if node is None:
                error = ToolOutput(
                    status="error",
                    content=f"Node not found: {node_path}",
                    content_type="text",
                )
                return [TextContent(type="text", text=error.model_dump_json())]

        self._parent_node_path = node_path
        self._child_prefix = "Q"

        from utils.palstore import iter_context

        ancestors, tlog = collect_traversal(iter_context(tree, node_path), "strata")
        self._tlog = tlog

        self._injected_history = build_context_from_ancestry(ancestors)
        self._tree = tree

        arguments["_context_used"] = tlog.total_tokens

        arguments.pop("continuation_id", None)
        arguments["thinking_mode"] = "max"
        arguments["temperature"] = 0

        return await super().execute(arguments)

    async def prepare_prompt(self, request: PalQueryRequest) -> str:
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
        base = f"=== PALTREE QUERY ===\n\n{user_content}{file_section}"
        self._last_base_prompt = base

        full_prompt = f"{injected}\n\n{base}" if injected else base
        self._last_full_prompt = full_prompt
        return full_prompt

    def format_response(self, response: str, _request: PalQueryRequest, _model_info: Optional[dict] = None) -> str:
        self._last_raw_response = response
        return (
            f"{response}\n\n---\n\nAGENT'S TURN: Evaluate this response from the PALTree alongside your own analysis."
        )

    def _create_continuation_offer(self, _request, _model_info: Optional[dict] = None):
        from utils.palstore import get_next_key

        tree = getattr(self, "_tree", None)
        parent_node_path = getattr(self, "_parent_node_path", None)
        child_prefix = getattr(self, "_child_prefix", "Q")
        if tree is None or parent_node_path is None:
            return None

        self._next_key = get_next_key(tree, parent_node_path, child_prefix)
        new_node_path = f"{parent_node_path}.{self._next_key}" if parent_node_path else self._next_key
        self._new_node_path = new_node_path
        continuation_id = f"{tree.tree_name}.{new_node_path}"

        return {
            "continuation_id": continuation_id,
            "note": f"Query recorded at {tree.tree_name}.{new_node_path}.",
        }

    def _record_assistant_turn(
        self, _continuation_id: str, response_text: str, request, model_info: Optional[dict]
    ) -> None:
        from utils.palstore import PalNode, add_palnode, save_tree
        from utils.response_formatter import render_markdown_output

        tree = getattr(self, "_tree", None)
        parent_node_path = getattr(self, "_parent_node_path", None)
        next_key = getattr(self, "_next_key", None)

        if tree is None or parent_node_path is None or next_key is None:
            logger.warning("querynode: missing tree state in _record_assistant_turn, skipping write")
            return

        raw = getattr(self, "_last_raw_response", response_text)
        model_name = model_info.get("model_name") if model_info else None
        provider_obj = model_info.get("provider") if model_info else None
        provider = str(provider_obj) if provider_obj and not isinstance(provider_obj, str) else provider_obj
        prompt = self.get_request_prompt(request)

        input_dict = {
            "tool_name": self.get_name(),
            "model": model_name,
            "node_path": parent_node_path,
            "prompt": getattr(self, "_last_base_prompt", ""),
        }
        output_dict = {
            "status": "success",
            "content": raw,
            "metadata": {"model_used": model_name, "provider_used": provider},
        }

        node = PalNode(
            label=_truncate_label(prompt) if prompt else None,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            model=model_name,
            files=self.get_request_files(request),
            input=render_markdown_output(input_dict),
            output=render_markdown_output(output_dict),
        )

        add_palnode(tree, parent_node_path, next_key, node)
        save_tree(tree)


# ---------------------------------------------------------------------------
# forknode
# ---------------------------------------------------------------------------


class PalForkTool(BaseTool):
    def get_name(self) -> str:
        return "forknode"

    def get_description(self) -> str:
        return (
            "Insert a fork node as a child of the target node in a PALTree, branching to explore alternatives\n"
            "without disrupting the main lineage. Returns a new tree_path for the fork branch.\n"
            "Use treelist to find the tree_path to fork from; use growlayer or querynode with the returned fork tree_path."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tree_path": {
                    "type": "string",
                    "description": TREE_PATH_DESCRIPTION,
                },
                "label": {
                    "type": "string",
                    "description": "Optional short label for the fork point (e.g. 'alt-approach-A').",
                },
            },
            "required": ["tree_path"],
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
        from utils.palstore import (
            PalNode,
            add_palnode,
            get_next_key,
            load_tree,
            parse_tree_path,
            save_tree,
        )

        tree_path = arguments.get("tree_path", "")
        label = arguments.get("label")

        try:
            canonical, node_segments = parse_tree_path(tree_path)
        except (KeyError, ValueError) as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        tree = load_tree(canonical)
        if tree is None:
            error = ToolOutput(status="error", content=f'PALTree file not found: "{canonical}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        node_path = ".".join(node_segments)
        next_key = get_next_key(tree, node_path, "F")
        fork_node = PalNode(
            label=label,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        new_path = add_palnode(tree, node_path, next_key, fork_node)
        save_tree(tree)

        from utils.palstore import TraversalLog

        tlog = TraversalLog(traversal_type="resolve")
        tlog.record(new_path, fork_node)
        tool_output = ToolOutput(
            status="success",
            content=(
                f"Fork created.\n\ntree_path: {new_path}\n\nUse this tree_path to build a new branch from this point."
            ),
            content_type="text",
            metadata={"tree_path": new_path, "parent_tree_path": tree_path, **tlog.to_dict()},
        )
        return [TextContent(type="text", text=tool_output.model_dump_json())]


# ---------------------------------------------------------------------------
# listnode
# ---------------------------------------------------------------------------


class PalListTool(BaseTool):
    def get_name(self) -> str:
        return "treelist"

    def get_description(self) -> str:
        return (
            "List PALTrees and their full node trees, returning tree_paths needed for growlayer, querynode,\n"
            "readnode, forknode, and other tools. Defaults to the current working directory.\n"
            "Pass directory to scope to a different project, or tree_path to drill into a specific subtree."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": (
                        "Absolute path to filter trees by project directory. Defaults to the current working directory."
                    ),
                },
                "tree_path": {
                    "type": "string",
                    "description": (
                        "Show only the subtree rooted at this node (e.g. 'myproject' or 'myproject.L2'). "
                        "Use readnode for the full content of a single node."
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

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        from tools.models import ToolOutput
        from utils.palstore import TraversalLog, list_trees, load_tree, parse_tree_path, resolve_node

        directory = arguments.get("directory", os.getcwd())
        tree_path = arguments.get("tree_path")

        tlog = TraversalLog(traversal_type="dfs")

        if tree_path:
            try:
                canonical, node_segments = parse_tree_path(tree_path)
            except (KeyError, ValueError) as exc:
                error = ToolOutput(status="error", content=str(exc), content_type="text")
                return [TextContent(type="text", text=error.model_dump_json())]

            tree = load_tree(canonical)
            if tree is None:
                error = ToolOutput(
                    status="error", content=f'PALTree file not found: "{canonical}".', content_type="text"
                )
                return [TextContent(type="text", text=error.model_dump_json())]

            node_path = ".".join(node_segments)
            if not node_path:
                n_nodes = len(tree.children)
                lines = [f"{tree.tree_path}  ({n_nodes} nodes)"]
                lines.extend(_render_tree(tree, indent=1, tlog=tlog))
            else:
                node = resolve_node(tree, node_path)
                if node is not None:
                    full_node_path = f"{canonical}.{node_path}"
                    lines = [f"{full_node_path}"]
                    lines.extend(_render_tree(node.children, root_path=full_node_path, indent=1, tlog=tlog))
                else:
                    lines = [f"Node not found: {node_path}"]

            content = "\n".join(lines)
        else:
            trees = list_trees(directory)
            if trees:
                lines = []
                for tree in trees:
                    n_nodes = len(tree.children)
                    lines.append(f"{tree.tree_path}  ({n_nodes} nodes)")
                    lines.extend(_render_tree(tree, indent=1, tlog=tlog))
                content = "\n".join(lines)
            else:
                scope = f" for directory '{directory}'" if directory else ""
                content = f"No PALTrees found{scope}."

        tool_output = ToolOutput(
            status="success",
            content=content,
            content_type="text",
            metadata={**tlog.to_dict()},
        )
        return [TextContent(type="text", text=tool_output.model_dump_json())]


# ---------------------------------------------------------------------------
# readnode
# ---------------------------------------------------------------------------


class PalReadTool(BaseTool):
    def get_name(self) -> str:
        return "readnode"

    def get_description(self) -> str:
        return (
            "Read a PALNode's metadata, input, and output — the primary tool for context revival.\n"
            "Returns label, model, timestamp, file list, input text, and output text.\n"
            "After reading, use listnodefiles to see attached files, then readnodefile to selectively load relevant ones."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tree_path": {
                    "type": "string",
                    "description": TREE_PATH_DESCRIPTION,
                },
                "node_path": {
                    "type": "string",
                    "description": NODE_PATH_DESCRIPTION,
                },
            },
            "required": ["tree_path", "node_path"],
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
        from utils.palstore import TraversalLog, load_tree, resolve_node, resolve_tree_path

        tree_path = arguments.get("tree_path", "")
        node_path = arguments.get("node_path", "")

        try:
            canonical = resolve_tree_path(tree_path)
        except (KeyError, ValueError) as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        tree = load_tree(canonical)
        if tree is None:
            error = ToolOutput(status="error", content=f'PALTree file not found: "{canonical}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        node = resolve_node(tree, node_path)

        if node is None:
            error = ToolOutput(
                status="error",
                content=f"Node not found: {node_path}",
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        from utils.response_formatter import format_layer_markdown

        full_path = f"{canonical}.{node_path}" if node_path else canonical
        content = format_layer_markdown(
            full_path,
            label=node.label,
            model=node.model,
            timestamp=node.timestamp,
            files=node.files,
            input_text=node.input,
            output_text=node.output,
        )

        tlog = TraversalLog(traversal_type="resolve")
        tlog.record(full_path, node)
        tool_output = ToolOutput(
            status="success",
            content=content,
            content_type="text",
            metadata={"tree_path": canonical, "node_path": node_path, **tlog.to_dict()},
        )
        return [TextContent(type="text", text=tool_output.model_dump_json())]


# ---------------------------------------------------------------------------
# renametree
# ---------------------------------------------------------------------------


class PalRenameTool(BaseTool):
    def get_name(self) -> str:
        return "renametree"

    def get_description(self) -> str:
        return (
            "Rename a root PALTree, updating the tree file, index, and any armed state atomically.\n"
            "Use treelist to confirm the current tree_path before renaming."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tree_path": {
                    "type": "string",
                    "description": TREE_PATH_DESCRIPTION,
                },
                "new_name": {
                    "type": "string",
                    "description": "New name for the tree (e.g. 'myproject-v2'). No dots allowed.",
                },
            },
            "required": ["tree_path", "new_name"],
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
        from utils.palstore import rename_tree, resolve_tree_path

        tree_path = arguments.get("tree_path", "")
        new_name = arguments.get("new_name", "")

        if not tree_path or not new_name:
            tool_output = ToolOutput(
                status="error",
                content="tree_path and new_name are required.",
                content_type="text",
            )
            return [TextContent(type="text", text=tool_output.model_dump_json())]

        try:
            canonical = resolve_tree_path(tree_path)
        except (KeyError, ValueError) as exc:
            tool_output = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=tool_output.model_dump_json())]

        try:
            rename_tree(canonical, new_name)
        except (KeyError, ValueError) as exc:
            tool_output = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=tool_output.model_dump_json())]

        from utils.palstore import TraversalLog

        directory = canonical.rsplit(":", 1)[0]
        new_canonical = f"{directory}:{new_name}"
        tlog = TraversalLog(traversal_type="none")
        tool_output = ToolOutput(
            status="success",
            content=(f"Tree renamed.\n\nold tree_path: {canonical}\nnew tree_path: {new_canonical}"),
            content_type="text",
            metadata={"tree_path": new_canonical, "old_tree_path": canonical, **tlog.to_dict()},
        )
        return [TextContent(type="text", text=tool_output.model_dump_json())]


# ---------------------------------------------------------------------------
# exportnode
# ---------------------------------------------------------------------------


class PalExportTool(BaseTool):
    def get_name(self) -> str:
        return "treedump"

    def get_description(self) -> str:
        return (
            "Export an entire PALTree to a self-contained markdown file.\n"
            "Serializes all nodes, metadata, inputs, outputs, and file references\n"
            "into a single portable document suitable for archival, handoffs, or direct file reads."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tree_path": {
                    "type": "string",
                    "description": TREE_PATH_DESCRIPTION,
                },
                "output_path": {
                    "type": "string",
                    "description": (
                        "Absolute path for the output markdown file. "
                        "Parent directory must exist. Example: '/home/user/.claude/handoffs/myproject-export.md'"
                    ),
                },
            },
            "required": ["tree_path", "output_path"],
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
        from utils.palstore import PalNode

        node: PalNode
        heading = "#" * min(depth, 6)
        lines = [f"{heading} {path}", ""]

        meta_parts = []
        if node.label:
            meta_parts.append(f"**Label:** {node.label}")
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

        if node.input:
            lines.extend(["### Input", "", node.input, ""])
        if node.output:
            lines.extend(["### Output", "", node.output, ""])

        return lines

    def _walk_tree(self, nodes: dict, parent_path: str, depth: int, tlog=None) -> list[str]:
        """Render all nodes depth-first using iter_dfs."""
        from utils.palstore import iter_dfs

        base_depth = parent_path.count(".")
        lines: list[str] = []
        for full_path, node in iter_dfs(parent_path, nodes):
            if tlog is not None:
                tlog.record(full_path, node)
            node_depth = depth + (full_path.count(".") - base_depth - 1)
            lines.extend(self._render_node(full_path, node, node_depth))
        return lines

    def _build_toc(self, nodes: dict, parent_path: str) -> list[str]:
        """Build a flat table of contents with anchor links using iter_dfs."""
        from utils.palstore import iter_dfs

        base_depth = parent_path.count(".")
        lines: list[str] = []
        for full_path, node in iter_dfs(parent_path, nodes):
            indent = full_path.count(".") - base_depth - 1
            pad = "  " * indent
            anchor = full_path.lower().replace(".", "")
            ts = (node.timestamp or "")[:10]
            date_part = f" — {ts}" if ts else ""
            lines.append(f"{pad}- [{full_path}](#{anchor}){date_part}")
        return lines

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        from tools.models import ToolOutput
        from utils.palstore import TraversalLog, load_tree, resolve_tree_path

        tree_path = arguments.get("tree_path", "")
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

        try:
            canonical = resolve_tree_path(tree_path)
        except (KeyError, ValueError) as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        tree = load_tree(canonical)
        if tree is None:
            error = ToolOutput(status="error", content=f'PALTree file not found: "{canonical}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        tlog = TraversalLog(traversal_type="dfs")

        # Build the markdown document
        doc: list[str] = []

        # Header
        doc.append(f"# PALTree Export: {tree.tree_path}")
        doc.append("")

        # Tree metadata
        doc.append(f"**Directory:** `{tree.directory}`")
        doc.append(f"**Created:** {tree.created_at}")
        if tree.label:
            doc.append(f"**Label:** {tree.label}")
        n_nodes = len(tree.children)
        doc.append(f"**Nodes:** {n_nodes}")
        doc.append(f"**Exported:** {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
        doc.append("")

        # Table of contents
        if tree.children:
            doc.append("## Table of Contents")
            doc.append("")
            doc.extend(self._build_toc(tree.children, tree.tree_path))
            doc.append("")

        # Separator
        doc.append("---")
        doc.append("")

        # All nodes
        doc.extend(self._walk_tree(tree.children, tree.tree_path, depth=2, tlog=tlog))

        content = "\n".join(doc)

        try:
            with open(output_path, "w") as f:
                f.write(content)
        except OSError as e:
            error = ToolOutput(status="error", content=f"Failed to write file: {e}", content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        tool_output = ToolOutput(
            status="success",
            content=f"Exported tree '{tree.tree_path}' ({n_nodes} nodes) to:\n{output_path}",
            content_type="text",
            metadata={"tree_path": tree_path, "output_path": output_path, "nodes": n_nodes, **tlog.to_dict()},
        )
        return [TextContent(type="text", text=tool_output.model_dump_json())]


# ---------------------------------------------------------------------------
# listfiles
# ---------------------------------------------------------------------------


class PalFileListTool(BaseTool):
    def get_name(self) -> str:
        return "listnodefiles"

    def get_description(self) -> str:
        return (
            "List all files attached across a PALTree, grouped by node.\n"
            "Shows which files were seeded into each node with timestamps and labels.\n"
            "Part of the context revival flow: readnode → listnodefiles → readnodefile on context-relevant files."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tree_path": {
                    "type": "string",
                    "description": TREE_PATH_DESCRIPTION,
                },
                "node_path": {
                    "type": "string",
                    "description": NODE_PATH_DESCRIPTION + " Omit to list files across the entire tree.",
                },
            },
            "required": ["tree_path"],
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

    def _collect_files(self, nodes: dict, parent_path: str, tlog=None) -> list[tuple[str, str, str, list[str]]]:
        """Collect (node_path, timestamp, label, files) for nodes with files using iter_dfs."""
        from utils.palstore import iter_dfs

        results: list[tuple[str, str, str, list[str]]] = []
        for full_path, node in iter_dfs(parent_path, nodes):
            if tlog is not None:
                tlog.record(full_path, node)
            if node.files:
                results.append((full_path, node.timestamp or "", node.label or "", list(node.files)))
        return results

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        from tools.models import ToolOutput
        from utils.palstore import TraversalLog, load_tree, resolve_node, resolve_tree_path

        tree_path = arguments.get("tree_path", "")
        node_path = arguments.get("node_path", "")

        try:
            canonical = resolve_tree_path(tree_path)
        except (KeyError, ValueError) as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        tree = load_tree(canonical)
        if tree is None:
            error = ToolOutput(status="error", content=f'PALTree file not found: "{canonical}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        tlog = TraversalLog(traversal_type="dfs")
        if not node_path:
            collected = self._collect_files(tree.children, canonical, tlog=tlog)
            scope = canonical
        else:
            node = resolve_node(tree, node_path)
            if node is None:
                error = ToolOutput(status="error", content=f"Node not found: {node_path}", content_type="text")
                return [TextContent(type="text", text=error.model_dump_json())]
            full_node_path = f"{canonical}.{node_path}"
            collected = self._collect_files(node.children, full_node_path, tlog=tlog)
            if node.files:
                collected.insert(0, (full_node_path, node.timestamp or "", node.label or "", list(node.files)))
            scope = full_node_path

        if not collected:
            content = f"No files attached to any node in '{scope}'."
        else:
            total_files = sum(len(files) for _, _, _, files in collected)
            lines = [f"**{total_files} files across {len(collected)} nodes in '{scope}':**", ""]
            for npath, timestamp, label, files in collected:
                date = timestamp[:10] if timestamp else "unknown"
                label_part = f' — "{_truncate_label(label, 60)}"' if label else ""
                lines.append(f"**{npath}**{label_part} — {date}")
                for f in files:
                    lines.append(f"  {f}")
                lines.append("")
            content = "\n".join(lines)

        tool_output = ToolOutput(
            status="success",
            content=content,
            content_type="text",
            metadata={**tlog.to_dict()},
        )
        return [TextContent(type="text", text=tool_output.model_dump_json())]


# ---------------------------------------------------------------------------
# readfile
# ---------------------------------------------------------------------------


class PalFileReadTool(BaseTool):
    def get_name(self) -> str:
        return "readnodefile"

    def get_description(self) -> str:
        return (
            "Read a specific file from a PALNode's stored content blob (falls back to disk).\n"
            "Use after listnodefiles to selectively load only the files relevant to the current task.\n"
            "This targeted approach avoids the token cost of querynode, which sends all nodes to an external model."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tree_path": {
                    "type": "string",
                    "description": TREE_PATH_DESCRIPTION,
                },
                "node_path": {
                    "type": "string",
                    "description": NODE_PATH_DESCRIPTION + " Omit to search the entire tree.",
                },
                "file_path": {
                    "type": "string",
                    "description": "Absolute path of the file to read (must match a path in the node's files list).",
                },
            },
            "required": ["tree_path", "file_path"],
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

    def _find_nodes_with_file(
        self, nodes: dict, parent_path: str, file_path: str, tlog=None
    ) -> list[tuple[str, str, "Any"]]:
        """Find nodes referencing file_path using iter_dfs. Returns (node_path, timestamp, node)."""
        from utils.palstore import PalNode, iter_dfs

        results: list[tuple[str, str, PalNode]] = []
        for full_path, node in iter_dfs(parent_path, nodes):
            if tlog is not None:
                tlog.record(full_path, node)
            if node.files and file_path in node.files:
                results.append((full_path, node.timestamp or "", node))
        return results

    def _extract_file_from_blob(self, content_blob: str, file_path: str) -> Optional[str]:
        """Extract a single file's content from the node content blob.

        Handles BEGIN FILE markers (returns content directly).
        Returns None for BEGIN DIFF markers or if the file is not found,
        which causes the caller to fall back to reading from disk.
        """
        from utils.file_diff import extract_file_from_content_blob

        return extract_file_from_content_blob(content_blob, file_path)

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        from tools.models import ToolOutput
        from utils.palstore import TraversalLog, load_tree, resolve_node, resolve_tree_path

        tree_path = arguments.get("tree_path", "")
        node_path = arguments.get("node_path", "")
        file_path = arguments.get("file_path", "")

        if not file_path:
            error = ToolOutput(status="error", content="file_path is required.", content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        try:
            canonical = resolve_tree_path(tree_path)
        except (KeyError, ValueError) as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        tree = load_tree(canonical)
        if tree is None:
            error = ToolOutput(status="error", content=f'PALTree file not found: "{canonical}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        tlog = TraversalLog(traversal_type="dfs")
        if not node_path:
            matches = self._find_nodes_with_file(tree.children, canonical, file_path, tlog=tlog)
        else:
            node = resolve_node(tree, node_path)
            if node is None:
                error = ToolOutput(status="error", content=f"Node not found: {node_path}", content_type="text")
                return [TextContent(type="text", text=error.model_dump_json())]
            full_node_path = f"{canonical}.{node_path}"
            matches = self._find_nodes_with_file(node.children, full_node_path, file_path, tlog=tlog)
            if node.files and file_path in node.files:
                matches.insert(0, (full_node_path, node.timestamp or "", node))

        if not matches:
            scope = f"{canonical}.{node_path}" if node_path else canonical
            error = ToolOutput(
                status="error",
                content=f"File '{file_path}' not found in any node under '{scope}'. Use listnodefiles to discover attached files.",
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        # Use most recent node
        matches.sort(key=lambda m: m[1], reverse=True)
        matched_path, timestamp, target_node = matches[0]

        # Try extracting from input blob (file content is embedded in the input field)
        extracted = None
        if target_node.input:
            extracted = self._extract_file_from_blob(target_node.input, file_path)

        source = "tree"
        if extracted is None:
            # Fall back to reading from disk
            try:
                with open(file_path, encoding="utf-8", errors="replace") as f:
                    extracted = f.read()
                source = "disk"
            except OSError as e:
                error = ToolOutput(
                    status="error",
                    content=f"File content not in node blob and cannot read from disk: {e}",
                    content_type="text",
                )
                return [TextContent(type="text", text=error.model_dump_json())]

        header = f"# {os.path.basename(file_path)}\n\n"
        header += f"**Source:** {source} (node {matched_path}, {timestamp[:10] if timestamp else 'unknown'})\n"
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
            metadata={
                "tree_path": canonical,
                "file_path": file_path,
                "source": source,
                "node_path": matched_path,
                **tlog.to_dict(),
            },
        )
        return [TextContent(type="text", text=tool_output.model_dump_json())]


# ---------------------------------------------------------------------------
# writenodefile
# ---------------------------------------------------------------------------


class PalFileWriteTool(BaseTool):
    def get_name(self) -> str:
        return "writenodefile"

    def get_description(self) -> str:
        return (
            "Write a file to a PALNode's file list. Adds the file path reference to the node\n"
            "so it appears in listnodefiles and can be retrieved via readnodefile.\n"
            "The file must exist on disk. Use treelist to find node paths."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tree_path": {
                    "type": "string",
                    "description": TREE_PATH_DESCRIPTION,
                },
                "node_path": {
                    "type": "string",
                    "description": NODE_PATH_DESCRIPTION,
                },
                "file_path": {
                    "type": "string",
                    "description": "Absolute path of the file to attach to the node. Must exist on disk.",
                },
            },
            "required": ["tree_path", "node_path", "file_path"],
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
        from utils.palstore import load_tree, resolve_node, resolve_tree_path, save_tree

        tree_path = arguments.get("tree_path", "")
        node_path = arguments.get("node_path", "")
        file_path = arguments.get("file_path", "")

        if not file_path:
            error = ToolOutput(status="error", content="file_path is required.", content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        if not os.path.isabs(file_path):
            error = ToolOutput(status="error", content="file_path must be an absolute path.", content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        if not os.path.isfile(file_path):
            error = ToolOutput(status="error", content=f"File does not exist: {file_path}", content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        try:
            canonical = resolve_tree_path(tree_path)
        except (KeyError, ValueError) as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        tree = load_tree(canonical)
        if tree is None:
            error = ToolOutput(status="error", content=f'PALTree file not found: "{canonical}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        node = resolve_node(tree, node_path)
        if node is None:
            error = ToolOutput(status="error", content=f"Node not found: {node_path}", content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        from utils.palstore import TraversalLog

        full_path = f"{canonical}.{node_path}"
        tlog = TraversalLog(traversal_type="resolve")
        tlog.record(full_path, node)

        if file_path in node.files:
            tool_output = ToolOutput(
                status="success",
                content=f"File already attached to node {node_path}: {file_path}",
                content_type="text",
                metadata={
                    "tree_path": canonical,
                    "node_path": node_path,
                    "file_path": file_path,
                    "action": "already_present",
                    **tlog.to_dict(),
                },
            )
            return [TextContent(type="text", text=tool_output.model_dump_json())]

        node.files.append(file_path)
        save_tree(tree)

        tool_output = ToolOutput(
            status="success",
            content=f"File attached to node {node_path}: {file_path}\n\nTotal files on node: {len(node.files)}",
            content_type="text",
            metadata={
                "tree_path": canonical,
                "node_path": node_path,
                "file_path": file_path,
                "action": "added",
                "total_files": len(node.files),
                **tlog.to_dict(),
            },
        )
        return [TextContent(type="text", text=tool_output.model_dump_json())]


# ---------------------------------------------------------------------------
# traversenode
# ---------------------------------------------------------------------------


class PalTraverseTool(BaseTool):
    def get_name(self) -> str:
        return "traversetree"

    def get_description(self) -> str:
        return (
            "Traverse a PALTree between two nodes and render the conversation history.\n"
            "Supports two modes:\n"
            "  Strata traversal: when start/end are in different L-layers, walks through all\n"
            "  intermediate layers chronologically (tree rings). Both endpoints expand inward.\n"
            "  Ancestry traversal: when start is an ancestor of end, walks the parent→child chain.\n"
            "Use 'L' (no number) as shorthand for the last layer."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tree_path": {
                    "type": "string",
                    "description": "Root tree name (e.g. 'myproject'). Use treelist to discover tree names.",
                },
                "start_node": {
                    "type": "string",
                    "description": (
                        "Node path for the start of the range (e.g. 'L1', 'L2.Q3'). "
                        "Use 'L' for the last layer. Inclusive."
                    ),
                },
                "end_node": {
                    "type": "string",
                    "description": (
                        "Node path for the end of the range (e.g. 'L43', 'L24.Q7.F0.thinkdeep'). "
                        "Use 'L' for the last layer. Inclusive."
                    ),
                },
            },
            "required": ["tree_path", "start_node", "end_node"],
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
        from utils.palstore import calc_traversal, collect_traversal, load_tree, resolve_tree_path
        from utils.palstore_builder import build_context_from_ancestry

        tree_path = arguments.get("tree_path", "")
        start_node = arguments.get("start_node", "")
        end_node = arguments.get("end_node", "")

        if not start_node or not end_node:
            error = ToolOutput(
                status="error", content="Both start_node and end_node are required.", content_type="text"
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        try:
            canonical = resolve_tree_path(tree_path)
        except (KeyError, ValueError) as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        tree = load_tree(canonical)
        if tree is None:
            error = ToolOutput(status="error", content=f'PALTree file not found: "{canonical}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        try:
            range_nodes, tlog = collect_traversal(calc_traversal(tree, start_node, end_node), "strata")
        except ValueError as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        content = build_context_from_ancestry(range_nodes)
        if not content:
            content = "(No content nodes with prompt/response found in the traversed range.)"

        tool_output = ToolOutput(
            status="success",
            content=content,
            content_type="text",
            metadata={
                "tree_path": canonical,
                "start_node": start_node,
                "end_node": end_node,
                **tlog.to_dict(),
            },
        )
        return [TextContent(type="text", text=tool_output.model_dump_json())]


# ---------------------------------------------------------------------------
# movenode
# ---------------------------------------------------------------------------


class PalMoveTool(BaseTool):
    def get_name(self) -> str:
        return "movenode"

    def get_description(self) -> str:
        return (
            "Move a PALNode to a new location in the tree. Detaches from source and "
            "reattaches at destination with rollback on failure. Use treelist to find node paths."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tree_path": {
                    "type": "string",
                    "description": TREE_PATH_DESCRIPTION,
                },
                "source_node_path": {
                    "type": "string",
                    "description": "Dot-path of the node to move within the tree (e.g. 'L0.Q1').",
                },
                "dest_node_path": {
                    "type": "string",
                    "description": "Dot-path of the destination parent within the tree (e.g. 'L2'). Empty string for root.",
                },
                "dest_key": {
                    "type": "string",
                    "description": "Key name at destination (e.g. 'Q3'). If omitted, auto-generates the next available key using the source node's key prefix.",
                },
            },
            "required": ["tree_path", "source_node_path", "dest_node_path"],
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
        from utils.palstore import (
            _get_key_prefix,
            get_next_key,
            load_tree,
            move_palnode,
            resolve_node,
            resolve_tree_path,
            save_tree,
        )

        tree_path = arguments.get("tree_path", "")
        source_node_path = arguments.get("source_node_path", "")
        dest_node_path = arguments.get("dest_node_path", "")
        dest_key = arguments.get("dest_key")

        try:
            canonical = resolve_tree_path(tree_path)
        except (KeyError, ValueError) as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        tree = load_tree(canonical)
        if tree is None:
            error = ToolOutput(status="error", content=f'PALTree file not found: "{canonical}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        if dest_key is None:
            src_last_key = source_node_path.rsplit(".", 1)[-1] if source_node_path else ""
            prefix = _get_key_prefix(src_last_key) or ""
            dest_key = get_next_key(tree, dest_node_path, prefix)

        try:
            new_path = move_palnode(tree, source_node_path, dest_node_path, dest_key)
        except (KeyError, ValueError) as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        save_tree(tree)

        from utils.palstore import TraversalLog

        new_node_path = f"{dest_node_path}.{dest_key}" if dest_node_path else dest_key
        tlog = TraversalLog(traversal_type="resolve")
        moved_node = resolve_node(tree, new_node_path)
        if moved_node is not None:
            tlog.record(new_path, moved_node)
        tool_output = ToolOutput(
            status="success",
            content=f"Moved {source_node_path} to {new_node_path}.",
            content_type="text",
            metadata={
                "tree_path": canonical,
                "source_node_path": source_node_path,
                "new_node_path": new_node_path,
                "dest_node_path": dest_node_path,
                **tlog.to_dict(),
            },
        )
        return [TextContent(type="text", text=tool_output.model_dump_json())]


# ---------------------------------------------------------------------------
# copynode
# ---------------------------------------------------------------------------


class PalCopyTool(BaseTool):
    def get_name(self) -> str:
        return "clonetree"

    def get_description(self) -> str:
        return (
            "Deep copy a PALTree subtree to a new location, preserving the original. Use treelist to find node paths."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tree_path": {
                    "type": "string",
                    "description": TREE_PATH_DESCRIPTION,
                },
                "source_node_path": {
                    "type": "string",
                    "description": "Dot-path of the node to copy within the tree (e.g. 'L0.Q1').",
                },
                "dest_node_path": {
                    "type": "string",
                    "description": "Dot-path of the destination parent within the tree (e.g. 'L2'). Empty string for root.",
                },
                "dest_key": {
                    "type": "string",
                    "description": "Key name at destination (e.g. 'Q3'). If omitted, auto-generates the next available key using the source node's key prefix.",
                },
            },
            "required": ["tree_path", "source_node_path", "dest_node_path"],
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
        from utils.palstore import (
            _get_key_prefix,
            copy_palnode,
            get_next_key,
            load_tree,
            resolve_node,
            resolve_tree_path,
            save_tree,
        )

        tree_path = arguments.get("tree_path", "")
        source_node_path = arguments.get("source_node_path", "")
        dest_node_path = arguments.get("dest_node_path", "")
        dest_key = arguments.get("dest_key")

        try:
            canonical = resolve_tree_path(tree_path)
        except (KeyError, ValueError) as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        tree = load_tree(canonical)
        if tree is None:
            error = ToolOutput(status="error", content=f'PALTree file not found: "{canonical}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        if dest_key is None:
            src_last_key = source_node_path.rsplit(".", 1)[-1] if source_node_path else ""
            prefix = _get_key_prefix(src_last_key) or ""
            dest_key = get_next_key(tree, dest_node_path, prefix)

        try:
            new_path = copy_palnode(tree, source_node_path, dest_node_path, dest_key)
        except (KeyError, ValueError) as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        save_tree(tree)

        from utils.palstore import TraversalLog

        new_node_path = f"{dest_node_path}.{dest_key}" if dest_node_path else dest_key
        tlog = TraversalLog(traversal_type="resolve")
        copied_node = resolve_node(tree, new_node_path)
        if copied_node is not None:
            tlog.record(new_path, copied_node)
        tool_output = ToolOutput(
            status="success",
            content=f"Copied {source_node_path} to {new_node_path}.",
            content_type="text",
            metadata={
                "tree_path": canonical,
                "source_node_path": source_node_path,
                "new_node_path": new_node_path,
                "dest_node_path": dest_node_path,
                **tlog.to_dict(),
            },
        )
        return [TextContent(type="text", text=tool_output.model_dump_json())]


# ---------------------------------------------------------------------------
# foldnode
# ---------------------------------------------------------------------------


class PalFoldTool(BaseTool):
    def get_name(self) -> str:
        return "foldtree"

    def get_description(self) -> str:
        return (
            "Fold an ancestry range of nodes in a PALTree into a single aggregated node and insert it into the tree. "
            "Aggregates input/output content and unions file references. Use treelist to find node paths."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tree_path": {
                    "type": "string",
                    "description": TREE_PATH_DESCRIPTION,
                },
                "start_node_path": {
                    "type": "string",
                    "description": "Dot-path of range start within the tree (e.g. 'L0.Q1'). Inclusive.",
                },
                "end_node_path": {
                    "type": "string",
                    "description": "Dot-path of range end within the tree (e.g. 'L0.Q1.F0.Q2'). Must be a descendant of start. Inclusive.",
                },
                "dest_node_path": {
                    "type": "string",
                    "description": "Dot-path of the destination parent within the tree. Defaults to root (promotes to layer).",
                },
                "dest_key": {
                    "type": "string",
                    "description": "Key for the folded node at destination. Defaults to next available key.",
                },
                "label": {
                    "type": "string",
                    "description": "Optional label for the folded node.",
                },
            },
            "required": ["tree_path", "start_node_path", "end_node_path"],
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
        from utils.palstore import (
            add_palnode,
            fold_palnode_range,
            get_next_key,
            load_tree,
            resolve_tree_path,
            save_tree,
        )

        tree_path = arguments.get("tree_path", "")
        start_node_path = arguments.get("start_node_path", "")
        end_node_path = arguments.get("end_node_path", "")
        dest_node_path = arguments.get("dest_node_path")
        dest_key = arguments.get("dest_key")
        label = arguments.get("label")

        try:
            canonical = resolve_tree_path(tree_path)
        except (KeyError, ValueError) as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        tree = load_tree(canonical)
        if tree is None:
            error = ToolOutput(status="error", content=f'PALTree file not found: "{canonical}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        try:
            folded, tlog = fold_palnode_range(tree, start_node_path, end_node_path)
        except (KeyError, ValueError) as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        if label is not None:
            folded.label = label

        if dest_node_path is None:
            dest_node_path = ""

        if dest_key is None:
            dest_key = get_next_key(tree, dest_node_path, "")

        try:
            add_palnode(tree, dest_node_path, dest_key, folded)
        except (KeyError, ValueError) as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        save_tree(tree)

        new_node_path = f"{dest_node_path}.{dest_key}" if dest_node_path else dest_key
        content_len = len(folded.input or "")
        file_count = len(folded.files)
        tool_output = ToolOutput(
            status="success",
            content=f"Folded range {start_node_path}..{end_node_path} into {new_node_path} ({content_len} chars, {file_count} files).",
            content_type="text",
            metadata={
                "tree_path": canonical,
                "new_node_path": new_node_path,
                "start_node_path": start_node_path,
                "end_node_path": end_node_path,
                "content_length": content_len,
                "file_count": file_count,
                **tlog.to_dict(),
            },
        )
        return [TextContent(type="text", text=tool_output.model_dump_json())]


# ---------------------------------------------------------------------------
# compacttree
# ---------------------------------------------------------------------------


class PalCompactTool(BaseTool):
    """Layer-by-layer compaction of a PALTree via clink CLI agents.

    Walks each L-node, dumps attached files to a temp directory, and lets a CLI
    agent (with filesystem access) evaluate the layer against the current project
    state. Returns a compacted markdown blob — the "seed" for a new tree.
    """

    def get_name(self) -> str:
        return "compacttree"

    def get_description(self) -> str:
        return (
            "Compact a PALTree: walk each layer with a CLI agent that has filesystem access,\n"
            "evaluate historical content against current project state, discard outdated material,\n"
            "and produce a compacted markdown blob. Non-destructive — source tree is unchanged."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "source_tree_path": {"type": "string", "description": TREE_PATH_DESCRIPTION},
                "focus": {
                    "type": "string",
                    "description": "Optional: guide what to preserve (e.g. 'architecture', 'design rationale').",
                },
                "model": {
                    "type": "string",
                    "description": f"Model to use for the clink agent. Default: '{_PALTREE_DEFAULT_MODEL}'.",
                },
            },
            "required": ["source_tree_path"],
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

        return ToolModelCategory.EXTENDED_REASONING

    async def prepare_prompt(self, _request) -> str:
        return ""

    def format_response(self, response: str, _request, _model_info=None) -> str:
        return response

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        import re
        import shutil
        import tempfile

        from clink import get_registry
        from clink.agents import CLIAgentError, create_agent
        from tools.models import ToolOutput
        from utils.palstore import (
            TraversalLog,
            iter_dfs,
            load_tree,
            resolve_tree_path,
        )
        from utils.response_formatter import strip_context_files
        from utils.token_utils import count_tokens

        source_path = arguments.get("source_tree_path", "")
        focus = arguments.get("focus", "")
        cli_name = "claude"
        model = arguments.get("model") or _PALTREE_DEFAULT_MODEL

        # --- Resolve tree ---
        try:
            canonical = resolve_tree_path(source_path)
            tree = load_tree(canonical)
            if tree is None:
                raise KeyError(f"PALTree file not found: {canonical}")
        except (KeyError, ValueError) as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        # ===================================================================
        # Phase 1: Audit — walk tree, collect L-nodes
        # ===================================================================
        all_nodes = list(iter_dfs(tree.tree_path, tree.children))
        tlog = TraversalLog(traversal_type="compact_audit")
        for path, node in all_nodes:
            tlog.record(path, node)

        l_nodes: list[tuple[str, Any]] = []

        def _layer_sort_key(k: str) -> int:
            if re.match(r"^L\d+$", k):
                return int(k[1:])
            if re.match(r"^\d+$", k):
                return int(k)
            return 999

        for key in sorted(tree.children, key=_layer_sort_key):
            if re.match(r"^L\d+$", key) or re.match(r"^\d+$", key):
                l_nodes.append((key, tree.children[key]))

        if not l_nodes:
            error = ToolOutput(
                status="error", content="Source tree has no layer nodes to compact.", content_type="text"
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        # Build tree outline
        outline_parts: list[str] = [f"Tree: {canonical}", f"Layers: {len(l_nodes)}", f"Tokens: ~{tlog.total_tokens}"]
        for path, node in all_nodes:
            key = path.rsplit(".", 1)[-1]
            depth = path.count(".") - tree.tree_path.count(".") - 1
            indent = "  " * depth
            ts = (node.timestamp or "")[:10]
            label = node.label or key
            files_str = ", ".join(os.path.basename(f) for f in node.files) if node.files else ""
            outline_parts.append(f"{indent}{key}: {label} ({ts}){f' — {files_str}' if files_str else ''}")
        tree_outline = "\n".join(outline_parts)

        logger.info(
            "[COMPACT] Phase 1: audit — %d nodes, %d L-layers, %d tokens",
            len(all_nodes),
            len(l_nodes),
            tlog.total_tokens,
        )

        # --- Resolve CLI client ---
        try:
            registry = get_registry()
            cli_names = registry.list_clients()
            if not cli_names:
                raise ValueError("No CLI clients configured for clink.")
            selected_cli = cli_name
            client_config = registry.get_client(selected_cli)
        except (KeyError, ValueError) as exc:
            error = ToolOutput(status="error", content=f"CLI resolution failed: {exc}", content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        role_config = client_config.get_role(None)

        # ===================================================================
        # Phase 2: Temp directory staging
        # ===================================================================
        temp_dir = tempfile.mkdtemp(prefix="pal-compact-")
        try:
            for l_key, l_node in l_nodes:
                layer_dir = os.path.join(temp_dir, l_key)
                os.makedirs(layer_dir, exist_ok=True)
                for filepath in l_node.files:
                    if os.path.isfile(filepath):
                        shutil.copy2(filepath, layer_dir)

            logger.info("[COMPACT] Phase 2: temp dir staged at %s", temp_dir)

            # ===================================================================
            # Phase 3: Clink compaction — layer-by-layer, single session
            # ===================================================================
            agent = create_agent(client_config)

            # Route the Claude CLI to DeepSeek's Anthropic-compatible API
            # when using a DeepSeek model (mirrors the cld wrapper script).
            deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "")
            if deepseek_key and model and "deepseek" in model.lower():
                agent.client.env["ANTHROPIC_AUTH_TOKEN"] = deepseek_key
                agent.client.env["ANTHROPIC_BASE_URL"] = "https://api.deepseek.com/anthropic"

            session_id: str | None = None
            compacted_segments: list[str] = []
            layers_kept = 0
            layers_discarded = 0
            layers_errored = 0
            abort_reason: str | None = None

            # Opening turn — session cwd is the temp root so --resume works across layers
            project_dir = tree.directory
            opening = (
                f"TREE COMPACTION — evaluating PALTree '{canonical}' "
                f"({len(l_nodes)} layers, ~{tlog.total_tokens} tokens) against current project state.\n\n"
                f"Project directory: {project_dir}\n"
                f"Layer files directory: {temp_dir}\n\n"
                f"=== TREE OUTLINE ===\n{tree_outline}\n\n"
                f"I will feed you each historical layer one at a time, oldest first.\n"
                f"For each layer, evaluate it against the current state of the codebase:\n"
                f"- DISCARD implementation details already realized in the current codebase\n"
                f"- DISCARD dead ends, resolved bugs, superseded decisions\n"
                f"- EXTRACT enduring design rationale, 'why' decisions, unwritten constraints\n"
                f"- EXTRACT architectural patterns and integration knowledge NOT obvious from code\n\n"
                f"If a layer has no enduring value, return exactly an empty string.\n"
                f"Otherwise return dense technical markdown — no conversational filler.\n"
                f"Each layer prompt will tell you which directory contains that layer's referenced files.\n"
                f"The project source is at: {project_dir}\n"
            )
            if focus:
                opening += f"\nFocus: {focus}\n"

            logger.info("[COMPACT] Phase 3: opening turn")

            try:
                opening_result = await agent.run(
                    role=role_config,
                    prompt=opening,
                    system_prompt=CONTEXT_PROMPT,
                    files=[],
                    images=[],
                    model=model,
                    cwd=temp_dir,
                )
                session_id = opening_result.parsed.metadata.get("session_id")
                logger.info("[COMPACT] Phase 3: opening done, session_id=%s", session_id)
            except CLIAgentError as exc:
                error = ToolOutput(
                    status="error",
                    content=f"CLI agent failed during opening: {exc}",
                    content_type="text",
                )
                return [TextContent(type="text", text=error.model_dump_json())]

            # Feed each L-node
            for layer_idx, (l_key, l_node) in enumerate(l_nodes):
                layer_parts = [f"=== LAYER {l_key} ==="]
                if l_node.label:
                    layer_parts.append(f"Label: {l_node.label}")
                if l_node.timestamp:
                    layer_parts.append(f"Date: {l_node.timestamp[:10]}")
                if l_node.files:
                    layer_parts.append(f"Files: {', '.join(l_node.files)}")
                if l_node.input:
                    cleaned_input = strip_context_files(l_node.input)
                    if cleaned_input:
                        layer_parts.append(f"\n--- INPUT ---\n{cleaned_input}")
                if l_node.output:
                    layer_parts.append(f"\n--- OUTPUT ---\n{l_node.output}")

                for child_key in sorted(l_node.children):
                    child = l_node.children[child_key]
                    if child.output:
                        child_label = child.label or child_key
                        layer_parts.append(f"\n--- {l_key}.{child_key}: {child_label} ---\n{child.output}")

                layer_content = "\n".join(layer_parts)
                layer_dir = os.path.join(temp_dir, l_key)
                layer_prompt = (
                    f"Layer {layer_idx + 1}/{len(l_nodes)} ({l_key}). "
                    f"Return compacted summary or empty string.\n\n"
                    f"FIRST: cd {layer_dir}\n"
                    f"Layer files are in that directory. Project source is at {project_dir}\n\n"
                    f"{layer_content}"
                )

                logger.info("[COMPACT] Phase 3: feeding %s (%d chars)", l_key, len(layer_content))

                try:
                    layer_result = await agent.run(
                        role=role_config,
                        prompt=layer_prompt,
                        system_prompt=CONTEXT_PROMPT,
                        files=[],
                        images=[],
                        model=model,
                        cwd=temp_dir,
                        session_id=session_id,
                    )
                    if not session_id:
                        session_id = layer_result.parsed.metadata.get("session_id")

                    response_text = layer_result.parsed.content.strip()
                    is_error = layer_result.parsed.metadata.get("is_error", False)

                    if is_error:
                        logger.error("[COMPACT] Phase 3: %s → CLI error: %r", l_key, response_text[:200])
                        abort_reason = f"Layer {l_key}: CLI error — {response_text[:200]}"
                        break

                    if response_text:
                        compacted_segments.append(response_text)
                        layers_kept += 1
                        logger.info("[COMPACT] Phase 3: %s → kept (%d chars)", l_key, len(response_text))
                    else:
                        layers_discarded += 1
                        logger.info("[COMPACT] Phase 3: %s → discarded", l_key)

                except CLIAgentError as exc:
                    logger.error("[COMPACT] Phase 3: %s failed: %s", l_key, exc)
                    abort_reason = f"Layer {l_key} failed: {exc}"
                    break

            if abort_reason:
                blob = ""
            else:
                blob = "\n\n".join(compacted_segments)

        finally:
            # ===================================================================
            # Phase 4: Cleanup
            # ===================================================================
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.info("[COMPACT] Phase 4: temp dir cleaned up")

        # ===================================================================
        # Phase 5: Return
        # ===================================================================
        blob_tokens = count_tokens(blob) if blob else 0
        compression = round((1 - blob_tokens / max(tlog.total_tokens, 1)) * 100, 1)

        _compact_meta = {
            "source_tree": canonical,
            "source_nodes": len(all_nodes),
            "source_tokens": tlog.total_tokens,
            "layers_kept": layers_kept,
            "layers_discarded": layers_discarded,
            "layers_errored": layers_errored,
            "output_tokens": blob_tokens,
            "compression_pct": compression,
            "cli_name": selected_cli,
            "session_id": session_id,
            **tlog.to_dict(),
        }

        if abort_reason:
            error = ToolOutput(
                status="error",
                content=(
                    f"Compaction aborted: {abort_reason}\n\n"
                    f"Source tree unchanged: {canonical}\n"
                    f"Processed before abort: {layers_kept} kept, {layers_discarded} discarded"
                ),
                content_type="text",
                metadata={**_compact_meta, "abort_reason": abort_reason},
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        if compression > 95.0 and layers_kept > 0:
            error = ToolOutput(
                status="error",
                content=(
                    f"Compaction failed: {compression}% compression exceeds 95% safety threshold.\n\n"
                    f"Source tree unchanged: {canonical}\n"
                    f"  Nodes: {len(all_nodes)} | Tokens: ~{tlog.total_tokens}\n"
                    f"  Layers: {layers_kept} kept, {layers_discarded} discarded, {layers_errored} errored\n"
                    f"  Output tokens: ~{blob_tokens}"
                ),
                content_type="text",
                metadata=_compact_meta,
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        summary_parts = [
            "Compaction complete.",
            "",
            f"Source: {canonical}",
            f"  Nodes: {len(all_nodes)} | Tokens: ~{tlog.total_tokens}",
            "",
            f"Compacted: {layers_kept} layers kept, {layers_discarded} discarded, {layers_errored} errored",
            f"  Tokens: ~{blob_tokens} | Compression: {compression}%",
            f"  CLI: {selected_cli} | Session: {session_id}",
            "",
            "=== COMPACTED SEED ===",
            blob,
        ]

        tool_output = ToolOutput(
            status="success",
            content="\n".join(summary_parts),
            content_type="text",
            metadata=_compact_meta,
        )
        return [TextContent(type="text", text=tool_output.model_dump_json())]


# ---------------------------------------------------------------------------
# regrowtree
# ---------------------------------------------------------------------------


class PalRegrowTool(BaseTool):
    """Seed a new PALTree from a compacted markdown blob.

    Reads a markdown file (produced by compacttree), splits on --- delimiters,
    and creates L-nodes from each segment. The user then continues growing
    the tree via normal growlayer calls.
    """

    def get_name(self) -> str:
        return "regrowtree"

    def get_description(self) -> str:
        return (
            "Seed a new PALTree from a compacted markdown file (produced by compacttree).\n"
            "Splits the file on '---' delimiters and creates one L-node per segment.\n"
            "Use growlayer afterwards to continue building the tree."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "source_markdown_path": {
                    "type": "string",
                    "description": "Absolute path to the compacted markdown file from compacttree.",
                },
                "new_tree_name": {
                    "type": "string",
                    "description": "Name for the new tree. No dots allowed.",
                },
                "directory": {
                    "type": "string",
                    "description": "Project directory for the new tree. Defaults to current working directory.",
                },
                "label": {
                    "type": "string",
                    "description": "Descriptive label for the new tree root.",
                },
            },
            "required": ["source_markdown_path", "new_tree_name"],
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

    async def prepare_prompt(self, _request) -> str:
        return ""

    def format_response(self, response: str, _request, _model_info=None) -> str:
        return response

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        import re

        from tools.models import ToolOutput
        from utils.palstore import (
            PalNode,
            PalRoot,
            add_palnode,
            load_tree,
            save_tree,
            update_index,
        )
        from utils.token_utils import count_tokens

        source_path = arguments.get("source_markdown_path", "")
        new_name = arguments.get("new_tree_name", "")
        directory = arguments.get("directory") or os.getcwd()
        label = arguments.get("label")

        # Validate inputs
        if not source_path or not os.path.isfile(source_path):
            error = ToolOutput(status="error", content=f"Source file not found: {source_path}", content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        if not new_name:
            error = ToolOutput(status="error", content="new_tree_name is required.", content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        if "." in new_name:
            error = ToolOutput(status="error", content="Tree name must not contain dots.", content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        canonical = f"{directory}:{new_name}"

        # Check for existing tree
        existing = load_tree(canonical)
        if existing is not None:
            error = ToolOutput(
                status="error",
                content=f"Tree already exists: {canonical}. Choose a different name.",
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        # Read and split the markdown blob
        with open(source_path, encoding="utf-8") as f:
            content = f.read()

        if not content.strip():
            error = ToolOutput(status="error", content="Source file is empty.", content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        # Split on --- delimiter (whitespace-tolerant)
        segments = re.split(r"\n\s*---\s*\n", content)
        segments = [seg.strip() for seg in segments if seg.strip()]

        if not segments:
            error = ToolOutput(status="error", content="No content segments found in source file.", content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        # Create the new tree
        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        new_tree = PalRoot(
            tree_path=canonical,
            label=label or f"Regrown from {os.path.basename(source_path)}",
            created_at=timestamp,
        )

        total_tokens = 0
        for i, segment in enumerate(segments):
            layer_key = f"L{i + 1}"
            # Extract label from first line
            first_line = segment.split("\n", 1)[0].strip()
            node_label = _truncate_label(first_line.lstrip("# ")) if first_line else f"Layer {i + 1}"

            node = PalNode(
                label=node_label,
                timestamp=timestamp,
                tool_name="regrowtree",
                input="Seeded from compacttree",
                output=segment,
                metadata={"seeded_from": source_path},
            )
            add_palnode(new_tree, "", layer_key, node)
            total_tokens += count_tokens(segment)

        save_tree(new_tree)
        update_index(canonical)

        logger.info("[REGROW] Tree created: %s (%d layers, %d tokens)", canonical, len(segments), total_tokens)

        summary_parts = [
            "Regrowth complete.",
            "",
            f"Source: {source_path}",
            f"New tree: {canonical}",
            f"  Layers: {len(segments)} | Tokens: ~{total_tokens}",
            "",
            "Use growlayer to continue building the tree.",
        ]

        tool_output = ToolOutput(
            status="success",
            content="\n".join(summary_parts),
            content_type="text",
            metadata={
                "new_tree": canonical,
                "layers": len(segments),
                "total_tokens": total_tokens,
                "source_file": source_path,
            },
        )
        return [TextContent(type="text", text=tool_output.model_dump_json())]


# ---------------------------------------------------------------------------
# deletenode
# ---------------------------------------------------------------------------


class PalDeleteTool(BaseTool):
    def get_name(self) -> str:
        return "deletenode"

    def get_description(self) -> str:
        return (
            "Delete a PALNode and shift subsequent same-prefix siblings down to fill the gap. "
            "Use treelist to find node paths."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tree_path": {
                    "type": "string",
                    "description": TREE_PATH_DESCRIPTION,
                },
                "node_path": {
                    "type": "string",
                    "description": NODE_PATH_DESCRIPTION,
                },
            },
            "required": ["tree_path", "node_path"],
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
        from utils.palstore import (
            delete_palnode_with_shift,
            load_tree,
            resolve_tree_path,
            save_tree,
        )

        tree_path = arguments.get("tree_path", "")
        node_path = arguments.get("node_path", "")

        try:
            canonical = resolve_tree_path(tree_path)
        except (KeyError, ValueError) as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        tree = load_tree(canonical)
        if tree is None:
            error = ToolOutput(status="error", content=f'PALTree file not found: "{canonical}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        try:
            result = delete_palnode_with_shift(tree, node_path)
        except (KeyError, ValueError) as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        save_tree(tree)

        from utils.palstore import TraversalLog

        tlog = TraversalLog(traversal_type="resolve")
        shifted_summary = [f"{old} -> {new}" for old, new in result["shifted"]]
        lines = [f"Deleted {result['deleted']}."]
        if shifted_summary:
            lines.append(f"Shifted {len(shifted_summary)} sibling(s): {', '.join(shifted_summary)}.")
        if result["had_children"]:
            lines.append("Warning: deleted node had children — subtree removed.")

        tool_output = ToolOutput(
            status="success",
            content=" ".join(lines),
            content_type="text",
            metadata={
                "tree_path": canonical,
                "deleted": result["deleted"],
                "shifted": shifted_summary,
                "had_children": result["had_children"],
                **tlog.to_dict(),
            },
        )
        return [TextContent(type="text", text=tool_output.model_dump_json())]


class PalDeleteTreeTool(BaseTool):
    def get_name(self) -> str:
        return "deletetree"

    def get_description(self) -> str:
        return "Delete an entire PALTree and its JSON file. Use treelist to find tree names."

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tree_path": {
                    "type": "string",
                    "description": TREE_PATH_DESCRIPTION,
                },
            },
            "required": ["tree_path"],
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
        from utils.palstore import (
            get_tree_file_path,
            load_index,
            resolve_tree_path,
            save_index,
        )

        tree_path = arguments.get("tree_path", "")
        if not tree_path:
            error = ToolOutput(status="error", content="tree_path is required.", content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        try:
            canonical = resolve_tree_path(tree_path)
        except (KeyError, ValueError) as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        tree_file = get_tree_file_path(canonical)

        if os.path.isfile(tree_file):
            os.remove(tree_file)

        # Remove from index
        index = load_index()
        index["trees"].pop(canonical, None)
        save_index(index)

        from utils.palstore import TraversalLog

        directory = canonical.rsplit(":", 1)[0]
        tlog = TraversalLog(traversal_type="none")
        tool_output = ToolOutput(
            status="success",
            content=f'PALTree "{canonical}" deleted.',
            content_type="text",
            metadata={"tree_path": canonical, "directory": directory, "file_removed": tree_file, **tlog.to_dict()},
        )
        return [TextContent(type="text", text=tool_output.model_dump_json())]
