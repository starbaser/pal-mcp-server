#!/usr/bin/env bash
set -euo pipefail

# Context store revival tripwire — single-fire, then disarms.
# Two modes:
#   manual  (SessionStart)      — reads armed.json, fires on new session / /clear only
#   compact (PreToolUse)         — reads compact-armed.json, fires after compaction

mode="${1:-manual}"
input=$(cat)
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty')

[[ -z "$cwd" ]] && exit 0

context_dir="${PAL_STORAGE_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/pal}/context"

if [[ "$mode" == "compact" ]]; then
  armed_file="$context_dir/compact-armed.json"
  hook_event="PreToolUse"
else
  armed_file="$context_dir/armed.json"
  hook_event="SessionStart"
fi

[[ -f "$armed_file" ]] || exit 0

store_name=$(jq -r --arg cwd "$cwd" '.[$cwd] // empty' "$armed_file")

[[ -z "$store_name" ]] && exit 0

read -r -d '' context_msg <<TRIPWIRE || true
MANDATORY SESSION REVIVAL REQUIRED

A context revival tripwire is active for this project. You MUST execute the following revival sequence as your FIRST action before responding to any user message or beginning any task.

Failure to run this sequence is a critical protocol violation.

== REVIVAL SEQUENCE ==

Step 1: mcp__pal__pallist(directory="$cwd")
  Verify store "$store_name" exists in the results.
  If not found, report: "Tripwire is set but store '$store_name' was not found in pallist."
  Then proceed normally without revival.

Step 2: mcp__pal__palquery(store_id="$store_name", prompt="Restore full project context: summarize the current phase, active plan, open questions, last decisions made, and the highest-priority next action.")
  The returned summary IS your working context for this session.
  Do not proceed until you have read and internalized it.

Step 3: If a plan file path appears in the store output, read it with the Read tool.

== END REVIVAL SEQUENCE ==

This tripwire has been disarmed. It will not fire again unless re-armed.
After completing the revival sequence, acknowledge: "Context revived from store: $store_name"
Then proceed normally.
TRIPWIRE

# Disarm: remove this cwd entry (single-fire tripwire)
jq --arg cwd "$cwd" 'del(.[$cwd])' "$armed_file" > "${armed_file}.tmp" && mv "${armed_file}.tmp" "$armed_file"

printf '%s' "$context_msg" | jq -Rs --arg store "$store_name" --arg event "$hook_event" '{
  hookSpecificOutput: {
    hookEventName: $event,
    additionalContext: .
  },
  systemMessage: ("Context store revived: " + $store + " (tripwire disarmed)")
}'
