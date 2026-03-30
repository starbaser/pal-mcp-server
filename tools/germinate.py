"""
Germinate tool — automated PALTree builder.

Scans a project directory, identifies architectural layers (inner core to outer bark),
then analyzes each layer with accumulated context. Each layer becomes a nested query
node with chain-of-thought synthesis, producing a complete PALTree in a single invocation.
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from mcp.types import TextContent

from config import DEFAULT_MODEL
from systemprompts.germinate_prompt import GERMINATE_ANALYSIS_PROMPT, GERMINATE_SYNTHESIS_PROMPT
from tools.shared.base_tool import BaseTool

logger = logging.getLogger(__name__)

EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    ".venv",
    "venv",
    ".env",
    ".tox",
    ".eggs",
    "dist",
    "build",
    ".nix-profile",
    ".direnv",
    ".devenv",
    ".kitstore",
    ".claude",
}

EXCLUDED_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".so",
    ".o",
    ".a",
    ".dylib",
    ".class",
    ".jar",
    ".whl",
    ".egg",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".svg",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp3",
    ".mp4",
    ".wav",
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
    ".lock",
    ".db",
    ".sqlite",
    ".sqlite3",
}

# Directory name → (layer_name, ring_number)
DIR_LAYER_MAP = {
    "core": ("Core", 0),
    "models": ("Core", 0),
    "model": ("Core", 0),
    "schema": ("Core", 0),
    "schemas": ("Core", 0),
    "types": ("Core", 0),
    "entities": ("Core", 0),
    "utils": ("Foundation", 1),
    "lib": ("Foundation", 1),
    "helpers": ("Foundation", 1),
    "common": ("Foundation", 1),
    "shared": ("Foundation", 1),
    "services": ("Business Logic", 2),
    "service": ("Business Logic", 2),
    "logic": ("Business Logic", 2),
    "domain": ("Business Logic", 2),
    "workflows": ("Business Logic", 2),
    "api": ("Interface", 3),
    "routes": ("Interface", 3),
    "handlers": ("Interface", 3),
    "views": ("Interface", 3),
    "endpoints": ("Interface", 3),
    "controllers": ("Interface", 3),
    "tools": ("Extensions", 4),
    "plugins": ("Extensions", 4),
    "extensions": ("Extensions", 4),
    "middleware": ("Extensions", 4),
    "cli": ("Surface", 5),
    "ui": ("Surface", 5),
    "web": ("Surface", 5),
    "frontend": ("Surface", 5),
    "config": ("Configuration", 6),
    "conf": ("Configuration", 6),
    "settings": ("Configuration", 6),
    "systemprompts": ("Configuration", 6),
}

# Filename patterns → (layer_name, ring_number)
FILE_LAYER_PATTERNS = [
    (["model", "schema", "types", "entity"], ("Core", 0)),
    (["util", "helper", "common"], ("Foundation", 1)),
    (["service", "manager", "worker"], ("Business Logic", 2)),
    (["route", "handler", "view", "endpoint", "controller"], ("Interface", 3)),
    (["plugin", "extension", "middleware"], ("Extensions", 4)),
    (["config", "settings"], ("Configuration", 6)),
    (["main", "app", "cli", "server", "__main__"], ("Surface", 5)),
]


@dataclass
class LayerSpec:
    name: str
    ring: int
    description: str
    files: list[str] = field(default_factory=list)
    key_files: list[str] = field(default_factory=list)


def _is_source_file(path: str) -> bool:
    """Check if a file is a source code file worth analyzing."""
    _, ext = os.path.splitext(path)
    if ext.lower() in EXCLUDED_EXTENSIONS:
        return False
    if os.path.basename(path).startswith("."):
        return False
    return True


def _select_key_files(files: list[str], max_files: int = 6) -> list[str]:
    """Select the most important files from a layer for analysis.

    Ranks by file size (larger files tend to contain more logic) and
    prioritizes recognized names (models.py, server.py, etc.).
    """
    if len(files) <= max_files:
        return files[:]

    scored = []
    for f in files:
        try:
            size = os.path.getsize(f)
        except OSError:
            size = 0
        basename = os.path.basename(f).lower()
        # Boost recognized important names
        boost = 0
        for patterns, _ in FILE_LAYER_PATTERNS:
            if any(p in basename for p in patterns):
                boost = 10000
                break
        if basename in ("__init__.py", "conftest.py"):
            boost = -5000
        scored.append((size + boost, f))

    scored.sort(reverse=True)
    return [f for _, f in scored[:max_files]]


def scan_project_layers(directory: str) -> list[LayerSpec]:
    """Scan a project directory and classify files into architectural layers.

    Three-pass approach:
    1. Directory topology — classify by parent directory name
    2. File-level fallback — classify by filename patterns
    3. Merge thin layers (<3 files) into neighbors
    """
    layer_files: dict[tuple[str, int], list[str]] = {}
    unclassified: list[str] = []

    for root, dirs, files in os.walk(directory):
        # Prune excluded directories
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]

        rel_root = os.path.relpath(root, directory)
        parts = rel_root.split(os.sep) if rel_root != "." else []

        # Pass 1: directory-level classification
        dir_layer = None
        for part in parts:
            part_lower = part.lower()
            if part_lower in DIR_LAYER_MAP:
                dir_layer = DIR_LAYER_MAP[part_lower]
                break

        # Skip test directories entirely
        if any(p.lower() in ("tests", "test", "testing", "spec", "specs") for p in parts):
            continue

        for fname in files:
            fpath = os.path.join(root, fname)
            if not _is_source_file(fpath):
                continue

            if dir_layer:
                layer_files.setdefault(dir_layer, []).append(fpath)
            else:
                unclassified.append(fpath)

    # Pass 2: file-level classification for unclassified files
    still_unclassified = []
    for fpath in unclassified:
        basename = os.path.basename(fpath).lower()
        stem = os.path.splitext(basename)[0]
        matched = False
        for patterns, layer_info in FILE_LAYER_PATTERNS:
            if any(p in stem for p in patterns):
                layer_files.setdefault(layer_info, []).append(fpath)
                matched = True
                break
        if not matched:
            still_unclassified.append(fpath)

    # Put remaining unclassified files into a "Root" layer
    if still_unclassified:
        root_layer = ("Root", 5)
        layer_files.setdefault(root_layer, []).extend(still_unclassified)

    # Build LayerSpec list sorted by ring number
    layers: list[LayerSpec] = []
    for (name, ring), files in sorted(layer_files.items(), key=lambda x: x[0][1]):
        layers.append(
            LayerSpec(
                name=name,
                ring=ring,
                description=f"{name} layer ({len(files)} files)",
                files=sorted(files),
                key_files=_select_key_files(sorted(files)),
            )
        )

    # Pass 3: merge thin layers (<3 files) into nearest neighbor
    layers = _merge_thin_layers(layers)

    return layers


def _merge_thin_layers(layers: list[LayerSpec], min_files: int = 3) -> list[LayerSpec]:
    """Merge layers with fewer than min_files into the nearest neighbor by ring."""
    if len(layers) <= 1:
        return layers

    thin = [ly for ly in layers if len(ly.files) < min_files]
    if not thin:
        return layers

    thick = [ly for ly in layers if len(ly.files) >= min_files]
    if not thick:
        return layers

    for t in thin:
        # Find nearest thick layer by ring distance
        nearest = min(thick, key=lambda x: abs(x.ring - t.ring))
        nearest.files.extend(t.files)
        nearest.files.sort()
        nearest.key_files = _select_key_files(nearest.files)
        nearest.description = f"{nearest.name} layer ({len(nearest.files)} files)"

    return thick


def _read_file_content(path: str, max_chars: int = 30000) -> str:
    """Read file content, truncating if too large."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            content = f.read(max_chars)
        if len(content) == max_chars:
            content += "\n... [truncated]"
        return content
    except OSError as e:
        return f"[Error reading file: {e}]"


def _format_files_for_prompt(files: list[str], directory: str) -> str:
    """Format file contents for injection into a model prompt."""
    sections = []
    for fpath in files:
        rel = os.path.relpath(fpath, directory)
        content = _read_file_content(fpath)
        sections.append(f"=== FILE: {rel} ===\n{content}\n=== END FILE ===")
    return "\n\n".join(sections)


class GerminateTool(BaseTool):
    def get_name(self) -> str:
        return "germinate"

    def get_description(self) -> str:
        return (
            "Automatically build a PALTree for a project by analyzing its codebase in concentric\n"
            "layers (inner core to outer bark). Scans the project, identifies architectural layers,\n"
            "then deeply analyzes each layer with accumulated context. Each layer becomes a query\n"
            "node with chain-of-thought synthesis, creating a complete, queryable knowledge base.\n"
            "Use treelist first to check if a tree already exists."
        )

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "Absolute path to the project directory to analyze. Defaults to current working directory.",
                },
                "tree_name": {
                    "type": "string",
                    "description": "Name for the PALTree. Defaults to the directory basename.",
                },
                "model": {
                    "type": "string",
                    "description": "Model to use for analysis. Defaults to auto-selected.",
                },
            },
            "required": [],
            "additionalProperties": False,
        }

    def get_annotations(self) -> dict:
        return {"readOnlyHint": False, "openWorldHint": False}

    def get_system_prompt(self) -> str:
        return ""

    def get_request_model(self):
        from tools.shared.base_models import ToolRequest

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
        from tools.models import ToolOutput
        from utils.model_context import ModelContext
        from utils.palstore import (
            PalNode,
            PalRoot,
            add_palnode,
            get_next_key,
            resolve_store_location,
            save_store,
            update_index,
            walk_palnode_ancestry,
        )
        from utils.palstore_builder import build_context_from_ancestry
        from utils.response_formatter import render_markdown_output

        directory = arguments.get("directory") or os.getcwd()
        if not os.path.isdir(directory):
            return self._error(f"directory does not exist: {directory}")

        tree_name = arguments.get("tree_name") or os.path.basename(directory.rstrip("/"))
        if "." in tree_name:
            return self._error("tree_name must not contain dots.")

        # Check for existing tree
        existing = resolve_store_location(tree_name)
        if existing is not None:
            return self._error(f'PALTree "{tree_name}" already exists. Use treelist to view it.')

        # Resolve model
        model_name = arguments.get("model") or DEFAULT_MODEL
        if model_name == "auto":
            from providers import ModelProviderRegistry

            model_name = ModelProviderRegistry.get_preferred_fallback_model("extended_reasoning")

        try:
            provider = self.get_model_provider(model_name)
        except ValueError as e:
            return self._error(str(e))

        model_context = ModelContext(model_name=model_name)

        # Phase 0: scan project layers
        logger.info("germinate: scanning %s", directory)
        layers = scan_project_layers(directory)

        if not layers:
            return self._error(f"No source files found in {directory}.")

        total_files = sum(len(ly.files) for ly in layers)
        logger.info("germinate: found %d layers, %d total files", len(layers), total_files)

        # Phase 1: create tree + L1 manifest
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        store = PalRoot(tree_path=tree_name, directory=directory, created_at=now)
        save_store(store)
        update_index(directory, tree_name)

        manifest_summary = self._build_manifest(directory, layers)
        l1_node = PalNode(
            entry_type="store",
            label="Project manifest and layer structure",
            timestamp=now,
            model="germinate",
            files=[],
            input=render_markdown_output(
                {
                    "tool": "germinate",
                    "directory": directory,
                    "layer_count": len(layers),
                    "total_files": total_files,
                    "layers": [{"name": ly.name, "ring": ly.ring, "file_count": len(ly.files)} for ly in layers],
                }
            ),
            output=manifest_summary,
        )
        add_palnode(store, tree_name, "L1", l1_node)
        save_store(store)
        logger.info("germinate: L1 manifest created")

        # Phase 2: per-layer analysis loop
        parent_path = f"{tree_name}.L1"
        layers_completed = 0
        layers_failed = []

        for i, layer in enumerate(layers):
            layer_label = f"[{i + 1}/{len(layers)}] {layer.name}"
            logger.info("germinate: analyzing layer %s", layer_label)

            try:
                # Build accumulated context from ancestry
                ancestors = walk_palnode_ancestry(store, parent_path)
                prior_context = build_context_from_ancestry(ancestors)

                # (a) Analyze: inject files + prior context
                analysis = await self._analyze_layer(
                    provider, model_name, model_context, layer, directory, prior_context
                )

                # (b) Synthesize: CoT query with accumulated context
                synthesis = await self._synthesize_layer(
                    provider, model_name, model_context, layer, analysis, prior_context
                )

                # (c) Persist as nested Q-node
                next_key = get_next_key(store, parent_path, "Q")
                q_node = PalNode(
                    entry_type="query",
                    label=f"Layer {layer.ring}: {layer.name}",
                    timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    model=model_name,
                    files=layer.files,
                    input=render_markdown_output(
                        {
                            "tool": "germinate",
                            "layer": layer.name,
                            "ring": layer.ring,
                            "description": layer.description,
                            "key_files": [os.path.relpath(f, directory) for f in layer.key_files],
                            "analysis": analysis,
                        }
                    ),
                    output=synthesis,
                )
                new_path = add_palnode(store, parent_path, next_key, q_node)
                save_store(store)

                parent_path = new_path
                layers_completed += 1
                logger.info("germinate: layer %s complete → %s", layer_label, new_path)

            except Exception:
                logger.exception("germinate: failed on layer %s", layer_label)
                layers_failed.append(layer.name)

        # Phase 3: return summary
        status = "success" if not layers_failed else "partial"
        summary_lines = [
            "PALTree germinated.\n",
            f"tree_path: {tree_name}",
            f"directory: {directory}",
            f"model: {model_name}",
            f"layers analyzed: {layers_completed}/{len(layers)}",
        ]
        if layers_failed:
            summary_lines.append(f"failed layers: {', '.join(layers_failed)}")
        summary_lines.append(f'\nUse querynode(tree_path="{parent_path}", ...) to query the complete tree.')
        summary_lines.append(f'Use readnode(tree_path="{parent_path}") to read the final layer synthesis.')

        output = ToolOutput(
            status=status,
            content="\n".join(summary_lines),
            content_type="text",
            metadata={
                "tree_path": tree_name,
                "final_node": parent_path,
                "layers_completed": layers_completed,
                "layers_total": len(layers),
                "model": model_name,
            },
        )
        return [TextContent(type="text", text=output.model_dump_json())]

    async def _analyze_layer(
        self,
        provider,
        model_name: str,
        model_context,
        layer: LayerSpec,
        directory: str,
        prior_context: str,
    ) -> str:
        """Deep analysis of a single layer's code. File contents injected but not persisted."""
        file_contents = _format_files_for_prompt(layer.key_files, directory)

        context_section = ""
        if prior_context:
            context_section = f"\n\n=== PRIOR LAYER CONTEXT ===\n{prior_context}\n=== END PRIOR CONTEXT ==="

        prompt = (
            f"=== LAYER ANALYSIS: {layer.name} (Ring {layer.ring}) ===\n\n"
            f"{layer.description}\n\n"
            f"All files in this layer ({len(layer.files)} total):\n"
            + "\n".join(f"  {os.path.relpath(f, directory)}" for f in layer.files)
            + f"\n\nKey files shown below ({len(layer.key_files)} of {len(layer.files)}):\n\n"
            + file_contents
            + context_section
            + "\n\nAnalyze this layer thoroughly."
        )

        validated_temp, _ = self.validate_and_correct_temperature(0.0, model_context)
        response = provider.generate_content(
            prompt=prompt,
            model_name=model_name,
            system_prompt=GERMINATE_ANALYSIS_PROMPT,
            temperature=validated_temp,
            thinking_mode="high",
        )
        return response.content

    async def _synthesize_layer(
        self,
        provider,
        model_name: str,
        model_context,
        layer: LayerSpec,
        analysis: str,
        prior_context: str,
    ) -> str:
        """CoT synthesis of a layer's analysis with accumulated context."""
        prompt = (
            f"=== PALTREE QUERY ===\n\n"
            f'Layer "{layer.name}" (Ring {layer.ring}) has been analyzed.\n\n'
            f"Analysis findings:\n\n{analysis}\n\n"
            f"Synthesize this layer's role in the project architecture."
        )

        if prior_context:
            prompt = f"{prior_context}\n\n{prompt}"

        validated_temp, _ = self.validate_and_correct_temperature(0.0, model_context)
        response = provider.generate_content(
            prompt=prompt,
            model_name=model_name,
            system_prompt=GERMINATE_SYNTHESIS_PROMPT,
            temperature=validated_temp,
            thinking_mode="max",
        )
        return response.content

    def _build_manifest(self, directory: str, layers: list[LayerSpec]) -> str:
        """Build a human-readable project manifest."""
        lines = [
            f"# Project Manifest: {os.path.basename(directory)}",
            "",
            f"Directory: {directory}",
            f"Layers: {len(layers)}",
            f"Total files: {sum(len(ly.files) for ly in layers)}",
            "",
            "## Layer Structure",
            "",
        ]
        for layer in layers:
            lines.append(f"### Ring {layer.ring}: {layer.name} ({len(layer.files)} files)")
            for f in layer.key_files[:6]:
                lines.append(f"  - {os.path.relpath(f, directory)}")
            if len(layer.files) > 6:
                lines.append(f"  - ... and {len(layer.files) - 6} more")
            lines.append("")
        return "\n".join(lines)

    def _error(self, message: str) -> list[TextContent]:
        from tools.models import ToolOutput

        output = ToolOutput(status="error", content=message, content_type="text")
        return [TextContent(type="text", text=output.model_dump_json())]
