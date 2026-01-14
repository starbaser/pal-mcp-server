# Environment Setup Guide for Clink Invoke

## How Environment Variables Work

### Two Environments in Play

1. **Python Script Environment (uv's temporary venv)**
   - Created by `uv run` for Python dependencies
   - Used only for imports: `pydantic`, `tyro`, `rich`, `attrs`, `jsonschema`
   - Automatically managed, no configuration needed

2. **CLI Subprocess Environment (your shell environment)**
   - **This is what the CLI command (`claude`, `gemini`, etc.) sees**
   - Inherits ALL variables from your shell
   - Plus CLI-specific variables from the config

## Environment Flow

```
Your Shell Environment
├── PATH=/usr/local/bin:/usr/bin:...
├── HOME=/home/username
├── ZAI_API_KEY=your-key
├── MY_CUSTOM_VAR=custom-value
└── ... (all your env vars)
         ↓
    os.environ.copy()
         ↓
CLI Config Template Expansion
├── ${ZAI_API_KEY} → your-key
└── ANTHROPIC_AUTH_TOKEN=your-key
         ↓
Merged Environment for CLI Subprocess
├── PATH=/usr/local/bin:/usr/bin:... (from shell)
├── HOME=/home/username (from shell)
├── MY_CUSTOM_VAR=custom-value (from shell)
├── ANTHROPIC_AUTH_TOKEN=your-key (from config, expanded)
├── ANTHROPIC_BASE_URL=https://api.z.ai/... (from config)
└── ... (all other vars from shell + config)
```

## Required Environment Variables

### For glmaude

```bash
# Add to ~/.zshrc or ~/.bashrc
export ZAI_API_KEY="your-zai-api-key-here"
```

**How it's used:**
```json
// conf/cli_clients/glmaude.json
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "${ZAI_API_KEY}",  // ← Expanded from your shell
    "ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic"
  }
}
```

The `${ZAI_API_KEY}` syntax reads from your shell environment and sets it as `ANTHROPIC_AUTH_TOKEN` for the claude CLI.

### For claude (official Anthropic)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

### For gemini

```bash
# Option 1: API key
export GEMINI_API_KEY="your-gemini-key"

# Option 2: Service account
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/credentials.json"
```

### For codex

```bash
export CODEX_API_KEY="your-codex-key"
```

## Verification

### Check environment variable expansion

```bash
# Verbose mode shows CLI-specific environment variables
uv run clink_invoke.py glmaude "test" --preview --verbose
```

Output will show:
```
Environment Variables:
  ANTHROPIC_AUTH_TOKEN: d58fe969...  ← Expanded from $ZAI_API_KEY
  ANTHROPIC_BASE_URL: https://api.z.ai/api/anthropic
  API_TIMEOUT_MS: 3000000
  ...
```

### Test that your shell environment is inherited

```bash
# Set custom variable
export TEST_VAR="hello"

# The CLI subprocess will see TEST_VAR (along with all your other env vars)
# The verbose output only shows CLI-specific vars, but subprocess has everything
```

## Common Issues

### "API key not set" errors

**Problem:** The CLI command can't find authentication credentials

**Solution:** Make sure the environment variable is exported in your shell:

```bash
# Wrong (won't work)
ZAI_API_KEY="key"  # Not exported

# Right
export ZAI_API_KEY="key"  # Exported to child processes
```

### "CLI executable not found"

**Problem:** The CLI command isn't in PATH

**Solution:** The subprocess uses YOUR PATH, so make sure the CLI is installed:

```bash
# Check if CLI is available
which claude  # Should show path like /usr/local/bin/claude

# If not found, install it
npm install -g @anthropics/claude-cli
```

### Environment variable not expanding

**Problem:** `${VAR_NAME}` in config isn't being replaced

**Solution:** Check that:
1. The variable is exported in your shell (`export VAR_NAME=value`)
2. The syntax is correct (`${VAR_NAME}`, not `$VAR_NAME`)
3. You're running from the correct shell (not a different terminal)

## Custom CLI Configurations

You can create custom CLI clients with environment variable expansion:

```bash
# Set your custom config path
export CLI_CLIENTS_CONFIG_PATH=~/my_cli_configs/
```

Create `~/my_cli_configs/my-custom-cli.json`:
```json
{
  "name": "my-custom-cli",
  "command": "my-cli",
  "additional_args": ["--format", "json"],
  "env": {
    "MY_CLI_TOKEN": "${MY_CUSTOM_TOKEN}",
    "MY_CLI_URL": "https://api.example.com"
  },
  "roles": {
    "default": {
      "prompt_path": "systemprompts/clink/default.txt",
      "role_args": []
    }
  }
}
```

Then use it:
```bash
export MY_CUSTOM_TOKEN="my-token"
uv run clink_invoke.py my-custom-cli "Hello"
```

## Environment Isolation

### What the Python script sees (uv's venv)

```bash
# These are used for Python imports
pip list  # Shows: pydantic, tyro, rich, attrs, jsonschema, pal-mcp-server
```

### What the CLI subprocess sees (your shell)

```bash
# These are used for the CLI command execution
env | grep ZAI  # Shows: ZAI_API_KEY=...
which claude    # Shows: /home/username/.local/bin/claude
```

The key point: **The CLI subprocess uses YOUR environment, not uv's virtual environment.**

This is the correct behavior! You want:
- The `claude` CLI from your PATH (not uv's)
- Your API keys from your shell (not uv's)
- Your custom environment variables

## Security Best Practices

### Never commit API keys

```bash
# ❌ Don't do this
echo "export ZAI_API_KEY=sk-..." >> ~/.zshrc

# ✅ Do this instead
# 1. Add to .zshrc
echo 'export ZAI_API_KEY="$(cat ~/.secrets/zai_key)"' >> ~/.zshrc

# 2. Store key in secure file
mkdir -p ~/.secrets
chmod 700 ~/.secrets
echo "your-key-here" > ~/.secrets/zai_key
chmod 600 ~/.secrets/zai_key
```

### Use environment-specific configs

```bash
# Development
export ZAI_API_KEY="dev-key"

# Production
export ZAI_API_KEY="prod-key"
```

## Testing Environment Setup

Run this test to verify everything works:

```bash
#!/bin/bash
echo "=== Environment Test ==="
echo ""
echo "1. Checking required env vars..."
echo "   ZAI_API_KEY: ${ZAI_API_KEY:+✓ Set} ${ZAI_API_KEY:-✗ Not set}"
echo ""
echo "2. Checking CLI availability..."
which claude >/dev/null && echo "   claude: ✓ Found" || echo "   claude: ✗ Not found"
echo ""
echo "3. Testing clink_invoke.py..."
uv run clink_invoke.py glmaude "test" --preview 2>&1 | head -5
echo ""
echo "✓ Environment setup complete!"
```

Save as `test_env.sh` and run:
```bash
chmod +x test_env.sh
./test_env.sh
```
