# Repository Guidelines

**Primary Documentation:** See `requirements.txt`, `requirements-dev.txt`, `CLAUDE.md` for development commands.

**Architecture Overview:** PAL MCP Server is a Python-based MCP (Model Context Protocol) server that orchestrates multi-model AI workflows. The server connects Claude CLI and other AI clients to multiple model providers (Gemini, OpenAI, Azure, X.AI, OpenRouter, DIAL, Ollama/custom) through a unified abstraction layer.

**Also read:** `CLAUDE.md` and `CLAUDE.local.md` if available for comprehensive development workflows.

## Quick Reference

```bash
# Setup (first time or after dependency changes)
./run-server.sh

# Run quality checks (linting, formatting, unit tests)
./code_quality_checks.sh

# Quick smoke test (6 essential tests)
python communication_simulator_test.py --quick

# Run integration tests with real API calls
./run_integration_tests.sh [--with-simulator]

# View server logs
tail -f logs/mcp_server.log
tail -f logs/mcp_activity.log  # Tool calls only
```

## Project Structure & Module Organization

```
pal-mcp-server/
├── server.py                 # Main MCP server entrypoint
├── config.py                # Centralized configuration and constants
├── tools/                   # MCP tool implementations
│   ├── simple/             # SimpleTool: single-call tools (chat, listmodels, version, challenge, apilookup)
│   ├── workflow/            # WorkflowTool: multi-step guided tools (codereview, debug, precommit, etc.)
│   ├── shared/             # Base classes, models, exceptions shared across tools
│   ├── clink.py            # CLI-to-CLI bridge for external AI agents
│   └── [tool].py           # Individual tool implementations
├── providers/               # AI model provider abstraction layer
│   ├── registries/         # Model capability registries per provider
│   ├── shared/             # Provider interfaces, capabilities, response models
│   ├── [provider].py       # Concrete provider implementations
│   └── registry.py         # Provider factory with API key detection
├── clink/                   # CLI registry and agent spawning system
│   ├── registry.py         # CLIClient registry (claude, codex, gemini)
│   ├── agents/             # CLI agent implementations
│   ├── parsers/            # CLI response parsers
│   └── models.py          # CLIClient configuration models
├── utils/                   # Shared utilities
│   ├── conversation_memory.py  # AI-to-AI conversation threading
│   ├── file_utils.py         # File handling, expansion, deduplication
│   ├── token_utils.py        # Token counting and allocation
│   └── model_restrictions.py  # Model allowlist filtering
├── systemprompts/           # System prompt definitions per tool
├── conf/                    # Model capability JSON files
│   └── cli_clients/        # CLI client configurations (claude.json, gemini.json, codex.json)
├── simulator_tests/          # End-to-end scenario tests
│   └── log_utils.py        # Log parsing utilities for test validation
├── tests/                   # Unit and integration tests
│   └── [provider]_cassettes/  # VCR cassettes for deterministic integration tests
└── docs/                    # User and contributor documentation
```

## Build, Test, and Development Commands

### Essential Commands

```bash
# Activate virtual environment (first time setup)
source .pal_venv/bin/activate

# or if using pyenv:
pyenv activate pal  # (if .python-version exists)

# Install/update dependencies and configure
./run-server.sh

# Run comprehensive code quality checks (ruff, black, isort, pytest)
./code_quality_checks.sh

# Quick smoke test (6 essential tests, ~2-3 minutes)
python communication_simulator_test.py --quick

# Run integration tests (requires API keys)
./run_integration_tests.sh
./run_integration_tests.sh --with-simulator  # + simulator tests
```

### Testing

```bash
# Run all unit tests (excludes integration tests)
python -m pytest tests/ -v -m "not integration"

# Run specific test file
python -m pytest tests/test_auto_mode_model_listing.py -v

# Run with coverage
python -m pytest tests/ --cov=. --cov-report=html -m "not integration"

# Run simulator tests (end-to-end validation)
python communication_simulator_test.py --quick --verbose
python communication_simulator_test.py --individual cross_tool_continuation
python communication_simulator_test.py --list-tests
```

### Linting and Formatting

```bash
# Run individually
.ruff check --fix       # Auto-fix linting issues
black .                  # Format code
isort .                  # Sort imports

# Or run all checks at once
./code_quality_checks.sh
```

### Server Operations

```bash
# Run server manually (for debugging)
.pal_venv/bin/python server.py

# Follow logs
tail -f logs/mcp_server.log
tail -f logs/mcp_activity.log  # Tool activity only

# Check for errors
grep "ERROR" logs/mcp_server.log | tail -20
```

## Coding Style & Architecture Patterns

### Code Style
- **Target:** Python 3.9+
- **Formatting:** Black (120 character line limit)
- **Import sorting:** isort (black profile)
- **Linting:** Ruff (E, W, F, I, B, C4, UP rules)
- **Type hints:** Explicit type hints preferred (use `typing.TYPE_CHECKING` for forward references)
- **Naming:** snake_case for modules/functions, PascalCase for classes
- **Docstrings:** Imperative, commit-time docstrings (not excessive)

### Tool Architecture

All MCP tools inherit from either `SimpleTool` or `WorkflowTool`:

**SimpleTool** (`tools/simple/base.py`):
- Single-call tools that complete work in one request to external AI
- Used for: `chat`, `listmodels`, `version`, `challenge`, `apilookup`, `clink`
- Pattern: implement `get_name()`, `get_description()`, and schema builder
- Example: `ChatTool` forwards prompts directly to models

**WorkflowTool** (`tools/workflow/base.py`):
- Multi-step guided tools that orchestrate systematic investigation + expert analysis
- Inherit from both `BaseTool` and `BaseWorkflowMixin`
- Used for: `codereview`, `debug`, `precommit`, `planner`, `analyze`, `refactor`, etc.
- Pattern: implement `get_required_actions()` (step guidance) and `should_call_expert_analysis()` (completion criteria)
- Flow: CLI calls tool → Tool tracks findings → Tool forces CLI pause → CLI investigates → Once complete, Tool calls external AI for expert analysis → Tool returns structured response

**Key gotcha:** Workflow tools use `ConsolidatedFindings` to aggregate findings across multiple steps. Do not directly accumulate state in tool instance variables.

### Provider Pattern

All model providers inherit from `ModelProvider` (`providers/base.py`):
- Define `MODEL_CAPABILITIES: dict[str, ModelCapabilities]` with model metadata
- Implement `get_provider_type()`, `_execute_request()`, and optionally `_lookup_capabilities()`
- Provider registry (`providers/registry.py`) detects enabled providers via API key presence
- Model restrictions (`utils/model_restrictions.py`) filter models based on `*_ALLOWED_MODELS` environment variables

**Key gotcha:** Providers must handle alias resolution (e.g., `flash` → `gemini-2.5-flash`) and temperature validation.

### CLINK (CLI-to-CLI Bridge)

The `clink` tool bridges MCP requests to external AI CLIs (Claude Code, Codex CLI, Gemini CLI):
- Registry: `clink/registry.py` discovers configured CLI clients from `conf/cli_clients/*.json`
- Agents: `clink/agents/` spawn isolated CLI instances as sub-processes
- Use for: Code review, debugging, or specialized analysis in fresh contexts without polluting main session
- CLIs return `<SUMMARY>...</SUMMARY>` tags for extracted final results

**Configuration:** CLI clients are configured in `conf/cli_clients/claude.json`, `gemini.json`, `codex.json` with:
- `command`: CLI executable path
- `args`: CLI arguments
- `roles`: Named role presets with custom system prompts (e.g., `planner`, `codereviewer`)

### Conversation Memory

- `utils/conversation_memory.py` handles AI-to-AI conversation threading
- Cross-tool conversation memory allows models to remember previous tool responses
- Controlled by `MAX_CONVERSATION_TURNS` environment variable
- **Key gotcha:** File deduplication (`utils/file_utils.py`) ensures files aren't re-embedded across turns, saving tokens

### MCP Protocol Considerations

**Token limit warning:** The MCP protocol (Claude CLI ↔ MCP Server) has a ~25K token limit. This is ONLY a transport limit, NOT an internal processing limit. The MCP Server can process 1M+ tokens internally.

- User input is limited to ~15K characters (60% of transport budget) by `MCP_PROMPT_SIZE_LIMIT`
- Large prompts must be sent as `prompt.txt` files to bypass MCP transport
- File embeddings, conversation history, and system prompts do NOT count against MCP limit

**Temperature setting:** All tools default to `TEMPERATURE=1.0` for optimal reasoning. Lower values can negatively impact model thinking abilities, especially for Gemini 3.0 Pro and O3/O4 models.

### Important Gotchas

1. **Temperature=1.0 is critical** for reasoning quality. Newer models handle randomness well; lowering it degrades thinking.

2. **Thinking modes** (`DEFAULT_THINKING_MODE_THINKDEEP`) only apply to models that support extended thinking (Gemini 2.5 Pro, GPT-5 models). Flash models use system prompt engineering instead.

3. **Tool state** is per-server-instance. Do not rely on persistent state across restarts.

4. **Conversation memory** uses file-based deduplication. Changing files between turns causes re-embedding.

5. **Model restrictions** apply even in `auto` mode. Claude will only select from `*_ALLOWED_MODELS`.

6. **Disabled tools** (`DISABLED_TOOLS`) are filtered at server startup. Changing requires restart.

7. **CLINK agents** run in isolated sub-processes. They don't share memory with main server.

8. **Cassettes** (VCR recordings) must be sanitized of API keys before committing. Use `python tests/sanitize_cassettes.py`.

## Testing Guidelines

### Test Categories

**Unit Tests** (`tests/`):
- Test individual components in isolation
- Use `pytest` with `@pytest.mark.integration` decorator for integration tests
- Use `VCR cassettes` for deterministic API tests (record once, replay forever)
- Cassettes stored in `tests/{provider}_cassettes/`
- Sanitize cassettes: `python tests/sanitize_cassettes.py` (removes API keys)

**Simulator Tests** (`simulator_tests/`):
- End-to-end scenario tests validating real MCP server behavior
- Test conversation threading, file handling, deduplication, cross-tool workflows
- Use `communication_simulator_test.py` harness
- Run with `--quick` for 6 essential tests covering core functionality

**Integration Tests** (`tests/` with `@pytest.mark.integration`):
- Make real API calls using configured provider keys
- Require valid API keys in `.env`
- Run with `./run_integration_tests.sh`
- Not included in `./code_quality_checks.sh` (run separately)

### Quick Test Mode

Run `python communication_simulator_test.py --quick` for essential coverage:
- `cross_tool_continuation` - Cross-tool conversation memory (chat, thinkdeep, codereview, analyze, debug)
- `conversation_chain_validation` - Core conversation threading and memory
- `consensus_workflow_accurate` - Consensus tool with flash model and stance testing
- `codereview_validation` - CodeReview tool with flash model and multi-step workflows
- `planner_validation` - Planner tool with flash model and complex planning
- `token_allocation_validation` - Token allocation and conversation history buildup

### Test Patterns

```python
# Unit test pattern
def test_some_behavior():
    # Given
    tool = ChatTool()
    # When
    result = tool.run(params)
    # Then
    assert result.is_success
    assert "expected" in result.content

# Integration test with VCR
@pytest.mark.integration
@pytest.mark.vcr("tests/openai_cassettes/chat_gpt5_moon_distance.json")
def test_chat_openai_integration():
    # Makes real API call (replayed from cassette)
    pass

# Simulator test pattern
class TestBasicConversation(BaseConversationTest):
    def test_conversation_flow(self):
        # Runs actual MCP server, validates logs
        result = self.run_simulation()
        self.assert_conversation_continued(result)
```

### Running Tests

```bash
# Before committing (unit tests only)
python -m pytest tests/ -v -m "not integration"

# After provider changes (integration tests)
./run_integration_tests.sh

# Quick validation (simulator)
python communication_simulator_test.py --quick

# Individual simulator test (better isolation)
python communication_simulator_test.py --individual cross_tool_continuation --verbose

# Coverage-sensitive changes
python -m pytest tests/ --cov=. --cov-report=html -m "not integration"
```

### Log Validation for Tests

Simulator tests use `simulator_tests/log_utils.py` to validate server logs:

```python
from simulator_tests.log_utils import LogUtils

# Get recent logs
recent_logs = LogUtils.get_recent_server_logs(lines=500)

# Check for errors
errors = LogUtils.check_server_logs_for_errors()

# Search for specific patterns
matches = LogUtils.search_logs_for_pattern("TOOL_CALL.*debug")
```

Capture relevant excerpts from `logs/mcp_server.log` or `logs/mcp_activity.log` when documenting test failures.

## Commit & Pull Request Guidelines
Follow Conventional Commits: `type(scope): summary`, where `type` is one of `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, or `chore`. Keep commits focused, referencing issues or simulator cases when helpful. Pull requests should outline intent, list validation commands executed, flag configuration or tool toggles, and attach screenshots or log snippets when user-visible behavior changes.

## GitHub CLI Commands
The GitHub CLI (`gh`) streamlines issue and PR management directly from the terminal.

### Viewing Issues
```bash
# View issue details in current repository
gh issue view <issue-number>

# View issue from specific repository
gh issue view <issue-number> --repo owner/repo-name

# View issue with all comments
gh issue view <issue-number> --comments

# Get issue data as JSON for scripting
gh issue view <issue-number> --json title,body,author,state,labels,comments

# Open issue in web browser
gh issue view <issue-number> --web
```

### Managing Issues
```bash
# List all open issues
gh issue list

# List issues with filters
gh issue list --label bug --state open

# Create a new issue
gh issue create --title "Issue title" --body "Description"

# Close an issue
gh issue close <issue-number>

# Reopen an issue
gh issue reopen <issue-number>
```

### Pull Request Operations
```bash
# View PR details
gh pr view <pr-number>

# List pull requests
gh pr list

# Create a PR from current branch
gh pr create --title "PR title" --body "Description"

# Check out a PR locally
gh pr checkout <pr-number>

# Merge a PR
gh pr merge <pr-number>
```

Install GitHub CLI: `brew install gh` (macOS) or visit https://cli.github.com for other platforms.

## Security & Configuration Tips

### API Key Management
- Store API keys and provider URLs in `.env` (copy from `.env.example`)
- **NEVER** commit secrets, API keys, or generated log artifacts
- Use `.env` file or MCP client config for API keys only
- Run `./run-server.sh` to regenerate environments and verify connectivity after dependency changes
- `PAL_MCP_FORCE_ENV_OVERRIDE=true` ensures `.env` values override system environment (prevents tool conflicts)

### Adding Providers or Tools

**Adding a new provider:**
1. Create `providers/new_provider.py` inheriting from `ModelProvider`
2. Define `MODEL_CAPABILITIES` with model metadata
3. Implement `get_provider_type()`, `_execute_request()`
4. Add provider to `providers/registry.py` detection logic
5. Create `conf/new_provider_models.json` with model definitions
6. Add API key detection to `run-server.sh`
7. Update `.env.example` with provider configuration
8. Document in `docs/adding_providers.md`

**Adding a new tool:**
1. Inherit from `SimpleTool` (single-call) or `WorkflowTool` (multi-step)
2. Implement required methods: `get_name()`, `get_description()`
3. For workflow tools: implement `get_required_actions()`, `should_call_expert_analysis()`, `prepare_expert_analysis_context()`
4. Create system prompt in `systemprompts/[tool]_prompt.py`
5. Add tool import to `server.py`
6. Add to `DISABLED_TOOLS` default in `.env.example` if non-essential
7. Document in `docs/tools/[tool].md`
8. Add simulator test in `simulator_tests/test_[tool]_validation.py`

### Environment Variables (Key Ones)

```bash
# Model selection
DEFAULT_MODEL=auto              # Let Claude pick, or 'pro', 'flash', 'o3', etc.

# Tool selection (comma-separated)
DISABLED_TOOLS=analyze,refactor,testgen  # Disable non-essential tools

# Model restrictions (comma-separated, optional)
OPENAI_ALLOWED_MODELS=o3-mini,o4-mini
GOOGLE_ALLOWED_MODELS=flash,pro

# Conversation memory
MAX_CONVERSATION_TURNS=50

# Thinking mode (for ThinkDeep tool)
DEFAULT_THINKING_MODE_THINKDEEP=high  # minimal, low, medium, high, max

# Logging
LOG_LEVEL=INFO  # DEBUG (default), INFO, WARNING, ERROR
```

### Important Security Considerations

1. **Path traversal protection:** All file paths are validated to prevent `../` attacks (`utils/file_utils.py`)

2. **API key sanitization:** VCR cassettes must be sanitized before commit. Test suite includes PII sanitization utilities.

3. **Docker security:** Run as non-root user, use read-only mounts where possible

4. **Custom providers:** When configuring `CUSTOM_API_URL` for local models, ensure endpoint is not publicly accessible

5. **CLINK subprocess isolation:** External CLI agents run in isolated sub-processes with limited access to parent process memory

6. **File deduplication:** Prevents token waste but also limits potential data leakage across conversation turns
