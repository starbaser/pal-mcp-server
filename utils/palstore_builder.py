"""
Context reconstruction utilities for tree-based context stores.

Builds conversation history from store node ancestry chains for model consumption,
and provides a bridge to hydrate ephemeral ThreadContext objects from store trees
so non-context tools (thinkdeep, analyze, chat, etc.) can continue from a store node.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from utils.palstore import PalNode, PalRoot
    from utils.conversation_memory import ThreadContext

logger = logging.getLogger(__name__)


def build_context_from_ancestry(ancestors: list[PalNode], include_files: bool = True) -> str:
    """Build formatted conversation history from ancestor chain.

    Each ancestor node's prompt+response becomes a user/assistant turn pair in the history.
    Fork nodes (entry_type="fork") are skipped since they have no prompt/response.

    Returns empty string if no ancestors have prompt+response content.
    """
    content_nodes = [n for n in ancestors if getattr(n, "entry_type", None) != "fork" and n.prompt and n.response]

    if not content_nodes:
        return ""

    parts = ["=== CONVERSATION HISTORY ===", ""]

    turn_num = 0
    for node in content_nodes:
        turn_num += 1
        # Extract the full prompt (with file blobs) from content when available.
        # content format: "{full_prompt}\n\n---\n\n{response}"
        content = getattr(node, "content", "")
        if content:
            user_turn = content.split("\n\n---\n\n", 1)[0]
        else:
            user_turn = node.prompt
        parts.append(f"--- Turn {turn_num} (user) ---")
        parts.append(user_turn)
        parts.append("")
        parts.append(f"--- Turn {turn_num} (assistant) ---")
        parts.append(node.response)
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


def hydrate_thread_context(store: PalRoot, node_path: str) -> ThreadContext:
    """Create an ephemeral ThreadContext from store tree for non-ctx tool bridge.

    Walks the ancestry from root to the target node, creates ConversationTurn objects
    from each ancestor's prompt+response, and stores the resulting thread in
    conversation_memory so reconstruct_thread_context can find it.

    Fork nodes (entry_type="fork") are skipped — they carry no prompt/response content.

    The hydrated thread is ephemeral; the store tree remains the source of truth.
    """
    from utils.palstore import walk_palnode_ancestry
    from utils.conversation_memory import add_turn, create_thread, get_thread

    ancestors = walk_palnode_ancestry(store, node_path)

    thread_id = create_thread(tool_name="ctx_hydrated", initial_request={}, model_name=None)

    for ancestor in ancestors:
        if getattr(ancestor, "entry_type", None) == "fork":
            continue

        # Prefer content blob (full API exchange) over separate prompt/response
        content = getattr(ancestor, "content", "")
        if content:
            add_turn(thread_id, "user", content)
        elif ancestor.prompt or ancestor.response:
            files = getattr(ancestor, "files", None) or None
            if ancestor.prompt:
                add_turn(thread_id, "user", ancestor.prompt, files=files)
            if ancestor.response:
                add_turn(thread_id, "assistant", ancestor.response)

    thread = get_thread(thread_id)
    if thread is None:
        raise RuntimeError(f"Failed to retrieve hydrated thread {thread_id} from storage")

    logger.debug(f"[CTX] Hydrated thread {thread_id} from store '{store.store_id}' at path '{node_path}'")

    return thread
