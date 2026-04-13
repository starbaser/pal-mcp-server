# Gemini via ccproxy (cloudcode-pa)

## Problem

PAL's Gemini provider has two paths:

1. **Native SDK** (`google.genai.Client`) — sends Gemini-native `contents` format with `role` fields
2. **Proxy mode** (`openai.OpenAI`) — sends OpenAI `messages` format

Setting `GEMINI_BASE_URL` triggers proxy mode (`_proxy_mode = True`), which uses the OpenAI client. ccproxy's `/gemini/` redirect route preserves the body as-is and forwards to `cloudcode-pa.googleapis.com`. But cloudcode-pa expects Gemini-native format, not OpenAI — so the request fails.

The native SDK path is correct for ccproxy. The `genai.Client` with a custom `base_url` sends native Gemini format (`contents` with `role` fields, `generationConfig`, etc.) and the Google SDK handles role injection, part formatting, and all the Gemini-specific envelope automatically.

## Fix

Decouple `base_url` from proxy mode. When `GEMINI_BASE_URL` is set, the native genai client should use it as `http_options.base_url` — the SDK sends to `{base_url}/v1beta/models/{model}:generateContent` in native format. Reserve proxy mode for an explicit opt-in flag.

### `providers/gemini.py`

```python
@property
def _proxy_mode(self) -> bool:
    """True only when explicitly opted into OpenAI-compatible proxy."""
    return get_env_bool("GEMINI_PROXY_MODE", False)
```

The `client` property already handles `base_url` correctly (lines 70-86) — it passes `base_url` to `types.HttpOptions` and constructs a native `genai.Client`. No change needed there.

### Environment

```
GEMINI_API_KEY=sk-ant-oat-ccproxy-gemini
GEMINI_BASE_URL=http://127.0.0.1:4000/gemini
# GEMINI_PROXY_MODE not set — uses native SDK through ccproxy
```

The genai SDK sends: `POST http://127.0.0.1:4000/gemini/v1beta/models/gemini-3-flash-preview:generateContent`

ccproxy's redirect rule matches `/gemini/`, rewrites to `cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse`, and the compliance profile wraps the body in the cloudcode-pa envelope (`{model, project, request: {<body>}}`).

### Content format

The native genai SDK automatically adds `"role": "user"` to content entries. cloudcode-pa **requires** this field — bare `{"parts": [...]}` without role returns 400 INVALID_ARGUMENT. This is why the native SDK path works and hand-rolled requests without `role` don't.

### ccproxy routing

ccproxy config (already in place):

```yaml
inspector:
  transforms:
    - match_path: /gemini/
      mode: redirect
      dest_provider: gemini
      dest_host: cloudcode-pa.googleapis.com
      dest_path: /v1internal:streamGenerateContent?alt=sse
      dest_api_key_ref: gemini
```

ccproxy compliance profile (learned from Gemini CLI traffic) adds:
- Body wrapper: `{model, project, user_prompt_id, request: {<original body>}}`
- Headers: `x-goog-api-client`, `content-type`, `user-agent`
- OAuth Bearer token from `~/.gemini/oauth_creds.json`

## Verified

```bash
# Direct through ccproxy — 200 OK
curl -X POST "http://127.0.0.1:4001/v1internal:streamGenerateContent?alt=sse" \
  -H "Content-Type: application/json" \
  -H "x-goog-api-key: sk-ant-oat-ccproxy-gemini" \
  -d '{"contents":[{"role":"user","parts":[{"text":"hello"}]}],"model":"gemini-3-flash-preview"}'
```

## Applied

`_proxy_mode` decoupled from `base_url`. The property now reads `GEMINI_PROXY_MODE` env var (default `False`) instead of testing `self._base_url is not None`. The `client` property was already correct — it passes `base_url` through `types.HttpOptions` to the native `genai.Client`, which sends Gemini-native format.

```python
# providers/gemini.py
@property
def _proxy_mode(self) -> bool:
    return get_env_bool("GEMINI_PROXY_MODE", False)
```

1295 unit tests pass, no regressions. The 9 pre-existing failures (`oboros`, `anthropic` missing from venv) are unrelated.
