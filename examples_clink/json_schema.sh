#!/usr/bin/env bash
# JSON schema examples

# Inline schema
uv run clink_invoke.py claude-zai \
  "Extract the person's name and age from this text: 'John Smith is 25 years old and works as a developer.'" \
  --json-schema '{"type":"object","properties":{"name":{"type":"string"},"age":{"type":"integer"},"occupation":{"type":"string"}},"required":["name","age"]}'

# Schema from file
uv run clink_invoke.py claude-zai \
  "Parse this data" \
  --json-schema schemas_clink/user_info.json \
  --files README.md

# Disable validation (useful when output might not match exactly)
uv run clink_invoke.py claude-zai \
  "Generate a user profile" \
  --json-schema schemas_clink/user_info.json \
  --validate-schema false

# Verbose mode with schema
uv run clink_invoke.py claude-zai \
  "Extract information" \
  --json-schema schemas_clink/user_info.json \
  --verbose
