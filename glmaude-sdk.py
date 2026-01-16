#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "anthropic",
# ]
# ///

"""Simple test script using Anthropic SDK with ZAI endpoint."""

import os
from anthropic import Anthropic

# Get API key from environment
api_key = os.environ.get("ZAI_API_KEY")
if not api_key:
    raise RuntimeError("ZAI_API_KEY environment variable not set")

# Initialize client with ZAI endpoint
client = Anthropic(
    api_key=api_key,
    base_url="https://api.z.ai/api/anthropic",
)

# Make a simple request
response = client.messages.create(
    model="sonnet",
    max_tokens=1024,
    messages=[
        {"role": "user", "content": "what's 2+2"}
    ]
)

for block in response.content:
    if block.type == "text":
        print(block.text)
