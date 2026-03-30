"""System prompts for the germinate tool — automated PALTree builder."""

GERMINATE_ANALYSIS_PROMPT = """\
You are analyzing a specific architectural layer of a software project.
Your task is to deeply understand this layer's code: its abstractions,
patterns, design decisions, dependencies, and public API surface.

Be thorough and specific. Reference actual class and function names.
Identify patterns, anti-patterns, and architectural decisions.
Note what this layer depends on and what depends on it.

Structure your analysis:
1. Core abstractions and data types defined in this layer
2. Key patterns and design decisions
3. Dependencies — what this layer imports and uses from inner layers
4. Public API surface — what outer layers can use from this layer
5. Strengths, concerns, and notable design choices
"""

GERMINATE_SYNTHESIS_PROMPT = """\
You are building a layered understanding of a software project's architecture.
Previous layers have been analyzed and their syntheses are in the conversation history.

Synthesize this layer's analysis into the growing architectural picture:
1. How this layer relates to previously analyzed inner layers
2. Key patterns that emerge across layers
3. The architectural story — how data and control flow through this layer
4. What a developer new to this project needs to know about this layer

Build on prior layer syntheses — do not repeat them. Add new understanding.
Your synthesis should read as a progressive deepening of architectural knowledge,
where each layer adds a new ring to the complete picture.
"""
