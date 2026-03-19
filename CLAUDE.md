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
python -m pytest tests/test_refactor.py -v          # Single test file
./run_integration_tests.sh                          # Integration tests (needs Ollama)

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
- `TOOLS` dict maps tool names to instances
- `handle_call_tool()` routes requests, resolves models, reconstructs conversation context
- `configure_providers()` registers providers based on API keys
- `parse_model_option()` splits `"model:option"` format (e.g. `"gemini-pro:for"` → model + option)

**`tools/`** — MCP tool implementations. Two base classes:
- `SimpleTool` (`tools/simple/base.py`) — single request/response (chat, clink, imagegen, perceive)
- `WorkflowTool` (`tools/workflow/base.py`) — multi-step workflows (analyze, codereview, debug, planner, etc.)

Both inherit from `BaseTool` (`tools/shared/base_tool.py`). Required methods: `get_name()`, `get_description()`, `get_input_schema()`, `get_system_prompt()`, `execute()`.

**`providers/`** — AI provider abstraction
- `base.py`: Abstract `ModelProvider` interface with `generate_content()`, `get_capabilities()`, retry logic
- `registry.py`: `ModelProviderRegistry` singleton — lazy-initializes providers, resolves models by priority
- Priority order: GOOGLE → OPENAI → AZURE → XAI → ZAI → DIAL → CUSTOM → OPENROUTER

**`utils/conversation_memory.py`** — Stateless MCP → Stateful conversations
- In-memory `ThreadContext` storage with UUID keys
- `continuation_id` parameter enables multi-turn conversations
- Cross-tool continuation: context flows between any tools (analyze → codereview → debug)
- Dual prioritization: newest-first for token budgeting, chronological for LLM presentation
- Configurable backend: `"memory"` (in-process) or `"file"` (survives restarts)

**`systemprompts/`** — Each tool has a corresponding `*_prompt.py` file (1:1 naming convention)

**`config.py`** — Central configuration: version, model defaults, token limits, storage paths, timeouts

### Model Resolution

Models are resolved early at the MCP boundary in `handle_call_tool()`:
1. Parse `model:option` format (e.g., `"gemini-pro:for"` → model name + option string)
2. Resolve `"auto"` → concrete model via `registry.get_preferred_fallback_model(tool_category)`
3. Create `ModelContext` with capabilities and token allocation
4. Pass resolved context to tool

Tools declare their preferred model tier via `get_model_category()` → `ToolModelCategory`:
- `EXTENDED_REASONING` — most tools (codereview, debug, analyze, thinkdeep, etc.)
- `FAST_RESPONSE` — chat, listmodels, version
- `BALANCED` — perceive
- `IMAGE_GENERATION` — imagegen

### MCP Transport Limits

`MCP_PROMPT_SIZE_LIMIT` (~60K chars default) limits **user input** crossing MCP transport. It does NOT limit system prompts, file content embedded by tools, conversation history, or prompts sent to external models.

### clink Tool (CLI-to-CLI Bridge)

`tools/clink.py` spawns external AI CLIs as subagents:
- Sets `PAL_MCP_CLINK=1` to identify headless sessions
- Loads agent definitions from `.claude/agents/` directories
- Returns structured JSON responses with continuation support
- Output exceeding `MAX_MCP_OUTPUT_TOKENS` is offloaded to `.claude/output/`

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

The `ctxarm` tool arms a context store for automatic revival on every Claude session start. When armed, a SessionStart hook fires before the user's first message, forcing Claude to run `ctxlist` + `ctxquery` to restore project context from the silo.

### Usage

```
# Arm (requires an existing store from ctxinit)
mcp__pal__ctxarm(store_id="my-project", directory="/path/to/project")

# Disarm
mcp__pal__ctxarm(store_id="my-project", directory="/path/to/project", disarm=true)

# Or via slash command
/arm-ctxstore my-project
```

### How It Works

```
ctxarm tool ──▶ ~/.claude/pal/context/armed.json
                { "/path/to/project": "my-project" }

SessionStart hook ──▶ reads armed.json
                  ──▶ looks up cwd
                  ──▶ injects revival additionalContext
```

### Hook Installation

The SessionStart hook must be registered in `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/scripts/ctx-arm.sh"
          }
        ]
      }
    ]
  }
}
```

The hook script (`ctx-arm.sh`) reads `${PAL_STORAGE_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/pal}/context/armed.json`, looks up the session's `cwd`, and if armed, emits `additionalContext` with a mandatory revival sequence. Requires `jq`.

### Storage

- Armed state: `~/.claude/pal/context/armed.json` (directory → store_id mapping)
- Store registry: `~/.claude/pal/context/stores.json`
- MCP handshake shows `[armed]` marker on armed stores

## Environment Variables

Key variables (see `.env.example` for full list):
- `GEMINI_API_KEY`, `OPENAI_API_KEY`, `XAI_API_KEY`, `OPENROUTER_API_KEY` — Provider credentials
- `CUSTOM_API_URL` — Local models (Ollama, vLLM)
- `DEFAULT_MODEL` — Default model (`"auto"` for intelligent selection)
- `DISABLED_TOOLS` — Comma-separated list to disable tools
- `LOG_LEVEL` — DEBUG, INFO, WARNING, ERROR

## Testing Strategy

1. **Unit tests** (`tests/`): Fast, no API calls, test individual functions. `asyncio_mode = auto` in pytest.ini.
2. **Integration tests** (`@pytest.mark.integration`): Use local Ollama models or real API keys.
3. **Simulator tests** (`simulator_tests/`): End-to-end with real API keys, 30 available tests.

Quick simulator mode covers: cross-tool continuation, conversation threading, consensus workflow, codereview workflow, planner workflow, token allocation.

## Code Style

- Line length: 120 (black, isort, ruff)
- isort profile: `black`
- ruff selects: E, W, F, I, B, C4, UP; ignores E501, B008, C901, B904

## Code Navigation (kit-dev-mcp)

This project is indexed for kit-dev-mcp repo tools. At session start, load the tools and open the repo:

```
# Load tools
ToolSearch query: "select:mcp__kitstore__open_repository,mcp__kitstore__warm_cache,mcp__kitstore__grep_code,mcp__kitstore__grep_ast,mcp__kitstore__extract_symbols,mcp__kitstore__get_symbol_code,mcp__kitstore__find_symbol_usages,mcp__kitstore__get_file_tree,mcp__kitstore__analyze_dependencies,mcp__kitstore__review_diff"

# Open and warm
open_repository(path_or_url="/home/eigenmage/dev/opt/pal-mcp-server") → repo_id
warm_cache(repo_id, warm_file_tree=true, warm_symbols=true)
```

### Available Tools

| Tool | Purpose | Example |
|------|---------|---------|
| `grep_code(repo_id, pattern)` | Fast literal string search | `grep_code(repo_id, "handle_call_tool")` |
| `grep_ast(repo_id, pattern)` | AST-aware semantic search (tree-sitter) | `grep_ast(repo_id, "class BaseTool")` |
| `extract_symbols(repo_id, file_path)` | List functions/classes/types in a file | `extract_symbols(repo_id, "server.py")` |
| `get_symbol_code(repo_id, file_path, symbol)` | Get a symbol's full source | `get_symbol_code(repo_id, "server.py", "handle_call_tool")` |
| `find_symbol_usages(repo_id, symbol_name)` | Find where a symbol is used across repo | `find_symbol_usages(repo_id, "BaseTool")` |
| `get_file_tree(repo_id)` | Repository file structure | `get_file_tree(repo_id)` |
| `analyze_dependencies(path)` | Dependency graph via import parsing | `analyze_dependencies("/home/eigenmage/dev/opt/pal-mcp-server")` |
| `review_diff(repo_id, diff_spec)` | AI review of git diffs | `review_diff(repo_id, "HEAD~1")` |

### When to Use

- **Symbol navigation**: `extract_symbols` + `get_symbol_code` for lazy loading (token efficient)
- **Cross-file tracing**: `find_symbol_usages` to trace how classes/functions propagate
- **Pattern search**: `grep_ast` for structural matches (class defs, function signatures); `grep_code` for literal strings
- **Pre-commit review**: `review_diff` for AI-assisted diff review
