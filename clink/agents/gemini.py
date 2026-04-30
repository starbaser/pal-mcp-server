"""Gemini-specific CLI agent hooks."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import Any

from clink.models import ResolvedCLIClient, ResolvedCLIRole
from clink.parsers.base import ParsedCLIResponse

from .base import AgentOutput, BaseCLIAgent, CLIAgentError

_RETRY_DELAYS = [60, 300, 600]  # 1m, 5m, 10m


def _is_retryable_capacity_error(stderr: str) -> bool:
    """Check for transient capacity errors (NOT hard quota exhaustion)."""
    return "RetryableQuotaError" in stderr or "No capacity available" in stderr


class GeminiAgent(BaseCLIAgent):
    """Gemini-specific behaviour.

    Gemini CLI ingests files via ``@/path/to/file`` directives prepended to
    the prompt text (sent over stdin).  This agent overrides ``run`` to inject
    those directives for every attached file and media path.

    Transient capacity errors (``RetryableQuotaError``) are retried up to
    3 times with escalating backoff (1m, 5m, 10m).  Hard quota exhaustion
    (``TerminalQuotaError``) is returned immediately.
    """

    def __init__(self, client: ResolvedCLIClient):
        super().__init__(client)

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
        session_id: str | None = None,
    ) -> AgentOutput:
        prompt = self._prepend_file_directives(prompt, files=files, images=images)
        run_kwargs = dict(
            role=role, prompt=prompt, system_prompt=system_prompt,
            files=files, images=images, json_schema=json_schema,
            model=model, cwd=cwd, session_id=session_id,
        )

        last_error: CLIAgentError | None = None
        for attempt in range(1 + len(_RETRY_DELAYS)):
            try:
                return await super().run(**run_kwargs)
            except CLIAgentError as exc:
                if not _is_retryable_capacity_error(exc.stderr):
                    raise
                last_error = exc

                if attempt >= len(_RETRY_DELAYS):
                    self._logger.warning(
                        "Gemini capacity unavailable — all %d retries exhausted", len(_RETRY_DELAYS),
                    )
                    raise

                delay = _RETRY_DELAYS[attempt]
                self._logger.warning(
                    "Gemini capacity unavailable (attempt %d/%d) — retrying in %ds",
                    attempt + 1, len(_RETRY_DELAYS), delay,
                )
                await asyncio.sleep(delay)

        raise last_error  # unreachable, satisfies type checker

    @staticmethod
    def _prepend_file_directives(
        prompt: str,
        *,
        files: Sequence[str],
        images: Sequence[str],
    ) -> str:
        """Prepend ``@path`` directives so Gemini CLI reads file content inline."""
        directives: list[str] = []
        for path in images:
            directives.append(f"@{path}")
        for path in files:
            directives.append(f"@{path}")
        if not directives:
            return prompt
        return " ".join(directives) + " " + prompt

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
        combined = "\n".join(part for part in (stderr, stdout) if part)
        if not combined:
            return None

        # Stack traces may contain non-JSON JavaScript objects (unquoted
        # keys) that appear before the actual JSON error response.  Scan
        # brace-delimited blocks from right to left, accepting the first
        # one that parses as valid JSON and carries an "error" or
        # "session_id" key.
        payload: dict[str, Any] | None = None
        brace_index: int = -1
        search_pos = len(combined)
        while search_pos > 0:
            search_pos = combined.rfind("{", 0, search_pos)
            if search_pos == -1:
                break
            try:
                candidate = json.loads(combined[search_pos:])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate.get("error"), dict) or "session_id" in candidate:
                payload = candidate
                brace_index = search_pos
                break

        if payload is None:
            return None

        error_block = payload.get("error")
        if not isinstance(error_block, dict):
            return None

        code = error_block.get("code")
        err_type = error_block.get("type")
        detail_message = error_block.get("message")

        prologue = combined[:brace_index].strip()
        lines: list[str] = []
        if prologue and (not detail_message or prologue not in detail_message):
            lines.append(prologue)
        if detail_message:
            lines.append(detail_message)

        header = "Gemini CLI reported a tool failure"
        if code:
            header = f"{header} ({code})"
        elif err_type:
            header = f"{header} ({err_type})"

        content_lines = [header.rstrip(".") + "."]
        content_lines.extend(lines)
        message = "\n".join(content_lines).strip()

        metadata = {
            "cli_error_recovered": True,
            "cli_error_code": code,
            "cli_error_type": err_type,
            "cli_error_payload": payload,
        }

        parsed = ParsedCLIResponse(content=message or header, metadata=metadata)
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
