# Handoff: ctxlist output fixes

The ctxlist tool was reworked to use the unified format (tree-based `StoreRoot`/`StoreNode` from `context_store.py`). The renderer is `_render_store_tree` in `tools/context.py` lines 37-80. It's close to the target format but has these issues:

## 1. Key order in store JSON files is alphabetical

The renderer iterates `node.children` in dict insertion order (correct), but the store JSON files were written with keys sorted alphabetically during migration. Result: L1, L10, L11, ..., L2, L20 instead of L1, L2, L3, ..., L10.

**Fix:** Sort keys naturally (numeric suffix) when writing store JSON, or sort at render time with a natural sort key: `sorted(nodes.items(), key=lambda kv: _natural_sort_key(kv[0]))`.

## 2. Q16 follow-up children use bare numeric keys

Q16's children are stored as keys `1`, `2`, `3`, `4`, `5` in the JSON. They render as `[1]`, `[2]` etc. The target format uses dot-prefixed relative segments: `[.1]` or `[.Q16.1]` to match the convention of `.thinkdeep0`, `.analyze0`.

**Fix:** In `_render_store_tree`, for query/fork children that aren't tool-type, prefix the key with `.` in the display: `[.{key}]` instead of `[{key}]`.

## 3. Layer count shows 36 but should be 37

`_count_l_children` counts keys matching `L\d+` in the direct children dict. If one layer is at the wrong tree depth or uses a different key format, it would be missed.

**Verify:** Check the store JSON to see if all 37 layers are present as direct children, or if one was lost in migration.

## 4. Dates all show 2026-03-18

Many entries that should be 2026-03-19 or 2026-03-20 (per the old ctxread TOC which read from conversation turn timestamps) all show 2026-03-18. The `node.timestamp` was likely set during bulk migration rather than from the original turn timestamps.

**Fix:** If original turn timestamps are available in conversation memory, backfill `node.timestamp` from the user turn's timestamp for each node.

## 5. Layer entries only show title, not prompt text

The target format shows BOTH the title (context_label) in the link AND the prompt text (what was actually sent) in quotes after:

```
- [L1. Layer 0: Project CLAUDE.md — current state](#clearcode-history.L1)  "=== CONTEXT LAYER SUBMISSION === ..." — 2026-03-18 — CLAUDE.md
```

Currently the renderer only shows the label in the link for store-type nodes (`f"{key}. {node.label}"`). The `node.prompt` field exists on `StoreNode` but is not displayed for store entries.

**Fix:** In `_render_store_tree`, for `etype == "store"` nodes that have a `node.prompt`, add the truncated prompt text in quotes after the link, same as query nodes do:

```
{p}- [{label_part}](#{full_path})  "{_truncate_label(node.prompt, 60)}"{date_part}{files_part}
```

This gives both pieces of information: the TITLE tells you what the layer is about, the PROMPT TEXT shows what was actually written.

## Reference files

- Renderer: `tools/context.py` `_render_store_tree` (lines 37-80)
- Store model: `utils/context_store.py` `StoreRoot`/`StoreNode`
- Target format: `fix-context-store.md` under "Tool consolidation"
- Old output for comparison: `ctx-output.md`
