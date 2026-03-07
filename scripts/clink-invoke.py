#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pal-mcp-server @ file:///home/starbased/dev/opt/pal-mcp-server",
#     "pydantic>=2.0",
#     "tyro>=0.8.0",
#     "rich>=13.0",
#     "attrs>=23.0",
#     "jsonschema>=4.0",
# ]
# ///
"""
Standalone clink invocation wrapper.

Reuses PAL MCP's clink infrastructure (registry, agent definitions, command
building, environment setup, parsing) to invoke external AI CLIs directly
from the shell. Supports the full agent role system including agent:name
lookups with @import resolution.

Modes:
  - Shell command output (default): prints an eval-ready command
  - Execute (--execute): runs the CLI as a subprocess and parses output
  - Preview (--preview): shows the full command without executing
  - Interactive (omit prompt): launches CLI without piped stdin

Examples:
  # Print shell command for claude with default role
  clink-invoke.py claude "Explain this codebase"

  # Execute gemini with a named agent from .claude/agents/
  clink-invoke.py gemini "Research topic X" --role agent:researcher --execute

  # Preview claude with agent from absolute path
  clink-invoke.py claude "Refactor auth" --role agent:/home/user/.claude/agents/refactorer.md --preview

  # Execute with JSON schema (Claude only)
  clink-invoke.py claude "Extract entities" --json-schema schema.json --execute

  # Interactive mode (no prompt, launches CLI directly)
  clink-invoke.py gemini --execute

  # Override model and working directory
  clink-invoke.py claude "Fix tests" --model opus --cwd /home/user/project --execute
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shlex
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

import attrs
import jsonschema
import tyro
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.syntax import Syntax
from rich.table import Table

from clink import get_registry
from clink.agent_definitions import (
    AgentDefinitionError,
    is_agent_role,
    load_agent_definition,
    parse_agent_role,
)
from clink.agents import create_agent
from clink.agents.claude import ClaudeAgent
from clink.constants import BUILTIN_PROMPTS_DIR
from clink.models import ResolvedCLIClient, ResolvedCLIRole

console = Console()
err_console = Console(stderr=True)

SESSION_DIR = Path.home() / ".clink_sessions"
BASE_PROMPT_PATH = BUILTIN_PROMPTS_DIR / "default.txt"


# ============================================================================
# Data Models
# ============================================================================


@attrs.define
class ConversationTurn:
    role: str
    content: str
    files: list[str] = attrs.field(factory=list)
    timestamp: str = attrs.field(factory=lambda: datetime.now(timezone.utc).isoformat())
    cli_name: str | None = None
    model_name: str | None = None


@attrs.define
class ConversationState:
    id: str
    created_at: str
    last_updated: str
    turns: list[ConversationTurn] = attrs.field(factory=list)

    def save(self) -> None:
        SESSION_DIR.mkdir(exist_ok=True)
        (SESSION_DIR / f"{self.id}.json").write_text(json.dumps(attrs.asdict(self), indent=2))

    @classmethod
    def load(cls, conversation_id: str) -> ConversationState:
        session_file = SESSION_DIR / f"{conversation_id}.json"
        if not session_file.exists():
            raise FileNotFoundError(f"Session not found: {conversation_id}")
        data = json.loads(session_file.read_text())
        turns = [ConversationTurn(**t) for t in data.get("turns", [])]
        return cls(id=data["id"], created_at=data["created_at"], last_updated=data["last_updated"], turns=turns)

    @classmethod
    def create_new(cls) -> ConversationState:
        now = datetime.now(timezone.utc).isoformat()
        return cls(id=str(uuid.uuid4()), created_at=now, last_updated=now)


# ============================================================================
# Agent Role Resolution (mirrors tools/clink.py logic)
# ============================================================================


def load_base_prompt() -> str:
    """Load the base system prompt applied to all agent roles."""
    try:
        return BASE_PROMPT_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def resolve_role(
    client: ResolvedCLIClient,
    role: str | None,
    cwd: Path,
) -> tuple[ResolvedCLIRole, str]:
    """Resolve a role string to a ResolvedCLIRole and system prompt text.

    Handles three patterns:
      - Standard roles (default, planner, codereviewer)
      - agent:name  → search .claude/agents/ by frontmatter name
      - agent:/path → load from absolute path

    Returns (role_config, system_prompt_text).
    """
    if is_agent_role(role):
        definition_str = parse_agent_role(role) if role else None

        agent_definition = None
        if definition_str:
            try:
                agent_definition = load_agent_definition(definition_str, project_dir=cwd)
            except AgentDefinitionError as exc:
                err_console.print(f"[red]Error:[/red] {exc}")
                sys.exit(1)

        role_config = ResolvedCLIRole(
            name=agent_definition.name if agent_definition else "agent",
            prompt_path=agent_definition.path if agent_definition else Path("<none>"),
            role_args=[],
            description=f"Agent: {agent_definition.name}" if agent_definition else "General purpose agent",
        )

        base_prompt = load_base_prompt()
        agent_prompt = agent_definition.get_system_prompt() if agent_definition else ""
        system_prompt_text = f"{base_prompt}\n\n{agent_prompt}".strip() if agent_prompt else base_prompt
        return role_config, system_prompt_text

    # Standard role
    try:
        role_config = client.get_role(role)
    except KeyError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    system_prompt_text = role_config.prompt_path.read_text(encoding="utf-8") if role_config.prompt_path else ""
    return role_config, system_prompt_text


# ============================================================================
# Command & Environment Building
# ============================================================================


def build_command(
    agent,
    role_config: ResolvedCLIRole,
    system_prompt: str | None,
    json_schema: dict | None = None,
    model: str | None = None,
) -> list[str]:
    """Build the CLI command using the agent's _build_command method."""
    kwargs: dict[str, Any] = {"role": role_config, "system_prompt": system_prompt}
    if isinstance(agent, ClaudeAgent):
        kwargs["json_schema"] = json_schema
        kwargs["model"] = model
    elif model:
        kwargs["model"] = model
    return agent._build_command(**kwargs)


def build_environment(client: ResolvedCLIClient) -> dict[str, str]:
    """Build subprocess environment: caller's env + PAL_MCP_CLINK + client vars.

    Strips uv's virtual environment modifications so the subprocess
    resolves executables from the caller's real PATH.
    """
    from utils.env import expand_env_vars

    env = os.environ.copy()

    # Strip uv's venv from the environment
    venv = env.pop("VIRTUAL_ENV", None)
    if venv and "PATH" in env:
        env["PATH"] = ":".join(p for p in env["PATH"].split(":") if venv not in p)

    env["PAL_MCP_CLINK"] = "1"
    env.update(expand_env_vars(client.env))
    return env


def resolve_executable(command: list[str], env: dict[str, str]) -> list[str]:
    """Resolve the first element of command to an absolute path."""
    resolved = shutil.which(command[0], path=env.get("PATH"))
    if not resolved:
        err_console.print(f"[red]Error:[/red] Executable '{command[0]}' not found in PATH")
        sys.exit(1)
    return [resolved] + command[1:]


# ============================================================================
# Shell Command Rendering
# ============================================================================


def render_shell_command(
    command: list[str],
    prompt: str | None,
    client_env: dict[str, str],
) -> str:
    """Render an eval-ready shell command string.

    Format: ENV=val ... command args [< <(cat <<'DELIM' prompt DELIM)]
    """
    from utils.env import expand_env_vars

    parts = []

    expanded = expand_env_vars(client_env)
    for key, value in expanded.items():
        if not key.replace("_", "").isalnum():
            raise ValueError(f"Invalid environment variable name: {key}")
        parts.append(f"{key}={shlex.quote(value)}")

    parts.extend(shlex.quote(arg) for arg in command)

    if prompt:
        tag = hashlib.sha256(f"{prompt}{time.time_ns()}".encode()).hexdigest()[:16].upper()
        delimiter = f"CLINK_EOF_{tag}"
        parts.append("<")
        parts.append(f"<(cat <<'{delimiter}'\n{prompt}\n{delimiter}\n)")

    return " ".join(parts)


# ============================================================================
# Prompt Assembly
# ============================================================================


def build_prompt_with_files(prompt: str, files: list[Path]) -> str:
    parts = [prompt]
    for fp in files:
        if fp.exists():
            parts.append(f"\n\n# File: {fp}\n\n```\n{fp.read_text()}\n```")
        else:
            err_console.print(f"[yellow]Warning:[/yellow] File not found: {fp}")
    return "\n".join(parts)


def build_prompt_with_history(
    user_prompt: str,
    conversation: ConversationState,
    files: list[Path],
) -> tuple[str, list[Path]]:
    parts = []
    if conversation.turns:
        parts.append("=== Previous Conversation ===\n")
        for i, turn in enumerate(conversation.turns, 1):
            parts.append(f"--- Turn {i} ({turn.role}) ---")
            if turn.cli_name:
                parts.append(f"[CLI: {turn.cli_name}, Model: {turn.model_name or 'unknown'}]")
            parts.append(turn.content)
            parts.append("")
        parts.append("=== End of Previous Conversation ===\n")

    parts.append("=== Current Request ===")
    parts.append(user_prompt)

    all_files = list(files)
    for turn in reversed(conversation.turns):
        for fp in turn.files:
            if fp not in [str(f) for f in all_files]:
                all_files.append(Path(fp))

    return "\n\n".join(parts), all_files


# ============================================================================
# Execution
# ============================================================================


async def execute_subprocess(
    command: list[str],
    env: dict[str, str],
    prompt: str | None,
    cwd: str | None,
    timeout: int,
    cli_name: str,
    verbose: bool = False,
) -> tuple[str, str, int]:
    """Run CLI subprocess, return (stdout, stderr, returncode)."""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task(f"Executing {cli_name}...", total=None)

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE if prompt else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )

            stdin_bytes = prompt.encode("utf-8") if prompt else None
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(stdin_bytes),
                timeout=timeout,
            )

        except asyncio.TimeoutError:
            err_console.print(f"[red]Error:[/red] Timed out after {timeout}s")
            sys.exit(1)
        except FileNotFoundError:
            err_console.print(f"[red]Error:[/red] Executable not found: {command[0]}")
            sys.exit(1)

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")

    if verbose and stderr:
        err_console.print(f"\n[yellow]stderr:[/yellow]\n{stderr}")

    return stdout, stderr, process.returncode or 0


def parse_output(stdout: str, parser_name: str) -> dict[str, Any]:
    """Parse CLI output based on parser type."""
    if parser_name in ("claude_json", "gemini_json"):
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            err_console.print("[red]Error:[/red] Failed to parse JSON output")
            if stdout.strip():
                console.print("\n[yellow]Raw output:[/yellow]")
                console.print(stdout[:2000])
            sys.exit(1)
    elif parser_name == "codex_jsonl":
        # Accumulate agent_message items from JSONL
        messages = []
        for line in stdout.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                if event.get("type") == "agent_message":
                    messages.append(event.get("content", ""))
            except json.JSONDecodeError:
                continue
        return {"content": "\n".join(messages) if messages else stdout, "raw": not messages}

    return {"content": stdout, "raw": True}


def extract_result_text(output_data: dict[str, Any]) -> str:
    """Extract human-readable text from parsed output."""
    if "result" in output_data:
        return output_data["result"]
    if "structured_output" in output_data:
        return json.dumps(output_data["structured_output"], indent=2)
    if "response" in output_data:
        return output_data["response"]
    if "content" in output_data:
        return output_data["content"]
    return json.dumps(output_data, indent=2)


# ============================================================================
# Session Management
# ============================================================================


def list_sessions() -> None:
    if not SESSION_DIR.exists():
        console.print("[yellow]No sessions found[/yellow]")
        return
    sessions = list(SESSION_DIR.glob("*.json"))
    if not sessions:
        console.print("[yellow]No sessions found[/yellow]")
        return

    table = Table(title="Conversations")
    table.add_column("ID", style="cyan")
    table.add_column("Created", style="green")
    table.add_column("Turns", style="yellow")
    table.add_column("Last CLI", style="magenta")

    for sf in sorted(sessions, key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            c = ConversationState.load(sf.stem)
            table.add_row(
                c.id[:8] + "...",
                c.created_at[:19],
                str(len(c.turns)),
                (c.turns[-1].cli_name if c.turns else "N/A") or "N/A",
            )
        except Exception:
            continue
    console.print(table)


def clean_sessions(older_than: str) -> None:
    if not SESSION_DIR.exists():
        console.print("[yellow]No sessions to clean[/yellow]")
        return
    match = re.match(r"^(\d+)([hdw])$", older_than)
    if not match:
        err_console.print("[red]Error:[/red] Invalid time format. Use: 24h, 7d, 1w")
        sys.exit(1)

    value, unit = int(match.group(1)), match.group(2)
    threshold = value * {"h": 3600, "d": 86400, "w": 604800}[unit]
    now = datetime.now(timezone.utc)
    removed = 0

    for sf in SESSION_DIR.glob("*.json"):
        try:
            c = ConversationState.load(sf.stem)
            age = (now - datetime.fromisoformat(c.created_at)).total_seconds()
            if age > threshold:
                sf.unlink()
                removed += 1
        except Exception:
            continue

    console.print(f"[green]Removed {removed} session(s)[/green]" if removed else "[yellow]No old sessions[/yellow]")


# ============================================================================
# CLI Configuration (tyro)
# ============================================================================


@attrs.define
class Config:
    """Standalone clink CLI invocation wrapper."""

    prompt: Annotated[str | None, tyro.conf.Positional] = None
    """Prompt to send. Omit for interactive mode."""

    cli_name: str = "claude"
    """CLI client to use (claude, glmaude, gemini, codex)."""

    role: str | None = None
    """Role: standard name, 'agent', 'agent:<name>', or 'agent:</path>'."""

    cwd: str | None = None
    """Working directory for the agent. Defaults to $PWD."""

    model: str | None = None
    """Model override (e.g., opus, sonnet, haiku)."""

    files: list[Path] = attrs.field(factory=list)
    """Files to include in the prompt."""

    json_schema: str | None = None
    """JSON schema for structured output (JSON string or file path)."""

    system_prompt: str | None = None
    """Override system prompt entirely (text or file path)."""

    continue_from: str | None = None
    """Continuation ID from a previous session."""

    batch: Path | None = None
    """JSONL file with multiple prompts."""

    chain: bool = False
    """Chain batch prompts (each continues from previous)."""

    execute: bool = False
    """Execute as subprocess (default: output shell command)."""

    preview: bool = False
    """Show command and config without executing."""

    validate_schema: bool = True
    """Validate output against schema when provided."""

    output_file: Path | None = None
    """Save result text to file."""

    list_sessions: bool = False
    """List active sessions."""

    clean_sessions: bool = False
    """Clean old sessions."""

    older_than: str = "24h"
    """Age threshold for session cleanup (24h, 7d, 1w)."""

    verbose: bool = False
    """Verbose debug output."""

    no_color: bool = False
    """Disable colored output."""


# ============================================================================
# Main
# ============================================================================


async def run(config: Config) -> None:
    if config.no_color:
        console.no_color = True
        err_console.no_color = True

    if config.list_sessions:
        list_sessions()
        return
    if config.clean_sessions:
        clean_sessions(config.older_than)
        return
    if config.batch:
        await run_batch(config)
        return

    effective_cwd = Path(config.cwd) if config.cwd else Path.cwd()

    # --- Registry + client ---
    registry = get_registry()
    try:
        client = registry.get_client(config.cli_name)
    except KeyError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        err_console.print(f"Available: {', '.join(registry.list_clients())}")
        sys.exit(1)

    # --- Role + system prompt resolution ---
    if config.system_prompt:
        # Explicit override bypasses role resolution entirely
        sp_path = Path(config.system_prompt)
        system_prompt_text = sp_path.read_text() if sp_path.exists() else config.system_prompt

        try:
            role_config = client.get_role(config.role if not is_agent_role(config.role) else None)
        except KeyError:
            role_config = client.get_role(None)
    else:
        role_config, system_prompt_text = resolve_role(client, config.role, effective_cwd)

    # --- JSON schema ---
    json_schema = None
    if config.json_schema:
        schema_path = Path(config.json_schema)
        raw = schema_path.read_text() if schema_path.exists() else config.json_schema
        try:
            json_schema = json.loads(raw)
        except json.JSONDecodeError:
            err_console.print(f"[red]Error:[/red] Invalid JSON schema: {config.json_schema}")
            sys.exit(1)

    # --- Conversation history ---
    conversation = None
    if config.continue_from:
        try:
            conversation = ConversationState.load(config.continue_from)
            if config.verbose:
                console.print(f"[green]Loaded conversation with {len(conversation.turns)} turns[/green]")
        except FileNotFoundError as exc:
            err_console.print(f"[red]Error:[/red] {exc}")
            sys.exit(1)

    # --- Build prompt ---
    if config.prompt is None:
        full_prompt = None
        all_files = config.files
    elif conversation:
        full_prompt, all_files = build_prompt_with_history(config.prompt, conversation, config.files)
        full_prompt = build_prompt_with_files(full_prompt, all_files)
    else:
        full_prompt = build_prompt_with_files(config.prompt, config.files)
        all_files = config.files

    # --- Build command ---
    agent = create_agent(client)
    command = build_command(agent, role_config, system_prompt_text, json_schema, config.model)

    # --- Environment ---
    env = build_environment(client)
    command = resolve_executable(command, env)

    # --- Verbose / Preview info ---
    if config.verbose or config.preview:
        console.print("\n[cyan]Configuration:[/cyan]")
        console.print(f"  CLI:       {config.cli_name}")
        console.print(f"  Role:      {config.role or 'default'}")
        console.print(f"  CWD:       {effective_cwd}")
        console.print(f"  Model:     {config.model or '(client default)'}")
        console.print(f"  Files:     {len(all_files)}")
        console.print(f"  Schema:    {'Yes' if json_schema else 'No'}")
        console.print(f"  Runner:    {type(agent).__name__}")
        console.print(f"  Parser:    {client.parser}")
        console.print(f"  Timeout:   {client.timeout_seconds}s")

        if is_agent_role(config.role):
            console.print(f"  Agent:     {role_config.name}")
            if role_config.description:
                console.print(f"  Desc:      {role_config.description}")

        console.print(f"\n[cyan]Command:[/cyan]\n  {' '.join(shlex.quote(a) for a in command)}")

        if system_prompt_text:
            prompt_preview = system_prompt_text[:200] + ("..." if len(system_prompt_text) > 200 else "")
            console.print(f"\n[cyan]System prompt ({len(system_prompt_text)} chars):[/cyan]\n  {prompt_preview}")

        if full_prompt:
            console.print(f"\n[cyan]Prompt length:[/cyan] {len(full_prompt)} chars")
        else:
            console.print("\n[cyan]Mode:[/cyan] Interactive (no prompt)")

    # --- Shell command mode (default) ---
    if not config.execute and not config.preview:
        shell_cmd = render_shell_command(command, full_prompt, client.env)
        console.print(shell_cmd)
        return

    # --- Preview mode ---
    if config.preview:
        shell_cmd = render_shell_command(command, full_prompt, client.env)
        console.print(f"\n[cyan]Shell command:[/cyan]\n{shell_cmd}")
        return

    # --- Execute mode ---
    stdout, stderr, returncode = await execute_subprocess(
        command=command,
        env=env,
        prompt=full_prompt,
        cwd=str(effective_cwd),
        timeout=client.timeout_seconds,
        cli_name=config.cli_name,
        verbose=config.verbose,
    )

    if returncode != 0:
        # Try recovery for Claude (parse even on non-zero exit)
        if client.parser == "claude_json" and stdout.strip():
            try:
                output_data = json.loads(stdout)
                err_console.print(f"[yellow]Warning:[/yellow] Exit code {returncode}, but output parsed successfully")
            except json.JSONDecodeError:
                err_console.print(f"[red]Error:[/red] Exit code {returncode}")
                if stderr:
                    err_console.print(stderr)
                sys.exit(returncode)
        else:
            err_console.print(f"[red]Error:[/red] Exit code {returncode}")
            if stderr:
                err_console.print(stderr)
            sys.exit(returncode)
    else:
        output_data = parse_output(stdout, client.parser)

    result_text = extract_result_text(output_data)

    # Validate schema
    if json_schema and config.validate_schema:
        try:
            # Parse structured_output or the raw result
            validate_target = output_data.get("structured_output") or json.loads(result_text)
            jsonschema.validate(instance=validate_target, schema=json_schema)
            if config.verbose:
                console.print("[green]Schema validation passed[/green]")
        except (json.JSONDecodeError, jsonschema.ValidationError) as exc:
            err_console.print(f"[red]Schema validation failed:[/red] {exc}")
            sys.exit(1)

    # Display
    console.print("\n[cyan]Output:[/cyan]")
    if isinstance(output_data, dict) and not output_data.get("raw"):
        if any(k in output_data for k in ("result", "response", "structured_output")):
            console.print(result_text)
        else:
            console.print(Syntax(json.dumps(output_data, indent=2), "json", theme="monokai"))
    else:
        console.print(result_text)

    # Save to file
    if config.output_file:
        config.output_file.write_text(result_text)
        console.print(f"\n[green]Saved to {config.output_file}[/green]")

    # Update conversation state
    if conversation is None:
        conversation = ConversationState.create_new()
        console.print(f"\n[cyan]Conversation ID:[/cyan] {conversation.id}")

    if config.prompt:
        conversation.turns.append(
            ConversationTurn(
                role="user",
                content=config.prompt,
                files=[str(f) for f in config.files],
                cli_name=config.cli_name,
            )
        )
        conversation.turns.append(
            ConversationTurn(
                role="assistant",
                content=result_text,
                cli_name=config.cli_name,
                model_name=output_data.get("model", "unknown") if isinstance(output_data, dict) else "unknown",
            )
        )
        conversation.last_updated = datetime.now(timezone.utc).isoformat()
        conversation.save()


async def run_batch(config: Config) -> None:
    if not config.batch or not config.batch.exists():
        err_console.print(f"[red]Error:[/red] Batch file not found: {config.batch}")
        sys.exit(1)

    effective_cwd = Path(config.cwd) if config.cwd else Path.cwd()

    registry = get_registry()
    try:
        client = registry.get_client(config.cli_name)
    except KeyError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    if config.system_prompt:
        sp_path = Path(config.system_prompt)
        system_prompt_text = sp_path.read_text() if sp_path.exists() else config.system_prompt
        role_config = client.get_role(None)
    else:
        role_config, system_prompt_text = resolve_role(client, config.role, effective_cwd)

    agent = create_agent(client)
    env = build_environment(client)

    results = []
    conversation = None

    for line in config.batch.read_text().splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            err_console.print(f"[yellow]Skipping invalid JSON:[/yellow] {line[:80]}")
            continue

        prompt = item.get("prompt", "")
        files = [Path(f) for f in item.get("files", [])]
        console.print(f"\n[cyan]Processing:[/cyan] {prompt[:60]}...")

        if config.chain and conversation:
            full_prompt, all_files = build_prompt_with_history(prompt, conversation, files)
            full_prompt = build_prompt_with_files(full_prompt, all_files)
        else:
            full_prompt = build_prompt_with_files(prompt, files)

        command = build_command(agent, role_config, system_prompt_text, model=config.model)
        resolved_cmd = resolve_executable(command, env)

        stdout, stderr, returncode = await execute_subprocess(
            command=resolved_cmd,
            env=env,
            prompt=full_prompt,
            cwd=str(effective_cwd),
            timeout=client.timeout_seconds,
            cli_name=config.cli_name,
            verbose=config.verbose,
        )

        if returncode != 0:
            err_console.print(f"[red]Error:[/red] Exit code {returncode} for prompt: {prompt[:40]}...")
            result_text = f"ERROR (exit {returncode}): {stderr[:500]}"
        else:
            output_data = parse_output(stdout, client.parser)
            result_text = extract_result_text(output_data)

        results.append(
            {
                "prompt": prompt,
                "output": result_text,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

        if config.chain:
            if conversation is None:
                conversation = ConversationState.create_new()
            conversation.turns.append(
                ConversationTurn(role="user", content=prompt, files=[str(f) for f in files], cli_name=config.cli_name)
            )
            conversation.turns.append(ConversationTurn(role="assistant", content=result_text, cli_name=config.cli_name))

    output_file = config.output_file or config.batch.with_suffix(".results.json")
    output_file.write_text(json.dumps(results, indent=2))
    console.print(f"\n[green]Results saved to {output_file}[/green]")

    if config.chain and conversation:
        conversation.last_updated = datetime.now(timezone.utc).isoformat()
        conversation.save()
        console.print(f"[cyan]Conversation ID:[/cyan] {conversation.id}")


def main() -> None:
    config = tyro.cli(Config)
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
