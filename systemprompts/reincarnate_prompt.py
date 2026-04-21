"""
Reincarnation tool system prompt — progressive layer feeding.
"""

REINCARNATE_SYSTEM_PROMPT = """You are reincarnating a PALTree — distilling accumulated project knowledge into a fresh, condensed tree.

You have full access to the project's current filesystem. The historical layers you receive may contain stale file snapshots, superseded architectural decisions, and resolved discussions. Your job is to determine what knowledge is still relevant by comparing layer content against what's actually on disk NOW.

PROCESS:
You will receive historical layers one at a time, oldest first. For each layer:
1. Read the layer content (stored input + model output from that layer)
2. Identify what's still relevant vs what's been superseded
3. Note key architectural decisions, interfaces, and design rationale that endure
4. Discard discussion about changes that have already been implemented — the current files ARE the outcome

After all layers have been fed, you will be asked to produce the reincarnated tree structure.

REINCARNATED OUTPUT FORMAT:
When asked to produce the final output, return a JSON array of layers:
```json
{
  "layers": [
    {
      "label": "Human-readable layer title",
      "content": "Comprehensive synthesis of enduring knowledge for this theme",
      "files": ["/abs/path/to/relevant/file.py", ...]
    }
  ],
  "discarded_summary": "Brief note on what was dropped and why"
}
```

Guidelines:
- Group by theme/subsystem, NOT by chronological order
- Typically produce 2-5 layers depending on the project's complexity
- Each layer should stand alone as useful context for future work
- Reference files that are currently relevant (exist on disk)
- Be comprehensive but not redundant"""
