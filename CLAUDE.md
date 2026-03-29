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
       │ reconstruct_thread_context()
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
- `TOOLS` dict maps tool names to instances (31 tools registered)
- `ESSENTIAL_TOOLS = {"version", "listmodels"}` — cannot be disabled
- `handle_call_tool()` routes requests, resolves models, reconstructs conversation context
- `configure_providers()` registers providers based on API keys
- `parse_model_option()` splits `"model:option"` format (e.g. `"gemini-pro:for"` → model + option), preserving OpenRouter suffixes (`:free`, `:beta`, `:preview`)
- Store continuation: `_resolve_store_continuation()` hydrates threads from context store paths (not just UUIDs). Two modes: CONTINUE (same tool, same node) and FORK (auto-create fork + tool child)
- Response post-processing pipeline: `_inject_store_path_continuation()` → `_save_response_content()` → `_apply_output_format()`

**`tools/`** — MCP tool implementations. Two base classes:
- `SimpleTool` (`tools/simple/base.py`) — single request/response (chat, clink, imagegen, perceive, ctx tools)
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

**`systemprompts/`** — Each tool has a corresponding `*_prompt.py` file (1:1 naming convention). Tools without prompts (clink, ctx*, listmodels, version, apilookup, challenge) return `""` from `get_system_prompt()`.

**`config.py`** — Central configuration: version, model defaults, token limits, storage paths, timeouts

**`conf/`** — JSON model catalogs per provider (e.g. `gemini_models.json`, `openai_models.json`). Each defines model capabilities, aliases, context windows, and intelligence scores.

### Model Resolution

Models are resolved early at the MCP boundary in `handle_call_tool()`:
1. Parse `model:option` format (e.g., `"gemini-pro:for"` → model name + option string)
2. Resolve `"auto"` → concrete model via `registry.get_preferred_fallback_model(tool_category)`
3. Create `ModelContext` with capabilities and token allocation
4. Pass resolved context to tool

Tools declare their preferred model tier via `get_model_category()` → `ToolModelCategory`:
- `EXTENDED_REASONING` — most tools (codereview, debug, analyze, thinkdeep, ctxstore, ctxquery, etc.)
- `FAST_RESPONSE` — chat, listmodels, version, ctxlist, ctxread, ctxarm, ctxfork, ctxinit, ctxrename, ctxexport, ctxfilelist, ctxfileread
- `BALANCED` — perceive, clink
- `IMAGE_GENERATION` — imagegen

Tools that override `requires_model() → False` bypass model resolution entirely: clink, planner, consensus, docgen, tracer, challenge, apilookup, listmodels, version, and all ctx* tools except ctxstore and ctxquery.

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

**Context Tools** (`tools/context.py`) have a split inheritance:
```
BaseTool (direct) ─── CtxInitTool, CtxForkTool, CtxListTool, CtxReadTool,
                      CtxArmTool, CtxRenameTool, CtxExportTool,
                      CtxFileListTool, CtxFileReadTool
                      (requires_model=False, pure filesystem)

SimpleTool → ContextBaseTool ─── CtxStoreTool, CtxQueryTool
                                 (requires_model=True, thinking_mode="max")
```

**Context Store Tree Rules** (`utils/context_store.py`):

`add_child()` enforces structural node rules via `VALID_CHILD_KEYS`. Each parent type allows only specific child key categories:

```
           │ L-child │ Q-child │ F-child │ numeric │ tool-child
──────────┼─────────┼─────────┼─────────┼─────────┼───────────
root      │    ✓    │    ✗    │    ✓    │    ✗    │    ✗
store     │    ✗    │    ✓    │    ✓    │    ✗    │    ✗
query     │    ✗    │    ✓    │    ✓    │    ✓    │    ✗
fork      │    ✓    │    ✓    │    ✓    │    ✗    │    ✓
tool      │    ✗    │    ✗    │    ✓    │    ✓    │    ✗
```

Key rule: **L-nodes cannot have L-children**. `CtxStoreTool` uses `resolve_layer_insertion_point()` to find the correct sibling-level parent when called on an L-node path (e.g., `myproject.L7` → inserts `L8` at root, not `L7.L1`).

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

## Context Revival (ctxarm)

The `ctxarm` tool is a single-fire tripwire: it arms a context store for revival on the next user prompt, then automatically disarms. When armed, a `SessionStart` hook injects `additionalContext` forcing Claude to run `ctxlist` + `ctxquery` to restore project context, then removes the armed entry so subsequent prompts proceed normally.

After compaction, a `PostCompact` hook auto-arms the most recently used store for the project, so the next tool call triggers a full revival from the store (compaction summaries are lossy).

### How It Works

```
ctxarm tool ──▶ ~/.claude/pal/context/armed.json
                { "/path/to/project": "my-project" }

SessionStart hook ──▶ reads armed.json
                  ──▶ looks up cwd
                  ──▶ injects revival additionalContext
                  ──▶ removes cwd entry (disarms)

PostCompact hook ──▶ reads store-index.json
                 ──▶ finds most recent store for cwd
                 ──▶ arms it in compact-armed.json

PreToolUse hook ──▶ reads compact-armed.json
                ──▶ if armed: injects revival + disarms
                ──▶ fires on next tool call (immediate post-compact)
```

The tripwire uses two separate armed files and hook events:

| Scenario | Armed file | Hook | Fires on resume? |
|----------|-----------|------|-------------------|
| Manual arm (ctxarm) | `armed.json` | `SessionStart` | No |
| Post-compact auto-arm | `compact-armed.json` | `PreToolUse` | N/A (same session) |

Hook scripts are at `scripts/ctx-arm.sh` and `scripts/ctx-compact.sh`.

### Storage

- Armed state: `~/.claude/pal/context/armed.json` (directory → store_id mapping)
- Compact armed state: `~/.claude/pal/context/compact-armed.json`
- Store index: `~/.claude/pal/context/store-index.json`
- MCP handshake shows `[armed]` marker on armed stores

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

- **PR tests** (`test.yml`): Matrix across Python 3.10/3.11/3.12, runs lint + unit tests
- **Release** (`semantic-release.yml`): `python-semantic-release` on push to `main`, syncs version to `config.py` via `scripts/sync_version.py`
- **Docker** (`docker-pr.yml`, `docker-release.yml`): Multi-platform builds (`linux/amd64,linux/arm64`) to `ghcr.io`
