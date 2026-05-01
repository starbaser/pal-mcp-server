"""Execute configured CLI agents for the clink tool and parse output."""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import shutil
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from clink.constants import DEFAULT_STREAM_LIMIT
from clink.models import ResolvedCLIClient, ResolvedCLIRole
from clink.parsers import BaseParser, ParsedCLIResponse, ParserError, get_parser

logger = logging.getLogger("clink.agent")


@dataclass
class AgentOutput:
    """Container returned by CLI agents after successful execution."""

    parsed: ParsedCLIResponse
    sanitized_command: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float
    parser_name: str
    output_file_content: str | None = None


class CLIAgentError(RuntimeError):
    """Raised when a CLI agent fails (non-zero exit, timeout, parse errors)."""

    def __init__(self, message: str, *, returncode: int | None = None, stdout: str = "", stderr: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class BaseCLIAgent:
    """Execute a configured CLI command and parse its output."""

    def __init__(self, client: ResolvedCLIClient):
        self.client = client
        self._parser: BaseParser = get_parser(client.parser)
        self._logger = logging.getLogger(f"clink.runner.{client.name}")

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
        # Files and images are already embedded into the prompt by the tool; they are
        # accepted here only to keep parity with SimpleTool callers.
        _ = (files, images, json_schema)
        # The runner simply executes the configured CLI command for the selected role.
        command = self._build_command(role=role, system_prompt=system_prompt, model=model, session_id=session_id)
        env = self._build_environment()

        # Resolve executable path for cross-platform compatibility (especially Windows)
        executable_name = command[0]
        resolved_executable = shutil.which(executable_name)
        if resolved_executable is None:
            raise CLIAgentError(
                f"Executable '{executable_name}' not found in PATH for CLI '{self.client.name}'. "
                f"Ensure the command is installed and accessible."
            )
        command[0] = resolved_executable

        sanitized_command = list(command)

        # Use explicit cwd parameter if provided, otherwise fall back to client working_dir
        effective_cwd = cwd if cwd else (str(self.client.working_dir) if self.client.working_dir else None)
        limit = DEFAULT_STREAM_LIMIT

        stdout_text = ""
        stderr_text = ""
        output_file_content: str | None = None
        start_time = time.monotonic()

        output_file_path: Path | None = None
        command_with_output_flag = list(command)

        if self.client.output_to_file:
            fd, tmp_path = tempfile.mkstemp(prefix="clink-", suffix=".json")
            os.close(fd)
            output_file_path = Path(tmp_path)
            flag_template = self.client.output_to_file.flag_template
            try:
                rendered_flag = flag_template.format(path=str(output_file_path))
            except KeyError as exc:  # pragma: no cover - defensive
                raise CLIAgentError(f"Invalid output flag template '{flag_template}': missing placeholder {exc}")
            command_with_output_flag.extend(shlex.split(rendered_flag))
            sanitized_command = list(command_with_output_flag)

        self._logger.debug("Executing CLI command: %s", " ".join(sanitized_command))
        if effective_cwd:
            self._logger.debug("Working directory: %s", effective_cwd)

        prompt_bytes = prompt.encode("utf-8")
        self._logger.info("Sending %d bytes (%d chars) to %s stdin", len(prompt_bytes), len(prompt), self.client.name)

        try:
            process = await asyncio.create_subprocess_exec(
                *command_with_output_flag,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=effective_cwd,
                limit=limit,
                env=env,
            )
        except FileNotFoundError as exc:
            raise CLIAgentError(f"Executable not found for CLI '{self.client.name}': {exc}") from exc

        stdout_bytes, stderr_bytes = await process.communicate(prompt_bytes)

        duration = time.monotonic() - start_time
        return_code = process.returncode
        assert return_code is not None, "returncode should be set after communicate()"
        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")

        self._logger.info(
            "%s completed rc=%d duration=%.1fs stdout=%d stderr=%d chars",
            self.client.name, return_code, duration, len(stdout_text), len(stderr_text),
        )

        if output_file_path and output_file_path.exists():
            output_file_content = output_file_path.read_text(encoding="utf-8", errors="replace")
            if self.client.output_to_file and self.client.output_to_file.cleanup:
                try:
                    output_file_path.unlink()
                except OSError:  # pragma: no cover - best effort cleanup
                    pass

            if output_file_content and not stdout_text.strip():
                stdout_text = output_file_content

        if return_code != 0:
            recovered = self._recover_from_error(
                returncode=return_code,
                stdout=stdout_text,
                stderr=stderr_text,
                sanitized_command=sanitized_command,
                duration_seconds=duration,
                output_file_content=output_file_content,
            )
            if recovered is not None:
                return recovered

        if return_code != 0:
            # Extract a meaningful snippet from stderr so the error message
            # is not opaque when recovery fails to parse structured output.
            stderr_snippet = ""
            if stderr_text:
                # Look for an error-like line, preferring lines containing
                # 'Error' or 'error'. Skip JSON, stack frames, JS objects.
                for line in reversed(stderr_text.strip().splitlines()):
                    stripped = line.strip()
                    if not stripped:
                        continue
                    if stripped[0] in ("{", "}", '"') or stripped.startswith(("at ", "  ")):
                        continue
                    # Prefer the actual error line if we've seen one
                    if "error" in stripped.lower():
                        stderr_snippet = stripped[:300]
                        break
                    if not stderr_snippet:
                        stderr_snippet = stripped[:300]
                if not stderr_snippet:
                    stderr_snippet = stderr_text.strip().splitlines()[-1].strip()[:300]

            message = f"CLI '{self.client.name}' exited with status {return_code}"
            if stderr_snippet:
                message += f": {stderr_snippet}"

            raise CLIAgentError(
                message,
                returncode=return_code,
                stdout=stdout_text,
                stderr=stderr_text,
            )

        try:
            parsed = self._parser.parse(stdout_text, stderr_text)
        except ParserError as exc:
            raise CLIAgentError(
                f"Failed to parse output from CLI '{self.client.name}': {exc}",
                returncode=return_code,
                stdout=stdout_text,
                stderr=stderr_text,
            ) from exc

        return AgentOutput(
            parsed=parsed,
            sanitized_command=sanitized_command,
            returncode=return_code,
            stdout=stdout_text,
            stderr=stderr_text,
            duration_seconds=duration,
            parser_name=self._parser.name,
            output_file_content=output_file_content,
        )

    def _build_command(
        self,
        *,
        role: ResolvedCLIRole,
        system_prompt: str | None,
        model: str | None = None,
        session_id: str | None = None,
    ) -> list[str]:
        base = list(self.client.executable)
        base.extend(self.client.internal_args)
        base.extend(self.client.config_args)

        if model:
            base = self._filter_flag(base, "--model")
            base.extend(["--model", model])

        base.extend(role.role_args)

        if session_id:
            base.extend(["--resume", session_id])

        return base

    @staticmethod
    def _filter_flag(command: list[str], flag: str) -> list[str]:
        """Remove a flag and its value from command list."""
        result = []
        skip_next = False
        for item in command:
            if skip_next:
                skip_next = False
                continue
            if item == flag:
                skip_next = True
                continue
            result.append(item)
        return result

    def _build_environment(self) -> dict[str, str]:
        from utils.env import expand_env_vars

        env = os.environ.copy()

        # Strip PAL's own venv so child CLIs resolve executables from the
        # caller's real PATH — prevents agentic subprocesses (e.g. Gemini
        # --yolo) from using PAL's python3 to create orphan venvs in the
        # user's project directories.
        venv = env.pop("VIRTUAL_ENV", None)
        if venv and "PATH" in env:
            env["PATH"] = ":".join(p for p in env["PATH"].split(":") if venv not in p)

        # Strip Claude Code's own session marker so nested CLI invocations don't
        # mistake themselves for running inside a Claude Code session.
        env.pop("CLAUDECODE", None)

        # Strip ccproxy sentinel keys — these are only valid when routed through
        # ccproxy's MITM layer; child CLI processes connect directly to external APIs.
        for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
            value = env.get(var, "")
            if "ccproxy" in value:
                env.pop(var, None)

        # Set marker variable to identify clink invocations
        env["PAL_MCP_CLINK"] = "1"

        # Prevent Gemini CLI's sandbox relaunch path from double-reading stdin.
        # When spawned with piped stdin, Phase 1 (sandbox detection) reads stdin
        # and injects it as --prompt, then Phase 2 (inside sandbox) reads stdin
        # again from the still-open pipe — duplicating the prompt in the API request
        # as two consecutive user messages, which Gemini rejects as INVALID_ARGUMENT.
        env["GEMINI_CLI_NO_RELAUNCH"] = "1"

        # Node.js CLIs (Gemini) default to ~4GB heap which OOMs on large prompts.
        node_opts = env.get("NODE_OPTIONS", "")
        if "--max-old-space-size" not in node_opts:
            env["NODE_OPTIONS"] = f"{node_opts} --max-old-space-size=32768".strip()

        expanded_client_env = expand_env_vars(self.client.env)
        env.update(expanded_client_env)
        return env

    # ------------------------------------------------------------------
    # Error recovery hooks
    # ------------------------------------------------------------------

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
        """Hook for subclasses to convert CLI errors into successful outputs.

        Return an AgentOutput to treat the failure as success, or None to signal
        that normal error handling should proceed.
        """

        return None
