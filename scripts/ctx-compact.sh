#!/usr/bin/env bash
set -euo pipefail

# PostCompact hook: auto-arms the context store after compaction.
# Compaction is lossy — arms the most recently used store for this project
# so the next UserPromptSubmit triggers a full revival from the store.

input=$(cat)
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty')

[[ -z "$cwd" ]] && exit 0

context_dir="${PAL_STORAGE_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/pal}/context"
index_file="$context_dir/store-index.json"
armed_file="$context_dir/compact-armed.json"

[[ -f "$index_file" ]] || exit 0

# Look up directory slug for this cwd
dir_slug=$(jq -r --arg cwd "$cwd" '.directories[$cwd] // empty' "$index_file")
[[ -z "$dir_slug" ]] && exit 0

# Find most recently modified store file in the directory
store_dir="$context_dir/$dir_slug"
[[ -d "$store_dir" ]] || exit 0

store_file=$(ls -t "$store_dir/"*.json 2>/dev/null | head -1)
[[ -z "$store_file" ]] && exit 0

store_name=$(basename "$store_file" .json)

# Arm: write cwd → store_name into armed.json
if [[ -f "$armed_file" ]]; then
  jq --arg cwd "$cwd" --arg store "$store_name" '.[$cwd] = $store' "$armed_file" > "${armed_file}.tmp" && mv "${armed_file}.tmp" "$armed_file"
else
  jq -n --arg cwd "$cwd" --arg store "$store_name" '{($cwd): $store}' > "$armed_file"
fi

jq -n --arg store "$store_name" '{
  systemMessage: ("Compaction complete — context store re-armed: " + $store)
}'
