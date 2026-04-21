"""Claude-specific CLI agent hooks."""

from __future__ import annotations

import json
from collections.abc import Sequence

from clink.models import ResolvedCLIRole
from clink.parsers.base import ParserError

from .base import AgentOutput, BaseCLIAgent, CLIAgentError


class ClaudeAgent(BaseCLIAgent):
    """Claude CLI agent with system-prompt injection and json-schema support."""

    async def run(
        self,
        *,
        role: ResolvedCLIRole,
        prompt: str,
        system_prompt: str | None = None,
        files: Sequence[str],
        images: Sequence[str],
        json_schema: dict | None = None,
        model: str | None = None,
        cwd: str | None = None,
    ) -> AgentOutput:
        self._json_schema = json_schema
        return await super().run(
            role=role,
            prompt=prompt,
            system_prompt=system_prompt,
            files=files,
            images=images,
            json_schema=json_schema,
            model=model,
            cwd=cwd,
        )

    def _build_command(
        self,
        *,
        role: ResolvedCLIRole,
        system_prompt: str | None,
        json_schema: dict | None = None,
        model: str | None = None,
        session_id: str | None = None,
    ) -> list[str]:
        command = list(self.client.executable)
        command.extend(self.client.internal_args)
        command.extend(self.client.config_args)

        if model:
            command = self._filter_flag(command, "--model")
            command.extend(["--model", model])

        if system_prompt and "--append-system-prompt" not in self.client.config_args:
            command.extend(["--append-system-prompt", system_prompt])

        schema_to_use = json_schema if json_schema is not None else getattr(self, "_json_schema", None)
        if schema_to_use is not None:
            try:
                schema_json = json.dumps(schema_to_use)
            except (TypeError, ValueError) as exc:
                raise CLIAgentError(
                    f"Failed to serialize json_schema for CLI '{self.client.name}': {exc}. "
                    f"Schema must be JSON-serializable. Received type: {type(schema_to_use).__name__}"
                ) from exc
            command.extend(["--json-schema", schema_json])

        command.extend(role.role_args)

        if session_id:
            command.extend(["--resume", session_id])

        return command

    def _recover_from_error(
        self,
        *,
        returncode: int,
        stdout: str,
        stderr: str,
        sanitized_command: list[str],
        duration_seconds: float,
        output_file_content: str | None,
    ) -> AgentOutput | None:
        try:
            parsed = self._parser.parse(stdout, stderr)
        except ParserError:
            return None

        return AgentOutput(
            parsed=parsed,
            sanitized_command=sanitized_command,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration_seconds,
            parser_name=self._parser.name,
            output_file_content=output_file_content,
        )
