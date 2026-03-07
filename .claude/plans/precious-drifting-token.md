# Plan: Add `perceive` — Media Intelligence Extraction Tool

## Context

With audio support and strict capability enforcement now shipped (commit `2737fc9`), PAL has the infrastructure for Gemini-native multimodal processing. The user wants a dedicated tool that exhaustively extracts structured intelligence from media files (image, video, audio) — producing objective, temporally-marked, machine-parseable output that text-only models (Claude, GPT) can consume as input data. This is a "perception" tool: it lets non-multimodal models reason about content they can't natively see or hear.

Design validated through 4-step Gemini 3.1 Pro thinkdeep session (continuation_id: `0b193bc8-f5cb-42e9-bc73-f162e8bae683`).

## Architecture Decision

**One unified tool** (`perceive`), not three separate tools per media type.

Rationale:
- Simpler UX — "perceive this file" regardless of type
- System prompt adapts based on detected media types
- Media types overlap (video has audio tracks, images have text)
- Follows PAL convention: tools are verbs (`analyze`, `chat`, `debug`), not nouns
- `_pre_execute_validate()` enforces Gemini-only routing regardless

## Changes

### 1. Create `systemprompts/perceive_prompt.py`

System prompt that enforces structured, objective, temporally-marked output. Adapts per media type:

```python
PERCEIVE_PROMPT = """You are a Multimodal Intelligence Analyst. Your task is to extract comprehensive, objective, structured data from the provided media.

CORE RULES:
- Be exhaustively thorough. Capture every detail — do not summarize or skip content.
- Be strictly objective. Describe what IS present, not what it might mean.
- Never hallucinate or infer content not directly observable.
- If the user provides a focus prompt, prioritize that area but still capture baseline context for everything else.
- If no focus prompt is provided, perform full comprehensive extraction.

OUTPUT FORMAT — Structured Markdown with typed sections. Adapt based on media type:

FOR VIDEO:
## Media Summary
- Type, duration, resolution, frame rate

## Timeline
### [MM:SS–MM:SS] Scene description
- **Visual**: What is shown on screen (objects, people, UI, text, motion)
- **Audio**: What is heard (speech, music, ambient sounds)
- **Text/OCR**: Any readable text (on-screen, slides, captions, code)

## Speakers
- Count, identification, characteristics, attribution

## Extracted Text (OCR)
- All readable text with timestamps: `[MM:SS] Source: "text content"`

FOR AUDIO:
## Media Summary
- Type, duration, format, channels

## Timeline
### [MM:SS–MM:SS] Segment description
- **Speech**: Speaker-attributed transcription
- **Sounds**: Non-speech audio events
- **Tone**: Emotional register, pace, volume changes

## Speakers
- Count, characteristics, identification where possible

## Full Transcript
- Speaker-attributed, timestamped transcript

FOR IMAGES:
## Media Summary
- Type, dimensions, format

## Scene Description
- Overall composition, setting, perspective, lighting

## Objects & Entities
- Identified items with spatial positions (top-left, center, etc.)

## Text & Data (OCR)
- All readable text, labels, numbers, code

## Technical Notes
- Focus, exposure, color palette, notable artifacts

FOR MIXED MEDIA (multiple items):
- Number each item: `## Item 1: [filename]`, `## Item 2: [filename]`
- Apply the appropriate schema per item
- Add `## Cross-References` section noting relationships between items

Always conclude with:
## Analysis Summary
- Brief factual summary of key content extracted
"""
```

### 2. Update `systemprompts/__init__.py`

Add import and `__all__` entry:
```python
from .perceive_prompt import PERCEIVE_PROMPT
# Add "PERCEIVE_PROMPT" to __all__
```

### 3. Create `tools/perceive.py`

Follows `ImageGenTool` pattern exactly. Key differences:
- `media` is **required** (nothing to perceive without it)
- `prompt` is **optional** (exhaustive extraction by default, focused when provided)
- `_pre_execute_validate()` checks that the resolved model supports the specific media types in the request
- `get_model_category()` returns `ToolModelCategory.BALANCED` (perception is understanding, not generation)

```python
"""Media intelligence extraction — structured analysis of images, video, and audio."""
import logging
from typing import TYPE_CHECKING, Any, Optional
from pydantic import Field

if TYPE_CHECKING:
    from tools.models import ToolModelCategory

from systemprompts import PERCEIVE_PROMPT
from tools.shared.base_models import COMMON_FIELD_DESCRIPTIONS, ToolRequest
from .simple.base import SimpleTool
from utils.media_utils import is_audio_file, is_video_file

logger = logging.getLogger(__name__)

PERCEIVE_FIELD_DESCRIPTIONS = {
    "prompt": (
        "Optional focus for the analysis. When provided, prioritizes extracting "
        "information relevant to this prompt while still capturing baseline context. "
        "When omitted, performs comprehensive extraction of all content."
    ),
    "media": (
        "Media files to analyze — absolute file paths or base64 data URLs. "
        "Supports images, video, and audio. At least one file is required."
    ),
}

class PerceiveRequest(ToolRequest):
    media: list[str] = Field(
        ...,
        description=PERCEIVE_FIELD_DESCRIPTIONS["media"],
        min_length=1,
    )
    prompt: str = Field(
        default="",
        description=PERCEIVE_FIELD_DESCRIPTIONS["prompt"],
    )

class PerceiveTool(SimpleTool):
    def get_name(self) -> str:
        return "perceive"

    def get_description(self) -> str:
        return (
            "Extract structured intelligence from images, video, and audio files. "
            "Produces objective, temporally-marked, machine-parseable analysis that "
            "text-only models can consume. Supports follow-up queries via continuation_id."
        )

    def get_annotations(self) -> dict[str, Any]:
        return {"readOnlyHint": True}

    def get_system_prompt(self) -> str:
        return PERCEIVE_PROMPT

    def get_model_category(self) -> "ToolModelCategory":
        from tools.models import ToolModelCategory
        return ToolModelCategory.BALANCED

    def get_request_model(self):
        return PerceiveRequest

    def get_tool_fields(self) -> dict[str, dict[str, Any]]:
        return {
            "media": {
                "type": "array",
                "items": {"type": "string"},
                "description": PERCEIVE_FIELD_DESCRIPTIONS["media"],
            },
            "prompt": {
                "type": "string",
                "description": PERCEIVE_FIELD_DESCRIPTIONS["prompt"],
            },
        }

    def get_required_fields(self) -> list[str]:
        return ["media"]

    def _pre_execute_validate(self) -> Optional[dict]:
        """Verify the resolved model supports the media types in the request."""
        ctx = getattr(self, "_model_context", None)
        if ctx is None:
            return None
        caps = getattr(ctx, "capabilities", None)
        if caps is None:
            return None

        media_items = self._current_arguments.get("media", [])
        if not media_items:
            return {
                "status": "error",
                "content": "At least one media file is required for the perceive tool.",
                "content_type": "text",
            }

        model_name = getattr(ctx, "model_name", "unknown")
        has_video = any(is_video_file(m) for m in media_items)
        has_audio = any(is_audio_file(m) for m in media_items)
        has_image = any(not is_video_file(m) and not is_audio_file(m) for m in media_items)

        if has_image and not caps.supports_images:
            return self._capability_error(model_name, "image")
        if has_video and not caps.supports_video:
            return self._capability_error(model_name, "video")
        if has_audio and not caps.supports_audio:
            return self._capability_error(model_name, "audio")

        return None

    def _capability_error(self, model_name: str, media_type: str) -> dict:
        return {
            "status": "error",
            "content": (
                f"Model '{model_name}' does not support {media_type} input. "
                f"The perceive tool requires a Gemini model with native multimodal support "
                f"(e.g. 'gemini-3.1-pro-preview' or 'gemini-2.5-pro'). "
                f"Specify a capable model using the model parameter."
            ),
            "content_type": "text",
            "metadata": {
                "error_type": "capability_error",
                "model_name": model_name,
                "unsupported_media_type": media_type,
            },
        }

    async def prepare_prompt(self, request) -> str:
        user_prompt = self.get_request_prompt(request)
        if user_prompt:
            return f"Focus your analysis on: {user_prompt}"
        return "Perform a comprehensive analysis of the attached media."

    def format_response(self, response: str, request, model_info=None) -> str:
        if not response or not response.strip():
            return "Media analysis complete but no structured output was generated."
        return response
```

### 4. Update `tools/__init__.py`

Add import and `__all__` entry:
```python
from .perceive import PerceiveTool
# Add "PerceiveTool" to __all__
```

### 5. Update `server.py`

**Import** (line ~68, in the `from tools import` block):
```python
from tools import PerceiveTool
```

**TOOLS dict** (line ~282, before closing brace):
```python
"perceive": PerceiveTool(),
```

### 6. Tests — `tests/test_perceive.py`

```python
class TestPerceiveToolValidation:
    """Test _pre_execute_validate() for media capability checks."""

    def test_no_media_returns_error(self)
    def test_image_without_supports_images_returns_error(self)
    def test_video_without_supports_video_returns_error(self)
    def test_audio_without_supports_audio_returns_error(self)
    def test_image_with_capable_model_passes(self)
    def test_video_with_capable_model_passes(self)
    def test_audio_with_capable_model_passes(self)
    def test_mixed_media_checks_all_types(self)
    def test_no_model_context_passes(self)  # graceful when context unavailable

class TestPerceiveToolSchema:
    """Test get_tool_fields, get_required_fields, get_name, get_description."""

    def test_media_is_required(self)
    def test_prompt_is_optional(self)
    def test_tool_name(self)
    def test_tool_description(self)

class TestPerceivePrompt:
    """Test prepare_prompt() behavior."""

    def test_with_focus_prompt(self)      # returns "Focus your analysis on: ..."
    def test_without_prompt(self)          # returns comprehensive analysis text
    def test_empty_prompt(self)            # treated same as no prompt
```

## Files to Create/Modify

| File | Action | Change |
|---|---|---|
| `systemprompts/perceive_prompt.py` | **Create** | `PERCEIVE_PROMPT` constant |
| `systemprompts/__init__.py` | Modify | Import + `__all__` entry |
| `tools/perceive.py` | **Create** | `PerceiveTool(SimpleTool)` implementation |
| `tools/__init__.py` | Modify | Import + `__all__` entry |
| `server.py` | Modify | Import + TOOLS dict registration |
| `tests/test_perceive.py` | **Create** | Validation, schema, and prompt tests |

## Existing Code to Reuse

| Pattern | Source | Reuse |
|---|---|---|
| `SimpleTool` base class | `tools/simple/base.py` | Inherit — handles execute flow, media, continuation |
| `_pre_execute_validate()` hook | `tools/shared/base_tool.py:1451` | Override for capability checks |
| `ImageGenTool` structure | `tools/imagegen.py` | Copy class structure, field descriptions, validation |
| `is_video_file()`, `is_audio_file()` | `utils/media_utils.py` | Detect media types in validation |
| `ToolRequest` base model | `tools/shared/base_models.py` | Inherit for `PerceiveRequest` |
| Prompt export pattern | `systemprompts/__init__.py` | Follow existing import + `__all__` convention |

## Orchestration Structure

**CRITICAL**: This plan has been orchestrated for parallel execution.
All predicates verified grounded (10/10). Continuation: `0b193bc8-f5cb-42e9-bc73-f162e8bae683`.

### Dependency Graph

```
T1 (perceive_prompt.py) ─┬─▶ T2 (systemprompts/__init__.py)
                          └─▶ T3 (tools/perceive.py) ─┬─▶ T4 (tools/__init__.py) ─▶ T5 (server.py)
                                                       └─▶ T6 (tests/test_perceive.py)
```

### Batch 1 — Inline (Opus direct)
| Task | Description | Action |
|------|-------------|--------|
| T1 | Create `systemprompts/perceive_prompt.py` | Write file — prompt text is in plan |
| T2 | Update `systemprompts/__init__.py` | 2 line edits — import + `__all__` |

### Batch 2 — PARALLEL (launch simultaneously)
| Task | Description | Agent | Model |
|------|-------------|-------|-------|
| T3 | Create `tools/perceive.py` | python | sonnet |
| T6 | Create `tests/test_perceive.py` | python | sonnet |

T6 can be authored in parallel since the full interface is specified in the plan. Runtime dep on T3 but write-time independent.

### Batch 3 — Inline (Opus direct)
| Task | Description | Action |
|------|-------------|--------|
| T4 | Update `tools/__init__.py` | 2 line edits — import + `__all__` |
| T5 | Update `server.py` | 2 line edits — import + TOOLS dict |

### Batch 4 — Verification
```bash
uv run --with pytest --with pytest-asyncio pytest tests/test_perceive.py -v --tb=short
uv run --with pytest --with pytest-asyncio pytest tests/ -m "not integration" --tb=short -q
```

### Execution Instructions

1. **Batch 1**: Opus writes T1 (perceive_prompt.py) and edits T2 (systemprompts/__init__.py) inline
2. **Batch 2**: Launch T3 + T6 as parallel python agents
3. **Batch 3**: Opus edits T4 (tools/__init__.py) and T5 (server.py) inline
4. **Batch 4**: Run tests, iterate on failures

### Inline Tasks (Opus Direct)
| Task | Rationale |
|------|-----------|
| T1 | Pure transcription from plan — Write call |
| T2 | 2 trivial line edits, same directory as T1 |
| T4 | 2 trivial line edits |
| T5 | 2 trivial line edits |

## Implementation Context (Rehydration)

### Critical Background
- `perceive` follows the exact `ImageGenTool` pattern from `tools/imagegen.py`
- `SimpleTool` base class handles: execute flow, media extraction, continuation_id, model resolution, temperature, system prompt augmentation
- `_pre_execute_validate()` is called at `tools/simple/base.py:329` after model context resolution
- `ToolModelCategory.BALANCED` (not DEEP_THINKING) — the enum uses `EXTENDED_REASONING` not `DEEP_THINKING`
- `media` field on `PerceiveRequest` must use `Field(...)` (required, no default) with `min_length=1`
- `prompt` field defaults to empty string `""` — `prepare_prompt()` checks truthiness to decide focus vs comprehensive

### Decision Rationale
- **One tool, not three**: Simpler UX, system prompt adapts per media type, follows PAL verb convention
- **BALANCED not EXTENDED_REASONING**: Perception is understanding, not deep reasoning. Pro models still available via explicit `model` parameter.
- **media required, prompt optional**: Nothing to perceive without media. Exhaustive by default, focused when prompted.

### What NOT to Do
- Do NOT create a new `ToolModelCategory` — use `BALANCED`
- Do NOT import `COMMON_FIELD_DESCRIPTIONS` unless actually using it (imagegen imports it but perceive defines its own `PERCEIVE_FIELD_DESCRIPTIONS`)
- Do NOT add `get_default_temperature()` override — base default of 0.5 is fine for analytical work
- Do NOT add a PROMPT_TEMPLATES entry in server.py — imagegen doesn't have one either

### Start Now
Execute Batch 1: Write `systemprompts/perceive_prompt.py`, then edit `systemprompts/__init__.py`.

## Verification

```bash
# Unit tests
uv run --with pytest --with pytest-asyncio pytest tests/test_perceive.py -v --tb=short

# Full regression
uv run --with pytest --with pytest-asyncio pytest tests/ -m "not integration" --tb=short -q

# Live tests (after server restart)
mcp__pal__perceive media=["/path/to/screenshot.png"]
mcp__pal__perceive media=["/path/to/demo.mp4"] prompt="Extract all code shown on screen"
mcp__pal__perceive media=["/path/to/meeting.mp3"] prompt="Transcribe with speaker attribution"
```
