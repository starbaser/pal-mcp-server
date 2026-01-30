# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

```bash
# Setup and run server (handles venv, deps, config)
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
       ↓ MCP JSON-RPC
   server.py
       ↓ handle_call_tool()
   Tool Registry (TOOLS dict)
       ↓
   Tool.execute()
       ↓ reconstruct_thread_context()
   Conversation Memory (utils/conversation_memory.py)
       ↓ Provider selection
   ModelProviderRegistry
       ↓
   External Model (Gemini/OpenAI/etc.)
```

### Key Components

**`server.py`** - Entry point and MCP protocol handler
- `TOOLS` dict maps tool names to instances
- `handle_call_tool()` routes requests, resolves models, reconstructs conversation context
- `configure_providers()` registers providers based on API keys

**`tools/`** - MCP tool implementations
- Each tool inherits from `BaseTool` (`tools/shared/base_tool.py`)
- Required methods: `get_name()`, `get_description()`, `get_input_schema()`, `execute()`
- Tools use `ToolModelCategory` enum to hint preferred model type (FAST, BALANCED, DEEP_THINKING)

**`providers/`** - AI provider abstraction
- `base.py`: Abstract `ModelProvider` interface
- `registry.py`: `ModelProviderRegistry` singleton for provider management
- Provider implementations: `gemini.py`, `openai.py`, `azure_openai.py`, `xai.py`, `openrouter.py`, `custom.py`
- Priority: Native APIs → Custom endpoints → OpenRouter (catch-all)

**`utils/conversation_memory.py`** - Stateless MCP → Stateful conversations
- In-memory `ThreadContext` storage with UUID keys
- `continuation_id` parameter enables multi-turn conversations
- Cross-tool continuation: context flows between analyze → codereview → debug
- Dual prioritization: newest-first for token efficiency, chronological for LLM presentation

**`systemprompts/`** - AI instruction modules
- Each tool has corresponding `*_prompt.py` file
- `clink/` subdirectory for CLI agent prompts

### Model Resolution

Models are resolved early at the MCP boundary in `handle_call_tool()`:
1. Parse `model:option` format (e.g., "gemini-pro:for")
2. Resolve "auto" to specific model via registry
3. Create `ModelContext` with capabilities and token allocation
4. Pass resolved context to tool

### MCP Transport Limits

`MCP_PROMPT_SIZE_LIMIT` in `config.py` limits **user input** crossing MCP transport (~60K chars default). This does NOT limit:
- System prompts added internally by tools
- File content embedded by tools
- Conversation history
- Prompts sent to external models (managed by model-specific limits)

### clink Tool (CLI-to-CLI Bridge)

`tools/clink.py` spawns external AI CLIs as subagents:
- Sets `PAL_MCP_CLINK=1` to identify headless sessions
- Loads agent definitions from `.claude/agents/` directories
- Returns structured JSON responses with continuation support

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
        return ToolModelCategory.BALANCED  # FAST, BALANCED, or DEEP_THINKING

    async def execute(self, arguments: dict) -> list[TextContent]:
        # Implementation
        pass
```

Register in `server.py`:
```python
TOOLS = {
    "mytool": MyTool(),
    ...
}
```

## Environment Variables

Key variables (see `.env.example` for full list):
- `GEMINI_API_KEY`, `OPENAI_API_KEY`, `XAI_API_KEY`, `OPENROUTER_API_KEY` - Provider credentials
- `CUSTOM_API_URL` - Local models (Ollama, vLLM)
- `DEFAULT_MODEL` - Default model ("auto" for intelligent selection)
- `DISABLED_TOOLS` - Comma-separated list to disable tools
- `LOG_LEVEL` - DEBUG, INFO, WARNING, ERROR
- `CONVERSATION_TIMEOUT_HOURS` - Thread expiration (default: 6)

## Testing Strategy

1. **Unit tests** (`tests/`): Fast, no API calls, test individual functions
2. **Integration tests** (`@pytest.mark.integration`): Use local Ollama models (free)
3. **Simulator tests** (`simulator_tests/`): End-to-end with real API keys

Quick test mode covers: cross-tool continuation, conversation threading, consensus workflow, codereview workflow, planner workflow, token allocation.
