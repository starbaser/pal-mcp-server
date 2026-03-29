#!/usr/bin/env bash
set -euo pipefail

# Register PAL MCP server as an mcptools alias
# Requires: mcp (mcptools) on PATH, venv already set up via ./run-server.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHER="$SCRIPT_DIR/pal-serve.sh"

if ! command -v mcp &>/dev/null; then
    echo "Error: mcp (mcptools) not found on PATH" >&2
    echo "Install: https://github.com/f/mcptools" >&2
    exit 1
fi

if [[ ! -x "$LAUNCHER" ]]; then
    echo "Error: $LAUNCHER not found or not executable" >&2
    exit 1
fi

if [[ ! -d "$SCRIPT_DIR/.pal_venv" ]]; then
    echo "Error: .pal_venv not found — run ./run-server.sh once first to set up the venv" >&2
    exit 1
fi

# Remove existing alias if present, then re-add
mcp alias remove pal 2>/dev/null || true
mcp alias add pal "$LAUNCHER"

echo "Verifying..."
if mcp tools pal &>/dev/null; then
    echo "Done. PAL registered as 'pal' in mcptools."
    echo ""
    echo "  mcp tools pal              # list tools"
    echo "  mcp call pallist pal       # call a tool"
    echo "  mcp shell pal              # interactive session"
    echo ""
    echo "See docs/mcptools-cli.md for the full reference."
else
    echo "Warning: 'mcp tools pal' failed — check --server-logs for details" >&2
    echo "  mcp tools pal --server-logs" >&2
    exit 1
fi
