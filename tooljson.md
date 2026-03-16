# PAL MCP Tool JSON Schemas

All PAL tools excluding `clink`. Each section contains the full JSON schema as returned by ToolSearch.

---

## analyze

**Description:** Performs comprehensive code analysis with systematic investigation and expert validation. Use for architecture, performance, maintainability, and pattern analysis. Guides through structured code review and strategic planning.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "AnalyzeRequest",
  "type": "object",
  "additionalProperties": false,
  "required": ["step", "step_number", "total_steps", "next_step_required", "findings"],
  "properties": {
    "analysis_type": {
      "default": "general",
      "description": "Type of analysis to perform (architecture, performance, security, quality, general)",
      "enum": ["architecture", "performance", "security", "quality", "general"],
      "type": "string"
    },
    "confidence": {
      "description": "Your confidence in the analysis: exploring, low, medium, high, very_high, almost_certain, or certain. 'certain' indicates the analysis is complete and ready for validation.",
      "enum": ["exploring", "low", "medium", "high", "very_high", "almost_certain", "certain"],
      "type": "string"
    },
    "continuation_id": {
      "description": "Unique thread continuation ID for multi-turn conversations. Works across different tools. ALWAYS reuse the last continuation_id you were given—this preserves full conversation context, files, and findings so the agent can resume seamlessly.",
      "type": "string"
    },
    "files_checked": {
      "description": "List all files examined (absolute paths). Include even ruled-out files to track exploration path.",
      "items": { "type": "string" },
      "type": "array"
    },
    "findings": {
      "description": "Summary of discoveries from this step, including architectural patterns, tech stack assessment, scalability characteristics, performance implications, maintainability factors, and strategic improvement opportunities. IMPORTANT: Document both strengths (good patterns, solid architecture) and concerns (tech debt, overengineering, unnecessary complexity). In later steps, confirm or update past findings with additional evidence.",
      "type": "string"
    },
    "issues_found": {
      "description": "Issues or concerns identified during analysis, each with severity level (critical, high, medium, low)",
      "items": { "type": "object" },
      "type": "array"
    },
    "media": {
      "description": "Optional absolute paths to architecture diagrams or visual references that help with analysis context.",
      "items": { "type": "string" },
      "type": "array"
    },
    "model": {
      "description": "The default model is 'gemini-3.1-pro-preview'. Override only when the user explicitly requests a different model, and use that exact name. If the requested model fails validation, surface the server error instead of substituting another model. When unsure, use the `listmodels` tool for details. Preferred alternatives: gpt-5.2 (score 100, 400K ctx, thinking, code-gen); gpt-5.1-codex (score 100, 400K ctx, thinking, code-gen); gemini-2.5-pro (score 100, 1.0M ctx, thinking, code-gen); gemini-3.1-pro-preview (score 100, 1.0M ctx, thinking, code-gen); gpt-5.2-pro (score 100, 400K ctx, thinking, code-gen); +29 more via `listmodels`.",
      "type": "string"
    },
    "next_step_required": {
      "description": "Set to true if you plan to continue the investigation with another step. False means you believe the analysis is complete and ready for expert validation.",
      "type": "boolean"
    },
    "output_format": {
      "default": "detailed",
      "description": "How to format the output (summary, detailed, actionable)",
      "enum": ["summary", "detailed", "actionable"],
      "type": "string"
    },
    "relevant_context": {
      "description": "Methods/functions identified as involved in the issue",
      "items": { "type": "string" },
      "type": "array"
    },
    "relevant_files": {
      "description": "Subset of files_checked directly relevant to analysis findings (absolute paths). Include files with significant patterns, architectural decisions, or strategic improvement opportunities.",
      "items": { "type": "string" },
      "type": "array"
    },
    "step": {
      "description": "The analysis plan. Step 1: State your strategy, including how you will map the codebase structure, understand business logic, and assess code quality, performance implications, and architectural patterns. Later steps: Report findings and adapt the approach as new insights emerge.",
      "type": "string"
    },
    "step_number": {
      "description": "The index of the current step in the analysis sequence, beginning at 1. Each step should build upon or revise the previous one.",
      "minimum": 1,
      "type": "integer"
    },
    "temperature": {
      "description": "0 = deterministic · 1 = creative.",
      "maximum": 1,
      "minimum": 0,
      "type": "number"
    },
    "thinking_mode": {
      "description": "Reasoning depth: minimal, low, medium, high, or max.",
      "enum": ["minimal", "low", "medium", "high", "max"],
      "type": "string"
    },
    "total_steps": {
      "description": "Your current estimate for how many steps will be needed to complete the analysis. Adjust as new findings emerge.",
      "minimum": 1,
      "type": "integer"
    },
    "use_assistant_model": {
      "default": true,
      "description": "Use assistant model for expert analysis after workflow steps. False skips expert analysis, relies solely on your personal investigation. Defaults to True for comprehensive validation.",
      "type": "boolean"
    }
  }
}
```

---

## apilookup

**Description:** Use this tool automatically when you need current API/SDK documentation, latest version info, breaking changes, deprecations, migration guides, or official release notes. This tool searches authoritative sources (official docs, GitHub, package registries) to ensure up-to-date accuracy.

```json
{
  "type": "object",
  "required": ["prompt"],
  "properties": {
    "prompt": {
      "description": "The API, SDK, library, framework, or technology you need current documentation, version info, breaking changes, or migration guidance for.",
      "type": "string"
    }
  }
}
```

---

## challenge

**Description:** Prevents reflexive agreement by forcing critical thinking and reasoned analysis when a statement is challenged. Trigger automatically when a user critically questions, disagrees or appears to push back on earlier answers, and use it manually to sanity-check contentious claims.

```json
{
  "type": "object",
  "required": ["prompt"],
  "properties": {
    "prompt": {
      "description": "Statement to scrutinize. If you invoke `challenge` manually, strip the word 'challenge' and pass just the statement. Automatic invocations send the full user message as-is; do not modify it.",
      "type": "string"
    }
  }
}
```

---

## chat

**Description:** General chat and collaborative thinking partner for brainstorming, development discussion, getting second opinions, and exploring ideas. Use for ideas, validations, questions, and thoughtful explanations.

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["prompt", "working_directory_absolute_path"],
  "properties": {
    "absolute_file_paths": {
      "description": "Full, absolute file paths to relevant code in order to share with external model",
      "items": { "type": "string" },
      "type": "array"
    },
    "continuation_id": {
      "description": "Unique thread continuation ID for multi-turn conversations. Works across different tools. ALWAYS reuse the last continuation_id you were given—this preserves full conversation context, files, and findings so the agent can resume seamlessly.",
      "type": "string"
    },
    "media": {
      "description": "Image paths (absolute) or base64 strings for optional visual context.",
      "items": { "type": "string" },
      "type": "array"
    },
    "model": {
      "description": "The default model is 'gemini-3.1-pro-preview'. Override only when the user explicitly requests a different model, and use that exact name. If the requested model fails validation, surface the server error instead of substituting another model. When unsure, use the `listmodels` tool for details. Preferred alternatives: gpt-5.2 (score 100, 400K ctx, thinking, code-gen); gpt-5.1-codex (score 100, 400K ctx, thinking, code-gen); gemini-2.5-pro (score 100, 1.0M ctx, thinking, code-gen); gemini-3.1-pro-preview (score 100, 1.0M ctx, thinking, code-gen); gpt-5.2-pro (score 100, 400K ctx, thinking, code-gen); +29 more via `listmodels`.",
      "type": "string"
    },
    "prompt": {
      "description": "Your question or idea for collaborative thinking to be sent to the external model. Provide detailed context, including your goal, what you've tried, and any specific challenges. WARNING: Large inline code must NOT be shared in prompt. Provide full-path to files on disk as separate parameter.",
      "type": "string"
    },
    "temperature": {
      "description": "0 = deterministic · 1 = creative.",
      "maximum": 1,
      "minimum": 0,
      "type": "number"
    },
    "thinking_mode": {
      "description": "Reasoning depth: minimal, low, medium, high, or max.",
      "enum": ["minimal", "low", "medium", "high", "max"],
      "type": "string"
    },
    "working_directory_absolute_path": {
      "description": "Absolute path to an existing directory where generated code artifacts can be saved.",
      "type": "string"
    }
  }
}
```

---

## codereview

**Description:** Performs systematic, step-by-step code review with expert validation. Use for comprehensive analysis covering quality, security, performance, and architecture. Guides through structured investigation to ensure thoroughness.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "CodereviewRequest",
  "type": "object",
  "additionalProperties": false,
  "required": ["step", "step_number", "total_steps", "next_step_required", "findings"],
  "properties": {
    "confidence": {
      "description": "Confidence level: exploring (just starting), low (early investigation), medium (some evidence), high (strong evidence), very_high (comprehensive understanding), almost_certain (near complete confidence), certain (100% confidence locally - no external validation needed)",
      "enum": ["exploring", "low", "medium", "high", "very_high", "almost_certain", "certain"],
      "type": "string"
    },
    "continuation_id": {
      "description": "Unique thread continuation ID for multi-turn conversations. Works across different tools. ALWAYS reuse the last continuation_id you were given—this preserves full conversation context, files, and findings so the agent can resume seamlessly.",
      "type": "string"
    },
    "files_checked": {
      "description": "Absolute paths of every file reviewed, including those ruled out.",
      "items": { "type": "string" },
      "type": "array"
    },
    "findings": {
      "description": "Capture findings (positive and negative) across quality, security, performance, and architecture; update each step.",
      "type": "string"
    },
    "focus_on": {
      "description": "Optional note on areas to emphasise (e.g. 'threading', 'auth flow').",
      "type": "string"
    },
    "hypothesis": {
      "description": "Current theory about issue/goal based on work",
      "type": "string"
    },
    "issues_found": {
      "description": "Issues with severity (critical/high/medium/low) and descriptions.",
      "items": { "type": "object" },
      "type": "array"
    },
    "media": {
      "description": "Optional diagram or screenshot paths that clarify review context.",
      "items": { "type": "string" },
      "type": "array"
    },
    "model": {
      "description": "The default model is 'gemini-3.1-pro-preview'. Override only when the user explicitly requests a different model, and use that exact name. If the requested model fails validation, surface the server error instead of substituting another model. When unsure, use the `listmodels` tool for details. Preferred alternatives: gpt-5.2 (score 100, 400K ctx, thinking, code-gen); gpt-5.1-codex (score 100, 400K ctx, thinking, code-gen); gemini-2.5-pro (score 100, 1.0M ctx, thinking, code-gen); gemini-3.1-pro-preview (score 100, 1.0M ctx, thinking, code-gen); gpt-5.2-pro (score 100, 400K ctx, thinking, code-gen); +29 more via `listmodels`.",
      "type": "string"
    },
    "next_step_required": {
      "description": "True when another review step follows. External validation: step 1 → True, step 2 → False. Internal validation: set False immediately. Apply the same rule on continuation flows.",
      "type": "boolean"
    },
    "relevant_context": {
      "description": "Methods/functions identified as involved in the issue",
      "items": { "type": "string" },
      "type": "array"
    },
    "relevant_files": {
      "description": "Step 1: list all files/dirs under review. Must be absolute full non-abbreviated paths. Final step: narrow to files tied to key findings.",
      "items": { "type": "string" },
      "type": "array"
    },
    "review_type": {
      "default": "full",
      "description": "Review focus: full, security, performance, or quick.",
      "enum": ["full", "security", "performance", "quick"],
      "type": "string"
    },
    "review_validation_type": {
      "default": "external",
      "description": "Set 'external' (default) for expert follow-up or 'internal' for local-only review.",
      "enum": ["external", "internal"],
      "type": "string"
    },
    "severity_filter": {
      "default": "all",
      "description": "Lowest severity to include when reporting issues (critical/high/medium/low/all).",
      "enum": ["critical", "high", "medium", "low", "all"],
      "type": "string"
    },
    "standards": {
      "description": "Coding standards or style guides to enforce.",
      "type": "string"
    },
    "step": {
      "description": "Review narrative. Step 1: outline the review strategy. Later steps: report findings. MUST cover quality, security, performance, and architecture. Reference code via `relevant_files`; avoid dumping large snippets.",
      "type": "string"
    },
    "step_number": {
      "description": "Current review step (starts at 1) – each step should build on the last.",
      "minimum": 1,
      "type": "integer"
    },
    "temperature": {
      "description": "0 = deterministic · 1 = creative.",
      "maximum": 1,
      "minimum": 0,
      "type": "number"
    },
    "thinking_mode": {
      "description": "Reasoning depth: minimal, low, medium, high, or max.",
      "enum": ["minimal", "low", "medium", "high", "max"],
      "type": "string"
    },
    "total_steps": {
      "description": "Number of review steps planned. External validation: two steps (analysis + summary). Internal validation: one step. Use the same limits when continuing an existing review via continuation_id.",
      "minimum": 1,
      "type": "integer"
    },
    "use_assistant_model": {
      "default": true,
      "description": "Use assistant model for expert analysis after workflow steps. False skips expert analysis, relies solely on your personal investigation. Defaults to True for comprehensive validation.",
      "type": "boolean"
    }
  }
}
```

---

## consensus

**Description:** Builds multi-model consensus through systematic analysis and structured debate. Use for complex decisions, architectural choices, feature proposals, and technology evaluations. Consults multiple models with different stances to synthesize comprehensive recommendations.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "ConsensusRequest",
  "type": "object",
  "additionalProperties": false,
  "required": ["step", "step_number", "total_steps", "next_step_required", "findings"],
  "properties": {
    "continuation_id": {
      "description": "Unique thread continuation ID for multi-turn conversations. Works across different tools. ALWAYS reuse the last continuation_id you were given—this preserves full conversation context, files, and findings so the agent can resume seamlessly.",
      "type": "string"
    },
    "current_model_index": {
      "description": "0-based index of the next model to consult (managed internally).",
      "minimum": 0,
      "type": "integer"
    },
    "findings": {
      "description": "Step 1: your independent analysis for later synthesis (not shared with other models). Steps 2+: summarize the newest model response.",
      "type": "string"
    },
    "media": {
      "description": "Optional absolute image paths or base64 references that add helpful visual context.",
      "items": { "type": "string" },
      "type": "array"
    },
    "model_responses": {
      "description": "Internal log of responses gathered so far.",
      "items": { "type": "object" },
      "type": "array"
    },
    "models": {
      "description": "User-specified roster of models to consult (provide at least two entries). Each entry may include model, stance (for/against/neutral), and stance_prompt. Each (model, stance) pair must be unique.",
      "items": {
        "properties": {
          "model": { "type": "string" },
          "stance": {
            "default": "neutral",
            "enum": ["for", "against", "neutral"],
            "type": "string"
          },
          "stance_prompt": { "type": "string" }
        },
        "required": ["model"],
        "type": "object"
      },
      "minItems": 2,
      "type": "array"
    },
    "next_step_required": {
      "description": "True if more model consultations remain; set false when ready to synthesize.",
      "type": "boolean"
    },
    "relevant_files": {
      "description": "Optional supporting files that help the consensus analysis. Must be absolute full, non-abbreviated paths.",
      "items": { "type": "string" },
      "type": "array"
    },
    "step": {
      "description": "Consensus prompt. Step 1: write the exact proposal/question every model will see (use 'Evaluate...', not meta commentary). Steps 2+: capture internal notes about the latest model response—these notes are NOT sent to other models.",
      "type": "string"
    },
    "step_number": {
      "description": "Current step index (starts at 1). Step 1 is your analysis; steps 2+ handle each model response.",
      "minimum": 1,
      "type": "integer"
    },
    "total_steps": {
      "description": "Total steps = number of models consulted plus the final synthesis step.",
      "minimum": 1,
      "type": "integer"
    },
    "use_assistant_model": {
      "default": true,
      "description": "Use assistant model for expert analysis after workflow steps. False skips expert analysis, relies solely on your personal investigation. Defaults to True for comprehensive validation.",
      "type": "boolean"
    }
  }
}
```

---

## debug

**Description:** Performs systematic debugging and root cause analysis for any type of issue. Use for complex bugs, mysterious errors, performance issues, race conditions, memory leaks, and integration problems. Guides through structured investigation with hypothesis testing and expert analysis.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "DebugRequest",
  "type": "object",
  "additionalProperties": false,
  "required": ["step", "step_number", "total_steps", "next_step_required", "findings"],
  "properties": {
    "confidence": {
      "description": "Your confidence in the hypothesis: exploring (starting out), low (early idea), medium (some evidence), high (strong evidence), very_high (very strong evidence), almost_certain (nearly confirmed), certain (100% confidence - root cause and fix are both confirmed locally with no need for external validation). WARNING: Do NOT use 'certain' unless the issue can be fully resolved with a fix, use 'very_high' or 'almost_certain' instead when not 100% sure. Using 'certain' means you have ABSOLUTE confidence locally and PREVENTS external model validation.",
      "enum": ["exploring", "low", "medium", "high", "very_high", "almost_certain", "certain"],
      "type": "string"
    },
    "continuation_id": {
      "description": "Unique thread continuation ID for multi-turn conversations. Works across different tools. ALWAYS reuse the last continuation_id you were given—this preserves full conversation context, files, and findings so the agent can resume seamlessly.",
      "type": "string"
    },
    "files_checked": {
      "description": "All examined files (absolute paths), including ruled-out ones.",
      "items": { "type": "string" },
      "type": "array"
    },
    "findings": {
      "description": "Discoveries: clues, code/log evidence, disproven theories. Be specific. If no bug found, document clearly as valid.",
      "type": "string"
    },
    "hypothesis": {
      "description": "Concrete root cause theory from evidence. Can revise. Valid: 'No bug found - user misunderstanding' or 'Symptoms unrelated to code' if supported.",
      "type": "string"
    },
    "issues_found": {
      "description": "Issues identified with severity levels during work",
      "items": { "type": "object" },
      "type": "array"
    },
    "media": {
      "description": "Optional screenshots/visuals clarifying issue (absolute paths).",
      "items": { "type": "string" },
      "type": "array"
    },
    "model": {
      "description": "The default model is 'gemini-3.1-pro-preview'. Override only when the user explicitly requests a different model, and use that exact name. If the requested model fails validation, surface the server error instead of substituting another model. When unsure, use the `listmodels` tool for details. Preferred alternatives: gpt-5.2 (score 100, 400K ctx, thinking, code-gen); gpt-5.1-codex (score 100, 400K ctx, thinking, code-gen); gemini-2.5-pro (score 100, 1.0M ctx, thinking, code-gen); gemini-3.1-pro-preview (score 100, 1.0M ctx, thinking, code-gen); gpt-5.2-pro (score 100, 400K ctx, thinking, code-gen); +29 more via `listmodels`.",
      "type": "string"
    },
    "next_step_required": {
      "description": "True if you plan to continue the investigation with another step. False means root cause is known or investigation is complete. IMPORTANT: When continuation_id is provided (continuing a previous conversation), set this to False to immediately proceed with expert analysis.",
      "type": "boolean"
    },
    "relevant_context": {
      "description": "Methods/functions identified as involved in the issue",
      "items": { "type": "string" },
      "type": "array"
    },
    "relevant_files": {
      "description": "Files directly relevant to issue (absolute paths). Cause, trigger, or manifestation locations.",
      "items": { "type": "string" },
      "type": "array"
    },
    "step": {
      "description": "Investigation step. Step 1: State issue+direction. Symptoms misleading; 'no bug' valid. Trace dependencies, verify hypotheses. Use relevant_files for code; this for text only.",
      "type": "string"
    },
    "step_number": {
      "description": "Current step index (starts at 1). Build upon previous steps.",
      "minimum": 1,
      "type": "integer"
    },
    "temperature": {
      "description": "0 = deterministic · 1 = creative.",
      "maximum": 1,
      "minimum": 0,
      "type": "number"
    },
    "thinking_mode": {
      "description": "Reasoning depth: minimal, low, medium, high, or max.",
      "enum": ["minimal", "low", "medium", "high", "max"],
      "type": "string"
    },
    "total_steps": {
      "description": "Estimated total steps needed to complete the investigation. Adjust as new findings emerge. IMPORTANT: When continuation_id is provided (continuing a previous conversation), set this to 1 as we're not starting a new multi-step investigation.",
      "minimum": 1,
      "type": "integer"
    },
    "use_assistant_model": {
      "default": true,
      "description": "Use assistant model for expert analysis after workflow steps. False skips expert analysis, relies solely on your personal investigation. Defaults to True for comprehensive validation.",
      "type": "boolean"
    }
  }
}
```

---

## docgen

**Description:** Generates comprehensive code documentation with systematic analysis of functions, classes, and complexity. Use for documentation generation, code analysis, complexity assessment, and API documentation. Analyzes code structure and patterns to create thorough documentation.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "DocgenRequest",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "step", "step_number", "total_steps", "next_step_required", "findings",
    "document_complexity", "document_flow", "update_existing",
    "comments_on_complex_logic", "num_files_documented", "total_files_to_document"
  ],
  "properties": {
    "comments_on_complex_logic": {
      "default": true,
      "description": "True (default) to add inline comments around non-obvious logic.",
      "type": "boolean"
    },
    "continuation_id": {
      "description": "Unique thread continuation ID for multi-turn conversations. Works across different tools. ALWAYS reuse the last continuation_id you were given—this preserves full conversation context, files, and findings so the agent can resume seamlessly.",
      "type": "string"
    },
    "document_complexity": {
      "default": true,
      "description": "Include algorithmic complexity (Big O) analysis when True (default).",
      "type": "boolean"
    },
    "document_flow": {
      "default": true,
      "description": "Include call flow/dependency notes when True (default).",
      "type": "boolean"
    },
    "findings": {
      "description": "Important findings, evidence and insights discovered in this step",
      "type": "string"
    },
    "issues_found": {
      "description": "Issues identified with severity levels during work",
      "items": { "type": "object" },
      "type": "array"
    },
    "next_step_required": {
      "description": "Whether another work step is needed. When false, aim to reduce total_steps to match step_number to avoid mismatch.",
      "type": "boolean"
    },
    "num_files_documented": {
      "default": 0,
      "description": "Count of files finished so far. Increment only when a file is fully documented.",
      "minimum": 0,
      "type": "integer"
    },
    "relevant_context": {
      "description": "Methods/functions identified as involved in the issue",
      "items": { "type": "string" },
      "type": "array"
    },
    "relevant_files": {
      "description": "Files identified as relevant to issue/goal (FULL absolute paths to real files/folders - DO NOT SHORTEN)",
      "items": { "type": "string" },
      "type": "array"
    },
    "step": {
      "description": "Current work step content and findings from your overall work",
      "type": "string"
    },
    "step_number": {
      "description": "Current step number in work sequence (starts at 1)",
      "minimum": 1,
      "type": "integer"
    },
    "total_files_to_document": {
      "default": 0,
      "description": "Total files identified in discovery; completion requires matching this count.",
      "minimum": 0,
      "type": "integer"
    },
    "total_steps": {
      "description": "Estimated total steps needed to complete work",
      "minimum": 1,
      "type": "integer"
    },
    "update_existing": {
      "default": true,
      "description": "True (default) to polish inaccurate or outdated docs instead of leaving them untouched.",
      "type": "boolean"
    },
    "use_assistant_model": {
      "default": true,
      "description": "Use assistant model for expert analysis after workflow steps. False skips expert analysis, relies solely on your personal investigation. Defaults to True for comprehensive validation.",
      "type": "boolean"
    }
  }
}
```

---

## imagegen

**Description:** Generate images using AI models with native image generation capability. Describe what you want and receive generated images. Supports iterative refinement via continuation_id and image editing via reference images in media.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "additionalProperties": false,
  "required": ["prompt"],
  "properties": {
    "absolute_file_paths": {
      "description": "Full paths to relevant code",
      "items": { "type": "string" },
      "type": "array"
    },
    "continuation_id": {
      "description": "Unique thread continuation ID for multi-turn conversations. Works across different tools. ALWAYS reuse the last continuation_id you were given—this preserves full conversation context, files, and findings so the agent can resume seamlessly.",
      "type": "string"
    },
    "media": {
      "description": "Optional reference images (absolute paths or base64) for style transfer or editing.",
      "items": { "type": "string" },
      "type": "array"
    },
    "model": {
      "description": "The default model is 'gemini-3.1-pro-preview'. Override only when the user explicitly requests a different model, and use that exact name. If the requested model fails validation, surface the server error instead of substituting another model. When unsure, use the `listmodels` tool for details. Preferred alternatives: gpt-5.2 (score 100, 400K ctx, thinking, code-gen); gpt-5.1-codex (score 100, 400K ctx, thinking, code-gen); gemini-2.5-pro (score 100, 1.0M ctx, thinking, code-gen); gemini-3.1-pro-preview (score 100, 1.0M ctx, thinking, code-gen); gpt-5.2-pro (score 100, 400K ctx, thinking, code-gen); +29 more via `listmodels`.",
      "type": "string"
    },
    "prompt": {
      "description": "Describe the image to generate. Be as specific or abstract as you like — the model expands terse descriptions into rich visual prompts. For editing, describe the desired changes to the reference image.",
      "type": "string"
    },
    "temperature": {
      "description": "0 = deterministic · 1 = creative.",
      "maximum": 1,
      "minimum": 0,
      "type": "number"
    },
    "thinking_mode": {
      "description": "Reasoning depth: minimal, low, medium, high, or max.",
      "enum": ["minimal", "low", "medium", "high", "max"],
      "type": "string"
    }
  }
}
```

---

## listmodels

**Description:** Shows which AI model providers are configured, available model names, their aliases and capabilities.

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": [],
  "properties": {}
}
```

---

## perceive

**Description:** Extract structured intelligence from images, video, and audio using multimodal AI. Analyze visual content, transcribe speech, describe scenes, identify objects, extract text, and answer questions about media. Supports continuation_id for multi-turn analysis sessions.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "additionalProperties": false,
  "required": ["media"],
  "properties": {
    "absolute_file_paths": {
      "description": "Full paths to relevant code",
      "items": { "type": "string" },
      "type": "array"
    },
    "continuation_id": {
      "description": "Unique thread continuation ID for multi-turn conversations. Works across different tools. ALWAYS reuse the last continuation_id you were given—this preserves full conversation context, files, and findings so the agent can resume seamlessly.",
      "type": "string"
    },
    "media": {
      "description": "Media to analyze — absolute file paths or base64 data URLs. Accepts images (JPEG, PNG, WebP, GIF), video (MP4, MOV, WebM), and audio (MP3, WAV, OGG, FLAC). At least one item is required.",
      "items": { "type": "string" },
      "minItems": 1,
      "type": "array"
    },
    "model": {
      "description": "The default model is 'gemini-3.1-pro-preview'. Override only when the user explicitly requests a different model, and use that exact name. If the requested model fails validation, surface the server error instead of substituting another model. When unsure, use the `listmodels` tool for details. Preferred alternatives: gpt-5.2 (score 100, 400K ctx, thinking, code-gen); gpt-5.1-codex (score 100, 400K ctx, thinking, code-gen); gemini-2.5-pro (score 100, 1.0M ctx, thinking, code-gen); gemini-3.1-pro-preview (score 100, 1.0M ctx, thinking, code-gen); gpt-5.2-pro (score 100, 400K ctx, thinking, code-gen); +29 more via `listmodels`.",
      "type": "string"
    },
    "prompt": {
      "description": "Optional focus prompt. Describe what to look for, extract, or analyze. When omitted, the model performs comprehensive analysis of all media.",
      "type": "string"
    },
    "temperature": {
      "description": "0 = deterministic · 1 = creative.",
      "maximum": 1,
      "minimum": 0,
      "type": "number"
    },
    "thinking_mode": {
      "description": "Reasoning depth: minimal, low, medium, high, or max.",
      "enum": ["minimal", "low", "medium", "high", "max"],
      "type": "string"
    }
  }
}
```

---

## planner

**Description:** Breaks down complex tasks through interactive, sequential planning with revision and branching capabilities. Use for complex project planning, system design, migration strategies, and architectural decisions. Builds plans incrementally with deep reflection for complex scenarios.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "PlannerRequest",
  "type": "object",
  "additionalProperties": false,
  "required": ["step", "step_number", "total_steps", "next_step_required"],
  "properties": {
    "branch_from_step": {
      "description": "If branching, the step number that this branch starts from.",
      "minimum": 1,
      "type": "integer"
    },
    "branch_id": {
      "description": "Name for this branch (e.g. 'approach-A', 'migration-path').",
      "type": "string"
    },
    "continuation_id": {
      "description": "Unique thread continuation ID for multi-turn conversations. Works across different tools. ALWAYS reuse the last continuation_id you were given—this preserves full conversation context, files, and findings so the agent can resume seamlessly.",
      "type": "string"
    },
    "is_branch_point": {
      "description": "True when this step creates a new branch to explore an alternative path.",
      "type": "boolean"
    },
    "is_step_revision": {
      "description": "Set true when you are replacing a previously recorded step.",
      "type": "boolean"
    },
    "model": {
      "description": "The default model is 'gemini-3.1-pro-preview'. Override only when the user explicitly requests a different model, and use that exact name. If the requested model fails validation, surface the server error instead of substituting another model. When unsure, use the `listmodels` tool for details. Preferred alternatives: gpt-5.2 (score 100, 400K ctx, thinking, code-gen); gpt-5.1-codex (score 100, 400K ctx, thinking, code-gen); gemini-2.5-pro (score 100, 1.0M ctx, thinking, code-gen); gemini-3.1-pro-preview (score 100, 1.0M ctx, thinking, code-gen); gpt-5.2-pro (score 100, 400K ctx, thinking, code-gen); +29 more via `listmodels`.",
      "type": "string"
    },
    "more_steps_needed": {
      "description": "True when you now expect to add additional steps beyond the prior estimate.",
      "type": "boolean"
    },
    "next_step_required": {
      "description": "Whether another work step is needed. When false, aim to reduce total_steps to match step_number to avoid mismatch.",
      "type": "boolean"
    },
    "revises_step_number": {
      "description": "Step number being replaced when revising.",
      "minimum": 1,
      "type": "integer"
    },
    "step": {
      "description": "Planning content for this step. Step 1: describe the task, problem and scope. Later steps: capture updates, revisions, branches, or open questions that shape the plan.",
      "type": "string"
    },
    "step_number": {
      "description": "Current step number in work sequence (starts at 1)",
      "minimum": 1,
      "type": "integer"
    },
    "total_steps": {
      "description": "Estimated total steps needed to complete work",
      "minimum": 1,
      "type": "integer"
    },
    "use_assistant_model": {
      "default": true,
      "description": "Use assistant model for expert analysis after workflow steps. False skips expert analysis, relies solely on your personal investigation. Defaults to True for comprehensive validation.",
      "type": "boolean"
    }
  }
}
```

---

## precommit

**Description:** Validates git changes and repository state before committing with systematic analysis. Use for multi-repository validation, security review, change impact assessment, and completeness verification. Guides through structured investigation with expert analysis.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "PrecommitRequest",
  "type": "object",
  "additionalProperties": false,
  "required": ["step", "step_number", "total_steps", "next_step_required", "findings"],
  "properties": {
    "compare_to": {
      "description": "Optional git ref (branch/tag/commit) to diff against; falls back to staged/unstaged changes.",
      "type": "string"
    },
    "confidence": {
      "description": "Confidence level: exploring (just starting), low (early investigation), medium (some evidence), high (strong evidence), very_high (comprehensive understanding), almost_certain (near complete confidence), certain (100% confidence locally - no external validation needed)",
      "enum": ["exploring", "low", "medium", "high", "very_high", "almost_certain", "certain"],
      "type": "string"
    },
    "continuation_id": {
      "description": "Unique thread continuation ID for multi-turn conversations. Works across different tools. ALWAYS reuse the last continuation_id you were given—this preserves full conversation context, files, and findings so the agent can resume seamlessly.",
      "type": "string"
    },
    "files_checked": {
      "description": "Absolute paths for every file examined, including ruled-out candidates.",
      "items": { "type": "string" },
      "type": "array"
    },
    "findings": {
      "description": "Record git diff insights, risks, missing tests, security concerns, and positives; update previous notes as you go.",
      "type": "string"
    },
    "focus_on": {
      "description": "Optional emphasis areas such as security, performance, or test coverage.",
      "type": "string"
    },
    "hypothesis": {
      "description": "Current theory about issue/goal based on work",
      "type": "string"
    },
    "include_staged": {
      "default": true,
      "description": "Whether to inspect staged changes (ignored when `compare_to` is set).",
      "type": "boolean"
    },
    "include_unstaged": {
      "default": true,
      "description": "Whether to inspect unstaged changes (ignored when `compare_to` is set).",
      "type": "boolean"
    },
    "issues_found": {
      "description": "List issues with severity (critical/high/medium/low) plus descriptions (bugs, security, performance, coverage).",
      "items": { "type": "object" },
      "type": "array"
    },
    "media": {
      "description": "Optional absolute paths to screenshots or diagrams that aid validation.",
      "items": { "type": "string" },
      "type": "array"
    },
    "model": {
      "description": "The default model is 'gemini-3.1-pro-preview'. Override only when the user explicitly requests a different model, and use that exact name. If the requested model fails validation, surface the server error instead of substituting another model. When unsure, use the `listmodels` tool for details. Preferred alternatives: gpt-5.2 (score 100, 400K ctx, thinking, code-gen); gpt-5.1-codex (score 100, 400K ctx, thinking, code-gen); gemini-2.5-pro (score 100, 1.0M ctx, thinking, code-gen); gemini-3.1-pro-preview (score 100, 1.0M ctx, thinking, code-gen); gpt-5.2-pro (score 100, 400K ctx, thinking, code-gen); +29 more via `listmodels`.",
      "type": "string"
    },
    "next_step_required": {
      "description": "True to continue with another step, False when validation is complete. CRITICAL: If total_steps>=3 or when `precommit_type = external`, set to True until the final step. When continuation_id is provided: Follow the same validation rules based on precommit_type.",
      "type": "boolean"
    },
    "path": {
      "description": "Absolute path to the repository root. Required in step 1.",
      "type": "string"
    },
    "precommit_type": {
      "default": "external",
      "description": "'external' (default, triggers expert model) or 'internal' (local-only validation).",
      "enum": ["external", "internal"],
      "type": "string"
    },
    "relevant_context": {
      "description": "Methods/functions identified as involved in the issue",
      "items": { "type": "string" },
      "type": "array"
    },
    "relevant_files": {
      "description": "Absolute paths of files involved in the change or validation (code, configs, tests, docs). Must be absolute full non-abbreviated paths.",
      "items": { "type": "string" },
      "type": "array"
    },
    "severity_filter": {
      "default": "all",
      "description": "Lowest severity to include when reporting issues.",
      "enum": ["critical", "high", "medium", "low", "all"],
      "type": "string"
    },
    "step": {
      "description": "Step 1: outline how you'll validate the git changes. Later steps: report findings. Review diffs and impacts, use `relevant_files`, and avoid pasting large snippets.",
      "type": "string"
    },
    "step_number": {
      "description": "Current pre-commit step number (starts at 1).",
      "minimum": 1,
      "type": "integer"
    },
    "temperature": {
      "description": "0 = deterministic · 1 = creative.",
      "maximum": 1,
      "minimum": 0,
      "type": "number"
    },
    "thinking_mode": {
      "description": "Reasoning depth: minimal, low, medium, high, or max.",
      "enum": ["minimal", "low", "medium", "high", "max"],
      "type": "string"
    },
    "total_steps": {
      "description": "Planned number of validation steps. External validation: use at most three (analysis → follow-ups → summary). Internal validation: a single step. Honour these limits when resuming via continuation_id.",
      "minimum": 3,
      "type": "integer"
    },
    "use_assistant_model": {
      "default": true,
      "description": "Use assistant model for expert analysis after workflow steps. False skips expert analysis, relies solely on your personal investigation. Defaults to True for comprehensive validation.",
      "type": "boolean"
    }
  }
}
```

---

## refactor

**Description:** Analyzes code for refactoring opportunities with systematic investigation. Use for code smell detection, decomposition planning, modernization, and maintainability improvements. Guides through structured analysis with expert validation.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "RefactorRequest",
  "type": "object",
  "additionalProperties": false,
  "required": ["step", "step_number", "total_steps", "next_step_required", "findings"],
  "properties": {
    "confidence": {
      "default": "incomplete",
      "description": "Your confidence in refactoring analysis: exploring (starting), incomplete (significant work remaining), partial (some opportunities found, more analysis needed), complete (comprehensive analysis finished, all major opportunities identified). WARNING: Use 'complete' ONLY when fully analyzed and can provide recommendations without expert help. 'complete' PREVENTS expert validation. Use 'partial' for large files or uncertain analysis.",
      "enum": ["exploring", "incomplete", "partial", "complete"],
      "type": "string"
    },
    "continuation_id": {
      "description": "Unique thread continuation ID for multi-turn conversations. Works across different tools. ALWAYS reuse the last continuation_id you were given—this preserves full conversation context, files, and findings so the agent can resume seamlessly.",
      "type": "string"
    },
    "files_checked": {
      "description": "List all files examined (absolute paths). Include even ruled-out files to track exploration path.",
      "items": { "type": "string" },
      "type": "array"
    },
    "findings": {
      "description": "Summary of discoveries from this step, including code smells and opportunities for decomposition, modernization, or organization. Document both strengths and weaknesses. In later steps, confirm or update past findings.",
      "type": "string"
    },
    "focus_areas": {
      "description": "Specific areas to focus on (e.g., 'performance', 'readability', 'maintainability', 'security')",
      "items": { "type": "string" },
      "type": "array"
    },
    "hypothesis": {
      "description": "Current theory about issue/goal based on work",
      "type": "string"
    },
    "issues_found": {
      "description": "Refactoring opportunities as dictionaries with 'severity' (critical/high/medium/low), 'type' (codesmells/decompose/modernize/organization), and 'description'. Include all improvement opportunities found.",
      "items": { "type": "object" },
      "type": "array"
    },
    "media": {
      "description": "Optional list of absolute paths to architecture diagrams, UI mockups, design documents, or visual references that help with refactoring context. Only include if they materially assist understanding or assessment.",
      "items": { "type": "string" },
      "type": "array"
    },
    "model": {
      "description": "The default model is 'gemini-3.1-pro-preview'. Override only when the user explicitly requests a different model, and use that exact name. If the requested model fails validation, surface the server error instead of substituting another model. When unsure, use the `listmodels` tool for details. Preferred alternatives: gpt-5.2 (score 100, 400K ctx, thinking, code-gen); gpt-5.1-codex (score 100, 400K ctx, thinking, code-gen); gemini-2.5-pro (score 100, 1.0M ctx, thinking, code-gen); gemini-3.1-pro-preview (score 100, 1.0M ctx, thinking, code-gen); gpt-5.2-pro (score 100, 400K ctx, thinking, code-gen); +29 more via `listmodels`.",
      "type": "string"
    },
    "next_step_required": {
      "description": "Set to true if you plan to continue the investigation with another step. False means you believe the refactoring analysis is complete and ready for expert validation.",
      "type": "boolean"
    },
    "refactor_type": {
      "default": "codesmells",
      "description": "Type of refactoring analysis to perform (codesmells, decompose, modernize, organization)",
      "enum": ["codesmells", "decompose", "modernize", "organization"],
      "type": "string"
    },
    "relevant_context": {
      "description": "Methods/functions identified as involved in the issue",
      "items": { "type": "string" },
      "type": "array"
    },
    "relevant_files": {
      "description": "Subset of files_checked with code requiring refactoring (absolute paths). Include files with code smells, decomposition needs, or improvement opportunities.",
      "items": { "type": "string" },
      "type": "array"
    },
    "step": {
      "description": "The refactoring plan. Step 1: State strategy. Later steps: Report findings. CRITICAL: Examine code for smells, and opportunities for decomposition, modernization, and organization. Use 'relevant_files' for code. FORBIDDEN: Large code snippets.",
      "type": "string"
    },
    "step_number": {
      "description": "The index of the current step in the refactoring investigation sequence, beginning at 1. Each step should build upon or revise the previous one.",
      "minimum": 1,
      "type": "integer"
    },
    "style_guide_examples": {
      "description": "Optional existing code files to use as style/pattern reference (must be FULL absolute paths to real files / folders - DO NOT SHORTEN). These files represent the target coding style and patterns for the project.",
      "items": { "type": "string" },
      "type": "array"
    },
    "temperature": {
      "description": "0 = deterministic · 1 = creative.",
      "maximum": 1,
      "minimum": 0,
      "type": "number"
    },
    "thinking_mode": {
      "description": "Reasoning depth: minimal, low, medium, high, or max.",
      "enum": ["minimal", "low", "medium", "high", "max"],
      "type": "string"
    },
    "total_steps": {
      "description": "Your current estimate for how many steps will be needed to complete the refactoring investigation. Adjust as new opportunities emerge.",
      "minimum": 1,
      "type": "integer"
    },
    "use_assistant_model": {
      "default": true,
      "description": "Use assistant model for expert analysis after workflow steps. False skips expert analysis, relies solely on your personal investigation. Defaults to True for comprehensive validation.",
      "type": "boolean"
    }
  }
}
```

---

## secaudit

**Description:** Performs comprehensive security audit with systematic vulnerability assessment. Use for OWASP Top 10 analysis, compliance evaluation, threat modeling, and security architecture review. Guides through structured security investigation with expert validation.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "SecauditRequest",
  "type": "object",
  "additionalProperties": false,
  "required": ["step", "step_number", "total_steps", "next_step_required", "findings"],
  "properties": {
    "audit_focus": {
      "default": "comprehensive",
      "description": "Primary focus area: owasp, compliance, infrastructure, dependencies, or comprehensive.",
      "enum": ["owasp", "compliance", "infrastructure", "dependencies", "comprehensive"],
      "type": "string"
    },
    "compliance_requirements": {
      "description": "Applicable compliance frameworks or standards (SOC2, PCI DSS, HIPAA, GDPR, ISO 27001, NIST, etc.).",
      "items": { "type": "string" },
      "type": "array"
    },
    "confidence": {
      "description": "exploring/low/medium/high/very_high/almost_certain/certain. 'certain' blocks external validation—use only when fully complete.",
      "enum": ["exploring", "low", "medium", "high", "very_high", "almost_certain", "certain"],
      "type": "string"
    },
    "continuation_id": {
      "description": "Unique thread continuation ID for multi-turn conversations. Works across different tools. ALWAYS reuse the last continuation_id you were given—this preserves full conversation context, files, and findings so the agent can resume seamlessly.",
      "type": "string"
    },
    "files_checked": {
      "description": "Absolute paths for every file inspected, including rejected candidates.",
      "items": { "type": "string" },
      "type": "array"
    },
    "findings": {
      "description": "Summarize vulnerabilities, auth issues, validation gaps, compliance notes, and positives; update prior findings as needed.",
      "type": "string"
    },
    "hypothesis": {
      "description": "Current theory about issue/goal based on work",
      "type": "string"
    },
    "issues_found": {
      "description": "Security issues with severity (critical/high/medium/low) and descriptions (vulns, auth flaws, injection, crypto, config).",
      "items": { "type": "object" },
      "type": "array"
    },
    "media": {
      "description": "Optional absolute paths to diagrams or threat models that inform the audit.",
      "items": { "type": "string" },
      "type": "array"
    },
    "model": {
      "description": "The default model is 'gemini-3.1-pro-preview'. Override only when the user explicitly requests a different model, and use that exact name. If the requested model fails validation, surface the server error instead of substituting another model. When unsure, use the `listmodels` tool for details. Preferred alternatives: gpt-5.2 (score 100, 400K ctx, thinking, code-gen); gpt-5.1-codex (score 100, 400K ctx, thinking, code-gen); gemini-2.5-pro (score 100, 1.0M ctx, thinking, code-gen); gemini-3.1-pro-preview (score 100, 1.0M ctx, thinking, code-gen); gpt-5.2-pro (score 100, 400K ctx, thinking, code-gen); +29 more via `listmodels`.",
      "type": "string"
    },
    "next_step_required": {
      "description": "True while additional threat analysis remains; set False once you are ready to hand off for validation.",
      "type": "boolean"
    },
    "relevant_context": {
      "description": "Methods/functions identified as involved in the issue",
      "items": { "type": "string" },
      "type": "array"
    },
    "relevant_files": {
      "description": "Absolute paths for security-relevant files (auth modules, configs, sensitive code).",
      "items": { "type": "string" },
      "type": "array"
    },
    "security_scope": {
      "description": "Security context (web, mobile, API, cloud, etc.) including stack, user types, data sensitivity, and threat landscape.",
      "type": "string"
    },
    "severity_filter": {
      "default": "all",
      "description": "Minimum severity to include when reporting security issues.",
      "enum": ["critical", "high", "medium", "low", "all"],
      "type": "string"
    },
    "step": {
      "description": "Step 1: outline the audit strategy (OWASP Top 10, auth, validation, etc.). Later steps: report findings. MANDATORY: use `relevant_files` for code references and avoid large snippets.",
      "type": "string"
    },
    "step_number": {
      "description": "Current security-audit step number (starts at 1).",
      "minimum": 1,
      "type": "integer"
    },
    "temperature": {
      "description": "0 = deterministic · 1 = creative.",
      "maximum": 1,
      "minimum": 0,
      "type": "number"
    },
    "thinking_mode": {
      "description": "Reasoning depth: minimal, low, medium, high, or max.",
      "enum": ["minimal", "low", "medium", "high", "max"],
      "type": "string"
    },
    "threat_level": {
      "default": "medium",
      "description": "Assess the threat level: low (internal/low-risk), medium (customer-facing/business data), high (regulated or sensitive), critical (financial/healthcare/PII).",
      "enum": ["low", "medium", "high", "critical"],
      "type": "string"
    },
    "total_steps": {
      "description": "Expected number of audit steps; adjust as new risks surface.",
      "minimum": 1,
      "type": "integer"
    },
    "use_assistant_model": {
      "default": true,
      "description": "Use assistant model for expert analysis after workflow steps. False skips expert analysis, relies solely on your personal investigation. Defaults to True for comprehensive validation.",
      "type": "boolean"
    }
  }
}
```

---

## testgen

**Description:** Creates comprehensive test suites with edge case coverage for specific functions, classes, or modules. Analyzes code paths, identifies failure modes, and generates framework-specific tests. Be specific about scope - target particular components rather than testing everything.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "TestgenRequest",
  "type": "object",
  "additionalProperties": false,
  "required": ["step", "step_number", "total_steps", "next_step_required", "findings"],
  "properties": {
    "confidence": {
      "description": "Indicate your current confidence in the test generation assessment. Use: 'exploring' (starting analysis), 'low' (early investigation), 'medium' (some patterns identified), 'high' (strong understanding), 'very_high' (very strong understanding), 'almost_certain' (nearly complete test plan), 'certain' (100% confidence - test plan is thoroughly complete and all test scenarios are identified with no need for external model validation). Do NOT use 'certain' unless the test generation analysis is comprehensively complete, use 'very_high' or 'almost_certain' instead if not 100% sure. Using 'certain' means you have complete confidence locally and prevents external model validation.",
      "enum": ["exploring", "low", "medium", "high", "very_high", "almost_certain", "certain"],
      "type": "string"
    },
    "continuation_id": {
      "description": "Unique thread continuation ID for multi-turn conversations. Works across different tools. ALWAYS reuse the last continuation_id you were given—this preserves full conversation context, files, and findings so the agent can resume seamlessly.",
      "type": "string"
    },
    "files_checked": {
      "description": "Absolute paths of every file examined, including those ruled out.",
      "items": { "type": "string" },
      "type": "array"
    },
    "findings": {
      "description": "Summarise functionality, critical paths, edge cases, boundary conditions, error handling, and existing test patterns. Cover both happy and failure paths.",
      "type": "string"
    },
    "hypothesis": {
      "description": "Current theory about issue/goal based on work",
      "type": "string"
    },
    "issues_found": {
      "description": "Issues identified with severity levels during work",
      "items": { "type": "object" },
      "type": "array"
    },
    "media": {
      "description": "Optional absolute paths to diagrams or visuals that clarify the system under test.",
      "items": { "type": "string" },
      "type": "array"
    },
    "model": {
      "description": "The default model is 'gemini-3.1-pro-preview'. Override only when the user explicitly requests a different model, and use that exact name. If the requested model fails validation, surface the server error instead of substituting another model. When unsure, use the `listmodels` tool for details. Preferred alternatives: gpt-5.2 (score 100, 400K ctx, thinking, code-gen); gpt-5.1-codex (score 100, 400K ctx, thinking, code-gen); gemini-2.5-pro (score 100, 1.0M ctx, thinking, code-gen); gemini-3.1-pro-preview (score 100, 1.0M ctx, thinking, code-gen); gpt-5.2-pro (score 100, 400K ctx, thinking, code-gen); +29 more via `listmodels`.",
      "type": "string"
    },
    "next_step_required": {
      "description": "True while more investigation or planning remains; set False when test planning is ready for expert validation.",
      "type": "boolean"
    },
    "relevant_context": {
      "description": "Methods/functions identified as involved in the issue",
      "items": { "type": "string" },
      "type": "array"
    },
    "relevant_files": {
      "description": "Absolute paths of code that requires new or updated tests (implementation, dependencies, existing test fixtures).",
      "items": { "type": "string" },
      "type": "array"
    },
    "step": {
      "description": "Test plan for this step. Step 1: outline how you'll analyse structure, business logic, critical paths, and edge cases. Later steps: record findings and new scenarios as they emerge.",
      "type": "string"
    },
    "step_number": {
      "description": "Current test-generation step (starts at 1) — each step should build on prior work.",
      "minimum": 1,
      "type": "integer"
    },
    "temperature": {
      "description": "0 = deterministic · 1 = creative.",
      "maximum": 1,
      "minimum": 0,
      "type": "number"
    },
    "thinking_mode": {
      "description": "Reasoning depth: minimal, low, medium, high, or max.",
      "enum": ["minimal", "low", "medium", "high", "max"],
      "type": "string"
    },
    "total_steps": {
      "description": "Estimated number of steps needed for test planning; adjust as new scenarios appear.",
      "minimum": 1,
      "type": "integer"
    },
    "use_assistant_model": {
      "default": true,
      "description": "Use assistant model for expert analysis after workflow steps. False skips expert analysis, relies solely on your personal investigation. Defaults to True for comprehensive validation.",
      "type": "boolean"
    }
  }
}
```

---

## thinkdeep

**Description:** Performs multi-stage investigation and reasoning for complex problem analysis. Use for architecture decisions, complex bugs, performance challenges, and security analysis. Provides systematic hypothesis testing, evidence-based investigation, and expert validation.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "ThinkdeepRequest",
  "type": "object",
  "additionalProperties": false,
  "required": ["step", "step_number", "total_steps", "next_step_required", "findings"],
  "properties": {
    "confidence": {
      "description": "Confidence level: exploring (just starting), low (early investigation), medium (some evidence), high (strong evidence), very_high (comprehensive understanding), almost_certain (near complete confidence), certain (100% confidence locally - no external validation needed)",
      "enum": ["exploring", "low", "medium", "high", "very_high", "almost_certain", "certain"],
      "type": "string"
    },
    "continuation_id": {
      "description": "Unique thread continuation ID for multi-turn conversations. Works across different tools. ALWAYS reuse the last continuation_id you were given—this preserves full conversation context, files, and findings so the agent can resume seamlessly.",
      "type": "string"
    },
    "files_checked": {
      "description": "List of files examined during this work step",
      "items": { "type": "string" },
      "type": "array"
    },
    "findings": {
      "description": "Important findings, evidence and insights discovered in this step",
      "type": "string"
    },
    "focus_areas": {
      "description": "Focus aspects (architecture, performance, security, etc.)",
      "items": { "type": "string" },
      "type": "array"
    },
    "hypothesis": {
      "description": "Current theory about issue/goal based on work",
      "type": "string"
    },
    "issues_found": {
      "description": "Issues identified with severity levels during work",
      "items": { "type": "object" },
      "type": "array"
    },
    "media": {
      "description": "Optional absolute media file paths or base64 blobs for visual context.",
      "items": { "type": "string" },
      "type": "array"
    },
    "model": {
      "description": "The default model is 'gemini-3.1-pro-preview'. Override only when the user explicitly requests a different model, and use that exact name. If the requested model fails validation, surface the server error instead of substituting another model. When unsure, use the `listmodels` tool for details. Preferred alternatives: gpt-5.2 (score 100, 400K ctx, thinking, code-gen); gpt-5.1-codex (score 100, 400K ctx, thinking, code-gen); gemini-2.5-pro (score 100, 1.0M ctx, thinking, code-gen); gemini-3.1-pro-preview (score 100, 1.0M ctx, thinking, code-gen); gpt-5.2-pro (score 100, 400K ctx, thinking, code-gen); +29 more via `listmodels`.",
      "type": "string"
    },
    "next_step_required": {
      "description": "Whether another work step is needed. When false, aim to reduce total_steps to match step_number to avoid mismatch.",
      "type": "boolean"
    },
    "problem_context": {
      "description": "Additional context about problem/goal. Be expressive.",
      "type": "string"
    },
    "relevant_context": {
      "description": "Methods/functions identified as involved in the issue",
      "items": { "type": "string" },
      "type": "array"
    },
    "relevant_files": {
      "description": "Files identified as relevant to issue/goal (FULL absolute paths to real files/folders - DO NOT SHORTEN)",
      "items": { "type": "string" },
      "type": "array"
    },
    "step": {
      "description": "Current work step content and findings from your overall work",
      "type": "string"
    },
    "step_number": {
      "description": "Current step number in work sequence (starts at 1)",
      "minimum": 1,
      "type": "integer"
    },
    "temperature": {
      "description": "0 = deterministic · 1 = creative.",
      "maximum": 1,
      "minimum": 0,
      "type": "number"
    },
    "thinking_mode": {
      "description": "Reasoning depth: minimal, low, medium, high, or max.",
      "enum": ["minimal", "low", "medium", "high", "max"],
      "type": "string"
    },
    "total_steps": {
      "description": "Estimated total steps needed to complete work",
      "minimum": 1,
      "type": "integer"
    },
    "use_assistant_model": {
      "default": true,
      "description": "Use assistant model for expert analysis after workflow steps. False skips expert analysis, relies solely on your personal investigation. Defaults to True for comprehensive validation.",
      "type": "boolean"
    }
  }
}
```

---

## tracer

**Description:** Performs systematic code tracing with modes for execution flow or dependency mapping. Use for method execution analysis, call chain tracing, dependency mapping, and architectural understanding. Supports precision mode (execution flow) and dependencies mode (structural relationships).

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "TracerRequest",
  "type": "object",
  "additionalProperties": false,
  "required": ["step", "step_number", "total_steps", "next_step_required", "findings", "target_description", "trace_mode"],
  "properties": {
    "confidence": {
      "description": "Confidence level: exploring (just starting), low (early investigation), medium (some evidence), high (strong evidence), very_high (comprehensive understanding), almost_certain (near complete confidence), certain (100% confidence locally - no external validation needed)",
      "enum": ["exploring", "low", "medium", "high", "very_high", "almost_certain", "certain"],
      "type": "string"
    },
    "continuation_id": {
      "description": "Unique thread continuation ID for multi-turn conversations. Works across different tools. ALWAYS reuse the last continuation_id you were given—this preserves full conversation context, files, and findings so the agent can resume seamlessly.",
      "type": "string"
    },
    "files_checked": {
      "description": "List of files examined during this work step",
      "items": { "type": "string" },
      "type": "array"
    },
    "findings": {
      "description": "Important findings, evidence and insights discovered in this step",
      "type": "string"
    },
    "media": {
      "description": "Optional paths to architecture diagrams or flow charts that help understand the tracing context.",
      "items": { "type": "string" },
      "type": "array"
    },
    "model": {
      "description": "The default model is 'gemini-3.1-pro-preview'. Override only when the user explicitly requests a different model, and use that exact name. If the requested model fails validation, surface the server error instead of substituting another model. When unsure, use the `listmodels` tool for details. Preferred alternatives: gpt-5.2 (score 100, 400K ctx, thinking, code-gen); gpt-5.1-codex (score 100, 400K ctx, thinking, code-gen); gemini-2.5-pro (score 100, 1.0M ctx, thinking, code-gen); gemini-3.1-pro-preview (score 100, 1.0M ctx, thinking, code-gen); gpt-5.2-pro (score 100, 400K ctx, thinking, code-gen); +29 more via `listmodels`.",
      "type": "string"
    },
    "next_step_required": {
      "description": "Whether another work step is needed. When false, aim to reduce total_steps to match step_number to avoid mismatch.",
      "type": "boolean"
    },
    "relevant_context": {
      "description": "Methods/functions identified as involved in the issue",
      "items": { "type": "string" },
      "type": "array"
    },
    "relevant_files": {
      "description": "Files identified as relevant to issue/goal (FULL absolute paths to real files/folders - DO NOT SHORTEN)",
      "items": { "type": "string" },
      "type": "array"
    },
    "step": {
      "description": "Current work step content and findings from your overall work",
      "type": "string"
    },
    "step_number": {
      "description": "Current step number in work sequence (starts at 1)",
      "minimum": 1,
      "type": "integer"
    },
    "target_description": {
      "description": "Description of what to trace and WHY. Include context about what you're trying to understand or analyze.",
      "type": "string"
    },
    "total_steps": {
      "description": "Estimated total steps needed to complete work",
      "minimum": 1,
      "type": "integer"
    },
    "trace_mode": {
      "description": "Type of tracing: 'ask' (default - prompts user to choose mode), 'precision' (execution flow) or 'dependencies' (structural relationships)",
      "enum": ["precision", "dependencies", "ask"],
      "type": "string"
    },
    "use_assistant_model": {
      "default": true,
      "description": "Use assistant model for expert analysis after workflow steps. False skips expert analysis, relies solely on your personal investigation. Defaults to True for comprehensive validation.",
      "type": "boolean"
    }
  }
}
```

---

## version

**Description:** Get server version, configuration details, and list of available tools.

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": [],
  "properties": {}
}
```
