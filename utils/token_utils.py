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
    """Count tokens using the cl100k_base tiktoken encoding.

    For texts over 100K chars, falls back to a character heuristic
    since tiktoken can be slow on certain inputs at scale.
    """
    if not text:
        return 0
    if len(text) > 100_000:
        return len(text) // 4
    return len(_get_encoding().encode(text))


# Default fallback for token limit (conservative estimate)
DEFAULT_CONTEXT_WINDOW = 200_000  # Conservative fallback for unknown models


def estimate_tokens(text: str) -> int:
    """Count tokens using the cl100k_base tiktoken encoding.

    Args:
        text: The text to count tokens for

    Returns:
        int: Token count
    """
    return count_tokens(text)


def check_token_limit(text: str, context_window: int = DEFAULT_CONTEXT_WINDOW) -> tuple[bool, int]:
    """
    Check if text exceeds the specified token limit.

    This function is used to validate that prepared prompts will fit
    within the model's context window, preventing API errors and ensuring
    reliable operation.

    Args:
        text: The text to check
        context_window: The model's context window size (defaults to conservative fallback)

    Returns:
        Tuple[bool, int]: (is_within_limit, estimated_tokens)
        - is_within_limit: True if the text fits within context_window
        - estimated_tokens: The estimated token count
    """
    estimated = estimate_tokens(text)
    return estimated <= context_window, estimated
