"""clink tool - bridge PAL MCP requests to external AI CLIs."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NoReturn

from mcp.types import TextContent
from pydantic import Field

from clink import get_registry
from clink.agent_definitions import (
    AgentDefinition,
    AgentDefinitionError,
    is_agent_role,
    load_agent_definition,
    parse_agent_role,
)
from clink.agents import AgentOutput, CLIAgentError, create_agent
from clink.constants import BUILTIN_PROMPTS_DIR
from clink.models import ResolvedCLIClient, ResolvedCLIRole
from config import TEMPERATURE_BALANCED
from tools.models import ToolModelCategory, ToolOutput
from tools.shared.base_models import COMMON_FIELD_DESCRIPTIONS, ToolRequest
from tools.shared.exceptions import ToolExecutionError
from tools.simple.base import SchemaBuilder, SimpleTool
from utils.env import get_env

logger = logging.getLogger(__name__)

# Base system prompt applied to all agent roles
BASE_PROMPT_PATH = BUILTIN_PROMPTS_DIR / "default.txt"


def _load_base_prompt() -> str:
    """Load the base system prompt that applies to all agent roles."""
    try:
        return BASE_PROMPT_PATH.read_text(encoding="utf-8").strip()
    except Exception as exc:
        logger.warning("Failed to load base prompt from %s: %s", BASE_PROMPT_PATH, exc)
        return ""


class CLinkRequest(ToolRequest):
    """Request model for clink tool.

    Inherits from ToolRequest for common fields (model, continuation_id, media, etc.)
    and adds clink-specific fields for CLI agent invocation.
    """

    prompt: str = Field(..., description="Prompt forwarded to the target CLI.")
    cwd: str = Field(
        ...,
        description=(
            "REQUIRED. The absolute path where the agent will execute. This determines:\n"
            "- Which CLAUDE.md the agent discovers (project context)\n"
            "- Which .claude/agents/ directory is searched first\n"
            "- The agent's working directory for all file operations\n\n"
            "CWD Decision Matrix:\n"
            "| Scenario | CWD Value |\n"
            "|----------|----------|\n"
            "| Working on a project | Project root (e.g., /home/user/dev/myproject) |\n"
            "| General task, no project | Current working directory |\n"
            "| Specific directory context | That directory's absolute path |\n\n"
            "CRITICAL: Always pass the directory where you want the agent to operate. "
            "Incorrect CWD means the agent misses project-specific instructions and context."
        ),
    )
    cli_name: str | None = Field(
        default=None,
        description="Configured CLI client name to invoke. Defaults to the first configured CLI if omitted.",
    )
    role: str | None = Field(
        default=None,
        description="Optional role preset defined in the CLI configuration (defaults to 'default').",
    )
    absolute_file_paths: list[str] = Field(
        default_factory=list,
        description=COMMON_FIELD_DESCRIPTIONS["absolute_file_paths"],
    )
    # Override media to default to empty list instead of None for CLI compatibility
    media: list[str] = Field(
        default_factory=list,
        description=COMMON_FIELD_DESCRIPTIONS["media"],
    )
    json_schema: dict | str | None = Field(
        default=None,
        description=(
            "Optional JSON schema for structured output. Accepts:\n"
            "  - dict: JSON schema object (existing behavior)\n"
            "  - str: File path to .json file (absolute or relative to cwd) OR inline JSON string\n\n"
            "When provided, the resolved schema is passed to the CLI agent via --json-schema flag. "
            "Schema validation is performed by the CLI agent; invalid schemas will cause the CLI "
            "to return an error. Only supported by Claude CLI."
        ),
    )


class CLinkTool(SimpleTool):
    """Bridge MCP requests to configured CLI agents.

    Schema metadata is cached at construction time and execution relies on the shared
    SimpleTool hooks for conversation memory. Prompt preparation is customised so we
    pass instructions and file references suitable for another CLI agent.
    """

    def __init__(self) -> None:
        # Cache registry metadata so the schema surfaces concrete enum values.
        self._registry = get_registry()
        self._cli_names = self._registry.list_clients()
        self._role_map: dict[str, list[str]] = {name: self._registry.list_roles(name) for name in self._cli_names}
        self._all_roles: list[str] = sorted({role for roles in self._role_map.values() for role in roles})
        if "gemini" in self._cli_names:
            self._default_cli_name = "gemini"
        else:
            self._default_cli_name = self._cli_names[0] if self._cli_names else None
        self._active_system_prompt: str = ""
        super().__init__()

    def get_name(self) -> str:
        return "clink"

    def get_description(self) -> str:
        return (
            "Link a request to an external AI CLI (Gemini CLI, Qwen CLI, etc.) through PAL MCP to reuse "
            "their capabilities inside existing workflows."
        )

    def get_annotations(self) -> dict[str, Any]:
        return {"readOnlyHint": True}

    def requires_model(self) -> bool:
        return False

    def get_model_category(self) -> ToolModelCategory:
        return ToolModelCategory.BALANCED

    def get_default_temperature(self) -> float:
        return TEMPERATURE_BALANCED

    def get_system_prompt(self) -> str:
        return self._active_system_prompt or ""

    def get_request_model(self):
        return CLinkRequest

    def get_input_schema(self) -> dict[str, Any]:
        # Surface configured CLI names and roles directly in the schema so MCP clients
        # (and downstream agents) can discover available options without consulting
        # a separate registry call.
        role_descriptions = []
        for name in self._cli_names:
            roles = ", ".join(sorted(self._role_map.get(name, ["default"]))) or "default"
            role_descriptions.append(f"{name}: {roles}")

        if role_descriptions:
            cli_available = ", ".join(self._cli_names) if self._cli_names else "(none configured)"
            default_text = (
                f" Default: {self._default_cli_name}." if self._default_cli_name and len(self._cli_names) <= 1 else ""
            )
            cli_description = (
                "Configured CLI client name (from conf/cli_clients). Available: " + cli_available + default_text
            )
            role_description = (
                "Role preset or agent definition. Standard roles per CLI: "
                + "; ".join(role_descriptions)
                + ". Agent roles: 'agent' (general purpose), 'agent:<name>' (agent by name in .claude/agents/), "
                + "'agent:<absolute_path>' (agent from file path), "
                + "or 'agent:<relative_path>' (resolved relative to cwd, e.g. 'agent:./agents/custom.md')."
            )
        else:
            cli_description = "Configured CLI client name (from conf/cli_clients)."
            role_description = "Optional role preset defined for the selected CLI (defaults to 'default')."

        properties = {
            "prompt": {
                "type": "string",
                "description": "User request forwarded to the CLI (conversation context is pre-applied).",
            },
            "cwd": {
                "type": "string",
                "description": (
                    "REQUIRED. The absolute path where the agent will execute. This determines:\n"
                    "- Which CLAUDE.md the agent discovers (project context)\n"
                    "- Which .claude/agents/ directory is searched first\n"
                    "- The agent's working directory for all file operations\n\n"
                    "CWD Decision Matrix:\n"
                    "| Scenario | CWD Value |\n"
                    "|----------|----------|\n"
                    "| Working on a project | Project root (e.g., /home/user/dev/myproject) |\n"
                    "| General task, no project | Current working directory |\n"
                    "| Specific directory context | That directory's absolute path |\n\n"
                    "CRITICAL: Always pass the directory where you want the agent to operate. "
                    "Incorrect CWD means the agent misses project-specific instructions and context."
                ),
            },
            "cli_name": {
                "type": "string",
                "enum": self._cli_names,
                "description": cli_description,
            },
            "role": {
                "type": "string",
                "description": role_description,
            },
            "absolute_file_paths": SchemaBuilder.SIMPLE_FIELD_SCHEMAS["absolute_file_paths"],
            "media": SchemaBuilder.COMMON_FIELD_SCHEMAS["media"],
            "continuation_id": SchemaBuilder.COMMON_FIELD_SCHEMAS["continuation_id"],
            "json_schema": {
                "anyOf": [
                    {"type": "object"},
                    {"type": "string"},
                ],
                "description": (
                    "Optional JSON schema for structured output. Accepts:\n"
                    "  - object: JSON schema dict (existing behavior)\n"
                    "  - string: File path to .json schema file (absolute or relative to cwd), "
                    "or an inline JSON string\n\n"
                    "The resolved schema is passed to the CLI agent via --json-schema flag. "
                    "Schema validation is performed by the CLI agent. Only supported by Claude CLI."
                ),
            },
            "model": {
                "type": "string",
                "description": "Model override (e.g., 'opus', 'sonnet', 'haiku'). Overrides CLI client default.",
            },
        }

        schema = {
            "type": "object",
            "properties": properties,
            "required": ["prompt", "cwd"],
            "additionalProperties": False,
        }

        if len(self._cli_names) > 1:
            schema["required"].append("cli_name")

        return schema

    def get_tool_fields(self) -> dict[str, dict[str, Any]]:
        """Unused by clink because we override the schema end-to-end."""
        return {}

    async def execute(self, arguments: dict[str, Any]) -> list[TextContent]:
        self._current_arguments = arguments
        request = self.get_request_model()(**arguments)

        path_error = self._validate_file_paths(request)
        if path_error:
            self._raise_tool_error(path_error)

        # Validate cwd parameter
        cwd_path = Path(request.cwd)
        if not cwd_path.is_absolute():
            self._raise_tool_error(f"cwd must be an absolute path, got: {request.cwd}")
        if not cwd_path.exists():
            self._raise_tool_error(f"cwd path does not exist: {request.cwd}")
        if not cwd_path.is_dir():
            self._raise_tool_error(f"cwd must be a directory, not a file: {request.cwd}")

        # Environment variable override takes precedence over request parameter
        cli_override = get_env("CLINK_CLI_OVERRIDE")
        if cli_override:
            if cli_override not in self._cli_names:
                logger.warning(
                    "CLINK_CLI_OVERRIDE=%s not in configured clients %s, ignoring override",
                    cli_override,
                    self._cli_names,
                )
                selected_cli = request.cli_name or self._default_cli_name
            else:
                logger.debug("CLINK_CLI_OVERRIDE forcing CLI: %s", cli_override)
                selected_cli = cli_override
        else:
            selected_cli = request.cli_name or self._default_cli_name

        if not selected_cli:
            self._raise_tool_error("No CLI clients are configured for clink.")

        try:
            client_config = self._registry.get_client(selected_cli)
        except KeyError as exc:
            self._raise_tool_error(str(exc))

        # Save client reference for use in prompt preparation
        self._current_client = client_config

        # Handle agent roles specially
        agent_definition: AgentDefinition | None = None
        if is_agent_role(request.role):
            try:
                agent_definition = self._resolve_agent_role(request.role, project_dir=cwd_path)
            except AgentDefinitionError as exc:
                self._raise_tool_error(str(exc))

            # Create synthetic role config for agent definitions
            role_config = ResolvedCLIRole(
                name=agent_definition.name if agent_definition else "agent",
                prompt_path=agent_definition.path if agent_definition else Path("<none>"),
                role_args=[],
                description=f"Agent: {agent_definition.name}" if agent_definition else "General purpose agent",
            )
            # Base prompt + agent-specific content
            base_prompt = _load_base_prompt()
            agent_prompt = agent_definition.get_system_prompt() if agent_definition else ""
            system_prompt_text = f"{base_prompt}\n\n{agent_prompt}".strip() if agent_prompt else base_prompt
        else:
            # Standard role resolution (legacy roles use their own prompt without base)
            try:
                role_config = client_config.get_role(request.role)
            except KeyError as exc:
                self._raise_tool_error(str(exc))
            system_prompt_text = role_config.prompt_path.read_text(encoding="utf-8")

        absolute_file_paths = self.get_request_files(request)
        images = self.get_request_media(request)
        continuation_id = self.get_request_continuation_id(request)

        self._model_context = arguments.get("_model_context")
        include_system_prompt = not self._use_external_system_prompt(client_config)

        try:
            prompt_text = await self._prepare_prompt_for_role(
                request,
                role_config,
                system_prompt=system_prompt_text,
                include_system_prompt=include_system_prompt,
            )
        except Exception as exc:
            logger.exception("Failed to prepare clink prompt")
            self._raise_tool_error(f"Failed to prepare prompt: {exc}")

        resolved_schema = self._resolve_json_schema(request.json_schema, request.cwd)

        session_id = self._current_arguments.get("_clink_session_id")

        agent = create_agent(client_config)
        try:
            result = await agent.run(
                role=role_config,
                prompt=prompt_text,
                system_prompt=system_prompt_text if system_prompt_text.strip() else None,
                files=absolute_file_paths,
                images=images,
                json_schema=resolved_schema,
                model=request.model,
                cwd=request.cwd,
                session_id=session_id,
            )
        except CLIAgentError as exc:
            metadata = self._build_error_metadata(client_config, exc)
            self._raise_tool_error(
                f"CLI '{client_config.name}' execution failed: {exc}",
                metadata=metadata,
            )

        response_session_id = result.parsed.metadata.get("session_id")
        if response_session_id and continuation_id:
            from utils.conversation_memory import set_thread_session_id

            set_thread_session_id(continuation_id, response_session_id)

        metadata = self._build_success_metadata(client_config, role_config, result)
        metadata = self._prune_metadata(metadata, client_config, reason="normal")

        content = result.parsed.content

        model_info = {
            "provider": client_config.name,
            "model_name": result.parsed.metadata.get("model_used"),
        }

        if continuation_id:
            try:
                self._record_assistant_turn(continuation_id, content, request, model_info)
            except Exception:
                logger.debug("Failed to record assistant turn for continuation %s", continuation_id, exc_info=True)

        continuation_offer = self._create_continuation_offer(request, model_info)
        if continuation_offer:
            tool_output = self._create_continuation_offer_response(
                content,
                continuation_offer,
                request,
                model_info,
            )
            tool_output.metadata = self._merge_metadata(tool_output.metadata, metadata)
            if resolved_schema is not None:
                tool_output.content_type = "json"
        else:
            content_type = "json" if resolved_schema is not None else "text"
            tool_output = ToolOutput(
                status="success",
                content=content,
                content_type=content_type,
                metadata=metadata,
            )

        return [TextContent(type="text", text=tool_output.model_dump_json())]

    async def prepare_prompt(self, request) -> str:
        cli_name = request.cli_name or self._default_cli_name
        if not cli_name:
            raise ValueError("No CLI name specified and no default configured")
        client_config = self._registry.get_client(cli_name)

        # Handle agent roles specially
        project_dir = Path(request.cwd) if hasattr(request, "cwd") and request.cwd else None
        if is_agent_role(request.role):
            agent_definition = self._resolve_agent_role(request.role, project_dir=project_dir)
            role_config = ResolvedCLIRole(
                name=agent_definition.name if agent_definition else "agent",
                prompt_path=agent_definition.path if agent_definition else Path("<none>"),
                role_args=[],
                description=f"Agent: {agent_definition.name}" if agent_definition else "General purpose agent",
            )
            # Base prompt + agent-specific content
            base_prompt = _load_base_prompt()
            agent_prompt = agent_definition.get_system_prompt() if agent_definition else ""
            system_prompt_text = f"{base_prompt}\n\n{agent_prompt}".strip() if agent_prompt else base_prompt
        else:
            role_config = client_config.get_role(request.role)
            system_prompt_text = role_config.prompt_path.read_text(encoding="utf-8")

        include_system_prompt = not self._use_external_system_prompt(client_config)
        return await self._prepare_prompt_for_role(
            request,
            role_config,
            system_prompt=system_prompt_text,
            include_system_prompt=include_system_prompt,
        )

    async def _prepare_prompt_for_role(
        self,
        request: CLinkRequest,
        role: ResolvedCLIRole,
        *,
        system_prompt: str,
        include_system_prompt: bool,
    ) -> str:
        """Load the role prompt and assemble the final user message."""
        self._active_system_prompt = system_prompt
        try:
            user_content = self.handle_prompt_file_with_fallback(request).strip()
            guidance = self._agent_capabilities_guidance(self._current_client.name)
            file_section = self._format_file_references(self.get_request_files(request))

            sections: list[str] = []
            active_prompt = self.get_system_prompt().strip()
            if include_system_prompt and active_prompt:
                sections.append(active_prompt)
            sections.append(guidance)
            sections.append("=== USER REQUEST ===\n" + user_content)
            if file_section:
                sections.append("=== FILE REFERENCES ===\n" + file_section)
            sections.append("Provide your response below using your own CLI tools as needed:")
            return "\n\n".join(sections)
        finally:
            self._active_system_prompt = ""

    def _use_external_system_prompt(self, client: ResolvedCLIClient) -> bool:
        runner_name = (client.runner or client.name).lower()
        return runner_name == "claude"

    def _build_success_metadata(
        self,
        client: ResolvedCLIClient,
        role: ResolvedCLIRole,
        result: AgentOutput,
    ) -> dict[str, Any]:
        """Capture execution metadata for successful CLI calls."""
        metadata: dict[str, Any] = {
            "cli_name": client.name,
            "role": role.name,
            "command": result.sanitized_command,
            "duration_seconds": round(result.duration_seconds, 3),
            "parser": result.parser_name,
            "return_code": result.returncode,
        }
        metadata.update(result.parsed.metadata)

        if result.stderr.strip():
            metadata.setdefault("stderr", result.stderr.strip())
        if result.output_file_content and "raw" not in metadata:
            metadata["raw_output_file"] = result.output_file_content
        return metadata

    def _merge_metadata(self, base: dict[str, Any] | None, extra: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base or {})
        merged.update(extra)
        return merged

    def _prune_metadata(
        self,
        metadata: dict[str, Any],
        client: ResolvedCLIClient,
        *,
        reason: str,
    ) -> dict[str, Any]:
        """Remove heavy debugging fields from metadata before returning to MCP client.

        Drops: events, raw, raw_events, raw_output_file, command.
        Preserves: cli_name, role, model_used, duration_seconds, session_id,
        return_code, usage, and all offload/error fields.
        """
        cleaned = dict(metadata)
        heavy_fields = ("events", "raw", "raw_events", "raw_output_file", "command")
        removed = []

        for field in heavy_fields:
            value = cleaned.pop(field, None)
            if value is not None:
                removed.append(field)
                if logger.isEnabledFor(logging.DEBUG):
                    if isinstance(value, (dict, list)):
                        logger.debug(
                            "Pruned '%s' from %s metadata (%s): %d items",
                            field,
                            client.name,
                            reason,
                            len(value),
                        )
                    else:
                        logger.debug(
                            "Pruned '%s' from %s metadata (%s): %d chars",
                            field,
                            client.name,
                            reason,
                            len(str(value)),
                        )

        if removed:
            cleaned[f"pruned_for_{reason}"] = removed

        return cleaned

    def _build_error_metadata(self, client: ResolvedCLIClient, exc: CLIAgentError) -> dict[str, Any]:
        """Assemble metadata for failed CLI calls."""
        metadata: dict[str, Any] = {
            "cli_name": client.name,
            "return_code": exc.returncode,
        }
        if exc.stdout:
            metadata["stdout"] = exc.stdout.strip()
        if exc.stderr:
            metadata["stderr"] = exc.stderr.strip()
        return metadata

    def _raise_tool_error(self, message: str, metadata: dict[str, Any] | None = None) -> NoReturn:
        error_output = ToolOutput(status="error", content=message, content_type="text", metadata=metadata)
        raise ToolExecutionError(error_output.model_dump_json())

    def _agent_capabilities_guidance(self, cli_name: str) -> str:
        return (
            f"You are operating through the {cli_name} CLI agent. You have access to read-only "
            "CLI capabilities — reading files, listing directories, web searches, grep, and any other "
            "non-destructive tools. Gather current information yourself and deliver the final answer without "
            "asking the PAL MCP host to perform searches or file reads."
        )

    def _format_file_references(self, files: list[str]) -> str:
        if not files:
            return ""

        references: list[str] = []
        for file_path in files:
            try:
                path = Path(file_path)
                stat = path.stat()
                modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
                size = stat.st_size
                references.append(f"- {file_path} (last modified {modified}, {size} bytes)")
            except OSError:
                references.append(f"- {file_path} (unavailable)")
        return "\n".join(references)

    def _resolve_json_schema(self, schema_input: dict | str | None, cwd: str) -> dict | None:
        """Resolve json_schema parameter to a dict.

        Accepts dict (pass-through), file path, or inline JSON string.
        File paths are resolved relative to cwd.
        """
        if schema_input is None:
            return None

        if isinstance(schema_input, dict):
            return schema_input

        schema_str = schema_input.strip()

        potential_path = Path(schema_str)
        if not potential_path.is_absolute():
            potential_path = Path(cwd) / potential_path

        if potential_path.is_file():
            try:
                content = potential_path.read_text(encoding="utf-8")
                schema_dict = json.loads(content)
            except json.JSONDecodeError as exc:
                self._raise_tool_error(f"json_schema file '{potential_path}' contains invalid JSON: {exc}")
            except Exception as exc:
                self._raise_tool_error(f"Failed to read json_schema file '{potential_path}': {exc}")
        else:
            try:
                schema_dict = json.loads(schema_str)
            except json.JSONDecodeError as exc:
                self._raise_tool_error(f"json_schema is neither a valid file path nor valid JSON: {exc}")

        if not isinstance(schema_dict, dict):
            self._raise_tool_error(f"json_schema must resolve to a JSON object, got {type(schema_dict).__name__}")

        return schema_dict

    def _resolve_agent_role(self, role: str | None, project_dir: Path | None = None) -> AgentDefinition | None:
        """Resolve agent role to agent definition.

        Supports patterns:
        - "agent" - plain general purpose
        - "agent:researcher" - named agent from .claude/agents/
        - "agent:/path/to/agent.md" - agent from absolute file path
        - "agent:./relative/agent.md" - agent from path relative to cwd

        Args:
            role: The role string to resolve
            project_dir: The project directory (resolved cwd) used for agent
                         name search and relative path resolution.
        """
        if role is None or role == "agent":
            logger.debug("Using general purpose agent (no definition)")
            return None

        definition = parse_agent_role(role)
        if not definition:
            logger.debug("Using general purpose agent (no definition after parse)")
            return None

        resolved_project_dir = project_dir if project_dir is not None else Path.cwd()
        return load_agent_definition(definition, project_dir=resolved_project_dir)
