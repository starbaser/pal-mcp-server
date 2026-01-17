# Plan: Add `model` Parameter to Clink

## Overview

Add an optional `model` parameter to clink for per-call model specification, overriding the CLI client config default.

## Files to Modify

| File | Changes |
|------|---------|
| `tools/clink.py` | Add `model` field to request + schema, pass to agent |
| `clink/agents/base.py` | Add `model` param to `run()` and `_build_command()` |
| `clink/agents/claude.py` | Inject `--model` flag into command |

## Implementation

### 1. `tools/clink.py` - CLinkRequest (~line 50)

```python
model: str | None = Field(
    default=None,
    description="Model override (e.g., 'opus', 'sonnet', 'haiku').",
)
```

### 2. `tools/clink.py` - Schema (~line 177)

```python
"model": {
    "type": "string",
    "description": "Model override (e.g., 'opus', 'sonnet', 'haiku').",
},
```

### 3. `tools/clink.py` - agent.run() (~line 283)

Add `model=request.model` to the call.

### 4. `clink/agents/base.py` - run() signature (~line 55)

Add `model: str | None = None` parameter, forward to `_build_command()`.

### 5. `clink/agents/base.py` - _build_command() (~line 194)

Add `model: str | None = None` parameter.

### 6. `clink/agents/claude.py` - _build_command() (~line 37)

Add parameter and inject `--model` flag:
```python
if model:
    command = [c for i, c in enumerate(command)
               if not (c == "--model" or (i > 0 and command[i-1] == "--model"))]
    command.extend(["--model", model])
```

## Verification

1. `./code_quality_checks.sh`
2. Manual test: `clink with claude using model opus`
3. Update AGENCY.md, sync to docstore
