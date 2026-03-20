"""
NarrateWorkflow tool - Step-by-step document composition grounded in source files and context store content

This tool provides a structured workflow for composing polished, human-readable documents from
source code, context store pages, and structured investigation notes. It guides the CLI agent
through systematic source-gathering steps with forced pauses between each step to ensure
thorough file examination and observation capture before the expert model writes the document.

Key features:
- Step-by-step source-gathering workflow with progress tracking
- Context store integration (resolve store_id to thread turns, select specific pages)
- Context-aware file embedding (references during investigation, full content for writing)
- Automatic structural observation tracking
- Expert writing integration with external models
- Support for multiple document types (overview, deep_dive, rationale, reference, general)
"""

import logging
from typing import TYPE_CHECKING, Any, Literal, Optional

from pydantic import Field, model_validator

if TYPE_CHECKING:
    from tools.models import ToolModelCategory

from config import TEMPERATURE_CREATIVE
from systemprompts import NARRATE_PROMPT
from tools.shared.base_models import WorkflowRequest

from .workflow.base import WorkflowTool

logger = logging.getLogger(__name__)

# Tool-specific field descriptions for narrate workflow
NARRATE_WORKFLOW_FIELD_DESCRIPTIONS = {
    "step": (
        "The source-gathering plan. Step 1: State your strategy, including which files and directories to map, "
        "which context store pages to read, and how you will structure your investigation to cover all source "
        "material needed for the document. Later steps: Report structural observations and adapt the plan as "
        "new source content is examined."
    ),
    "step_number": (
        "The index of the current step in the source-gathering sequence, beginning at 1. Each step should build "
        "upon or revise the previous one."
    ),
    "total_steps": (
        "Your current estimate for how many steps will be needed to gather all source material. "
        "Adjust as new content is discovered."
    ),
    "next_step_required": (
        "Set to true if you plan to continue gathering source material with another step. False means you believe "
        "all necessary source content has been collected and is ready for the expert writer."
    ),
    "findings": (
        "Structural observations extracted from this step — component responsibilities, data flows, key abstractions, "
        "design patterns, and notable implementation details. These observations are the writer's raw material. "
        "IMPORTANT: Be precise and concrete; cite specific file paths, class names, and function names. "
        "In later steps, confirm or supplement past observations with additional evidence."
    ),
    "files_checked": (
        "List all files examined (absolute paths). Include even ruled-out files to track exploration path."
    ),
    "relevant_files": (
        "Subset of files_checked whose content directly contributes to the document being written (absolute paths). "
        "Include files that define the components, interfaces, or behaviors the document will describe."
    ),
    "relevant_context": (
        "List methods/functions central to the document's subject matter, in 'ClassName.methodName' or "
        "'functionName' format. Prioritize those the expert writer will need to reference in the narrative."
    ),
    "document_type": (
        "The type of document to produce. Guides structure and depth: "
        "'overview' for high-level system maps, 'deep_dive' for focused subsystem examination, "
        "'rationale' for design-decision explanations, 'reference' for API/interface documentation, "
        "'general' for topic-driven free-form documents."
    ),
    "store_id": (
        "Context store identifier to use as additional source material. When provided, the expert writer will "
        "receive selected pages from this store as authoritative background knowledge (prior analysis, "
        "architectural decisions, session context)."
    ),
    "store_pages": (
        "1-indexed page numbers from the context store to include. Each page is a user+assistant turn pair. "
        "Omit to include no store content even when store_id is set. The CLI may update this list across steps "
        "as investigation clarifies which pages are relevant."
    ),
}


class NarrateWorkflowRequest(WorkflowRequest):
    """Request model for narrate workflow source-gathering steps"""

    # Required fields for each source-gathering step
    step: str = Field(..., description=NARRATE_WORKFLOW_FIELD_DESCRIPTIONS["step"])
    step_number: int = Field(..., description=NARRATE_WORKFLOW_FIELD_DESCRIPTIONS["step_number"])
    total_steps: int = Field(..., description=NARRATE_WORKFLOW_FIELD_DESCRIPTIONS["total_steps"])
    next_step_required: bool = Field(..., description=NARRATE_WORKFLOW_FIELD_DESCRIPTIONS["next_step_required"])

    # Source-gathering tracking fields
    findings: str = Field(..., description=NARRATE_WORKFLOW_FIELD_DESCRIPTIONS["findings"])
    files_checked: list[str] = Field(
        default_factory=list, description=NARRATE_WORKFLOW_FIELD_DESCRIPTIONS["files_checked"]
    )
    relevant_files: list[str] = Field(
        default_factory=list, description=NARRATE_WORKFLOW_FIELD_DESCRIPTIONS["relevant_files"]
    )
    relevant_context: list[str] = Field(
        default_factory=list, description=NARRATE_WORKFLOW_FIELD_DESCRIPTIONS["relevant_context"]
    )

    # Narrate-specific fields
    document_type: Optional[Literal["overview", "deep_dive", "rationale", "reference", "general"]] = Field(
        "general", description=NARRATE_WORKFLOW_FIELD_DESCRIPTIONS["document_type"]
    )
    store_id: Optional[str] = Field(None, description=NARRATE_WORKFLOW_FIELD_DESCRIPTIONS["store_id"])
    store_pages: Optional[list[int]] = Field(None, description=NARRATE_WORKFLOW_FIELD_DESCRIPTIONS["store_pages"])

    @model_validator(mode="after")
    def validate_step_one_requirements(self):
        """Ensure step 1 has required relevant_files."""
        if self.step_number == 1:
            if not self.relevant_files:
                raise ValueError("Step 1 requires 'relevant_files' field to specify files or directories to narrate")
        return self


class NarrateTool(WorkflowTool):
    """
    Narrate workflow tool for composing polished documents grounded in source files and context stores.

    This tool implements a structured source-gathering workflow that guides the agent through
    methodical investigation steps, ensuring thorough file examination and observation capture
    before the expert model writes the final document. It supports multiple document types
    (overview, deep_dive, rationale, reference, general) and can incorporate context store
    pages as additional authoritative background material.
    """

    def __init__(self):
        super().__init__()
        self.initial_request = None
        self.narrate_config = {}

    def get_name(self) -> str:
        return "narrate"

    def get_description(self) -> str:
        return (
            "Composes polished, human-readable documents grounded in source files and context store content. "
            "Use for overviews, deep dives, design rationale, and reference documentation. "
            "Guides systematic source gathering before handing off to an expert technical writer."
        )

    def get_system_prompt(self) -> str:
        return NARRATE_PROMPT

    def get_default_temperature(self) -> float:
        return TEMPERATURE_CREATIVE

    def get_model_category(self) -> "ToolModelCategory":
        """Narrate workflow requires thorough source synthesis and high-quality writing"""
        from tools.models import ToolModelCategory

        return ToolModelCategory.EXTENDED_REASONING

    def get_workflow_request_model(self):
        """Return the narrate workflow-specific request model."""
        return NarrateWorkflowRequest

    def get_input_schema(self) -> dict[str, Any]:
        """Generate input schema using WorkflowSchemaBuilder with narrate-specific overrides."""
        from .workflow.schema_builders import WorkflowSchemaBuilder

        # Fields excluded from narrate workflow (inherited from WorkflowRequest but not used)
        excluded_fields = {"hypothesis", "confidence", "issues_found"}

        # Narrate workflow-specific field overrides
        narrate_field_overrides = {
            "step": {
                "type": "string",
                "description": NARRATE_WORKFLOW_FIELD_DESCRIPTIONS["step"],
            },
            "step_number": {
                "type": "integer",
                "minimum": 1,
                "description": NARRATE_WORKFLOW_FIELD_DESCRIPTIONS["step_number"],
            },
            "total_steps": {
                "type": "integer",
                "minimum": 1,
                "description": NARRATE_WORKFLOW_FIELD_DESCRIPTIONS["total_steps"],
            },
            "next_step_required": {
                "type": "boolean",
                "description": NARRATE_WORKFLOW_FIELD_DESCRIPTIONS["next_step_required"],
            },
            "findings": {
                "type": "string",
                "description": NARRATE_WORKFLOW_FIELD_DESCRIPTIONS["findings"],
            },
            "files_checked": {
                "type": "array",
                "items": {"type": "string"},
                "description": NARRATE_WORKFLOW_FIELD_DESCRIPTIONS["files_checked"],
            },
            "relevant_files": {
                "type": "array",
                "items": {"type": "string"},
                "description": NARRATE_WORKFLOW_FIELD_DESCRIPTIONS["relevant_files"],
            },
            "document_type": {
                "type": "string",
                "enum": ["overview", "deep_dive", "rationale", "reference", "general"],
                "default": "general",
                "description": NARRATE_WORKFLOW_FIELD_DESCRIPTIONS["document_type"],
            },
            "store_id": {
                "type": "string",
                "description": NARRATE_WORKFLOW_FIELD_DESCRIPTIONS["store_id"],
            },
            "store_pages": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1},
                "description": NARRATE_WORKFLOW_FIELD_DESCRIPTIONS["store_pages"],
            },
        }

        return WorkflowSchemaBuilder.build_schema(
            tool_specific_fields=narrate_field_overrides,
            model_field_schema=self.get_model_field_schema(),
            auto_mode=self.is_effective_auto_mode(),
            tool_name=self.get_name(),
            excluded_workflow_fields=list(excluded_fields),
        )

    def get_required_actions(
        self, step_number: int, confidence: str, findings: str, total_steps: int, request=None
    ) -> list[str]:
        """Define required actions for each source-gathering phase."""
        if step_number == 1:
            store_id = getattr(request, "store_id", None) if request else None
            store_note = (
                f" If store_id '{store_id}' is provided, explore it via ctxread to identify relevant pages."
                if store_id
                else ""
            )
            return [
                "Map the file structure of all relevant_files — list directories, modules, and key files",
                "Identify the major components, classes, and interfaces involved in the topic" + store_note,
                "Note entry points, public APIs, and primary data flows at a high level",
                "Do NOT read full file bodies yet — map scope first",
                "Identify which specific files and store pages will be needed in subsequent steps",
            ]
        elif step_number < total_steps:
            return [
                "Read the identified source files in full — extract concrete structural observations",
                "Document specific class names, function signatures, and module relationships",
                "Trace primary execution paths and data transformations through the code",
                "Read any specified store pages and note relevant background context",
                "Record all observations with precise file references for the expert writer",
            ]
        else:
            return [
                "Verify all source files directly relevant to the document topic have been examined",
                "Confirm that structural observations cover the components, flows, and patterns to be documented",
                "Ensure store pages with relevant background context have been read if store_id is set",
                "Check that relevant_files and relevant_context are complete and accurate",
                "Confirm findings contain sufficient concrete detail for the expert writer to proceed",
            ]

    def should_call_expert_analysis(self, consolidated_findings, request=None) -> bool:
        """
        Always call expert writing for comprehensive document composition.

        Narration benefits from a dedicated writer model if we have any meaningful source material.
        """
        if request and not self.get_request_use_assistant_model(request):
            return False

        return len(consolidated_findings.relevant_files) > 0 or len(consolidated_findings.findings) >= 1

    def prepare_expert_analysis_context(self, consolidated_findings) -> str:
        """Prepare context for external model call for final document composition."""
        context_parts = [
            f"=== NARRATIVE REQUEST ===\n{self.initial_request or 'Document composition workflow initiated'}\n=== END REQUEST ==="
        ]

        # Add document parameters
        document_type = self.narrate_config.get("document_type", "general")
        context_parts.append(f"\n=== DOCUMENT PARAMETERS ===\nDocument type: {document_type}\n=== END PARAMETERS ===")

        # Add context store pages if available
        store_id = self.narrate_config.get("store_id")
        store_pages = self.narrate_config.get("store_pages")
        if store_id and store_pages:
            store_section = self._build_store_section(store_id, store_pages)
            if store_section:
                context_parts.append(store_section)

        # Add investigation summary
        investigation_summary = self._build_narrate_summary(consolidated_findings)
        context_parts.append(f"\n=== AGENT'S INVESTIGATION ===\n{investigation_summary}\n=== END INVESTIGATION ===")

        # Add relevant code elements if available
        if consolidated_findings.relevant_context:
            methods_text = "\n".join(f"- {method}" for method in consolidated_findings.relevant_context)
            context_parts.append(f"\n=== RELEVANT CODE ELEMENTS ===\n{methods_text}\n=== END CODE ELEMENTS ===")

        return "\n".join(context_parts)

    def _build_store_section(self, store_id: str, store_pages: list[int]) -> str:
        """Read the requested pages from a context store and format them for the expert writer."""
        try:
            from utils.context_registry import resolve_thread_id
            from utils.conversation_memory import get_thread

            thread_id = resolve_thread_id(store_id)
            if thread_id is None:
                logger.warning(
                    f"[NARRATE] store_id '{store_id}' not found in context registry — skipping store section"
                )
                return ""

            thread = get_thread(thread_id)
            if thread is None:
                logger.warning(
                    f"[NARRATE] thread {thread_id} for store '{store_id}' not found in memory — skipping store section"
                )
                return ""

            page_parts = []
            for page_num in store_pages:
                # Pages are 1-indexed; each page = one user+assistant turn pair
                pair_start = 2 * (page_num - 1)
                pair_end = pair_start + 2
                pair = thread.turns[pair_start:pair_end]

                if not pair:
                    logger.warning(f"[NARRATE] store '{store_id}' page {page_num} is out of range — skipping")
                    continue

                # Extract label from model_metadata on the assistant turn if present
                label = f"page {page_num}"
                for turn in pair:
                    if turn.role == "assistant" and turn.model_metadata:
                        candidate = turn.model_metadata.get("context_label")
                        if candidate:
                            label = candidate
                            break

                page_content_parts = []
                for turn in pair:
                    if turn.content:
                        page_content_parts.append(turn.content)

                if page_content_parts:
                    page_text = "\n\n".join(page_content_parts)
                    page_parts.append(f"--- Page {page_num}: {label} ---\n{page_text}")

            if not page_parts:
                return ""

            pages_text = "\n\n".join(page_parts)
            return f"\n=== CONTEXT STORE: {store_id} ===\n{pages_text}\n=== END CONTEXT STORE ==="

        except Exception as e:
            logger.warning(f"[NARRATE] Failed to build store section for '{store_id}': {type(e).__name__}: {e}")
            return ""

    def _build_narrate_summary(self, consolidated_findings) -> str:
        """Prepare a comprehensive summary of the source-gathering investigation."""
        summary_parts = [
            "=== SYSTEMATIC SOURCE-GATHERING INVESTIGATION SUMMARY ===",
            f"Total steps: {len(consolidated_findings.findings)}",
            f"Files examined: {len(consolidated_findings.files_checked)}",
            f"Relevant files identified: {len(consolidated_findings.relevant_files)}",
            f"Code elements identified: {len(consolidated_findings.relevant_context)}",
            "",
            "=== INVESTIGATION PROGRESSION ===",
        ]

        for finding in consolidated_findings.findings:
            summary_parts.append(finding)

        return "\n".join(summary_parts)

    def should_include_files_in_expert_prompt(self) -> bool:
        """Include source files in expert writing prompt for grounded composition."""
        return True

    def should_embed_system_prompt(self) -> bool:
        """Embed system prompt so the expert writer receives full writing directives."""
        return True

    def get_expert_thinking_mode(self) -> str:
        """Use high thinking mode for thorough document composition."""
        return "high"

    def should_skip_expert_analysis(self, request, consolidated_findings) -> bool:
        """Narrate workflow always uses expert writing for final document composition."""
        return False

    def get_expert_analysis_instruction(self) -> str:
        """Get specific instruction for the narrate expert writing pass."""
        document_type = self.narrate_config.get("document_type", "general")
        return (
            f"Compose a complete, polished {document_type} document. Write it as genuine narrative grounded entirely "
            "in the source files and context provided. Follow the structure and quality directives in your system prompt."
        )

    # Hook method overrides for narrate-specific behavior

    def prepare_step_data(self, request) -> dict:
        """Map narrate-specific fields for internal processing."""
        step_data = {
            "step": request.step,
            "step_number": request.step_number,
            "findings": request.findings,
            "files_checked": request.files_checked,
            "relevant_files": request.relevant_files,
            "relevant_context": request.relevant_context,
            "issues_found": [],  # Narrate workflow does not use issues_found
            "confidence": "medium",  # Fixed value for workflow compatibility
            "hypothesis": request.findings,  # Map findings to hypothesis for compatibility
            "media": [],
        }
        return step_data

    def store_initial_issue(self, step_description: str):
        """Store initial request and narrate configuration for the expert writer."""
        self.initial_request = step_description

    def get_completion_status(self) -> str:
        """Narrate tools use narration-specific status."""
        return "narrate_complete"

    def get_completion_data_key(self) -> str:
        """Narrate uses 'complete_narrate' key."""
        return "complete_narrate"

    def get_final_analysis_from_request(self, request):
        """Narrate tools use 'findings' field."""
        return request.findings

    def get_confidence_level(self, request) -> str:
        """Narrate tools use fixed confidence for consistency."""
        return "medium"

    def get_completion_message(self) -> str:
        """Narrate-specific completion message."""
        return (
            "Narration complete. MANDATORY: Present the document to the user in full. "
            "Do not summarize, truncate, or reformulate the expert's output."
        )

    def get_skip_reason(self) -> str:
        """Narrate-specific skip reason."""
        return "Completed source gathering locally"

    def get_skip_expert_analysis_status(self) -> str:
        """Narrate-specific expert analysis skip status."""
        return "skipped_due_to_complete_narration"

    def prepare_work_summary(self) -> str:
        """Narrate-specific work summary."""
        return self._build_narrate_summary(self.consolidated_findings)

    def get_completion_next_steps_message(self, expert_analysis_used: bool = False) -> str:
        """Narrate-specific completion next steps message."""
        store_note = ""
        if self.narrate_config.get("store_id") and self.narrate_config.get("store_pages"):
            store_note = " and context store content"

        base_message = (
            f"NARRATION IS COMPLETE. The document above was composed by the expert model grounded in source files"
            f"{store_note}. Present it to the user verbatim without summarizing or reformatting."
        )

        if expert_analysis_used:
            expert_guidance = self.get_expert_analysis_guidance()
            if expert_guidance:
                return f"{base_message}\n\n{expert_guidance}"

        return base_message

    def get_expert_analysis_guidance(self) -> str:
        """Provide specific guidance for handling the expert writer's document."""
        return (
            "The document produced by the assistant model above is the deliverable. Present it to the user as-is. "
            "Do not add your own summary, commentary, or reformatting unless the user explicitly asks."
        )

    def get_step_guidance_message(self, request) -> str:
        """Narrate-specific step guidance with detailed source-gathering instructions."""
        step_guidance = self.get_narrate_step_guidance(request.step_number, request)
        return step_guidance["next_steps"]

    def get_narrate_step_guidance(self, step_number: int, request) -> dict[str, Any]:
        """Provide step-specific guidance for narrate workflow."""
        required_actions = self.get_required_actions(
            step_number, "medium", request.findings, request.total_steps, request
        )

        if step_number == 1:
            store_clause = ""
            if getattr(request, "store_id", None):
                store_clause = (
                    " If a store_id was provided, use ctxread to explore the store and identify which pages "
                    "contain relevant background context — do not include pages yet, just identify them."
                )
            next_steps = (
                f"MANDATORY: DO NOT call the {self.get_name()} tool again immediately. You MUST first map "
                f"the source files specified in relevant_files using appropriate tools.{store_clause} "
                f"CRITICAL AWARENESS: At this stage you are mapping scope only — identify the major components, "
                f"key files, and overall structure without reading full file bodies yet. "
                f"Only call {self.get_name()} again AFTER completing your scope mapping. When you call "
                f"{self.get_name()} next time, use step_number: {step_number + 1} and report the structure "
                f"you mapped, files you identified for deep reading, and any store pages you flagged."
            )
        elif step_number < request.total_steps:
            next_steps = (
                f"STOP! Do NOT call {self.get_name()} again yet. Based on your scope map, you need to read "
                f"the source files and store pages in detail. MANDATORY ACTIONS before calling "
                f"{self.get_name()} step {step_number + 1}:\n"
                + "\n".join(f"{i + 1}. {action}" for i, action in enumerate(required_actions))
                + f"\n\nOnly call {self.get_name()} again with step_number: {step_number + 1} AFTER "
                + "completing these source-gathering tasks."
            )
        else:
            next_steps = (
                f"WAIT! Your source gathering needs final verification. DO NOT call {self.get_name()} immediately. REQUIRED ACTIONS:\n"
                + "\n".join(f"{i + 1}. {action}" for i, action in enumerate(required_actions))
                + f"\n\nREMEMBER: Ensure all source files and relevant context store pages have been examined. "
                f"Findings must contain sufficient concrete detail for the expert writer. "
                f"Then call {self.get_name()} with step_number: {step_number + 1}."
            )

        return {"next_steps": next_steps}

    def customize_workflow_response(self, response_data: dict, request) -> dict:
        """Customize response to match narrate workflow format."""
        # Store initial request and config on first step
        if request.step_number == 1:
            self.initial_request = request.step
            self.narrate_config = {
                "document_type": request.document_type,
                "store_id": request.store_id,
                "store_pages": request.store_pages,
            }

        # Persist store_pages on every step — the CLI may update which pages to include
        if request.store_pages is not None:
            self.narrate_config["store_pages"] = request.store_pages

        # Convert generic status names to narrate-specific ones
        tool_name = self.get_name()
        status_mapping = {
            f"{tool_name}_in_progress": "narration_in_progress",
            f"pause_for_{tool_name}": "pause_for_narration",
            f"{tool_name}_required": "narration_required",
            f"{tool_name}_complete": "narration_complete",
        }

        if response_data["status"] in status_mapping:
            response_data["status"] = status_mapping[response_data["status"]]

        # Rename status field to match narrate workflow
        if f"{tool_name}_status" in response_data:
            response_data["narration_status"] = response_data.pop(f"{tool_name}_status")

        # Map complete_narrate key
        if f"complete_{tool_name}" in response_data:
            response_data["complete_narrate"] = response_data.pop(f"complete_{tool_name}")

        # Map the completion flag to match narrate workflow
        if f"{tool_name}_complete" in response_data:
            response_data["narration_complete"] = response_data.pop(f"{tool_name}_complete")

        return response_data

    # Required abstract methods from BaseTool

    def get_request_model(self):
        """Return the narrate workflow-specific request model."""
        return NarrateWorkflowRequest

    async def prepare_prompt(self, request) -> str:
        """Not used - workflow tools use execute_workflow()."""
        return ""
