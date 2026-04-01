"""
Context reconstruction utilities for tree-based PALTrees.

Builds conversation history from node ancestry chains for model consumption.
The primary entry point is build_tree_context(), which constructs enhanced tool
arguments directly from PalNode ancestry — no ThreadContext intermediate needed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from utils.palstore import PalNode, PalRoot

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# build_tree_context — unified context builder for tree-path continuations
# ---------------------------------------------------------------------------


def build_tree_context(
    tree: PalRoot,
    node_path: str,
    arguments: dict,
    model_context=None,
) -> dict:
    """Build enhanced arguments directly from PalNode ancestry.

    Replaces hydrate_thread_context + reconstruct_thread_context for tree-path
    continuations. Walks ancestry, builds token-budgeted conversation history,
    and assembles the enhanced prompt — all without creating a ThreadContext.

    Returns the mutated arguments dict with injected history and metadata.
    Stores a TraversalLog in arguments["_traversal_log"].
    """
    from utils.palstore import collect_traversal, iter_ancestry

    ancestors, tlog = collect_traversal(iter_ancestry(tree, node_path), "ancestry")
    arguments["_traversal_log"] = tlog

    # Filter to content nodes (skip empty nodes)
    content_nodes = [n for n in ancestors if n.input or n.output]

    # Resolve model context if not provided
    if model_context is None:
        model_context = _resolve_model_context(arguments)

    # Build token-budgeted conversation history
    history_text, history_tokens = _build_budgeted_history(
        content_nodes,
        node_path,
        model_context,
    )

    # Assemble the enhanced prompt
    original_prompt = arguments.get("prompt", "")
    from server import get_follow_up_instructions

    follow_up = get_follow_up_instructions()
    if history_text:
        enhanced_prompt = f"{history_text}\n\n=== NEW USER INPUT ===\n{original_prompt}\n\n{follow_up}"
    else:
        enhanced_prompt = f"{original_prompt}\n\n{follow_up}"

    arguments["prompt"] = enhanced_prompt
    arguments["_original_user_prompt"] = original_prompt

    # Token budget metadata
    if model_context:
        token_allocation = model_context.calculate_token_allocation()
        remaining = max(0, token_allocation.content_tokens - history_tokens)
        arguments["_remaining_tokens"] = remaining
        arguments["_model_context"] = model_context
        arguments["_context_window"] = token_allocation.total_tokens
        arguments["_context_used"] = history_tokens

    # Model continuity: find the most recent model used in ancestry
    for node in reversed(content_nodes):
        if node.model:
            arguments.setdefault("model", node.model)
            break

    # Workflow metadata: extract from most recent node with metadata
    for node in reversed(ancestors):
        if node.metadata:
            arguments["_tree_workflow_metadata"] = node.metadata
            break

    arguments["_tree_context_built"] = True
    return arguments


def _resolve_model_context(arguments: dict):
    """Resolve a ModelContext from arguments or defaults."""
    mc = arguments.get("_model_context")
    if mc:
        return mc
    try:
        from providers.registry import ModelProviderRegistry

        registry = ModelProviderRegistry()
        model_name = arguments.get("_resolved_model_name") or arguments.get("model")
        if model_name:
            return registry.get_model_context(model_name)
        fallback = registry.get_preferred_fallback_model("extended_reasoning")
        if fallback:
            return registry.get_model_context(fallback)
    except Exception:
        pass
    return None


def _build_budgeted_history(
    content_nodes: list[PalNode],
    node_path: str,
    model_context=None,
) -> tuple[str, int]:
    """Build token-budgeted conversation history from PalNode ancestry.

    Follows the same Phase 1 (reverse-chrono collection) → Phase 2 (chrono presentation)
    pattern as build_conversation_history in conversation_memory.py.

    Returns (history_string, tokens_used).
    """
    from utils.token_utils import count_tokens

    if not content_nodes:
        return "", 0

    # Token budget
    if model_context:
        token_allocation = model_context.calculate_token_allocation()
        max_history_tokens = token_allocation.history_tokens
    else:
        max_history_tokens = 200_000  # conservative fallback

    # Collect files (newest-first, deduplicated)
    seen_files: set[str] = set()
    file_list: list[str] = []
    for node in reversed(content_nodes):
        for f in node.files or []:
            if f not in seen_files:
                seen_files.add(f)
                file_list.append(f)

    # Build header
    tool_name = None
    for node in reversed(content_nodes):
        if node.tool_name:
            tool_name = node.tool_name
            break

    header_parts = [
        "=== CONVERSATION HISTORY (CONTINUATION) ===",
        f"Tree: {node_path}",
    ]
    if tool_name:
        header_parts.append(f"Tool: {tool_name}")
    header_parts.extend(
        [
            f"Turn {len(content_nodes)}",
            "You are continuing this conversation thread from where it left off.",
        ]
    )

    # File listing section (paths only — content is already embedded in turn input)
    if file_list:
        header_parts.extend(
            [
                "",
                "=== FILES REFERENCED IN THIS CONVERSATION ===",
                "The following files have been referenced during this conversation:",
            ]
        )
        for f in file_list:
            header_parts.append(f"  {f}")
        header_parts.extend(
            [
                "=== END REFERENCED FILES ===",
            ]
        )

    header_parts.extend(["", "Previous conversation turns:"])
    header_text = "\n".join(header_parts)
    header_tokens = count_tokens(header_text)

    # Phase 1: Collect turns reverse-chronologically within token budget
    total_turn_tokens = 0
    turn_entries: list[tuple[int, str]] = []

    for idx in range(len(content_nodes) - 1, -1, -1):
        node = content_nodes[idx]
        turn_num = idx + 1

        # Build turn text
        turn_parts = []

        # User turn
        role_label = "Agent"
        user_header = f"\n--- Turn {turn_num} ({role_label}"
        if node.tool_name:
            user_header += f" using {node.tool_name}"
        if node.model:
            user_header += f" via {node.model}"
        user_header += ") ---"
        turn_parts.append(user_header)

        if node.files:
            turn_parts.append(f"Files used in this turn: {', '.join(node.files)}")
        turn_parts.append("")
        turn_parts.append(node.input)

        # Assistant turn
        if node.output:
            assistant_label = node.model or "Assistant"
            assistant_header = f"\n--- Turn {turn_num} ({assistant_label}"
            if node.tool_name:
                assistant_header += f" using {node.tool_name}"
            assistant_header += ") ---"
            turn_parts.append(assistant_header)
            turn_parts.append(node.output)

        turn_text = "\n".join(turn_parts)
        turn_tokens = count_tokens(turn_text)

        if header_tokens + total_turn_tokens + turn_tokens > max_history_tokens:
            break

        turn_entries.append((idx, turn_text))
        total_turn_tokens += turn_tokens

    # Phase 2: Reverse back to chronological
    turn_entries.reverse()

    # Assemble final history
    history_parts = [header_text]
    for _, turn_text in turn_entries:
        history_parts.append(turn_text)

    included = len(turn_entries)
    total = len(content_nodes)
    if included < total:
        history_parts.append(f"\n[Note: Showing {included} most recent turns out of {total} total]")

    history_parts.extend(
        [
            "",
            "=== END CONVERSATION HISTORY ===",
            "",
            "IMPORTANT: You are continuing an existing conversation thread. Build upon the previous exchanges shown above,",
            "reference earlier points, and maintain consistency with what has been discussed.",
            "",
            "DO NOT repeat or summarize previous analysis, findings, or instructions that are already covered in the",
            "conversation history. Instead, provide only new insights, additional analysis, or direct answers to",
            "the follow-up question / concerns / insights. Assume the user has read the prior conversation.",
            "",
            f"This is turn {total + 1} of the conversation - use the conversation history above to provide a coherent continuation.",
        ]
    )

    result = "\n".join(history_parts)
    total_tokens = header_tokens + total_turn_tokens
    return result, total_tokens


def build_context_from_ancestry(ancestors: list[PalNode], include_files: bool = True) -> str:
    """Build formatted conversation history from ancestor chain.

    Each ancestor node's input+output becomes a user/assistant turn pair in the history.
    Nodes without both input and output are skipped.

    Returns empty string if no ancestors have input+output content.
    """
    content_nodes = [n for n in ancestors if n.input and n.output]

    if not content_nodes:
        return ""

    parts = ["=== CONVERSATION HISTORY ===", ""]

    turn_num = 0
    for node in content_nodes:
        turn_num += 1
        parts.append(f"--- Turn {turn_num} (user) ---")
        parts.append(node.input)
        parts.append("")
        parts.append(f"--- Turn {turn_num} (assistant) ---")
        parts.append(node.output)
        parts.append("")

    if include_files:
        file_listing: list[str] = []
        for node in content_nodes:
            files = getattr(node, "files", None) or []
            for f in files:
                if f not in file_listing:
                    file_listing.append(f)

        if file_listing:
            parts.append("=== FILES REFERENCED IN THIS CONVERSATION ===")
            for f in file_listing:
                parts.append(f"  {f}")
            parts.append("")

    parts.append("=== END CONVERSATION HISTORY ===")

    return "\n".join(parts)


def hydrate_thread_context(store: PalRoot, node_path: str):
    """DEPRECATED: Use build_tree_context() instead.

    Creates an ephemeral ThreadContext from a PALTree. Kept for backward compatibility
    with legacy UUID-based continuation paths.
    """
    from utils.conversation_memory import add_turn, create_thread, get_thread
    from utils.palstore import walk_palnode_ancestry

    ancestors = walk_palnode_ancestry(store, node_path)

    thread_id = create_thread(tool_name="ctx_hydrated", initial_request={}, model_name=None)

    for ancestor in ancestors:
        files = getattr(ancestor, "files", None) or None
        if ancestor.input:
            add_turn(thread_id, "user", ancestor.input, files=files)
        if ancestor.output:
            add_turn(thread_id, "assistant", ancestor.output)

    thread = get_thread(thread_id)
    if thread is None:
        raise RuntimeError(f"Failed to retrieve hydrated thread {thread_id} from storage")

    logger.debug(f"[CTX] Hydrated thread {thread_id} from PALTree '{store.tree_path}' at path '{node_path}'")

    return thread
