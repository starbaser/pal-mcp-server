# PAL via mcptools CLI

Use [mcptools](https://github.com/f/mcptools) (`mcp` CLI) to call any PAL tool from the shell.

## Setup

```sh
# One-time: register the pal alias
mcp alias add pal /home/eigenmage/dev/opt/pal-mcp-server/pal-serve.sh

# Verify
mcp tools pal
```

The alias points to `pal-serve.sh`, a thin launcher that sources `.env` and execs `server.py` directly (bypasses the heavy `run-server.sh` setup). Requires the venv to already exist — run `./run-server.sh` once first.

## Calling Convention

```sh
mcp call <tool> pal -p '<json>'
```

Output formats: `--format table` (default), `--format json`, `--format pretty`

Debug server issues: add `--server-logs` to see server stderr.

## PALTree Tools

### Discovery

```sh
# List all PALTrees
mcp call treelist pal

# List PALTrees for a specific project
mcp call treelist pal -p '{"directory": "/abs/path/to/project"}'

# Drill into a specific PALTree
mcp call treelist pal -p '{"tree_path": "myproject"}'
```

### Read

```sh
# Read a node (metadata, prompt, response)
mcp call readnode pal -p '{"tree_path": "myproject.L1"}'

# List files attached to a PALTree
mcp call listnodefiles pal -p '{"tree_path": "myproject"}'

# Read a specific stored file
mcp call readnodefile pal -p '{"tree_path": "myproject.L1", "file_path": "/abs/path/to/file.py"}'

# Export PALTree to markdown
mcp call treedump pal -p '{"tree_path": "myproject", "output_path": "/abs/path/export.md"}'
```

### Create & Write

```sh
# Initialize a new PALTree
mcp call newtree pal -p '{"store_name": "myproject", "directory": "/abs/path/to/project"}'

# Add a layer (AI-synthesized — sends prompt + files to external model)
mcp call growlayer pal -p '{
  "tree_path": "myproject",
  "model": "gemini-2.5-pro",
  "prompt": "Project architecture overview",
  "context_label": "architecture",
  "absolute_file_paths": ["/abs/path/server.py", "/abs/path/config.py"]
}'

# Add a layer to an existing node
mcp call growlayer pal -p '{
  "tree_path": "myproject.L1",
  "model": "gemini-2.5-pro",
  "prompt": "Deep dive into the auth subsystem",
  "context_label": "auth deep-dive",
  "absolute_file_paths": ["/abs/path/auth.py"]
}'
```

### Query

```sh
# Query a PALTree (AI answers from stored context)
mcp call querynode pal -p '{
  "tree_path": "myproject",
  "model": "gemini-2.5-pro",
  "prompt": "What are the main architectural decisions?"
}'

# Follow-up query on a previous query result
mcp call querynode pal -p '{
  "tree_path": "myproject.L3.Q0",
  "model": "gemini-2.5-pro",
  "prompt": "Which files implement that decision?"
}'
```

### Lifecycle

```sh
# Fork a PALTree node (branch for experimentation)
mcp call forknode pal -p '{"tree_path": "myproject.L2", "label": "experiment"}'

# Rename a PALTree
mcp call renametree pal -p '{"tree_path": "myproject", "new_name": "proj-v2", "directory": "/abs/path"}'
```

## Other PAL Tools

```sh
# Chat with an external model
mcp call chat pal -p '{
  "model": "gemini-2.5-pro",
  "prompt": "Explain the observer pattern",
  "working_directory_absolute_path": "/home/eigenmage/dev"
}'

# Deep reasoning
mcp call thinkdeep pal -p '{
  "model": "gemini-2.5-pro",
  "step_number": 1,
  "total_steps": 1,
  "next_step_required": false,
  "step": "Analyze the tradeoffs between SSE and WebSocket for real-time updates",
  "findings": "",
  "problem_context": "Choosing a transport for live dashboard updates"
}'

# List available models
mcp call listmodels pal

# Server version and config
mcp call version pal
```

## Interactive Shell

For multiple calls in a row, use `mcp shell` to keep the server alive (avoids per-call startup):

```sh
mcp shell pal
```

Then type tool names with JSON params directly:

```
mcp > treelist {"directory": "/home/eigenmage/dev/opt/pal-mcp-server"}
mcp > readnode {"tree_path": "pal-mcp-server.L1"}
mcp > growlayer {"tree_path": "pal-mcp-server", "model": "gemini-2.5-pro", "prompt": "...", "context_label": "..."}
```

## Troubleshooting

**"initialization timed out"**: The alias must point to `pal-serve.sh` (thin launcher), not `run-server.sh` (heavy setup script).

**"No module named X"**: The venv is stale. Run `./run-server.sh` once to refresh dependencies, then use `pal-serve.sh` again.

**"no such file or directory"**: On NixOS, shebangs with `/bin/bash` fail. The `pal-serve.sh` script uses `#!/usr/bin/env bash` to avoid this.

**Server errors**: Add `--server-logs` to see stderr output from the PAL server.
