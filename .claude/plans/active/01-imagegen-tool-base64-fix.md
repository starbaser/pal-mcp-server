# PAL MCP: Dedicated `imagegen` Tool + base64 Bug Fix

## Context

The image generation response pipeline is complete (Batch 0-2 just landed): Gemini provider walks multi-part responses, `ModelResponse.generated_images` carries output, `SimpleTool.execute()` emits `ImageContent`. But nothing auto-routes to image-capable models — users must explicitly specify `model: gemini-3.1-flash-image-preview`. A dedicated `imagegen` tool solves this with auto-routing, a tuned system prompt, and clean MCP discoverability.

Additionally, the Gemini SDK's `Blob.data` field is `Optional[bytes]` (raw bytes), but the current code passes it directly to `ImageContent.data` which expects a base64 string. This is a serialization bug that will crash on real image generation responses.

---

## Bug Fix: base64 Encoding (MUST ship with tool)

**File:** `providers/gemini.py` ~line 307

**Problem:** `part.inline_data.data` is raw `bytes` (verified: `google.genai.types.Blob.data: Optional[bytes]`). Current code stores it directly in `generated_images` dict. `ImageContent.data` expects base64 string.

**Fix:** In the `_attempt()` closure, encode before appending:
```python
# Current (broken):
"data": part.inline_data.data,

# Fixed:
"data": base64.b64encode(part.inline_data.data).decode("utf-8"),
```

`base64` is already imported at the top of `gemini.py` (line 3).

---

## Dependency Graph

```
B0-a  → []         base64 bug fix (gemini.py)
B0-b  → []         ToolModelCategory.IMAGE_GENERATION + provider routing
B0-c  → []         System prompt (imagegen_prompt.py)
B1    → [B0-a,b,c] ImageGenTool + registration + tests
```

### Batch 0 — Foundation (PARALLEL: 1 inline + 2 agents)

| Task | Description | Files | Model | Agent |
|------|-------------|-------|-------|-------|
| B0-a | Fix base64 encoding bug | `providers/gemini.py` | **inline** | Opus direct — 1 edit, <2 work units |
| B0-b | Add IMAGE_GENERATION category + provider routing | `tools/models.py`, `providers/gemini.py` | sonnet | python |
| B0-c | Create imagegen system prompt | `systemprompts/imagegen_prompt.py`, `systemprompts/__init__.py` | haiku | python |

### Batch 1 — Tool + Registration + Tests (sequential after B0)

| Task | Description | Files | Model | Agent |
|------|-------------|-------|-------|-------|
| B1 | ImageGenTool class + server registration + tests | `tools/imagegen.py`, `tools/__init__.py`, `server.py`, `tests/test_imagegen.py` | sonnet | python |

### Predicate Verification (all passed)

| Predicate | Status | Evidence |
|-----------|--------|----------|
| `base64` imported in gemini.py | VERIFIED | `gemini.py:3` |
| `ToolModelCategory` enum | VERIFIED | `tools/models.py:11-16` |
| `SimpleTool` / `ChatTool` pattern | VERIFIED | `tools/simple/base.py:24`, `tools/chat.py:57` |
| `systemprompts/__init__.py` export pattern | VERIFIED | Lines 5-18 imports, 20-35 `__all__` |
| `TEMPERATURE_CREATIVE` in config.py | VERIFIED | `config.py:66` — value `1.0` |
| `server.py` TOOLS dict pattern | VERIFIED | `server.py:261-279` |
| `get_preferred_model()` routing structure | VERIFIED | `gemini.py:531-593` — insert between lines 574-576 |
| `ImageContent` importable from mcp.types | VERIFIED | `tools/simple/base.py:579` |

### Execution Instructions

**Batch 0**: B0-a inline edit + launch B0-b and B0-c agents simultaneously.
B0-a and B0-b both touch `gemini.py` but in different methods ~270 lines apart — no conflict.
**Batch 1**: After B0 complete, launch B1 agent.
**Verify**: Run tests after B1.

---

## Task Details

### B0-a: Fix base64 Encoding Bug

**File:** `providers/gemini.py`

In `_attempt()` closure (~line 306-309), change:
```python
generated_images.append({
    "data": part.inline_data.data,
    "mime_type": part.inline_data.mime_type or "image/png",
})
```
to:
```python
generated_images.append({
    "data": base64.b64encode(part.inline_data.data).decode("utf-8"),
    "mime_type": part.inline_data.mime_type or "image/png",
})
```

`base64` is already imported at line 3.

---

### B0-b: ToolModelCategory.IMAGE_GENERATION + Provider Routing

**File 1:** `tools/models.py` (line 11-16)

Add to `ToolModelCategory` enum:
```python
IMAGE_GENERATION = "image_generation"  # Native image generation capability
```

**File 2:** `providers/gemini.py` — `get_preferred_model()` (line 531-593)

Add a new category branch after the `EXTENDED_REASONING` block (~line 574), before the `FAST_RESPONSE` block:

```python
elif category == ToolModelCategory.IMAGE_GENERATION:
    # Prefer dedicated image generation models, then any image-capable model
    capability_map = self.get_all_model_capabilities()  # already fetched above at line 546
    image_gen_models = [
        m for m in allowed_models
        if m in capability_map and capability_map[m].supports_image_generation
    ]
    if image_gen_models:
        # Prefer the dedicated image preview model over general-purpose flash
        dedicated = [m for m in image_gen_models if "image" in m or "imagen" in m]
        if dedicated:
            return find_best(dedicated)
        return find_best(image_gen_models)
```

Note: `capability_map` is already fetched at line 546. Don't re-fetch — use the existing variable.

---

### B0-c: System Prompt

**Create:** `systemprompts/imagegen_prompt.py`

```python
IMAGEGEN_PROMPT = """You are an expert visual prompt engineer and image generation assistant.

When the user requests an image, your job is to:
1. Expand their request into a rich, detailed visual description
2. Generate the image directly — never ask for confirmation

Enrichment guidelines:
- Add specific details about composition, framing, and perspective
- Describe lighting conditions, color palette, and atmosphere
- Specify artistic style when the user's intent is clear
- Include texture, material, and surface quality details
- Maintain the user's core creative vision while enhancing specificity

When the user provides reference images, analyze them and incorporate their visual elements (style, palette, composition) into the generation.

For iterative refinement requests ("make it darker", "change the background"), apply the modification while preserving all other aspects of the previous generation.

Always respond with a brief description of what you generated alongside the image."""
```

**Modify:** `systemprompts/__init__.py`

Add import and export:
```python
from .imagegen_prompt import IMAGEGEN_PROMPT
# Add "IMAGEGEN_PROMPT" to __all__
```

---

### B1: ImageGenTool + Registration + Tests

**Create:** `tools/imagegen.py`

Follow `ChatTool` pattern. Key structure:

```python
"""Image generation tool — native AI image creation and editing."""

import logging
from typing import TYPE_CHECKING, Any, Optional

from pydantic import Field

if TYPE_CHECKING:
    from providers.shared import ModelCapabilities
    from tools.models import ToolModelCategory

from systemprompts import IMAGEGEN_PROMPT
from tools.shared.base_models import COMMON_FIELD_DESCRIPTIONS, ToolRequest

from .simple.base import SimpleTool

IMAGEGEN_FIELD_DESCRIPTIONS = {
    "prompt": (
        "Describe the image to generate. Be as specific or abstract as you like — "
        "the model expands terse descriptions into rich visual prompts. "
        "For editing, describe the desired changes to the reference image."
    ),
    "media": "Optional reference images (absolute paths or base64) for style transfer or editing.",
}


class ImageGenRequest(ToolRequest):
    prompt: str = Field(..., description=IMAGEGEN_FIELD_DESCRIPTIONS["prompt"])
    media: Optional[list[str]] = Field(
        default_factory=list,
        description=IMAGEGEN_FIELD_DESCRIPTIONS["media"],
    )


class ImageGenTool(SimpleTool):
    def get_name(self) -> str:
        return "imagegen"

    def get_description(self) -> str:
        return (
            "Generate images using AI models with native image generation capability. "
            "Describe what you want and receive generated images. Supports iterative "
            "refinement via continuation_id and image editing via reference images in media."
        )

    def get_annotations(self) -> dict[str, Any]:
        return {"readOnlyHint": True}

    def get_system_prompt(self) -> str:
        return IMAGEGEN_PROMPT

    def get_default_temperature(self) -> float:
        from config import TEMPERATURE_CREATIVE
        return TEMPERATURE_CREATIVE  # Higher temp for creative generation

    def get_model_category(self) -> "ToolModelCategory":
        from tools.models import ToolModelCategory
        return ToolModelCategory.IMAGE_GENERATION

    def get_request_model(self):
        return ImageGenRequest

    def get_tool_fields(self) -> dict[str, dict[str, Any]]:
        return {
            "prompt": {"type": "string", "description": IMAGEGEN_FIELD_DESCRIPTIONS["prompt"]},
            "media": {"type": "array", "items": {"type": "string"}, "description": IMAGEGEN_FIELD_DESCRIPTIONS["media"]},
        }

    def get_required_fields(self) -> list[str]:
        return ["prompt"]

    async def prepare_prompt(self, request) -> str:
        return self.get_request_prompt(request)

    def format_response(self, response: str, request, model_info=None) -> str:
        if not response or not response.strip():
            return "Image generated successfully based on your description."
        return response
```

**Check:** `TEMPERATURE_CREATIVE` — verify it exists in `config.py`. If not, use `1.0` directly or `TEMPERATURE_BALANCED`.

**Modify:** `tools/__init__.py`

Add:
```python
from .imagegen import ImageGenTool
# Add "ImageGenTool" to __all__
```

**Modify:** `server.py`

Add to imports (line 50-69):
```python
ImageGenTool,
```

Add to TOOLS dict (line 261-280):
```python
"imagegen": ImageGenTool(),  # Native AI image generation and editing
```

**Create:** `tests/test_imagegen.py`

Tests:
1. `test_imagegen_tool_name` — verify `get_name() == "imagegen"`
2. `test_imagegen_model_category` — verify returns `IMAGE_GENERATION`
3. `test_imagegen_format_response_empty` — verify empty response returns placeholder
4. `test_imagegen_format_response_passthrough` — verify non-empty response passes through
5. `test_imagegen_request_model` — verify `ImageGenRequest` validates correctly
6. `test_image_generation_category_routing` — verify `GeminiModelProvider.get_preferred_model(IMAGE_GENERATION, ...)` returns image-capable model
7. `test_base64_encoding_in_generated_images` — verify the base64 fix produces valid strings

---

## Implementation Context (Rehydration)

### Critical Background
- `SimpleTool.execute()` already handles `ImageContent` emission at lines 577-590 of `tools/simple/base.py`
- The `format_response` hook is called by `_parse_response` (line 600) — empty string from image-only responses flows through here
- `get_preferred_model()` uses `capability_map` variable fetched once at line 546 — do NOT re-fetch in new elif
- B0-a and B0-b both edit `gemini.py` but in completely separate methods: `_attempt()` (line 307) vs `get_preferred_model()` (line 575)
- `ChatTool` at `tools/chat.py:57` is the reference pattern — `ImageGenTool` follows the same `SimpleTool` inheritance

### Decision Rationale
- **ToolModelCategory.IMAGE_GENERATION over hardcoded model**: Clean OOP, future-proofs for multi-provider image gen
- **Keep continuation support**: Free via SimpleTool inheritance, enables iterative refinement
- **TEMPERATURE_CREATIVE (1.0)**: Higher creativity for image generation prompts
- **Haiku for B0-c**: Fully specified content with zero ambiguity, no reasoning needed
- **Inline for B0-a**: Single line change, agent overhead > task cost

### What NOT to Do
- Do NOT re-fetch `capability_map` in the IMAGE_GENERATION elif — use the variable already at line 546
- Do NOT set `response_modalities=["IMAGE"]` without `"TEXT"` — both needed (already handled in provider)
- Do NOT remove `ChatTool`'s implicit image gen support — chat passthrough stays, imagegen is the intentional path
- Do NOT add image generation routing to non-Gemini providers — they don't support it yet

### Inline Tasks (Opus Direct)

| Task | Rationale |
|------|-----------|
| B0-a | 1 edit in 1 file. `base64.b64encode(part.inline_data.data).decode("utf-8")` wrapping. |

### Start Now
Do B0-a inline edit, then launch B0-b and B0-c agents in parallel.

---

## Edge Cases

1. **Empty text response**: `format_response` returns placeholder text. `SimpleTool.execute()` already handles `model_response.content or generated_images` (line 458 fix from previous batch).

2. **User specifies non-image model**: The model resolution happens at the server boundary. If a user explicitly names a model, it's used as-is. If that model lacks `supports_image_generation`, Gemini won't set `response_modalities` and no images will be generated. The text response will explain the model can't generate images. This is acceptable — explicit model choice overrides auto-routing.

3. **No image-capable provider**: If the IMAGE_GENERATION category finds no models (e.g., only OpenAI configured), it falls through to the default BALANCED routing. The tool returns text-only response.

---

## Verification

1. **base64 fix**: `uv run python -c "import base64; data = b'test'; assert isinstance(base64.b64encode(data).decode('utf-8'), str)"` — sanity check
2. **Unit tests**: `uv run python -m pytest tests/test_imagegen.py tests/test_media_and_image_gen.py -v --tb=short`
3. **Regression**: `uv run python -m pytest tests/ -m "not integration" --tb=short -q`
4. **Live test**: After restart, `mcp__pal__imagegen` with prompt "a cat sitting on a rainbow" — verify ImageContent returned
5. **Model routing**: Verify `mcp__pal__listmodels` shows imagegen routes to `gemini-3.1-flash-image-preview`
