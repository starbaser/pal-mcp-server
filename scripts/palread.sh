#!/usr/bin/env bash
set -euo pipefail

# Read a PALTree node by its dot-path (e.g. lsp-spec.L1.Q0.F0.analyze)
# Usage: palread.sh <tree_path>

if [[ $# -lt 1 ]]; then
    echo "Usage: palread.sh <tree_path>" >&2
    echo "  e.g. palread.sh lsp-spec.L1.Q0.F0.analyze" >&2
    exit 1
fi

exec uv run python3 -c "
import sys
from utils.palstore import load_store, resolve_palnode, resolve_store_location

tree_path = sys.argv[1]
location = resolve_store_location(tree_path)
if location is None:
    print(f'Error: PALTree \"{tree_path}\" not found.', file=sys.stderr)
    sys.exit(1)

directory, root_id = location
store = load_store(directory, root_id)
if store is None:
    print(f'Error: PALTree file not found: \"{root_id}\".', file=sys.stderr)
    sys.exit(1)

node = resolve_palnode(store, tree_path)
if node is None:
    if tree_path == store.store_id:
        print(f'# {tree_path}')
        print(f'Type: PALTree root')
        print(f'Directory: {store.directory}')
        print(f'Created: {store.created_at}')
        if store.label:
            print(f'Label: {store.label}')
    else:
        print(f'Error: Node not found: {tree_path}', file=sys.stderr)
        sys.exit(1)
else:
    print(f'# {tree_path}')
    if node.label:
        print(f'Label: {node.label}')
    if node.entry_type:
        print(f'Type: {node.entry_type}')
    if node.model:
        print(f'Model: {node.model}')
    if node.timestamp:
        print(f'Timestamp: {node.timestamp}')
    if node.files:
        print('Files:')
        for f in node.files:
            print(f'  {f}')
    if node.prompt:
        print()
        print('## Prompt')
        print()
        print(node.prompt)
    if node.response:
        print()
        print('## Response')
        print()
        print(node.response)
" "$1"
