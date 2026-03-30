# palshebang — Auto-extraction of file-intent code blocks from PAL model responses

**Status:** proto-plan, not yet implemented

## Problem

PAL tools (chat, analyze, codereview, etc.) frequently generate complete files in their responses — not inline fragments, but content intended as a standalone file. Currently only the chat tool extracts these via a bespoke `<GENERATED-CODE>` XML mechanism. The extraction is chat-specific, the format is non-standard, and the output directory (`~/.claude/pal/code/`) is a flat pile of timestamped blobs with no project association.

## Design

A universal sigil convention for fenced code blocks that signals "this block is a complete file, not an inline fragment." The PAL model is instructed to use it; the server extracts matching blocks from any tool's response.

### Sigil format

````python
```python
#!/gen config.py
DEFAULT_TIMEOUT = 30
MAX_RETRIES = 3
```
````

The `#!/gen` line is the shebang. It carries the intended filename. The language tag on the fence is preserved (models already emit this naturally). The shebang is NOT a real shebang — it's a PAL extraction directive.

"File intent" means: not a snippet, not a diff, not an inline example. It's content the model considers a complete, self-contained file. The model decides when to use the sigil — the prompt guides but doesn't force. Inline fragments, partial examples, and illustrative snippets do NOT get the sigil.

## Extraction

Post-processing step in `server.py` (after tool execution, before MCP response). Applied to ALL model-requiring tools uniformly.

1. Regex scan for `` ```<lang>\n#!/gen <filename>\n...\n``` `` blocks
2. For each match:
   - Extract content (everything between shebang line and closing fence)
   - Save to disk (see Storage below)
   - Optionally strip the `#!/gen` line from the response text so the agent sees clean code. Or leave it — it's a one-liner and serves as a visual marker. Stripping is cleaner for copy-paste; leaving it is transparent about what happened.
3. Response text is otherwise unchanged — agent sees full code verbatim

## Storage

```
~/.claude/pal/code/{encoded-cwd}/{call-id}/
  ├── config.py
  └── utils/
      └── helpers.py
```

- **encoded-cwd**: same encoding as PALTree directories (e.g. `-home-eigenmage-dev-projects-foo`)
- **call-id**: `continuation_id` (store path or UUID) if available, else timestamp

If filename contains path separators (`#!/gen utils/helpers.py`), subdirectories are created. The filename is taken verbatim from the shebang.

## System Prompt

Addition to the base system prompt (all tools that call external models):

> When generating a complete, self-contained file — not a snippet or inline example — place `#!/gen <filename>` as the first line inside the code fence. This marks the block for automatic file extraction and archival.

The phrasing avoids "runnable" (which implies executability) and focuses on "complete, self-contained file" which is the actual intent signal.

## Migration

- Remove `<GENERATED-CODE>` extraction from `tools/chat.py`
- Remove `CODE_STORAGE_DIR` from `config.py` (or repurpose it)
- Old `.code` files in `~/.claude/pal/code/` become orphans (harmless)

## Scope

| File | Changes |
|------|---------|
| `server.py` | Add `_extract_gen_files()` post-processing step |
| `config.py` | Update `CODE_STORAGE_DIR` layout or add `GEN_STORAGE_DIR` |
| `tools/chat.py` | Remove `<GENERATED-CODE>` extraction |
| `systemprompts/` | Add `#!/gen` instruction to base prompt or shared fragment |

## Open Questions

- Strip the `#!/gen` line from persisted response? (cleaner for agent consumption)
- Strip it from the saved file too? (yes — it's not real code)
- What if the model uses `#!/gen` on a fragment by mistake? (save anyway — false positives are cheap, missed files are annoying)
- Should extraction add a note to the MCP response? e.g. "2 files extracted to ~/.claude/pal/code/..." (probably yes, as metadata)
- Filename collisions within same call-id directory? (overwrite — last wins)
