# AGENCY.md - Clink Agency Features

This document describes the custom agency features added to PAL MCP Server's clink tool, enabling autonomous agent spawning and structured output capabilities.

## Overview

The **clink** tool bridges PAL MCP to external AI CLIs, enabling multi-agent orchestration where one AI session can spawn isolated subagents with fresh context windows. Key features include:

- **CLI-to-CLI Bridge**: Spawn Claude, Codex, Gemini, or custom CLIs from PAL MCP
- **Structured Responses**: JSON schema support for programmatic output extraction
- **Role System**: Pre-configured system prompts for specialized tasks
- **Conversation Continuity**: Thread context across multiple clink invocations

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│ PAL MCP Server (Host Session)                                  │
│   User invokes clink tool via MCP                              │
│   ├─ prompt: "Review this code for security issues"            │
│   ├─ cli_name: "claude" or "glmaude"                           │
│   ├─ role: "agent", "agent:<name>", "agent:/path/to/file.md"  │
│   ├─ json_schema: {...} (optional structured output)           │
│   └─ absolute_file_paths: ["/path/to/file.py"]                 │
└────────────────────────┬───────────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────────────┐
│ CLinkTool (tools/clink.py)                                     │
│   ├─ Validates request parameters                              │
│   ├─ Loads CLI client config from conf/cli_clients/            │
│   ├─ Loads role system prompt                                  │
│   ├─ Prepares prompt with file context                         │
│   └─ Creates CLI agent instance                                │
└────────────────────────┬───────────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────────────┐
│ CLI Agent (clink/agents/claude.py)                             │
│   ├─ Builds CLI command with all flags                         │
│   │   └─ --json-schema (if provided)                           │
│   │   └─ --append-system-prompt (role prompt)                  │
│   ├─ Sets PAL_MCP_CLINK=1 environment variable                 │
│   └─ Spawns subprocess (isolated process)                      │
└────────────────────────┬───────────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────────────┐
│ External CLI (Claude Code, Gemini CLI, etc.)                   │
│   ├─ Receives: prompt + system prompt + files                  │
│   ├─ Executes with full CLI capabilities                       │
│   └─ Returns JSON-formatted response                           │
└────────────────────────┬───────────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────────────┐
│ Response Parsing & Return                                      │
│   ├─ Parser extracts structured content                        │
│   ├─ Extracts <SUMMARY>...</SUMMARY> for large outputs         │
│   └─ Returns to parent session                                 │
└────────────────────────────────────────────────────────────────┘
```

## Clink: CLI-to-CLI Bridge

### Configuration

CLI clients are configured in `conf/cli_clients/*.json`:

```
conf/cli_clients/
├── claude.json      # Standard Claude CLI
├── codex.json       # OpenAI Codex CLI
├── gemini.json      # Google Gemini CLI
└── glmaude.json     # Claude CLI → ZAI GLM models
```

**Example: claude.json**
```json
{
  "name": "claude",
  "command": "claude",
  "additional_args": ["--permission-mode", "acceptEdits", "--model", "sonnet"],
  "env": {},
  "roles": {
    "default": {
      "prompt_path": "systemprompts/clink/default.txt",
      "role_args": []
    },
    "planner": {
      "prompt_path": "systemprompts/clink/default_planner.txt",
      "role_args": []
    },
    "codereviewer": {
      "prompt_path": "systemprompts/clink/default_codereviewer.txt",
      "role_args": []
    }
  }
}
```

### Agent Definitions

Agent definitions are the **primary mechanism** for configuring agent behavior. They allow per-project customization via markdown files with optional YAML frontmatter.

#### Base System Prompt

All agent roles automatically receive a base system prompt (`systemprompts/clink/default.txt`) that establishes core directives:

```
You are a delegated CLI agent spawned by a parent orchestration session.

## Core Directives

**Complete or Fail**: Finish the task fully, or stop and report exactly what's blocking.
**Token Efficiency**: Be concise. Output returns to orchestrating session.
**Investigate Before Acting**: Use CLI tools to gather context.
**Verify Your Work**: If you make changes, confirm they work.

## Structured Output
If a JSON schema was provided, your response MUST conform to it exactly.

## Do NOT
- Create workarounds to appear successful
- Downgrade scope without explicit approval
- Skip verification steps
- Produce invalid JSON when schema is provided
```

#### Prompt Composition

For agent roles, the final system prompt is composed as:

```
┌─────────────────────────────────────┐
│ Base Prompt (always applied)        │
│   systemprompts/clink/default.txt   │
├─────────────────────────────────────┤
│ Agent-Specific Content (appended)   │
│   .claude/agents/<name>.md          │
└─────────────────────────────────────┘
```

- `role="agent"` → Base prompt only
- `role="agent:researcher"` → Base prompt + researcher.md content
- `role="agent:/path/to/custom.md"` → Base prompt + custom.md content

#### Role Patterns

| Pattern | Description | Example |
|---------|-------------|---------|
| `agent` | General purpose agent (uses default prompt) | `role="agent"` |
| `agent:<name>` | Load agent by name from `.claude/agents/` | `role="agent:researcher"` |
| `agent:/path` | Load agent from absolute path | `role="agent:/home/user/custom.md"` |

#### Agent File Format

Agent files are markdown with optional YAML frontmatter:

```markdown
---
name: researcher
description: Research specialist agent
---

You are a research specialist. Your role is to investigate code patterns,
analyze architecture, and provide detailed technical insights.
```

#### Search Order

When using `agent:<name>`, agent definitions are searched in this order:

1. **Project-level**: `{project_dir}/.claude/agents/*.md` (takes precedence)
2. **User-level**: `~/.claude/agents/*.md` (fallback)

The agent is matched by the `name` field in frontmatter. This allows projects to override user-level agent definitions with project-specific configurations.

#### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENTS_PREFIX` | `.claude/agents` | Override agent directory prefix |

#### Legacy Roles (Backwards Compatibility)

For backwards compatibility, hardcoded roles are still supported but agent definitions are recommended:

| Legacy Role | Prompt File | Purpose |
|-------------|-------------|---------|
| `default` | `systemprompts/clink/default.txt` | General autonomous agent |
| `planner` | `systemprompts/clink/default_planner.txt` | Task planning and decomposition |
| `codereviewer` | `systemprompts/clink/default_codereviewer.txt` | Code review and analysis |

These legacy roles are configured in the CLI client JSON files under the `roles` section.

### MCP Schema

The clink tool exposes this schema to MCP clients:

```json
{
  "type": "object",
  "properties": {
    "prompt": {
      "type": "string",
      "description": "User request forwarded to the CLI"
    },
    "cli_name": {
      "type": "string",
      "enum": ["claude", "gemini", "codex", "glmaude"],
      "description": "Configured CLI client to invoke"
    },
    "role": {
      "type": "string",
      "description": "Agent role pattern: 'agent' (general purpose), 'agent:<name>' (from .claude/agents/), 'agent:/path' (absolute path), or legacy roles like 'default', 'planner', 'codereviewer'"
    },
    "absolute_file_paths": {
      "type": "array",
      "items": {"type": "string"},
      "description": "Files to include as context"
    },
    "json_schema": {
      "type": "object",
      "description": "Optional JSON schema for structured output (Claude CLI only)"
    },
    "model": {
      "type": "string",
      "description": "Model override (e.g., 'opus', 'sonnet', 'haiku'). Overrides CLI client default."
    },
    "continuation_id": {
      "type": "string",
      "description": "Conversation ID for threading across calls"
    }
  },
  "required": ["prompt"]
}
```

## Structured Responses (JSON Schema)

### How It Works

When `json_schema` is provided to the clink tool:

1. The schema is serialized to JSON string
2. Passed to Claude CLI via `--json-schema` flag
3. CLI agent validates and produces conforming output
4. Response is parsed and returned

**Implementation** (`clink/agents/claude.py:37-56`):
```python
def _build_command(
    self, *, role: ResolvedCLIRole, system_prompt: str | None, json_schema: dict | None = None
) -> list[str]:
    command = list(self.client.executable)
    # ... command building ...

    schema_to_use = json_schema if json_schema is not None else getattr(self, "_json_schema", None)
    if schema_to_use is not None:
        try:
            schema_json = json.dumps(schema_to_use)
        except (TypeError, ValueError) as exc:
            raise CLIAgentError(
                f"Failed to serialize json_schema for CLI '{self.client.name}': {exc}. "
                f"Schema must be JSON-serializable."
            ) from exc
        command.extend(["--json-schema", schema_json])
```

### Schema Format

Standard JSON Schema format:

```json
{
  "type": "object",
  "properties": {
    "name": {"type": "string"},
    "age": {"type": "integer"},
    "occupation": {"type": "string"}
  },
  "required": ["name", "age"]
}
```

### Limitations

- **Claude CLI only**: JSON schema support requires Claude CLI's `--json-schema` flag
- **Validation by CLI**: Schema validation is performed by the target CLI, not PAL MCP
- **Serialization requirement**: Schema must be JSON-serializable (dict, list, str, int, float, bool, None)

## GLMaude Integration

### Configuration

`conf/cli_clients/glmaude.json`:
```json
{
  "name": "glmaude",
  "command": "claude",
  "additional_args": ["--permission-mode", "acceptEdits", "--model", "sonnet"],
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "${ZAI_API_KEY}",
    "ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic",
    "API_TIMEOUT_MS": "3000000",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-4.7",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-4.7",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "glm-4.5-air"
  },
  "roles": {
    "default": {
      "prompt_path": "systemprompts/clink/default.txt",
      "role_args": []
    }
  }
}
```

### Model Mapping

| Claude Model | GLM Model | Use Case |
|--------------|-----------|----------|
| opus | GLM-4.7 | Complex reasoning |
| sonnet | GLM-4.7 | Standard tasks |
| haiku | GLM-4.5-air | Fast, lightweight |

### Authentication

Environment variable mapping:
- `ZAI_API_KEY` → `ANTHROPIC_AUTH_TOKEN`
- Endpoint: `https://api.z.ai/api/anthropic`

The `${ZAI_API_KEY}` syntax is expanded at runtime from your environment.

## Environment Variables

### PAL_MCP_CLINK

When PAL MCP invokes CLI tools through clink, it sets:

```bash
PAL_MCP_CLINK=1
```

This enables scripts and hooks to distinguish:
- **Interactive sessions**: User is actively working in Claude Code
- **Headless sessions**: Claude Code invoked by PAL MCP via clink

**Example - Notification Hook**:
```bash
#!/bin/bash
# ~/.config/claude/hooks/user-prompt-submit

# Ignore notifications from headless clink sessions
if [ -n "$PAL_MCP_CLINK" ]; then
    exit 0  # Silently exit
fi

# Normal notification logic for interactive sessions
notify-send "Claude Code" "Processing your request..."
```

### AGENTS_PREFIX

Override the default agent directory prefix:

```bash
AGENTS_PREFIX=".config/agents"  # Default: .claude/agents
```

This allows customization of where agent definition files are searched.

### CLINK_CLI_OVERRIDE

Force all clink invocations to use a specific CLI client, regardless of the `cli_name` parameter:

```bash
CLINK_CLI_OVERRIDE="claude"  # Force all clink calls through claude CLI
```

This is useful for:
- **Automation**: Ensure consistent CLI client in CI/CD pipelines
- **Testing**: Force a specific client during test runs
- **Cost control**: Route all traffic through a specific provider

The override is validated against configured clients - if the specified client doesn't exist, a warning is logged and the original request parameter is used.


## Examples

### Basic Usage via MCP clink Tool

From Claude Code or any MCP client:

```
Use clink with claude to review authentication.py for security vulnerabilities
```

**Using agent definitions:**

General purpose agent:
```
Use clink with claude using role agent to analyze this codebase
```

Named agent from .claude/agents/:
```
Use clink with claude using role agent:researcher to investigate API patterns
```

Agent from absolute path:
```
Use clink with claude using role agent:/home/user/custom-agent.md to review code
```

**Using legacy roles:**
```
Use clink with claude using the codereviewer role to analyze api_handler.py
```

**Using model override:**
```
Use clink with claude using role agent and model opus to analyze complex architecture
```

### JSON Schema for Structured Output

Extract structured data:

```
Use clink with claude to extract user info from this text: "John Smith is 25 years old"

json_schema: {
  "type": "object",
  "properties": {
    "name": {"type": "string"},
    "age": {"type": "integer"}
  },
  "required": ["name", "age"]
}
```

### Using GLMaude for Alternative Model

Route through ZAI GLM models:
```
Use clink with glmaude to analyze the performance characteristics of sort_algorithm.py
```

## File Reference

| File | Purpose |
|------|---------|
| `tools/clink.py` | Main clink tool implementation |
| `clink/registry.py` | CLI client configuration registry |
| `clink/agents/base.py` | Base CLI agent execution logic |
| `clink/agents/claude.py` | Claude-specific agent with JSON schema |
| `clink/agent_definitions.py` | Agent definition loading and parsing |
| `clink/models.py` | Configuration models (CLIClientConfig, etc.) |
| `clink/parsers/` | CLI response parsers |
| `conf/cli_clients/` | CLI client JSON configurations |
| `systemprompts/clink/default.txt` | Base system prompt for all agent roles |
| `systemprompts/clink/` | Legacy role system prompts |

## Related Documentation

- `AGENTS.md` - Repository guidelines and architecture
- `CLAUDE.md` - Development commands and workflows
- `docs/tools/clink.md` - Additional clink documentation
