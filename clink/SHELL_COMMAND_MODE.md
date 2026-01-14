# Shell Command Mode (Default Behavior)

## Overview

By default, `clink_invoke.py` outputs a shell command that you can `eval` in your own shell. This ensures the CLI command runs in YOUR environment, not Python's virtual environment.

## Why Shell Command Mode?

### The Problem with Subprocess Execution

When running as a Python subprocess (old `--execute` mode):
```
Your Shell → Python (uv's venv) → subprocess → CLI command
                ↓
        VIRTUAL_ENV=/path/to/uv/venv
        PATH=/path/to/uv/venv/bin:...
```

The CLI subprocess inherits Python's modified environment, not yours.

### The Solution: Shell Command Output

With shell command mode (default):
```
Your Shell → Python (uv's venv) → outputs command string
                                         ↓
Your Shell → eval command → CLI runs in YOUR environment
```

The CLI runs directly in your shell, using your PATH, your venv, your env vars.

## How It Works

### 1. Generate Command

```bash
$ uv run clink_invoke.py glmaude "Say hello"
```

**Output:**
```bash
ANTHROPIC_AUTH_TOKEN=d58fe... ANTHROPIC_BASE_URL=https://api.z.ai/... claude --print --output-format json ... < <(cat <<'CLINK_PROMPT_EOF'
Say hello
CLINK_PROMPT_EOF
)
```

### 2. Eval in Your Shell

```bash
$ eval "$(uv run clink_invoke.py glmaude 'Say hello')"
```

This runs the command in YOUR shell, with YOUR environment.

## Environment Inheritance

### What the CLI Command Sees

When you `eval` the generated command:

```bash
# Your shell environment
PATH=/your/custom/path:/usr/local/bin:/usr/bin  # YOUR PATH
HOME=/home/username                              # YOUR HOME
VIRTUAL_ENV=/path/to/your/venv                   # YOUR venv (if any)
MY_CUSTOM_VAR=my_value                           # YOUR custom vars
ZAI_API_KEY=your-key                             # YOUR API keys

# Plus CLI-specific vars from config
ANTHROPIC_AUTH_TOKEN=your-key                    # From ${ZAI_API_KEY}
ANTHROPIC_BASE_URL=https://api.z.ai/...          # From config
```

**Key point:** The CLI command sees YOUR complete shell environment, not Python's.

## Command Format

The generated command uses this format:

```bash
ENV_VAR1=value1 ENV_VAR2=value2 command --args < <(cat <<'EOF'
prompt text
EOF
)
```

**Components:**
1. **Environment variables:** Set inline before the command
2. **Command with args:** The CLI command and its arguments
3. **Process substitution:** `<(cat <<'EOF' ...)` for multi-line prompts

## Usage Examples

### Basic Invocation

```bash
# Generate and eval in one line
eval "$(uv run clink_invoke.py glmaude 'Explain decorators')"
```

### With Files

```bash
eval "$(uv run clink_invoke.py glmaude 'Review this' --files auth.py models.py)"
```

### With Custom Role

```bash
eval "$(uv run clink_invoke.py glmaude 'Security audit' --role agency --files *.py)"
```

### Preview Without Running

```bash
# See the command without executing
uv run clink_invoke.py glmaude "test" --preview
```

## Comparison: Shell Command vs Subprocess

| Aspect | Shell Command (Default) | Subprocess (--execute) |
|--------|------------------------|------------------------|
| Environment | YOUR shell | Python's (uv venv) |
| PATH | YOUR PATH | uv's venv PATH first |
| VIRTUAL_ENV | YOUR venv (if any) | uv's venv |
| Custom vars | All visible | All visible |
| Performance | Slightly faster | Slightly slower |
| Output handling | Native to shell | Captured by Python |

## Advanced Usage

### Alias for Convenience

Add to your `.zshrc` or `.bashrc`:

```bash
# Shorthand for clink invoke
clink() {
    eval "$(uv run /path/to/clink_invoke.py "$@")"
}

# Usage
clink glmaude "Hello"
clink glmaude "Review code" --files main.py
```

### Capture Output

```bash
# Capture to variable
output=$(eval "$(uv run clink_invoke.py glmaude 'What is 2+2?')")
echo "$output"

# Pipe to other commands
eval "$(uv run clink_invoke.py glmaude 'Generate JSON')" | jq .
```

### Run in Specific Environment

```bash
# Activate your venv first
source my_venv/bin/activate

# Now the CLI sees your venv
eval "$(uv run clink_invoke.py glmaude 'test')"
```

## Troubleshooting

### Command not found

**Problem:** `claude: command not found`

**Solution:** The CLI isn't in your PATH. Install it:
```bash
npm install -g @anthropics/claude-cli
```

### Environment variables not set

**Problem:** API key errors

**Solution:** Export variables in your shell:
```bash
export ZAI_API_KEY="your-key"
```

### Quote escaping issues

**Problem:** Shell interprets special characters in prompt

**Solution:** Use single quotes for prompts with special chars:
```bash
eval "$(uv run clink_invoke.py glmaude 'Use $var literally')"
```

## When to Use --execute

The `--execute` flag runs as subprocess (old behavior). Use it when:
- You need to capture output in Python
- You're running in automation/CI where eval isn't available
- You need structured output handling (JSON parsing, etc.)

**Not recommended for interactive use** - use shell command mode instead.

## Security Notes

### Environment Variable Visibility

The generated command includes environment variables in the command line. This means:
- ✅ Variables are set only for this command
- ✅ They don't persist in your shell
- ⚠️  They're visible in process list (`ps aux`)
- ⚠️  They're visible in shell history

### Best Practices

```bash
# ✅ Good: Variables are inline, temporary
eval "$(uv run clink_invoke.py glmaude 'test')"

# ⚠️  Caution: Command visible in history
# Consider: set +o history before sensitive commands
```

## Summary

**Default behavior (shell command mode):**
- Outputs shell command for eval
- Runs in YOUR environment
- Uses YOUR PATH, venv, env vars
- Most natural and performant

**Alternative (--execute):**
- Runs as Python subprocess
- Uses Python's environment
- Good for automation
- Not recommended for interactive use
