#!/usr/bin/env bash
set -euo pipefail

# SessionStart hook: fires the ctxstore revival sequence.
# Reads armed.json from PAL storage — written by the ctxarm MCP tool.
# If cwd is armed, injects additionalContext forcing Claude to revive from the store.

input=$(cat)
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty')

[[ -z "$cwd" ]] && exit 0

armed_file="${PAL_STORAGE_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/pal}/context/armed.json"

[[ -f "$armed_file" ]] || exit 0

store_name=$(jq -r --arg cwd "$cwd" '.[$cwd] // empty' "$armed_file")

[[ -z "$store_name" ]] && exit 0

read -r -d '' context_msg <<TRIPWIRE || true
MANDATORY SESSION REVIVAL REQUIRED

A context revival tripwire is active for this project. You MUST execute the following revival sequence as your FIRST action before responding to any user message or beginning any task.

Failure to run this sequence is a critical protocol violation.

== REVIVAL SEQUENCE ==

Step 1: mcp__pal__ctxlist(directory="$cwd")
  Verify store "$store_name" exists in the results.
  If not found, report: "Tripwire is set but store '$store_name' was not found in ctxlist."
  Then proceed normally without revival.

Step 2: mcp__pal__ctxquery(store_id="$store_name", prompt="Restore full project context: summarize the current phase, active plan, open questions, last decisions made, and the highest-priority next action.")
  The returned summary IS your working context for this session.
  Do not proceed until you have read and internalized it.

Step 3: If a plan file path appears in the store output, read it with the Read tool.

== END REVIVAL SEQUENCE ==

After completing the revival sequence, acknowledge: "Context revived from store: $store_name"
Then proceed normally.
TRIPWIRE

printf '%s' "$context_msg" | jq -Rs '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: .
  }
}'
