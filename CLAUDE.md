# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

```bash
# Setup and run server (handles venv, deps, MCP registration)
./run-server.sh

# View logs
tail -f logs/mcp_server.log        # Full server log
tail -f logs/mcp_activity.log      # Tool calls only

# Code quality (REQUIRED before commits)
./code_quality_checks.sh           # Runs ruff, black, isort, unit tests

# Testing
python -m pytest tests/ -v -m "not integration"     # Unit tests only
python -m pytest tests/test_refactor.py -v           # Single test file
./run_integration_tests.sh                           # Integration tests (needs Ollama)

# Simulator tests (end-to-end with real API keys)
python communication_simulator_test.py --quick                        # 6 essential tests
python communication_simulator_test.py --individual <test_name> -v    # Single test
python communication_simulator_test.py --list-tests                   # List all tests
```

**After any code change**: Restart Claude session for changes to take effect.

## Architecture Overview

PAL MCP Server enables AI CLIs (Claude Code, Gemini CLI, Codex CLI) to orchestrate multiple AI models within a single conversation. The server runs on stdio using JSON-RPC (MCP protocol).

### Request Flow

```
CLI Client (Claude/Gemini/Codex)
       │ MCP JSON-RPC
       ▼
   server.py
       │ handle_call_tool()
       ▼
   Tool Registry (TOOLS dict)
       │
       ▼
   Tool.execute()
       │ build_store_context() / reconstruct_thread_context()
       ▼
   Conversation Memory (utils/conversation_memory.py)
       │ Provider selection
       ▼
   ModelProviderRegistry
       │
       ▼
   External Model (Gemini/OpenAI/etc.)
```

### Key Components

**`server.py`** — Entry point and MCP protocol handler
- `TOOLS` dict maps tool names to instances (34 tools registered)
- `ESSENTIAL_TOOLS = {"version", "listmodels"}` — cannot be disabled
- `handle_call_tool()` routes requests, resolves models, reconstructs conversation context
- `configure_providers()` registers providers based on API keys
- `parse_model_option()` splits `"model:option"` format (e.g. `"gemini-pro:for"` → model + option), preserving OpenRouter suffixes (`:free`, `:beta`, `:preview`)
- PALTree continuation: `_resolve_store_continuation()` calls `build_store_context()` directly from PALTree paths (not just UUIDs). Creates a numeric child node under the target for each continuation turn. PALTree paths skip `reconstruct_thread_context` — dispatch in `handle_call_tool` is bifurcated.
- Response post-processing pipeline: `_inject_store_path_continuation()` → `_save_response_content()` → `_inject_saved_content_path()` → `_extract_gen_files()` → `_apply_output_format()`
- `_extract_gen_files()`: universal `#!/>` sigil extraction from model responses — saves complete files to `CODE_STORAGE_DIR/{encoded_cwd}/{call_id}/`, strips sigil blocks from response content, adds `gen_files` metadata

**`tools/`** — MCP tool implementations. Two base classes:
- `SimpleTool` (`tools/simple/base.py`) — single request/response (chat, clink, imagegen, perceive, PALTree tools)
- `WorkflowTool` (`tools/workflow/base.py`) — multi-step workflows with expert analysis (analyze, codereview, debug, planner, etc.)

Both inherit from `BaseTool` (`tools/shared/base_tool.py`). Required methods: `get_name()`, `get_description()`, `get_input_schema()`, `get_system_prompt()`, `execute()`.

**`providers/`** — AI provider abstraction
- `base.py`: Abstract `ModelProvider` interface with `generate_content()`, `get_capabilities()`, retry logic
- `openai_compatible.py`: Shared base for all non-Gemini providers (OpenAI, Azure, XAI, ZAI, DIAL, Custom, OpenRouter)
- `registry.py`: `ModelProviderRegistry` singleton — lazy-initializes providers, resolves models by priority
- Priority order: GOOGLE → OPENAI → AZURE → XAI → ZAI → DIAL → CUSTOM → OPENROUTER

**`utils/conversation_memory.py`** — Stateless MCP → Stateful conversations
- `ThreadContext` storage with UUID keys, configurable backend: `"file"` (default, survives restarts) or `"memory"` (in-process)
- `continuation_id` parameter enables multi-turn conversations
- Cross-tool continuation: context flows between any tools (analyze → codereview → debug)
- `build_conversation_history()`: Phase 1 collects turns in REVERSE chronological order (newest-first for token budgeting), Phase 2 reverses back to chronological for LLM presentation
- `get_conversation_file_list()`: deduplicates files across turns, newest reference wins

**`systemprompts/`** — Each tool has a corresponding `*_prompt.py` file (1:1 naming convention). Tools without prompts (clink, PALTree tools, listmodels, version, apilookup, challenge) return `""` from `get_system_prompt()`. `PALSHEBANG_PROMPT` (`systemprompts/palshebang_prompt.py`) is injected universally into every model-calling tool via `BaseTool.get_capability_system_prompts()` — it is not per-tool.

**`config.py`** — Central configuration: version, model defaults, token limits, storage paths, timeouts

**`conf/`** — JSON model catalogs per provider (e.g. `gemini_models.json`, `openai_models.json`). Each defines model capabilities, aliases, context windows, and intelligence scores.

### Model Resolution

Models are resolved early at the MCP boundary in `handle_call_tool()`:
1. Parse `model:option` format (e.g., `"gemini-pro:for"` → model name + option string)
2. Resolve `"auto"` → concrete model via `registry.get_preferred_fallback_model(tool_category)`
3. Create `ModelContext` with capabilities and token allocation
4. Pass resolved context to tool

Tools declare their preferred model tier via `get_model_category()` → `ToolModelCategory`:
- `EXTENDED_REASONING` — most tools (codereview, debug, analyze, thinkdeep, addtreelayer, querynode, etc.)
- `FAST_RESPONSE` — chat, listmodels, version, treelist, readnode, forknode, newtree, renametree, treedump, listnodefiles, readnodefile, writenodefile
- `BALANCED` — perceive, clink
- `IMAGE_GENERATION` — imagegen

Tools that override `requires_model() → False` bypass model resolution entirely: clink, planner, consensus, docgen, tracer, challenge, apilookup, listmodels, version, germinate, upsertnode, and all PALTree tools except addtreelayer and querynode.

### Tool System

**SimpleTool execution flow** (`tools/simple/base.py`):
1. Validate request via Pydantic model
2. Resolve model, create `ModelContext`
3. Call `prepare_prompt()` (tool-specific)
4. Process files: `filter_new_files()` → `read_files()` (skips files already in conversation history)
5. Augment system prompt with capability-specific additions + language instruction
6. Call `provider.generate_content()`
7. `_parse_response()` → `format_response()` → record assistant turn → create continuation offer

**WorkflowTool** adds multi-step investigation via `BaseWorkflowMixin` (`tools/workflow/workflow_mixin.py`):
- `execute_workflow()` drives step-by-step analysis with configurable `get_required_actions()` per step
- `should_call_expert_analysis()` decides whether to invoke the expert model
- `is_continuation_workflow()` — when `continuation_id` is present, skips multi-step and runs as single request

**PALTree Tools** (`tools/palstore.py`) have a split inheritance:
```
BaseTool (direct) ─── PalInitTool, PalForkTool, PalListTool, PalReadTool,
                      PalRenameTool, PalExportTool,
                      PalFileListTool, PalFileReadTool, PalFileWriteTool,
                      PalMoveTool, PalCopyTool, PalFoldTool, PalDeleteTool,
                      PalDeleteTreeTool, PalTraverseTool, PalUpsertTool
                      (requires_model=False, pure filesystem)

BaseTool (direct) ─── GerminateTool
                      (requires_model=False, manages own provider calls internally)

SimpleTool → PalStoreBaseTool ─── PalAddTreeLayerTool, PalQueryTool
                                  (requires_model=True, thinking_mode="max")
```

**PalNode model** (`utils/palstore.py`):
- Fields: `input: str`, `output: str`, `files: list[str] = []`, `metadata: dict[str, Any] = {}`, `children: dict[str, PalNode] = {}`, `label: str`, `timestamp: str`, `model: str`, `tool_name: str`
- `input` = full tool call data rendered via `render_markdown_output()` (uses `oboros.tome.dumps` — TOME BFS-linearized markdown with `§` sigils)
- `output` = full tool response rendered via `render_markdown_output()`
- `files` = flat list of absolute path strings attached to this node (populated by `addtreelayer`/`querynode` via `absolute_file_paths`, or by `writenodefile` post-hoc)
- Each node stores only its own layer's data — the O(n²) content duplication bug is fixed
- `format_layer_markdown` (used by `readnode`/`treedump`) accepts `input_text`/`output_text` params

**PalRoot model** (`utils/palstore.py`):
- Fields: `tree_path: str`, `directory: str`, `children: dict[str, PalNode] = {}`
- `model_validator(mode="before")` migrates legacy `store_id` → `tree_path` in JSON files

**PALTree Node Rules** (`utils/palstore.py`):

`add_palnode()` inserts a child node with no structural restrictions. Any node can have any child with any key.

**`utils/palstore_builder.py`** — `build_store_context()` builds enhanced arguments directly from PalNode ancestry for a given store path. Replaces the former `hydrate_thread_context` approach. Uses token-budgeted history building via `_build_budgeted_history()`.

### MCP Transport Limits

`MCP_PROMPT_SIZE_LIMIT` (~60K chars default) limits **user input** crossing MCP transport. It does NOT limit system prompts, file content embedded by tools, conversation history, or prompts sent to external models.

### clink Tool (CLI-to-CLI Bridge)

`tools/clink.py` spawns external AI CLIs as subagents:
- `requires_model() → False` — bypasses PAL model resolution, manages its own CLI invocations
- Sets `PAL_MCP_CLINK=1` to identify headless sessions
- Builds schema dynamically at `__init__` by querying the CLI registry for available clients and roles
- Loads agent definitions from `.claude/agents/` directories
- Returns structured JSON responses with continuation support
- Output exceeding `MAX_MCP_OUTPUT_TOKENS` is offloaded to `{cwd}/.claude/output/`
- `CLINK_CLI_OVERRIDE` env var forces a specific CLI client

## Tool Implementation Pattern

```python
from tools.shared.base_tool import BaseTool
from tools.models import ToolModelCategory

class MyTool(BaseTool):
    def get_name(self) -> str:
        return "mytool"

    def get_description(self) -> str:
        return "Tool description for MCP clients"

    def get_input_schema(self) -> dict:
        return {"type": "object", "properties": {...}}

    def get_system_prompt(self) -> str:
        return "AI instructions for this tool"

    def get_model_category(self) -> ToolModelCategory:
        return ToolModelCategory.BALANCED

    async def execute(self, arguments: dict) -> list[TextContent]:
        pass
```

Register in `server.py` TOOLS dict. Tools that bypass model resolution override `requires_model() -> False`.

## PALTree Tool Reference

MCP tool names follow a scope convention: **node tools** (`*node`) operate on a single PALNode, **tree tools** (`tree*`/`*tree`) operate on a subtree or the whole tree. Source class names (e.g. `PalAddTreeLayerTool`) are unchanged.

The MCP parameter `tree_path` identifies nodes using dot-path notation. The `PalRoot` model field is also `tree_path`. A `model_validator(mode="before")` on `PalRoot` transparently migrates legacy JSON files that still use the old `store_id` key.

**PALTree path** formal definition: `𝒫 = { r · s₁ · s₂ · ⋯ · sₖ  |  r ∈ 𝒩,  sᵢ = (tᵢ, nᵢ),  ρ → t₁,  ∀i: tᵢ → tᵢ₊₁ }`

| MCP tool name    | Source class         | Model required | Scope |
|------------------|----------------------|----------------|-------|
| `newtree`        | PalInitTool          | No             | tree  |
| `addtreelayer`   | PalAddTreeLayerTool  | Yes            | node  |
| `upsertnode`     | PalUpsertTool        | No             | node  |
| `querynode`      | PalQueryTool         | Yes            | node  |
| `treelist`       | PalListTool          | No             | tree  |
| `readnode`       | PalReadTool          | No             | node  |
| `forknode`       | PalForkTool          | No             | node  |
| `renametree`     | PalRenameTool        | No             | tree  |
| `treedump`       | PalExportTool        | No             | tree  |
| `listnodefiles`  | PalFileListTool      | No             | node  |
| `readnodefile`   | PalFileReadTool      | No             | node  |
| `writenodefile`  | PalFileWriteTool     | No             | node  |
| `traversetree`   | PalTraverseTool      | No             | tree  |
| `movenode`       | PalMoveTool          | No             | node  |
| `clonetree`      | PalCopyTool          | No             | tree  |
| `foldtree`       | PalFoldTool          | No             | tree  |
| `deletenode`     | PalDeleteTool        | No             | node  |
| `deletetree`     | PalDeleteTreeTool    | No             | tree  |
| `germinate`      | GerminateTool        | No (internal)  | tree  |

**`tools/germinate.py`** — Automated PALTree builder. Scans a project directory (defaults to CWD when `directory` is omitted), identifies architectural layers (inner core → outer bark), then analyzes each layer with accumulated CoT context. Key design:
- Inherits `BaseTool` directly with `requires_model=False` — manages its own `get_model_provider()` + `generate_content()` calls internally (same pattern as `ConsensusTool._consult_model()`)
- Hybrid layer identification: heuristic directory/filename classification → merge thin layers (<3 files) into neighbors
- Two model calls per layer: (a) analyze with file contents injected, (b) synthesize with accumulated ancestry context
- Nested numeric nodes for context accumulation — each layer's node is the child of the previous layer's node, creating a linear ancestry chain. `walk_palnode_ancestry()` does pure parent-chain traversal.
- File contents are injected into prompts but **not persisted** in nodes — only analysis/synthesis text is saved. File paths go in `PalNode.files`.
- `save_store()` after each layer for crash recovery of partial gestations.

Node structure produced:
```
myproject (root)
└── 0: project manifest
    └── 0: innermost layer analysis
        └── 0: next layer (sees full ancestry chain)
            └── 0: outer layer
```

## Environment Variables

Key variables (see `.env.example` for full list):
- `GEMINI_API_KEY`, `OPENAI_API_KEY`, `XAI_API_KEY`, `OPENROUTER_API_KEY` — Provider credentials
- `CUSTOM_API_URL` — Local models (Ollama, vLLM)
- `DEFAULT_MODEL` — Default model (`"auto"` for intelligent selection)
- `DISABLED_TOOLS` — Comma-separated list to disable tools (default: `analyze,refactor,testgen,secaudit,docgen,tracer`)
- `LOG_LEVEL` — DEBUG, INFO, WARNING, ERROR
- `PAL_STORAGE_DIR` — Persistent storage root (default: `~/.claude/pal`)
- `CONVERSATION_STORAGE_BACKEND` — `"file"` (default) or `"memory"`
- `MAX_MCP_OUTPUT_TOKENS` — Output token limit before file offload (default: 25000)

## Testing Strategy

1. **Unit tests** (`tests/`): Fast, no API calls, test individual functions. `asyncio_mode = auto` in pytest.ini.
2. **Integration tests** (`@pytest.mark.integration`): Use local Ollama models or real API keys.
3. **Simulator tests** (`simulator_tests/`): End-to-end with real API keys, 30 available tests.

Quick simulator mode covers: cross-tool continuation, conversation threading, consensus workflow, codereview workflow, planner workflow, token allocation.

### Test Configuration

`conftest.py` sets `DEFAULT_MODEL=gemini-2.5-flash` for all tests and registers dummy API keys. Auto-mode tests are identified by filename/testname containing `"auto_mode"`, `"intelligent_fallback"`, or `"per_tool_model_defaults"` — all other tests have auto mode disabled via monkeypatch. Use `@pytest.mark.integration` for tests requiring real API calls.

## Code Style

- Line length: 120 (black, isort, ruff)
- isort profile: `black`
- ruff selects: E, W, F, I, B, C4, UP; ignores E501, B008, C901, B904

## CI/CD

GitHub Actions workflows have been removed. `requires-python = ">=3.13"`.
