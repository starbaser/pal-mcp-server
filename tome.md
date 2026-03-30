# TOME — Token-Optimized Markdown Encoding

TOME is a serialization format that converts nested JSON/dict data into BFS-linearized markdown optimized for LLM token efficiency. It lives in the `oboros` package.

## Install

```bash
# Git dependency (pyproject.toml)
uv add oboros --git https://github.com/starbaser/oboros.git

# Or manually in pyproject.toml:
[project]
dependencies = ["oboros"]

[tool.uv.sources]
oboros = { git = "https://github.com/starbaser/oboros.git" }
```

## Library Usage

```python
from oboros import tome

# Encode a dict to TOME format
text = tome.dumps(data)

# With options
text = tome.dumps(
    data,
    newline_threshold=3,  # min newlines to extract a string (default: 2)
    optimize=True,        # recursive cost optimizer (default: True)
)

# Custom comparator for extraction decisions
def my_comparator(key, value, full_key, ctx):
    """Return True to extract value as a markdown section."""
    if key == "response":
        return True  # always extract response fields
    return isinstance(value, str) and value.count("\n") >= 2

text = tome.dumps(data, comparator=my_comparator)
```

## CLI Usage

```bash
# Pipe JSON through the formatter
echo '{"key": "value"}' | oboros tome

# Disable cost optimizer (extract everything)
cat data.json | oboros tome --no-optimize

# Custom newline threshold
cat data.json | oboros tome --threshold 4
```

## Output Format

TOME produces BFS-linearized markdown. Each JSON object becomes its own YAML frontmatter block. Siblings are adjacent, child objects are deferred via pointer sigils.

```
# §root

---
name: kitstore
version: '1.0'
readme: → §readme
sessions:
- → §sessions[0]
- → §sessions[1]
---

## `readme`

A long multiline readme...

# `sessions[0]` ← root

---
id: abc
agent: → §sessions[0].agent
---
```

### Sigils

| Sigil | Usage | Example |
|-------|-------|---------|
| `§` | Pointer target prefix | `→ §sessions[0].agent` |
| `→` | Pointer arrow | `key: → §child.path` |
| `←` | Breadcrumb (heading) | `# \`path\` ← parent` |

### Heading Levels

- `#` — object blocks (deferred child dicts)
- `##` — multiline content sections (extracted text)

## Cost Optimizer

When `optimize=True` (default), TOME runs a DFS bottom-up pass before BFS rendering. For each subtree, it compares:

- **Extraction cost**: heading + fences + pointer (~15-20 tokens overhead)
- **Inlining cost**: YAML indentation (2 spaces x depth x lines)

If inlining is cheaper, the subtree stays in its parent's YAML frontmatter. Small, shallow objects get inlined automatically. Large or deep objects get extracted to their own blocks.

The breakeven formula:

```
Extract if:  lines × indent_tokens × depth  >  fixed_overhead + 2×T(path) + T(parent)
```

Token counts use tiktoken (`cl100k_base`). Short-circuit: subtrees under 3 lines always inline.
