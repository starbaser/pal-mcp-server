# Context Store System Rewrite

## Context

The context store system has a fundamental data model flaw: **all layer entries (L1, L2, ..., LN) resolve to the same thread UUID**. `increment_layer_count()` at `context_registry.py:106` creates new registry entries that all point to the same `thread_id`. This means `ctxquery` on L1 vs L30 returns identical model context — layer targeting is meaningless. Additionally, ctxlist/ctxtree have ~90% code overlap, ctxread's page parameter is a synthetic hack, and the registry-thread split has no transactional guarantee.

**PAL continuation_id**: `3d4975a8-fafc-4434-aed6-8cbdbeed7fd3` (analyze 3 + planner 5 + thinkdeep 3 steps)

## Files to Modify

| File | Action | Notes |
|------|--------|-------|
| `utils/context_store.py` | **CREATE** | New storage layer — replaces context_registry for ctx tools |
| `utils/context_builder.py` | **CREATE** | Tree-based context reconstruction + hydration bridge |
| `tools/context.py` | **REWRITE** | All 7 tool classes rewritten, CtxTreeTool removed, CtxForkTool added |
| `utils/context_registry.py` | **DELETE** | Replaced by context_store.py |
| `server.py` | **SURGICAL** | 4 functions updated |
| `config.py` | **MINOR** | Add CONTEXT_STORE_DIR path |
| `scripts/ctx-arm.sh` | **MINOR** | Path update if needed |
| `scripts/migrate_context_stores.py` | **CREATE** | One-time migration |
| `tests/test_context_store.py` | **CREATE** | Unit tests |

**PRESERVE** (shared by non-ctx tools): `utils/conversation_memory.py`, `utils/storage_backend.py`, `systemprompts/context_prompt.py`

## New Storage Layout

```
~/.claude/pal/context/
├── store-index.json                         # Global cache: store_id → directory
├── armed.json                               # Unchanged
├── -home-eigenmage-dev-projects-clearcode/   # Hash-encoded path
│   ├── clearcode-history.json               # One file per root store = full tree
│   └── clearcode-imgui-phase1.json
└── -home-eigenmage-dev-opt-pal-mcp-server/
    └── pal-mcp-server.json
```

Path encoding uses Claude Code's convention from `~/dev/scratch/claude_path.py`:
```python
def encode_directory(abs_path: str) -> str:
    return abs_path.replace("/", "-").replace(".", "-")
```
Both `/` and `.` → `-`. The `--` for dotfiles (e.g. `.config` → `--config`) is emergent.

Each `{store}.json` is a self-contained tree. The `store_id` dot-path maps directly to tree traversal:
`clearcode-history.Q7.thinkdeep0` → load file → `children["Q7"]` → `children["thinkdeep0"]`

**Constraint**: Dots prohibited in store names (enforced at ctxinit). Dashes are fine.

## Implementation Phases

### Phase 1: Foundation

#### 1a. `utils/context_store.py` (NEW)

Data models (Pydantic):
```python
class StoreNode(BaseModel):
    entry_type: Literal["store", "query", "tool", "fork"]
    label: str | None = None
    timestamp: str  # ISO 8601
    model: str | None = None
    files: list[str] = []
    prompt: str = ""
    response: str = ""
    tool_name: str | None = None
    children: dict[str, "StoreNode"] = {}

class StoreRoot(BaseModel):
    store_id: str
    directory: str
    label: str | None = None
    created_at: str
    children: dict[str, StoreNode] = {}
```

Functions:
- **Path utils**: `encode_directory()` (from `~/dev/scratch/claude_path.py` convention), `decode_directory()`, `get_store_dir()`, `get_store_path()`
- **Tree ops**: `load_store()`, `save_store()` (atomic), `resolve_node()`, `add_child()`, `walk_ancestry()`, `get_next_key()`
- **Index ops**: `load_index()`, `save_index()`, `update_index()`, `rebuild_index()`, `resolve_store_location()`
- **Armed ops**: `load_armed()`, `save_armed()`, `arm_store()`, `disarm_store()`, `get_armed_store()`, `list_armed_stores()` (moved from context_registry)
- **Listing**: `list_stores()`, `list_all_directories()`

`parse_store_path(store_id) -> (root_id, segments)`: Look up root in index first (longest prefix match), fallback to first-dot split.

#### 1b. `utils/context_builder.py` (NEW)

```python
def build_context_from_ancestry(ancestors: list[StoreNode], include_files=True) -> str:
    """Build formatted conversation history from ancestor chain."""

def hydrate_thread_context(store: StoreRoot, node_path: str) -> ThreadContext:
    """Create ephemeral ThreadContext from tree for non-ctx tool bridge."""
```

#### 1c. `config.py` update

Add `CONTEXT_STORE_DIR = os.path.join(PAL_STORAGE_DIR, "context")` (reuse existing path).

### Phase 2: Tool Rewrites — `tools/context.py`

#### Key architectural decision: SimpleTool pipeline integration

Context tools (ctxstore, ctxquery) still extend SimpleTool for model-calling infrastructure but bypass the thread system:

1. Do NOT set `continuation_id` → `server.py` skips `reconstruct_thread_context`
2. Load store tree in `execute()` before calling `super().execute()`
3. Build ancestry history → stash as `self._injected_history`
4. `prepare_prompt()` prepends `self._injected_history`
5. `_create_continuation_offer()` override: determine next key/path, return it (no thread creation)
6. `_record_assistant_turn()` override: write StoreNode to tree, save to disk
7. `format_response()`: stash `raw_text` as `self._last_raw_response` for tree storage

Call chain trace (SimpleTool._parse_response):
```
_parse_response(raw_text, request, model_info)
  ├── format_response(raw_text, ...) → stash raw_text
  ├── continuation_id = None → SKIP first _record_assistant_turn
  ├── _create_continuation_offer() → return {"continuation_id": "store.L1"}
  └── _create_continuation_offer_response()
       └── _record_assistant_turn("store.L1", formatted, ...) → TREE WRITE
```

#### CtxInitTool
- Validate no dots in store_name
- Create empty `StoreRoot`, write to disk
- Update `store-index.json`
- No thread creation

#### CtxStoreTool
- Load store → walk ancestry → build history
- Model call with ancestry context
- `_record_assistant_turn`: create `StoreNode(entry_type="store")`, add as L-child
- `_create_continuation_offer`: return new L-path

#### CtxQueryTool
Fork/continue unified by tree:
- Querying store/layer node → new Q-child (fork): `get_next_key(parent, "Q")`
- Querying query/tool node → numbered follow-up (continue): `get_next_key(parent, "")`
- Same ancestry context building, same model call pattern

#### CtxForkTool (NEW — no model call)
- `requires_model() -> False`
- Creates an empty leaf node in the store tree — no prompt, no response, no model call
- Uses `F` prefix: `my-store.F0`, `my-store.F1`, etc.
- `entry_type="fork"`
- Purpose: structural branching point. Subsequent ctxstore/ctxquery/tool calls chain off the fork's store_id
- Accepts: `store_id` (parent to fork from), optional `label`
- Returns: the new fork path (e.g. `my-store.F0`)
- Unlike ctxquery (which sends a prompt to the model and records prompt+response), ctxfork is purely structural

Example usage:
```
ctxfork(store_id="clearcode-history") → clearcode-history.F0
ctxstore(store_id="clearcode-history.F0", prompt="...", context_label="Phase 2 branch") → builds ancestry context from root through F0, adds L1 child
```

The ancestry walk treats fork nodes as passthrough — they contribute no prompt/response to the history, just establish a branching point. Child nodes of the fork inherit the fork's ancestry.

#### CtxListTool (absorbs CtxTreeTool)
- `ctxlist()` — all roots across all directories with full subtrees
- `ctxlist(directory=...)` — filtered
- `ctxlist(store_id=...)` — subtree of that node
- Output format with markdown links:
```
clearcode-history  (37 layers)
  - [L1. Layer 0: Project CLAUDE.md](#clearcode-history.L1) — 2026-03-18 — CLAUDE.md
  - [Q0](#clearcode-history.Q0)  "Synthesize everything about Babel…" — 2026-03-18
    - [.thinkdeep0](#clearcode-history.Q0.thinkdeep0)
  - [F0. Phase 2 branch](#clearcode-history.F0) — 2026-03-20
    - [L1. Phase 2 CLAUDE.md](#clearcode-history.F0.L1) — 2026-03-20
```

#### CtxReadTool
- `ctxread(store_id="store.L5")` → load store, resolve node, return prompt+response
- No TOC mode, no page parameter

#### CtxArmTool
- Use context_store armed functions (same logic, new import)

#### Remove CtxTreeTool
- Delete class from context.py
- Remove from server.py TOOLS dict

### Phase 3: server.py Surgery

#### `_build_store_listing()` (line 758)
Read from `context_store.list_stores(cwd)` instead of context_registry. Same output format.

#### `_resolve_store_continuation()` (line 793)
For non-ctx tools receiving store_id as continuation_id:
1. `resolve_store_location(continuation_id)` → detect store path
2. Load store tree, `hydrate_thread_context()` → ephemeral ThreadContext
3. Save hydrated thread in conversation_memory
4. Set `arguments["continuation_id"]` = hydrated UUID
5. Stash `arguments["_store_bridge"]` = {store, parent_path, new_path, child_key, tool_name}
6. `reconstruct_thread_context()` proceeds normally

Follow-up semantics:
- continuation_id points to tool node → numbered child (thinkdeep0.1)
- continuation_id points to store/query → new sibling (thinkdeep1)

#### `_inject_store_path_continuation()` (line 852)
After tool response, also call `_persist_tool_response_to_store()` to write response back to store tree.

#### `handle_call_tool()` routing
Remove `ctxtree` from TOOLS dict, add `ctxfork`. Context tools naturally bypass thread reconstruction (no continuation_id).

### Phase 4: Migration Script

`scripts/migrate_context_stores.py`:
1. Read `stores.json` + `thread_*.json` files
2. Group entries by directory, identify roots
3. For each root: pair thread turns by position → L-nodes
4. For query entries: load forked thread → Q-nodes with tool children
5. Handle follow-ups: subsequent turn pairs on query threads → numbered children
6. Write per-directory store JSON files
7. Build `store-index.json`
8. Non-destructive (old files preserved)

Edge cases: incomplete turn pairs (skip), missing thread files (empty nodes), deeply nested paths (recursive registry traversal).

### Phase 5: Tests

Unit tests (`tests/test_context_store.py`):
- `encode_directory`/`decode_directory` roundtrip
- Store CRUD lifecycle
- `resolve_node` with valid/invalid paths
- `add_child` at various depths
- `walk_ancestry` chain collection
- `get_next_key` with prefixes
- Index CRUD and rebuild
- Armed operations

Integration tests:
- Full lifecycle: ctxinit → ctxstore (3 layers) → ctxquery → ctxread → ctxlist
- Non-ctx bridge: ctxstore → thinkdeep(continuation_id=store_id) → verify tree
- Migration: old format → new format integrity

## Verification

1. Restart server, run `ctxinit` → verify JSON file created in hash-encoded directory
2. Run `ctxstore` 3x → verify L1, L2, L3 nodes in tree, model sees ancestry
3. Run `ctxquery` → verify Q0 child created, fork semantics work
4. Run `ctxread(store_id="store.L2")` → verify specific node content returned
5. Run `ctxlist` → verify unified tree output with markdown links
6. Run `ctxfork(store_id="store")` → verify F0 empty node in tree, no model call
6b. Run `ctxstore(store_id="store.F0", ...)` → verify L1 child under F0, ancestry includes root layers
7. Run `ctxarm` → verify armed.json updated
7. Run `thinkdeep(continuation_id="store.Q0")` → verify tree updated with tool node
8. Run `./code_quality_checks.sh` → verify ruff, black, isort, tests pass
9. Run migration script → verify existing stores preserved
