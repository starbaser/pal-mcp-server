You are the PAL MCP Server developer about to add Nano Banana Pro (Gemini 3 Pro Image) support to the imagegen tool. The foundational imagegen infrastructure just shipped — this session picks up where it left off.

## Current Status

- Branch: `starbased/main`
- Commit: `15fa86e` — feat(pal-mcp): add dedicated imagegen tool with auto-routing and image persistence
- Progress: imagegen v1 complete, Nano Banana Pro support is the next increment
- Episode: 01
- Plan: `.claude/plans/active/01-imagegen-tool-base64-fix.md`

## Completed Work

- ImageGenTool registered in server.py, inherits SimpleTool, auto-routes via `IMAGE_GENERATION` category
- `ToolModelCategory.IMAGE_GENERATION` enum + `get_preferred_model()` routing in `providers/gemini.py` — prefers models with `"image"` or `"imagen"` in the name
- base64 encoding fix in `providers/gemini.py:307` — `Blob.data` (raw bytes) now encoded before `ImageContent`
- System prompt (`systemprompts/imagegen_prompt.py`) for visual prompt enrichment
- Image persistence: `_save_generated_images()` in `SimpleTool` writes to `IMAGE_STORAGE_DIR`
- PAL storage refactored: `PAL_STORAGE_DIR` resolves project `.claude/pal/` first, falls back to `CLAUDE_CONFIG_DIR/pal/`; `CONVERSATION_STORAGE_DIR` and `IMAGE_STORAGE_DIR` are subdirectories
- All 912 unit tests passing (0 failures), including 11 imagegen-specific tests
- Live-tested: `gemini-3.1-flash-image-preview` generates and persists images to `.claude/pal/images/`

## What NOT to Do

- Do NOT re-fetch `capability_map` in the IMAGE_GENERATION elif of `get_preferred_model()` — it's already fetched at line 546
- Do NOT set `response_modalities=["IMAGE"]` without `"TEXT"` — both are needed (provider already handles this)
- Do NOT remove ChatTool's implicit image gen support — chat passthrough stays, imagegen is the intentional path
- Do NOT use `nano` (gpt-5-nano) for image generation — it's text-only, returns a description instead of an image. Only Gemini image models generate actual images currently.

## Decision Rationale

**ToolModelCategory over hardcoded model** (high confidence)
- Chose: IMAGE_GENERATION enum with provider routing
- Rejected: Hardcoded `gemini-3.1-flash-image-preview`
- Why: Future-proofs for multi-provider image gen and Nano Banana Pro addition

**PAL_STORAGE_DIR with project-level resolution** (high confidence)
- Chose: Check `{cwd}/.claude/` first, fall back to `CLAUDE_CONFIG_DIR`
- Rejected: Always `~/.claude/pal/`
- Why: User preference — PAL data belongs in the project directory when `.claude/` exists

**Save at SimpleTool level, not ImageGenTool** (medium confidence)
- Chose: `_save_generated_images()` in `SimpleTool.execute()`
- Why: Any tool (including chat) can generate images — persistence should be universal

## Critical Context

### Nano Banana Model Family (from Perplexity research)

| Model | Base | Strengths |
|-------|------|-----------|
| **Nano Banana Pro** | Gemini 3 Pro | Studio-quality, 4K, inpainting/outpainting, text rendering in 100+ languages, advanced creative controls |
| **Nano Banana 2** | Gemini 3.1 Flash | Fast, optimized for speed — this is what we currently use (`gemini-3.1-flash-image-preview`) |

Both support native image generation through the Gemini API. Both use SynthID watermarking.

### Key Files

| File | Role |
|------|------|
| `conf/gemini_models.json` | Model definitions — add Pro image model here |
| `providers/gemini.py:575` | `get_preferred_model()` IMAGE_GENERATION routing — may need priority update for Pro |
| `providers/gemini.py:218-240` | `_configure_generation_params()` — sets `response_modalities` based on `supports_image_generation` |
| `providers/shared/model_capabilities.py` | `ModelCapabilities` dataclass — `supports_image_generation` flag |
| `tools/imagegen.py` | ImageGenTool — may want Pro-specific features (inpainting, outpainting params) |
| `tools/simple/base.py:612` | `_save_generated_images()` — image persistence |
| `config.py:160-180` | `PAL_STORAGE_DIR`, `IMAGE_STORAGE_DIR` |

### IMAGE_GENERATION Routing Logic (gemini.py)

The current routing in `get_preferred_model()`:
1. Filters `allowed_models` to those with `supports_image_generation` capability
2. Among those, prefers models with `"image"` or `"imagen"` in name (dedicated models)
3. Falls back to any image-capable model

Adding Nano Banana Pro means: the routing should prefer Pro over Flash when both are available, since Pro is higher quality. The `find_best()` function uses intelligence score — Pro will naturally rank higher if scored correctly in `gemini_models.json`.

## Next Steps

1. **Find the Nano Banana Pro model ID** — confirm the exact API model name (likely `gemini-3-pro-image` or similar). Check Google AI Studio or Vertex AI docs.
2. **Add to `conf/gemini_models.json`** — model entry with correct capabilities (`supports_image_generation: true`, appropriate `intelligence_score`, context window, etc.)
3. **Verify routing** — with both Flash Image and Pro Image in the config, confirm `get_preferred_model(IMAGE_GENERATION)` returns the Pro model (higher score should win via `find_best`)
4. **Test with live generation** — generate an image with Pro model, verify quality difference vs Flash
5. **Consider Pro-specific features** — inpainting, outpainting, 4K resolution. These may need new parameters in `ImageGenRequest` or generation config changes in `_configure_generation_params()`

## Verification

```bash
# Unit tests
uv run --with pytest --with pytest-asyncio pytest tests/test_imagegen.py -v --tb=short

# Full regression
uv run --with pytest --with pytest-asyncio pytest tests/ -m "not integration" --tb=short -q

# Live test after adding Pro model
mcp__pal__imagegen with prompt and model auto-routing — should pick Pro over Flash
```

## Start Now

Research the exact Gemini 3 Pro Image model ID from the API. Check `conf/gemini_models.json` for the existing model entry pattern, then add the Pro image model with `supports_image_generation: true` and an intelligence score that ranks it above Flash Image.
