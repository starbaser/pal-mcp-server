# Clink Invoke - Standalone CLI Invocation Tool

A standalone Python script that uses PAL MCP Server's clink infrastructure to invoke external AI CLIs (claude, glmaude, gemini, codex) with the exact same command-building logic that PAL MCP uses internally.

## Features

- **Identical command building** - Uses PAL MCP's exact command construction logic
- **JSON schema support** - Structured output with validation
- **Conversation continuation** - Multi-turn conversations with persistent state
- **Batch processing** - Execute multiple prompts with optional chaining
- **Rich terminal output** - Beautiful formatting with progress indicators
- **Session management** - List and clean conversation sessions
- **Preview mode** - See exact command without executing

## Quick Start

### Installation

No installation needed! Uses `uv` with PEP 723 inline script metadata:

```bash
# Make script executable
chmod +x clink_invoke.py

# Run directly with uv
uv run clink_invoke.py --help
```

### Basic Usage

**Default behavior: Outputs shell command for eval in YOUR environment**

```bash
# Generate shell command (default)
uv run clink_invoke.py glmaude "Explain async/await in Python"

# Eval the command in your shell
eval "$(uv run clink_invoke.py glmaude 'Explain async/await')"

# With files
eval "$(uv run clink_invoke.py glmaude 'Review this code' --files auth.py models.py)"

# With role
eval "$(uv run clink_invoke.py glmaude 'Perform security review' --role agency --files *.py)"
```

**Alternative: Execute as subprocess (old behavior)**

```bash
# Execute directly (not recommended - uses Python's environment)
uv run clink_invoke.py glmaude "test" --execute

# Preview command
uv run clink_invoke.py glmaude "test" --preview
```

### Why Eval?

The script outputs a shell command that you eval in YOUR shell environment. This ensures:
- ✅ Uses YOUR PATH, not Python's virtual environment
- ✅ Uses YOUR shell environment variables
- ✅ No environment pollution from uv's venv
- ✅ Runs exactly as if you typed the command yourself

## Advanced Features

### JSON Schema Output

Extract structured data from prompts:

```bash
# Inline schema
uv run clink_invoke.py glmaude \
  "Extract name and age from: 'John is 25 years old'" \
  --json-schema '{"type":"object","properties":{"name":{"type":"string"},"age":{"type":"integer"}}}'

# Schema from file
uv run clink_invoke.py glmaude \
  "Parse this data" \
  --json-schema schemas_clink/user_info.json \
  --files data.txt
```

### Conversation Continuation

Maintain context across multiple invocations:

```bash
# Start conversation
uv run clink_invoke.py glmaude "Explain Python decorators"
# Output: Conversation ID: abc123-def456-...

# Continue conversation
uv run clink_invoke.py glmaude "Show me an example" --continue-from abc123-def456

# Continue with different CLI
uv run clink_invoke.py gemini "Explain it more simply" --continue-from abc123-def456

# List sessions
uv run clink_invoke.py --list-sessions

# Clean old sessions
uv run clink_invoke.py --clean-sessions --older-than 7d
```

### Batch Processing

Execute multiple prompts from a file:

```bash
# Create batch file (JSONL format)
cat > tasks.jsonl << EOF
{"prompt": "Explain generators", "files": []}
{"prompt": "Review function", "files": ["utils.py"]}
{"prompt": "Find security issues", "files": ["auth.py", "models.py"]}
EOF

# Execute batch (independent prompts)
uv run clink_invoke.py glmaude --batch tasks.jsonl --role agency

# Chain batch (each continues from previous)
uv run clink_invoke.py glmaude --batch tasks.jsonl --chain
```

## CLI Reference

### Required Arguments

- `cli_name` - CLI client to use (claude, glmaude, gemini, codex)
- `prompt` - User prompt to send to the CLI (not required for session management commands)

### Optional Arguments

**Execution:**
- `--role ROLE` - Role preset (default, agency, codereviewer, planner)
- `--files PATH [PATH ...]` - Files to include in the prompt
- `--json-schema SCHEMA` - JSON schema for structured output (string or file path)
- `--system-prompt PROMPT` - Override system prompt (text or file path)

**Conversation:**
- `--continue-from ID` - Continuation ID from previous conversation

**Batch Processing:**
- `--batch PATH` - JSONL file with multiple prompts to process
- `--chain` - Chain batch prompts (each continues from previous)

**Output:**
- `--preview` - Preview command without executing
- `--validate-schema / --no-validate-schema` - Validate output against schema (default: True)
- `--output-file PATH` - Save output to file

**Session Management:**
- `--list-sessions` - List active conversation sessions
- `--clean-sessions` - Clean old sessions
- `--older-than TIME` - Clean sessions older than (e.g., '24h', '7d', '1w')

**Debug:**
- `--verbose` - Enable verbose debug output
- `--no-color` - Disable colored output

## Configuration

### CLI Clients

The script uses PAL MCP's CLI client configurations from:

1. **Built-in configs:** `conf/cli_clients/` (in PAL MCP directory)
2. **Environment override:** `CLI_CLIENTS_CONFIG_PATH` (file or directory)
3. **User overrides:** `~/.pal/cli_clients/` (future support)

Available CLI clients (from PAL MCP):
- `claude` - Official Claude CLI with Anthropic API
- `glmaude` - Claude CLI with ZAI API (GLM models)
- `gemini` - Google Gemini CLI
- `codex` - Codex CLI

### Environment Variables

The script automatically expands `${VAR_NAME}` syntax in CLI configurations.

**Required environment variables:**

| CLI Client | Environment Variable | Description |
|------------|---------------------|-------------|
| `glmaude` | `ZAI_API_KEY` | ZAI API key (auto-mapped to `ANTHROPIC_AUTH_TOKEN`) |
| `claude` | `ANTHROPIC_API_KEY` | Official Anthropic API key |
| `gemini` | `GEMINI_API_KEY` or `GOOGLE_APPLICATION_CREDENTIALS` | Google Gemini/Cloud credentials |
| `codex` | `CODEX_API_KEY` | Codex API key |

**Setup:**
```bash
# Add to ~/.zshrc or ~/.bashrc
export ZAI_API_KEY="your-zai-api-key"
export ANTHROPIC_API_KEY="your-anthropic-key"

# Verify with verbose mode
uv run clink_invoke.py glmaude "test" --preview --verbose
```

**See `ENV_SETUP.md` for comprehensive environment setup guide, including:**
- How environment variables flow from your shell to the CLI subprocess
- Why the subprocess uses YOUR environment, not uv's virtual environment
- Security best practices for API keys
- Troubleshooting common environment issues

## Examples

See the `examples_clink/` directory for comprehensive examples:

- `basic_usage.sh` - Simple invocations
- `continuation.sh` - Conversation continuation workflows
- `json_schema.sh` - Structured output examples
- `batch_processing.sh` - Batch processing with JSONL files

## How It Works

1. **Configuration Loading:**
   - Loads CLI client configs from PAL MCP's registry
   - Merges internal defaults with user configurations
   - Resolves role and system prompts

2. **Command Building:**
   - Creates appropriate agent (ClaudeAgent, GeminiAgent, etc.)
   - Builds command array using agent's `_build_command()` method
   - Expands environment variables (e.g., `${ZAI_API_KEY}`)

3. **Execution:**
   - Executes subprocess with async support
   - Sends prompt via stdin
   - Captures stdout/stderr
   - Parses output using CLI-specific parser

4. **Output:**
   - Validates against JSON schema (if provided)
   - Formats output with Rich
   - Saves conversation state to disk

## Conversation Storage

Conversations are stored in `~/.clink_sessions/` as JSON files:

```json
{
  "id": "abc123-def456-...",
  "created_at": "2026-01-13T10:30:00+00:00",
  "last_updated": "2026-01-13T10:35:00+00:00",
  "turns": [
    {
      "role": "user",
      "content": "Explain Python decorators",
      "files": [],
      "timestamp": "2026-01-13T10:30:00+00:00",
      "cli_name": "glmaude",
      "model_name": null
    },
    {
      "role": "assistant",
      "content": "Decorators are...",
      "files": [],
      "timestamp": "2026-01-13T10:30:15+00:00",
      "cli_name": "glmaude",
      "model_name": "glm-4.7"
    }
  ]
}
```

## Dependencies

All dependencies are managed via PEP 723 inline script metadata:

- `pal-mcp-server` - Core clink functionality (local file dependency)
- `pydantic>=2.0` - Configuration models
- `tyro>=0.8.0` - CLI interface
- `rich>=13.0` - Terminal output formatting
- `attrs>=23.0` - Dataclass definitions
- `jsonschema>=4.0` - JSON schema validation

## Troubleshooting

### CLI executable not found

```bash
Error: CLI executable not found: claude
Make sure 'glmaude' CLI is installed and in PATH
```

**Solution:** Install the CLI tool:
```bash
# For Claude CLI
npm install -g @anthropics/claude-cli

# For Gemini CLI
# Follow Gemini CLI installation instructions

# For Codex CLI
# Follow Codex CLI installation instructions
```

### JSON schema validation failed

```bash
Schema validation failed:
'age' is a required property
```

**Solution:** Either fix the output or disable validation:
```bash
uv run clink_invoke.py ... --validate-schema false
```

### Session not found

```bash
Error: Session not found: abc123
```

**Solution:** List active sessions to find the correct ID:
```bash
uv run clink_invoke.py --list-sessions
```

## Comparison with PAL MCP

| Feature | PAL MCP (MCP Tool) | Clink Invoke (Standalone) |
|---------|-------------------|--------------------------|
| Command building | ✅ Identical | ✅ Identical |
| JSON schema | ✅ Yes | ✅ Yes |
| Conversation memory | ✅ In-memory (process) | ✅ Disk-based (persistent) |
| Batch processing | ❌ No | ✅ Yes |
| Session management | ❌ No | ✅ Yes |
| Preview mode | ❌ No | ✅ Yes |
| Requires MCP server | ✅ Yes | ❌ No |

## Contributing

This script is part of the PAL MCP Server project. For issues or contributions, see the main project repository.

## License

Same license as PAL MCP Server.
