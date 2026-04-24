"""
Token counting utilities for managing API context limits.
"""

from __future__ import annotations

import functools


@functools.lru_cache(maxsize=1)
def _get_encoding():
    import tiktoken

    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Count tokens using the cl100k_base tiktoken encoding."""
    if not text:
        return 0
    return len(_get_encoding().encode(text))


# Keep as an alias — many callsites import this name
estimate_tokens = count_tokens

# Default fallback for token limit (conservative estimate)
DEFAULT_CONTEXT_WINDOW = 200_000  # Conservative fallback for unknown models


def check_token_limit(text: str, context_window: int = DEFAULT_CONTEXT_WINDOW) -> tuple[bool, int]:
    """
    Check if text exceeds the specified token limit.

    Args:
        text: The text to check
        context_window: The model's context window size (defaults to conservative fallback)

    Returns:
        Tuple[bool, int]: (is_within_limit, estimated_tokens)
        - is_within_limit: True if the text fits within context_window
        - estimated_tokens: The estimated token count
    """
    estimated = count_tokens(text)
    return estimated <= context_window, estimated
