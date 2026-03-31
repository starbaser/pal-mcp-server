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
    "Dot-path to a PALNode in a PALTree. Root trees use a plain name (e.g. 'myproject'). "
    "Child nodes extend with dot notation (e.g. 'myproject.L0', 'myproject.L0.Q0'). "
    "Use treelist to discover tree paths. Returned by newtree, addtreelayer, querynode, and forknode."
)


def _truncate_label(text: str, max_len: int = 80) -> str:
    """Truncate label at a word boundary with ellipsis. Collapses newlines to spaces."""
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    truncated = text[:max_len].rsplit(" ", 1)[0]
    return truncated + "…"


def _natural_sort_key(key: str) -> tuple:
    """Sort key that handles mixed alpha-numeric keys naturally: 0, 1, ..., 10."""
    import re

    parts = re.split(r"(\d+)", key)
    return tuple(int(p) if p.isdigit() else p for p in parts)


def _render_tree(tree, root_path: str = "", indent: int = 0) -> list[str]:
    """Render a tree's node dict as indented markdown link lines."""
    from utils.palstore import PalNode

    lines: list[str] = []

    def _render_nodes(nodes: dict[str, PalNode], parent_path: str, depth: int) -> None:
        p = "  " * depth
        for key, node in sorted(nodes.items(), key=lambda kv: _natural_sort_key(kv[0])):
            full_path = f"{parent_path}.{key}"
            ts = (node.timestamp or "")[:10]

            if node.label:
                if node.label.startswith(f"{key}:"):
                    label_part = node.label
                else:
                    label_part = f"{key}. {node.label}"
            else:
                label_part = key
            date_part = f" — {ts}" if ts else ""
            files_part = ""
            if node.files:
                basenames = ", ".join(os.path.basename(f) for f in node.files)
                files_part = f" — {basenames}"
            lines.append(f"{p}- [{label_part}](#{full_path}){date_part}{files_part}")

            if node.children:
                _render_nodes(node.children, full_path, depth + 1)

    if isinstance(tree, dict):
        _render_nodes(tree, root_path, indent)
    else:
        _render_nodes(tree.children, tree.tree_path, indent)

    return lines


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class PalAddTreeLayerRequest(ToolRequest):
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

    def get_annotations(self) -> dict:
        return {"readOnlyHint": False, "openWorldHint": False}

    def _resolve_tree(self, tree_path: str):
        """Load tree for a given tree_path. Returns (tree, root_id).

        Raises KeyError if tree not found.
        """
        from utils.palstore import load_tree, resolve_tree_location

        location = resolve_tree_location(tree_path)
        if location is None:
            raise KeyError(f'PALTree "{tree_path}" not found.')
        directory, root_id = location
        tree = load_tree(directory, root_id)
        if tree is None:
            raise KeyError(f'PALTree file not found: "{root_id}".')
        return tree, root_id


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
            "use addtreelayer to add nodes after creation."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tree_name": {
                    "type": "string",
                    "description": (
                        "Unique name for the PALTree (e.g. 'myproject'). "
                        "No dots allowed — dots are reserved for dot-path child notation. "
                        "Becomes the root tree_path."
                    ),
                },
                "directory": {
                    "type": "string",
                    "description": "Absolute path to the project directory this tree is associated with.",
                },
            },
            "required": ["tree_name", "directory"],
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
        from utils.palstore import PalRoot, save_tree, update_index

        tree_name = arguments.get("tree_name", "")
        directory = arguments.get("directory", "")

        if "." in tree_name:
            error = ToolOutput(
                status="error",
                content="tree_name must not contain dots. Dots are reserved for path notation.",
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        from utils.palstore import resolve_tree_location

        existing = resolve_tree_location(tree_name)
        if existing is not None:
            error = ToolOutput(
                status="error",
                content=f'PALTree "{tree_name}" already exists.',
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        tree = PalRoot(
            tree_path=tree_name,
            directory=directory,
            created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        save_tree(tree)
        update_index(directory, tree_name)

        tool_output = ToolOutput(
            status="success",
            content=(
                f"PALTree created.\n\n"
                f"tree_path: {tree_name}\n"
                f"directory: {directory}\n\n"
                f'Use addtreelayer(tree_path="{tree_name}", ...) to add context layers.'
            ),
            content_type="text",
            metadata={"tree_path": tree_name, "directory": directory},
        )
        return [TextContent(type="text", text=tool_output.model_dump_json())]


# ---------------------------------------------------------------------------
# addtreelayer
# ---------------------------------------------------------------------------


class PalAddTreeLayerTool(PalTreeBaseTool):
    def get_name(self) -> str:
        return "addtreelayer"

    def get_description(self) -> str:
        return (
            "Add a context node to an existing PALTree, embedding files and prose for an external model to process.\n"
            "Each call appends a new numbered layer; seed 4-6 key files per layer for best results.\n"
            "Use newtree to create a tree first, then querynode to retrieve stored context."
        )

    def get_request_model(self):
        return PalAddTreeLayerRequest

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
                content="addtreelayer requires a tree_path. Use newtree to create a tree first.",
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        try:
            tree, _ = self._resolve_tree(tree_path)
        except KeyError as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        from utils.file_diff import build_file_state_from_ancestry
        from utils.palstore import walk_palnode_ancestry
        from utils.palstore_builder import build_context_from_ancestry

        ancestors = walk_palnode_ancestry(tree, tree_path)
        self._ancestors = ancestors
        self._prior_file_state = build_file_state_from_ancestry(ancestors)
        self._injected_history = build_context_from_ancestry(ancestors)
        self._tree = tree
        self._tree_path = tree_path

        # Estimate context usage for continuation_offer reporting
        arguments["_context_used"] = len(self._injected_history) // 4 if self._injected_history else 0

        arguments.pop("continuation_id", None)
        arguments["thinking_mode"] = "max"
        arguments["temperature"] = 0

        return await super().execute(arguments)

    async def prepare_prompt(self, request: PalAddTreeLayerRequest) -> str:
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

    def format_response(
        self, response: str, _request: PalAddTreeLayerRequest, _model_info: Optional[dict] = None
    ) -> str:
        self._last_raw_response = response
        return f"{response}\n\n---\n\nAGENT'S TURN: Context layer stored. Use the tree_path to add more layers or query this tree."

    def _create_continuation_offer(self, _request, _model_info: Optional[dict] = None):
        import re

        from utils.palstore import get_next_key

        tree = getattr(self, "_tree", None)
        tree_path = getattr(self, "_tree_path", None)
        if tree is None or tree_path is None:
            return None

        # Walk up past L-prefixed ancestors to prevent L→L nesting
        parts = tree_path.split(".")
        while len(parts) > 1 and re.match(r"^L\d+$", parts[-1]):
            parts.pop()
        insertion_parent = ".".join(parts)

        self._insertion_parent = insertion_parent
        self._next_key = get_next_key(tree, insertion_parent, "L")
        self._new_tree_path = f"{insertion_parent}.{self._next_key}"

        context_window, context_used = self._get_context_token_info()
        return {
            "continuation_id": self._new_tree_path,
            "context_window": context_window,
            "context_used": context_used,
            "note": f"Layer stored at {self._new_tree_path}.",
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
            logger.warning("addtreelayer: missing tree state in _record_assistant_turn, skipping write")
            return

        raw = getattr(self, "_last_raw_response", response_text)
        model_name = model_info.get("model_name") if model_info else None
        provider_obj = model_info.get("provider") if model_info else None
        provider = str(provider_obj) if provider_obj and not isinstance(provider_obj, str) else provider_obj

        input_dict = {
            "tool_name": self.get_name(),
            "model": model_name,
            "tree_path": getattr(self, "_tree_path", None),
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
        from utils.palstore import load_tree, resolve_tree_location

        tree_path = arguments.get("tree_path", "")
        if not tree_path:
            error = ToolOutput(status="error", content="tree_path is required.", content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        location = resolve_tree_location(tree_path)
        if location is None:
            error = ToolOutput(status="error", content=f'PALTree "{tree_path}" not found.', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        directory, root_id = location
        tree = load_tree(directory, root_id)
        if tree is None:
            error = ToolOutput(status="error", content=f'PALTree file not found: "{root_id}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        insert_mode = arguments.get("insert", False)

        if insert_mode:
            return await self._handle_insert(tree, tree_path, arguments)
        else:
            return await self._handle_update(tree, tree_path, arguments)

    async def _handle_update(self, tree, tree_path: str, arguments: dict[str, Any]) -> list[TextContent]:
        from tools.models import ToolOutput
        from utils.palstore import resolve_palnode, save_tree

        if tree_path == tree.tree_path:
            error = ToolOutput(
                status="error", content="Cannot upsert the root node. Target a child node.", content_type="text"
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        node = resolve_palnode(tree, tree_path)
        if node is None:
            error = ToolOutput(status="error", content=f'Node not found: "{tree_path}".', content_type="text")
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
        result = ToolOutput(
            status="success",
            content=f"Updated {', '.join(updated_fields)} on {tree_path}.",
            content_type="text",
            metadata={"tree_path": tree_path, "updated_fields": updated_fields},
        )
        return [TextContent(type="text", text=result.model_dump_json())]

    async def _handle_insert(self, tree, tree_path: str, arguments: dict[str, Any]) -> list[TextContent]:
        from tools.models import ToolOutput
        from utils.palstore import PalNode, add_palnode, get_next_key, resolve_palnode, save_tree

        # Verify parent exists (root or node)
        if tree_path != tree.tree_path:
            parent = resolve_palnode(tree, tree_path)
            if parent is None:
                error = ToolOutput(
                    status="error", content=f'Parent node not found: "{tree_path}".', content_type="text"
                )
                return [TextContent(type="text", text=error.model_dump_json())]

        # Determine child key
        child_key = arguments.get("child_key")
        if not child_key:
            child_prefix = arguments.get("child_prefix", "")
            child_key = get_next_key(tree, tree_path, child_prefix)

        # Check for duplicate key
        if tree_path == tree.tree_path:
            siblings = tree.children
        else:
            parent_node = resolve_palnode(tree, tree_path)
            siblings = parent_node.children if parent_node else {}
        if child_key in siblings:
            error = ToolOutput(
                status="error",
                content=f'Key "{child_key}" already exists under "{tree_path}". Use update mode or choose a different key.',
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
            new_path = add_palnode(tree, tree_path, child_key, node)
        except ValueError as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        save_tree(tree)
        result = ToolOutput(
            status="success",
            content=f"Inserted node at {new_path}.",
            content_type="text",
            metadata={"tree_path": new_path, "child_key": child_key},
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
            tree, _ = self._resolve_tree(tree_path)
        except KeyError as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        from utils.palstore import resolve_palnode, walk_palnode_ancestry
        from utils.palstore_builder import build_context_from_ancestry

        node = resolve_palnode(tree, tree_path)

        if node is None and tree_path != tree.tree_path:
            error = ToolOutput(
                status="error",
                content=f"Node not found: {tree_path}",
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        self._parent_path = tree_path
        self._child_prefix = "Q"

        ancestors = walk_palnode_ancestry(tree, tree_path)

        self._injected_history = build_context_from_ancestry(ancestors)
        self._tree = tree
        self._tree_path = tree_path

        arguments["_context_used"] = len(self._injected_history) // 4 if self._injected_history else 0

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
        parent_path = getattr(self, "_parent_path", None)
        child_prefix = getattr(self, "_child_prefix", "Q")
        if tree is None or parent_path is None:
            return None

        self._next_key = get_next_key(tree, parent_path, child_prefix)
        self._new_tree_path = f"{parent_path}.{self._next_key}"

        context_window, context_used = self._get_context_token_info()
        return {
            "continuation_id": self._new_tree_path,
            "context_window": context_window,
            "context_used": context_used,
            "note": f"Query recorded at {self._new_tree_path}.",
        }

    def _record_assistant_turn(
        self, _continuation_id: str, response_text: str, request, model_info: Optional[dict]
    ) -> None:
        from utils.palstore import PalNode, add_palnode, save_tree
        from utils.response_formatter import render_markdown_output

        tree = getattr(self, "_tree", None)
        parent_path = getattr(self, "_parent_path", None)
        next_key = getattr(self, "_next_key", None)

        if tree is None or parent_path is None or next_key is None:
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
            "tree_path": getattr(self, "_tree_path", None),
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

        add_palnode(tree, parent_path, next_key, node)
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
            "Use treelist to find the tree_path to fork from; use addtreelayer or querynode with the returned fork tree_path."
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
            resolve_tree_location,
            save_tree,
        )

        tree_path = arguments.get("tree_path", "")
        label = arguments.get("label")

        location = resolve_tree_location(tree_path)
        if location is None:
            error = ToolOutput(status="error", content=f'PALTree "{tree_path}" not found.', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        directory, root_id = location
        tree = load_tree(directory, root_id)
        if tree is None:
            error = ToolOutput(status="error", content=f'PALTree file not found: "{root_id}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        next_key = get_next_key(tree, tree_path, "F")
        fork_node = PalNode(
            label=label,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        new_path = add_palnode(tree, tree_path, next_key, fork_node)
        save_tree(tree)

        tool_output = ToolOutput(
            status="success",
            content=(
                f"Fork created.\n\n"
                f"tree_path: {new_path}\n\n"
                f"Use this tree_path to build a new branch from this point."
            ),
            content_type="text",
            metadata={"tree_path": new_path, "parent_tree_path": tree_path},
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
            "List PALTrees and their full node trees, returning tree_paths needed for addtreelayer, querynode,\n"
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
                        "Absolute path to filter trees by project directory. "
                        "Defaults to the current working directory."
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
        from utils.palstore import list_trees, load_tree, resolve_palnode, resolve_tree_location

        directory = arguments.get("directory", os.getcwd())
        tree_path = arguments.get("tree_path")

        if tree_path:
            location = resolve_tree_location(tree_path)
            if location is None:
                error = ToolOutput(status="error", content=f'PALTree "{tree_path}" not found.', content_type="text")
                return [TextContent(type="text", text=error.model_dump_json())]

            dir_, root_id = location
            tree = load_tree(dir_, root_id)
            if tree is None:
                error = ToolOutput(status="error", content=f'PALTree file not found: "{root_id}".', content_type="text")
                return [TextContent(type="text", text=error.model_dump_json())]

            node = resolve_palnode(tree, tree_path)
            if node is None and tree_path == tree.tree_path:
                n_nodes = len(tree.children)
                lines = [f"{tree.tree_path}  ({n_nodes} nodes)"]
                lines.extend(_render_tree(tree, indent=1))
            elif node is not None:
                lines = [f"{tree_path}"]
                lines.extend(_render_tree(node.children, root_path=tree_path, indent=1))
            else:
                lines = [f"Node not found: {tree_path}"]

            content = "\n".join(lines)
        else:
            trees = list_trees(directory)
            if trees:
                lines = []
                for tree in trees:
                    n_nodes = len(tree.children)
                    lines.append(f"{tree.tree_path}  ({n_nodes} nodes)")
                    lines.extend(_render_tree(tree, indent=1))
                content = "\n".join(lines)
            else:
                scope = f" for directory '{directory}'" if directory else ""
                content = f"No PALTrees found{scope}."

        tool_output = ToolOutput(
            status="success",
            content=content,
            content_type="text",
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

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        from tools.models import ToolOutput
        from utils.palstore import load_tree, resolve_palnode, resolve_tree_location

        tree_path = arguments.get("tree_path", "")

        location = resolve_tree_location(tree_path)
        if location is None:
            error = ToolOutput(status="error", content=f'PALTree "{tree_path}" not found.', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        directory, root_id = location
        tree = load_tree(directory, root_id)
        if tree is None:
            error = ToolOutput(status="error", content=f'PALTree file not found: "{root_id}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        node = resolve_palnode(tree, tree_path)

        if node is None:
            error = ToolOutput(
                status="error",
                content=f"Node not found: {tree_path}",
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]
        else:
            from utils.response_formatter import format_layer_markdown

            content = format_layer_markdown(
                tree_path,
                label=node.label,
                model=node.model,
                timestamp=node.timestamp,
                files=node.files,
                input_text=node.input,
                output_text=node.output,
            )

        tool_output = ToolOutput(
            status="success",
            content=content,
            content_type="text",
            metadata={"tree_path": tree_path},
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
                    "description": "Current root tree_path to rename (e.g. 'myproject'). Use treelist to confirm it exists.",
                },
                "new_name": {
                    "type": "string",
                    "description": "New name for the tree (e.g. 'myproject-v2'). No dots allowed.",
                },
                "directory": {
                    "type": "string",
                    "description": "Absolute path to the project directory the tree is associated with.",
                },
            },
            "required": ["tree_path", "new_name", "directory"],
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
        from utils.palstore import rename_tree

        tree_path = arguments.get("tree_path", "")
        new_name = arguments.get("new_name", "")
        directory = arguments.get("directory", "")

        if not tree_path or not new_name or not directory:
            tool_output = ToolOutput(
                status="error",
                content="tree_path, new_name, and directory are all required.",
                content_type="text",
            )
            return [TextContent(type="text", text=tool_output.model_dump_json())]

        try:
            rename_tree(directory, tree_path, new_name)
        except KeyError as e:
            tool_output = ToolOutput(status="error", content=str(e), content_type="text")
            return [TextContent(type="text", text=tool_output.model_dump_json())]
        except ValueError as e:
            tool_output = ToolOutput(status="error", content=str(e), content_type="text")
            return [TextContent(type="text", text=tool_output.model_dump_json())]

        tool_output = ToolOutput(
            status="success",
            content=(f"Tree renamed.\n\nold tree_path: {tree_path}\nnew tree_path: {new_name}\ndirectory: {directory}"),
            content_type="text",
            metadata={"tree_path": new_name, "old_tree_path": tree_path, "directory": directory},
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
        from utils.palstore import load_tree, resolve_tree_location

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

        location = resolve_tree_location(tree_path)
        if location is None:
            error = ToolOutput(status="error", content=f'PALTree "{tree_path}" not found.', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        directory, root_id = location
        tree = load_tree(directory, root_id)
        if tree is None:
            error = ToolOutput(status="error", content=f'PALTree file not found: "{root_id}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

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
        doc.extend(self._walk_tree(tree.children, tree.tree_path, depth=2))

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
            metadata={"tree_path": tree_path, "output_path": output_path, "nodes": n_nodes},
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
        from utils.palstore import load_tree, resolve_palnode, resolve_tree_location

        tree_path = arguments.get("tree_path", "")

        location = resolve_tree_location(tree_path)
        if location is None:
            error = ToolOutput(status="error", content=f'PALTree "{tree_path}" not found.', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        directory, root_id = location
        tree = load_tree(directory, root_id)
        if tree is None:
            error = ToolOutput(status="error", content=f'PALTree file not found: "{root_id}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        node = resolve_palnode(tree, tree_path)
        if node is None and tree_path == tree.tree_path:
            collected = self._collect_files(tree.children, tree.tree_path)
        elif node is not None:
            collected = self._collect_files(node.children, tree_path)
            if node.files:
                collected.insert(0, (tree_path, node.timestamp or "", node.label or "", list(node.files)))
        else:
            error = ToolOutput(status="error", content=f"Node not found: {tree_path}", content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        if not collected:
            content = f"No files attached to any node in '{tree_path}'."
        else:
            total_files = sum(len(files) for _, _, _, files in collected)
            lines = [f"**{total_files} files across {len(collected)} nodes in '{tree_path}':**", ""]
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

    def _find_nodes_with_file(self, nodes: dict, parent_path: str, file_path: str) -> list[tuple[str, str, "Any"]]:
        """Recursively find nodes referencing file_path. Returns (node_path, timestamp, node)."""
        from utils.palstore import PalNode

        results: list[tuple[str, str, PalNode]] = []
        for key in sorted(nodes, key=_natural_sort_key):
            node = nodes[key]
            full_path = f"{parent_path}.{key}"
            if node.files and file_path in node.files:
                results.append((full_path, node.timestamp or "", node))
            if node.children:
                results.extend(self._find_nodes_with_file(node.children, full_path, file_path))
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
        from utils.palstore import load_tree, resolve_palnode, resolve_tree_location

        tree_path = arguments.get("tree_path", "")
        file_path = arguments.get("file_path", "")

        if not file_path:
            error = ToolOutput(status="error", content="file_path is required.", content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        location = resolve_tree_location(tree_path)
        if location is None:
            error = ToolOutput(status="error", content=f'PALTree "{tree_path}" not found.', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        directory, root_id = location
        tree = load_tree(directory, root_id)
        if tree is None:
            error = ToolOutput(status="error", content=f'PALTree file not found: "{root_id}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        # Search from root or subtree
        node = resolve_palnode(tree, tree_path)
        if node is None and tree_path == tree.tree_path:
            matches = self._find_nodes_with_file(tree.children, tree.tree_path, file_path)
        elif node is not None:
            matches = self._find_nodes_with_file(node.children, tree_path, file_path)
            if node.files and file_path in node.files:
                matches.insert(0, (tree_path, node.timestamp or "", node))
        else:
            error = ToolOutput(status="error", content=f"Node not found: {tree_path}", content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        if not matches:
            error = ToolOutput(
                status="error",
                content=f"File '{file_path}' not found in any node under '{tree_path}'. Use listnodefiles to discover attached files.",
                content_type="text",
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        # Use most recent node
        matches.sort(key=lambda m: m[1], reverse=True)
        node_path, timestamp, target_node = matches[0]

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
            metadata={"tree_path": tree_path, "file_path": file_path, "source": source, "node_path": node_path},
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
                "file_path": {
                    "type": "string",
                    "description": "Absolute path of the file to attach to the node. Must exist on disk.",
                },
            },
            "required": ["tree_path", "file_path"],
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
        from utils.palstore import load_tree, resolve_palnode, resolve_tree_location, save_tree

        tree_path = arguments.get("tree_path", "")
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

        location = resolve_tree_location(tree_path)
        if location is None:
            error = ToolOutput(status="error", content=f'PALTree "{tree_path}" not found.', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        directory, root_id = location
        tree = load_tree(directory, root_id)
        if tree is None:
            error = ToolOutput(status="error", content=f'PALTree file not found: "{root_id}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        node = resolve_palnode(tree, tree_path)
        if node is None:
            error = ToolOutput(status="error", content=f"Node not found: {tree_path}", content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        if file_path in node.files:
            tool_output = ToolOutput(
                status="success",
                content=f"File already attached to node {tree_path}: {file_path}",
                content_type="text",
                metadata={"tree_path": tree_path, "file_path": file_path, "action": "already_present"},
            )
            return [TextContent(type="text", text=tool_output.model_dump_json())]

        node.files.append(file_path)
        save_tree(tree)

        tool_output = ToolOutput(
            status="success",
            content=f"File attached to node {tree_path}: {file_path}\n\nTotal files on node: {len(node.files)}",
            content_type="text",
            metadata={
                "tree_path": tree_path,
                "file_path": file_path,
                "action": "added",
                "total_files": len(node.files),
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
            "Traverse a PALTree path from start_node to end_node and render the exact "
            "conversation history content that would be sent to the PAL model — full thread "
            "rehydration with all turns and file blobs. "
            "The end_node must be a descendant of start_node."
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
                        "Segment path relative to tree root for the start of the range"
                        "(e.g. '0', '2.1'). Inclusive."
                    ),
                },
                "end_node": {
                    "type": "string",
                    "description": (
                        "Segment path relative to tree root for the end of the range"
                        "(e.g. '4', '2.1.0'). Must be a descendant of start_node. Inclusive."
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
        from utils.palstore import load_tree, resolve_tree_location, walk_palnode_range
        from utils.palstore_builder import build_context_from_ancestry

        tree_path = arguments.get("tree_path", "")
        start_node = arguments.get("start_node", "")
        end_node = arguments.get("end_node", "")

        if not start_node or not end_node:
            error = ToolOutput(
                status="error", content="Both start_node and end_node are required.", content_type="text"
            )
            return [TextContent(type="text", text=error.model_dump_json())]

        location = resolve_tree_location(tree_path)
        if location is None:
            error = ToolOutput(status="error", content=f'PALTree "{tree_path}" not found.', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        directory, root_id = location
        tree = load_tree(directory, root_id)
        if tree is None:
            error = ToolOutput(status="error", content=f'PALTree file not found: "{root_id}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        start_full = f"{root_id}.{start_node}"
        end_full = f"{root_id}.{end_node}"

        try:
            range_nodes = walk_palnode_range(tree, start_full, end_full)
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
                "tree_path": tree_path,
                "start_node": start_node,
                "end_node": end_node,
                "traversed_path": f"{start_full} → {end_full}",
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
                "source_path": {
                    "type": "string",
                    "description": "Full dot-path of the node to move (e.g. 'myproject.0.1.2').",
                },
                "dest_parent": {
                    "type": "string",
                    "description": "Full dot-path of the destination parent (e.g. 'myproject' for root level).",
                },
                "dest_key": {
                    "type": "string",
                    "description": "Key name at destination (e.g. '3'). If omitted, auto-generates the next available key using the source node's key prefix.",
                },
            },
            "required": ["source_path", "dest_parent"],
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
            parse_tree_path,
            resolve_tree_location,
            save_tree,
        )

        source_path = arguments.get("source_path", "")
        dest_parent = arguments.get("dest_parent", "")
        dest_key = arguments.get("dest_key")

        root_id, _ = parse_tree_path(source_path)

        location = resolve_tree_location(root_id)
        if location is None:
            error = ToolOutput(status="error", content=f'PALTree "{root_id}" not found.', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        directory, root_id = location
        tree = load_tree(directory, root_id)
        if tree is None:
            error = ToolOutput(status="error", content=f'PALTree file not found: "{root_id}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        if dest_key is None:
            _, src_segments = parse_tree_path(source_path)
            src_last_key = src_segments[-1] if src_segments else ""
            prefix = _get_key_prefix(src_last_key) or ""
            dest_key = get_next_key(tree, dest_parent, prefix)

        try:
            new_path = move_palnode(tree, source_path, dest_parent, dest_key)
        except (KeyError, ValueError) as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        save_tree(tree)

        tool_output = ToolOutput(
            status="success",
            content=f"Moved {source_path} to {new_path}.",
            content_type="text",
            metadata={"source_path": source_path, "new_path": new_path, "dest_parent": dest_parent},
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
            "Deep copy a PALTree subtree to a new location, preserving the original. "
            "Use treelist to find node paths."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "source_path": {
                    "type": "string",
                    "description": "Full dot-path of the node to copy (e.g. 'myproject.0.1.2').",
                },
                "dest_parent": {
                    "type": "string",
                    "description": "Full dot-path of the destination parent (e.g. 'myproject' for root level).",
                },
                "dest_key": {
                    "type": "string",
                    "description": "Key name at destination (e.g. '3'). If omitted, auto-generates the next available key using the source node's key prefix.",
                },
            },
            "required": ["source_path", "dest_parent"],
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
            parse_tree_path,
            resolve_tree_location,
            save_tree,
        )

        source_path = arguments.get("source_path", "")
        dest_parent = arguments.get("dest_parent", "")
        dest_key = arguments.get("dest_key")

        root_id, _ = parse_tree_path(source_path)

        location = resolve_tree_location(root_id)
        if location is None:
            error = ToolOutput(status="error", content=f'PALTree "{root_id}" not found.', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        directory, root_id = location
        tree = load_tree(directory, root_id)
        if tree is None:
            error = ToolOutput(status="error", content=f'PALTree file not found: "{root_id}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        if dest_key is None:
            _, src_segments = parse_tree_path(source_path)
            src_last_key = src_segments[-1] if src_segments else ""
            prefix = _get_key_prefix(src_last_key) or ""
            dest_key = get_next_key(tree, dest_parent, prefix)

        try:
            new_path = copy_palnode(tree, source_path, dest_parent, dest_key)
        except (KeyError, ValueError) as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        save_tree(tree)

        tool_output = ToolOutput(
            status="success",
            content=f"Copied {source_path} to {new_path}.",
            content_type="text",
            metadata={"source_path": source_path, "new_path": new_path, "dest_parent": dest_parent},
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
                "start_path": {
                    "type": "string",
                    "description": "Full dot-path of range start (e.g. 'myproject.0.1.analyze'). Inclusive.",
                },
                "end_path": {
                    "type": "string",
                    "description": "Full dot-path of range end (e.g. 'myproject.0.1.analyze.1.thinkdeep.1.planner'). Must be a descendant of start. Inclusive.",
                },
                "dest_parent": {
                    "type": "string",
                    "description": "Where to insert the folded node. Defaults to the root tree_path (promotes to layer).",
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
            "required": ["start_path", "end_path"],
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
            parse_tree_path,
            resolve_tree_location,
            save_tree,
        )

        start_path = arguments.get("start_path", "")
        end_path = arguments.get("end_path", "")
        dest_parent = arguments.get("dest_parent")
        dest_key = arguments.get("dest_key")
        label = arguments.get("label")

        root_id, _ = parse_tree_path(start_path)

        location = resolve_tree_location(root_id)
        if location is None:
            error = ToolOutput(status="error", content=f'PALTree "{root_id}" not found.', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        directory, root_id = location
        tree = load_tree(directory, root_id)
        if tree is None:
            error = ToolOutput(status="error", content=f'PALTree file not found: "{root_id}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        try:
            folded = fold_palnode_range(tree, start_path, end_path)
        except (KeyError, ValueError) as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        if label is not None:
            folded.label = label

        if dest_parent is None:
            dest_parent = tree.tree_path

        if dest_key is None:
            dest_key = get_next_key(tree, dest_parent, "")

        try:
            new_path = add_palnode(tree, dest_parent, dest_key, folded)
        except (KeyError, ValueError) as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        save_tree(tree)

        content_len = len(folded.content or "")
        file_count = len(folded.files)
        tool_output = ToolOutput(
            status="success",
            content=f"Folded range {start_path}..{end_path} into {new_path} ({content_len} chars, {file_count} files).",
            content_type="text",
            metadata={
                "new_path": new_path,
                "start_path": start_path,
                "end_path": end_path,
                "content_length": content_len,
                "file_count": file_count,
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
                "node_path": {
                    "type": "string",
                    "description": "Full dot-path of the node to delete (e.g. 'myproject.3').",
                },
            },
            "required": ["node_path"],
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
            parse_tree_path,
            resolve_tree_location,
            save_tree,
        )

        node_path = arguments.get("node_path", "")

        root_id, _ = parse_tree_path(node_path)

        location = resolve_tree_location(root_id)
        if location is None:
            error = ToolOutput(status="error", content=f'PALTree "{root_id}" not found.', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        directory, root_id = location
        tree = load_tree(directory, root_id)
        if tree is None:
            error = ToolOutput(status="error", content=f'PALTree file not found: "{root_id}".', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        try:
            result = delete_palnode_with_shift(tree, node_path)
        except (KeyError, ValueError) as exc:
            error = ToolOutput(status="error", content=str(exc), content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        save_tree(tree)

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
                "deleted": result["deleted"],
                "shifted": shifted_summary,
                "had_children": result["had_children"],
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
                    "description": "Root name of the PALTree to delete (e.g. 'myproject').",
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
            resolve_tree_location,
            save_index,
        )

        tree_path = arguments.get("tree_path", "")
        if not tree_path:
            error = ToolOutput(status="error", content="tree_path is required.", content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        location = resolve_tree_location(tree_path)
        if location is None:
            error = ToolOutput(status="error", content=f'PALTree "{tree_path}" not found.', content_type="text")
            return [TextContent(type="text", text=error.model_dump_json())]

        directory, root_id = location
        tree_file = get_tree_file_path(directory, root_id)

        if os.path.isfile(tree_file):
            os.remove(tree_file)

        # Remove from index
        index = load_index()
        index["trees"].pop(tree_path, None)
        save_index(index)

        tool_output = ToolOutput(
            status="success",
            content=f'PALTree "{tree_path}" deleted.',
            content_type="text",
            metadata={"tree_path": tree_path, "directory": directory, "file_removed": tree_file},
        )
        return [TextContent(type="text", text=tool_output.model_dump_json())]
