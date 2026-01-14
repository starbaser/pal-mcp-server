#!/usr/bin/env bash
# Conversation continuation examples

# Start a new conversation
echo "Starting conversation..."
CONV_ID=$(uv run clink_invoke.py claude-zai "Explain Python decorators" | grep "Conversation ID:" | cut -d: -f2 | tr -d ' ')
echo "Conversation ID: $CONV_ID"

# Continue the conversation
echo -e "\nContinuing conversation..."
uv run clink_invoke.py claude-zai "Show me an example" --continue-from "$CONV_ID"

# Continue with different CLI
echo -e "\nContinuing with different CLI..."
uv run clink_invoke.py gemini "Explain it more simply" --continue-from "$CONV_ID"

# List all active sessions
echo -e "\nActive sessions:"
uv run clink_invoke.py --list-sessions

# Clean old sessions
echo -e "\nCleaning sessions older than 7 days..."
uv run clink_invoke.py --clean-sessions --older-than 7d
