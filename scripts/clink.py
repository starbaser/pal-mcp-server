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
Standalone clink CLI invocation tool.

Uses PAL MCP Server's clink infrastructure to invoke external AI CLIs
(claude, claude-zai, gemini, codex) with the exact same command-building
logic that PAL MCP uses internally.

Features:
- Command building identical to PAL MCP
- JSON schema support for structured output
- Conversation continuation across sessions
- Batch processing with chaining
- Rich terminal output formatting
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import sys
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

# Import PAL MCP clink infrastructure
from clink import get_registry
from clink.agents import create_agent
from clink.agents.claude import ClaudeAgent

# Setup Rich console
console = Console()
err_console = Console(stderr=True)

# Session storage directory
SESSION_DIR = Path.home() / ".clink_sessions"


# ============================================================================
# Conversation State Management
# ============================================================================


@attrs.define
class ConversationTurn:
    """Single turn in a conversation."""

    role: str  # "user" or "assistant"
    content: str
    files: list[str] = attrs.field(factory=list)
    timestamp: str = attrs.field(factory=lambda: datetime.now(timezone.utc).isoformat())
    cli_name: str | None = None
    model_name: str | None = None


@attrs.define
class ConversationState:
    """Complete conversation state."""

    id: str
    created_at: str
    last_updated: str
    turns: list[ConversationTurn] = attrs.field(factory=list)

    def save(self) -> None:
        """Save conversation to disk."""
        session_file = SESSION_DIR / f"{self.id}.json"
        SESSION_DIR.mkdir(exist_ok=True)
        session_file.write_text(json.dumps(attrs.asdict(self), indent=2))

    @classmethod
    def load(cls, conversation_id: str) -> ConversationState:
        """Load conversation from disk."""
        session_file = SESSION_DIR / f"{conversation_id}.json"
        if not session_file.exists():
            raise FileNotFoundError(f"Session not found: {conversation_id}")
        data = json.loads(session_file.read_text())
        # Reconstruct turns
        turns = [ConversationTurn(**turn) for turn in data.get("turns", [])]
        return cls(
            id=data["id"],
            created_at=data["created_at"],
            last_updated=data["last_updated"],
            turns=turns,
        )

    @classmethod
    def create_new(cls) -> ConversationState:
        """Create new conversation."""
        now = datetime.now(timezone.utc).isoformat()
        return cls(id=str(uuid.uuid4()), created_at=now, last_updated=now)


# ============================================================================
# Helper Functions
# ============================================================================


def expand_env_vars(env: dict[str, str]) -> dict[str, str]:
    """Expand ${VAR_NAME} in environment values."""
    expanded = {}
    for key, value in env.items():
        # Replace ${VAR} with os.getenv(VAR)
        expanded[key] = re.sub(r"\$\{([^}]+)\}", lambda m: os.getenv(m.group(1), ""), value)
    return expanded


def build_prompt_with_files(prompt: str, files: list[Path]) -> str:
    """Build prompt with file contents appended."""
    parts = [prompt]
    for file_path in files:
        if file_path.exists():
            content = file_path.read_text()
            parts.append(f"\n\n# File: {file_path}\n\n```\n{content}\n```")
        else:
            err_console.print(f"[yellow]Warning:[/yellow] File not found: {file_path}")
    return "\n".join(parts)


def build_prompt_with_history(
    user_prompt: str, conversation: ConversationState, files: list[Path]
) -> tuple[str, list[Path]]:
    """Build prompt with conversation history.

    Mimics PAL MCP's build_conversation_history():
    - Includes previous turns in chronological order
    - Deduplicates files (newest-first priority)
    - Formats for CLI consumption
    """
    parts = []

    # Add conversation history
    if conversation.turns:
        parts.append("=== Previous Conversation ===\n")
        for i, turn in enumerate(conversation.turns, 1):
            parts.append(f"--- Turn {i} ({turn.role}) ---")
            if turn.cli_name:
                parts.append(f"[CLI: {turn.cli_name}, Model: {turn.model_name or 'unknown'}]")
            parts.append(turn.content)
            parts.append("")
        parts.append("=== End of Previous Conversation ===\n")

    # Add current prompt
    parts.append("=== Current Request ===")
    parts.append(user_prompt)

    # Deduplicate files (newest first)
    all_files = list(files)  # Current files first
    for turn in reversed(conversation.turns):
        for file_path in turn.files:
            if file_path not in [str(f) for f in all_files]:
                all_files.append(Path(file_path))

    return "\n\n".join(parts), all_files


def load_json_schema(schema_input: str) -> dict:
    """Load JSON schema from string or file."""
    # Try as file first
    schema_path = Path(schema_input)
    if schema_path.exists():
        return json.loads(schema_path.read_text())

    # Try as JSON string
    try:
        return json.loads(schema_input)
    except json.JSONDecodeError:
        err_console.print(f"[red]Error:[/red] Invalid JSON schema: {schema_input}")
        sys.exit(1)


def validate_output(output: dict, schema: dict) -> tuple[bool, str | None]:
    """Validate output against JSON schema."""
    try:
        jsonschema.validate(instance=output, schema=schema)
        return True, None
    except jsonschema.ValidationError as e:
        return False, str(e)


def build_shell_command(
    command: list[str],
    prompt: str | None,
    client_env: dict[str, str],
) -> str:
    """Build shell command string for eval.

    Format: ENV_VAR=value ... command args [< <(cat <<'EOF' prompt EOF)]

    If prompt is None, omits stdin redirection for interactive mode.
    """
    parts = []

    # Add environment variables (expanded from ${VAR})
    expanded_env = expand_env_vars(client_env)
    for key, value in expanded_env.items():
        # Validate key (environment variable names should be safe)
        if not key.replace("_", "").isalnum():
            raise ValueError(f"Invalid environment variable name: {key}")
        # Quote value to handle spaces and special chars
        parts.append(f"{key}={shlex.quote(value)}")

    # Add command with args
    parts.extend(shlex.quote(arg) for arg in command)

    # Add prompt via process substitution with heredoc (only if prompt provided)
    if prompt:
        # Use unique delimiter to prevent collision with prompt content
        # Generate delimiter from hash of prompt + random component
        import hashlib
        import time

        delimiter_base = hashlib.sha256(f"{prompt}{time.time_ns()}".encode()).hexdigest()[:16].upper()
        delimiter = f"CLINK_EOF_{delimiter_base}"

        # Using <(cat <<'EOF' ...) with unique delimiter
        # Single quotes prevent variable expansion in heredoc
        prompt_heredoc = f"<(cat <<'{delimiter}'\n{prompt}\n{delimiter}\n)"
        parts.append("<")
        parts.append(prompt_heredoc)

    return " ".join(parts)


# ============================================================================
# Core Execution Functions
# ============================================================================


async def execute_clink(
    cli_name: str,
    prompt: str,
    role: str | None = None,
    files: list[Path] | None = None,
    json_schema: dict | None = None,
    system_prompt: str | None = None,
    verbose: bool = False,
    preview: bool = False,
) -> dict[str, Any]:
    """Execute clink CLI invocation.

    Returns:
        dict with keys: output, metadata, command
    """
    files = files or []

    # Load registry and get client config
    registry = get_registry()
    try:
        client = registry.get_client(cli_name)
    except KeyError as e:
        err_console.print(f"[red]Error:[/red] {e}")
        available = ", ".join(registry.list_clients())
        err_console.print(f"Available CLIs: {available}")
        sys.exit(1)

    # Get role config
    try:
        role_config = client.get_role(role)
    except KeyError as e:
        err_console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    # Load system prompt from role or override
    if system_prompt:
        # Try as file first
        prompt_path = Path(system_prompt)
        if prompt_path.exists():
            system_prompt_text = prompt_path.read_text()
        else:
            system_prompt_text = system_prompt
    else:
        system_prompt_text = role_config.prompt_path.read_text() if role_config.prompt_path else None

    # Build full prompt with files
    full_prompt = build_prompt_with_files(prompt, files)

    # Create agent and build command
    agent = create_agent(client)

    # Build command - handle Claude agent specially for JSON schema support
    if isinstance(agent, ClaudeAgent) and json_schema:
        command = agent._build_command(role=role_config, system_prompt=system_prompt_text, json_schema=json_schema)
    elif hasattr(agent, "_build_command"):
        command = agent._build_command(role=role_config, system_prompt=system_prompt_text)
    else:
        err_console.print("[red]Error:[/red] Agent does not support command building")
        sys.exit(1)

    if verbose:
        console.print("\n[cyan]Configuration:[/cyan]")
        console.print(f"  CLI: {cli_name}")
        console.print(f"  Role: {role or 'default'}")
        console.print(f"  Files: {len(files)}")
        console.print(f"  JSON Schema: {'Yes' if json_schema else 'No'}")

        # Show environment variables if any
        if client.env:
            console.print("\n[cyan]Environment Variables:[/cyan]")
            expanded_env = expand_env_vars(client.env)
            for key, value in expanded_env.items():
                # Mask sensitive values
                if "KEY" in key or "TOKEN" in key or "SECRET" in key:
                    display_value = value[:8] + "..." if len(value) > 8 else "***"
                else:
                    display_value = value
                console.print(f"  {key}: {display_value}")

    if verbose or preview:
        console.print("\n[cyan]Command:[/cyan]")
        console.print("  " + " ".join(shlex.quote(arg) for arg in command))
        if full_prompt:
            console.print(f"\n[cyan]Prompt length:[/cyan] {len(full_prompt)} characters")
        else:
            console.print("\n[cyan]Mode:[/cyan] Interactive (no prompt)")

    if preview:
        return {"output": None, "metadata": {}, "command": command}

    # Execute command
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task(f"Executing {cli_name}...", total=None)

        # Expand environment variables
        # >>> DEBUG: Show environment at this exact point
        if verbose:
            console.print("\n[yellow]Environment Inspection (at subprocess creation):[/yellow]")
            console.print(f"  Python executable: {sys.executable}")
            console.print(f"  Python prefix: {sys.prefix}")
            console.print(f"  VIRTUAL_ENV: {os.environ.get('VIRTUAL_ENV', 'Not set')}")
            console.print(f"  PATH (first 3 entries): {':'.join(os.environ.get('PATH', '').split(':')[:3])}...")
            console.print(f"  Total env vars: {len(os.environ)}")

        # Start with current environment (includes uv's modifications)
        env = os.environ.copy()

        # Remove uv's virtual environment modifications
        # We want the subprocess to use ONLY the caller's original environment
        if "VIRTUAL_ENV" in env:
            uv_venv_path = env["VIRTUAL_ENV"]
            del env["VIRTUAL_ENV"]

            # Strip uv's venv from PATH
            if "PATH" in env:
                path_entries = env["PATH"].split(":")
                # Remove any path entries that point to uv's venv
                cleaned_path = [p for p in path_entries if uv_venv_path not in p]
                env["PATH"] = ":".join(cleaned_path)

        if verbose:
            console.print("\n[yellow]After cleaning uv's venv from environment:[/yellow]")
            console.print(f"  Cleaned env vars: {len(env)}")
            console.print(f"  VIRTUAL_ENV: {env.get('VIRTUAL_ENV', 'Removed ✓')}")
            console.print(f"  PATH (first entry): {env.get('PATH', '').split(':')[0]}")
            # Show some user env vars to prove it's the caller's environment
            if "ZAI_API_KEY" in env:
                console.print(f"  ZAI_API_KEY: {env['ZAI_API_KEY'][:10]}... (from caller)")
            if "HOME" in env:
                console.print(f"  HOME: {env['HOME']} (from caller)")

        env.update(expand_env_vars(client.env))

        if verbose:
            console.print("\n[yellow]After adding CLI-specific vars:[/yellow]")
            console.print(f"  Total env vars: {len(env)}")
            console.print(f"  CLI-specific vars added: {len(client.env)}")
            # Show that PATH is still from caller
            console.print(f"  PATH still from caller: {env.get('PATH', '').split(':')[0]}")

            # Show which executable will be used
            import shutil

            cli_executable = command[0]
            which_result = shutil.which(cli_executable, path=env.get("PATH"))
            console.print("\n[yellow]CLI Executable Resolution:[/yellow]")
            console.print(f"  Looking for: {cli_executable}")
            console.print(f"  Will use: {which_result}")
            if which_result and "uv" not in which_result:
                console.print("  ✓ From caller's PATH, not uv's venv")

        # Execute subprocess
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=client.working_dir,
                env=env,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(full_prompt.encode("utf-8")),
                timeout=client.timeout_seconds,
            )

            stdout = stdout_bytes.decode("utf-8")
            stderr = stderr_bytes.decode("utf-8")

            progress.update(task, completed=True)

        except asyncio.TimeoutError:
            err_console.print(f"[red]Error:[/red] Command timed out after {client.timeout_seconds}s")
            sys.exit(1)
        except FileNotFoundError:
            err_console.print(f"[red]Error:[/red] CLI executable not found: {command[0]}")
            err_console.print(f"Make sure '{cli_name}' CLI is installed and in PATH")
            sys.exit(1)
        except Exception as e:
            err_console.print(f"[red]Error:[/red] Failed to execute command: {e}")
            sys.exit(1)

    if process.returncode != 0:
        err_console.print(f"[red]Error:[/red] Command failed with exit code {process.returncode}")
        if stderr:
            err_console.print("\n[red]stderr:[/red]")
            err_console.print(stderr)
        sys.exit(1)

    if verbose and stderr:
        console.print("\n[yellow]stderr:[/yellow]")
        console.print(stderr)

    # Parse output based on client parser
    parser_name = client.parser
    if parser_name == "claude_json":
        try:
            output_data = json.loads(stdout)
        except json.JSONDecodeError:
            err_console.print("[red]Error:[/red] Failed to parse JSON output")
            console.print("\n[yellow]Raw output:[/yellow]")
            console.print(stdout)
            sys.exit(1)
    elif parser_name == "gemini_json":
        try:
            output_data = json.loads(stdout)
        except json.JSONDecodeError:
            err_console.print("[red]Error:[/red] Failed to parse JSON output")
            console.print("\n[yellow]Raw output:[/yellow]")
            console.print(stdout)
            sys.exit(1)
    else:
        # Fallback: treat as plain text
        output_data = {"content": stdout, "raw": True}

    return {"output": output_data, "metadata": {"cli": cli_name, "role": role}, "command": command}


# ============================================================================
# Session Management
# ============================================================================


def list_sessions() -> None:
    """List active conversation sessions."""
    if not SESSION_DIR.exists():
        console.print("[yellow]No active sessions found[/yellow]")
        return

    sessions = list(SESSION_DIR.glob("*.json"))
    if not sessions:
        console.print("[yellow]No active sessions found[/yellow]")
        return

    table = Table(title="Active Conversations")
    table.add_column("ID", style="cyan")
    table.add_column("Created", style="green")
    table.add_column("Turns", style="yellow")
    table.add_column("Last CLI", style="magenta")

    for session_file in sorted(sessions, key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            conversation = ConversationState.load(session_file.stem)
            last_cli = conversation.turns[-1].cli_name if conversation.turns else "N/A"
            table.add_row(
                conversation.id[:8] + "...",
                conversation.created_at[:19],
                str(len(conversation.turns)),
                last_cli or "N/A",
            )
        except Exception:
            continue

    console.print(table)


def clean_sessions(older_than: str) -> None:
    """Clean old sessions."""
    if not SESSION_DIR.exists():
        console.print("[yellow]No sessions to clean[/yellow]")
        return

    # Parse older_than (e.g., "24h", "7d", "1w")
    match = re.match(r"^(\d+)([hdw])$", older_than)
    if not match:
        err_console.print("[red]Error:[/red] Invalid time format. Use: 24h, 7d, 1w")
        sys.exit(1)

    value, unit = int(match.group(1)), match.group(2)
    unit_seconds = {"h": 3600, "d": 86400, "w": 604800}
    threshold_seconds = value * unit_seconds[unit]

    now = datetime.now(timezone.utc)
    removed = 0

    for session_file in SESSION_DIR.glob("*.json"):
        try:
            conversation = ConversationState.load(session_file.stem)
            created = datetime.fromisoformat(conversation.created_at)
            age_seconds = (now - created).total_seconds()

            if age_seconds > threshold_seconds:
                session_file.unlink()
                removed += 1
        except Exception:
            continue

    if removed > 0:
        console.print(f"[green]✓[/green] Removed {removed} old session(s)")
    else:
        console.print("[yellow]No old sessions found[/yellow]")


# ============================================================================
# CLI Configuration
# ============================================================================


@attrs.define
class CLinkInvokeConfig:
    """Configuration for clink invocation."""

    cli_name: Annotated[str, tyro.conf.Positional]
    """CLI client to use (claude, glmaude, gemini, codex)"""

    prompt: Annotated[str | None, tyro.conf.Positional] = None
    """User prompt to send to the CLI (optional - omit for interactive mode)"""

    # Optional execution parameters
    role: str | None = None
    """Role preset (default, agency, codereviewer, planner)"""

    files: list[Path] = attrs.field(factory=list)
    """Files to include in the prompt"""

    json_schema: str | None = None
    """JSON schema for structured output (JSON string or file path)"""

    system_prompt: str | None = None
    """Override system prompt (text or file path)"""

    # Conversation continuation
    continue_from: str | None = None
    """Continuation ID from previous conversation"""

    # Batch processing
    batch: Path | None = None
    """JSONL file with multiple prompts to process"""

    chain: bool = False
    """Chain batch prompts (each continues from previous)"""

    # Output control
    execute: bool = False
    """Execute command as subprocess (default: output shell command for eval)"""

    preview: bool = False
    """Preview command without executing"""

    validate_schema: bool = True
    """Validate output against schema (if provided)"""

    output_file: Path | None = None
    """Save output to file"""

    # Session management
    list_sessions: bool = False
    """List active conversation sessions"""

    clean_sessions: bool = False
    """Clean old sessions"""

    older_than: str = "24h"
    """Clean sessions older than (e.g., '24h', '7d', '1w')"""

    # Debug
    verbose: bool = False
    """Enable verbose debug output"""

    no_color: bool = False
    """Disable colored output"""


# ============================================================================
# Main Execution
# ============================================================================


async def main() -> None:
    """Main entry point."""
    config = tyro.cli(CLinkInvokeConfig)

    # Disable colors if requested
    if config.no_color:
        console.no_color = True
        err_console.no_color = True

    # Handle session management commands
    if config.list_sessions:
        list_sessions()
        return

    if config.clean_sessions:
        clean_sessions(config.older_than)
        return

    # Batch processing
    if config.batch:
        await process_batch(config)
        return

    # Load JSON schema if provided
    json_schema = load_json_schema(config.json_schema) if config.json_schema else None

    # Handle conversation continuation
    conversation = None
    if config.continue_from:
        try:
            conversation = ConversationState.load(config.continue_from)
            if config.verbose:
                console.print(f"[green]✓[/green] Loaded conversation with {len(conversation.turns)} turns")
        except FileNotFoundError as e:
            err_console.print(f"[red]Error:[/red] {e}")
            sys.exit(1)

    # Build prompt (None means interactive mode)
    if config.prompt is None:
        # Interactive mode - no prompt
        full_prompt = None
        full_prompt_with_files = None
        all_files = config.files
    elif conversation:
        full_prompt, all_files = build_prompt_with_history(config.prompt, conversation, config.files)
        full_prompt_with_files = build_prompt_with_files(full_prompt, all_files)
    else:
        full_prompt = config.prompt
        all_files = config.files
        full_prompt_with_files = build_prompt_with_files(full_prompt, all_files)

    # Load registry and get client config
    registry = get_registry()
    try:
        client = registry.get_client(config.cli_name)
    except KeyError as e:
        err_console.print(f"[red]Error:[/red] {e}")
        available = ", ".join(registry.list_clients())
        err_console.print(f"Available CLIs: {available}")
        sys.exit(1)

    # Get role config
    try:
        role_config = client.get_role(config.role)
    except KeyError as e:
        err_console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    # Load system prompt
    if config.system_prompt:
        prompt_path = Path(config.system_prompt)
        if prompt_path.exists():
            system_prompt_text = prompt_path.read_text()
        else:
            system_prompt_text = config.system_prompt
    else:
        system_prompt_text = role_config.prompt_path.read_text() if role_config.prompt_path else None

    # Create agent and build command
    agent = create_agent(client)
    if isinstance(agent, ClaudeAgent) and json_schema:
        command = agent._build_command(role=role_config, system_prompt=system_prompt_text, json_schema=json_schema)
    elif hasattr(agent, "_build_command"):
        command = agent._build_command(role=role_config, system_prompt=system_prompt_text)
    else:
        err_console.print("[red]Error:[/red] Agent does not support command building")
        sys.exit(1)

    # Default mode: Output shell command for eval
    if not config.execute and not config.preview:
        shell_cmd = build_shell_command(command, full_prompt_with_files, client.env)
        console.print(shell_cmd)
        return

    # Preview mode: Show command without executing
    if config.preview:
        if config.verbose:
            console.print("\n[cyan]Configuration:[/cyan]")
            console.print(f"  CLI: {config.cli_name}")
            console.print(f"  Role: {config.role or 'default'}")
            console.print(f"  Files: {len(all_files)}")

        console.print("\n[cyan]Shell Command (for eval):[/cyan]")
        shell_cmd = build_shell_command(command, full_prompt_with_files, client.env)
        console.print(shell_cmd)
        return

    # Execute mode: Run as subprocess
    result = await execute_clink(
        cli_name=config.cli_name,
        prompt=full_prompt,
        role=config.role,
        files=all_files,
        json_schema=json_schema,
        system_prompt=config.system_prompt,
        verbose=config.verbose,
        preview=False,
    )

    # Extract output
    output_data = result["output"]

    # Validate JSON schema if provided
    if json_schema and config.validate_schema:
        is_valid, error = validate_output(output_data, json_schema)
        if not is_valid:
            err_console.print("[red]Schema validation failed:[/red]")
            err_console.print(error)
            sys.exit(1)
        elif config.verbose:
            console.print("[green]✓[/green] Output matches schema")

    # Display output
    console.print("\n[cyan]Output:[/cyan]")
    if isinstance(output_data, dict) and not output_data.get("raw"):
        # Extract result from Claude JSON output
        if "result" in output_data:
            console.print(output_data["result"])
        elif "content" in output_data:
            console.print(output_data["content"])
        else:
            # Show full JSON for debugging or structured output
            syntax = Syntax(json.dumps(output_data, indent=2), "json", theme="monokai")
            console.print(syntax)
    else:
        console.print(output_data.get("content", str(output_data)))

    # Save to file if requested
    if config.output_file:
        if isinstance(output_data, dict):
            if "result" in output_data:
                config.output_file.write_text(output_data["result"])
            elif "content" in output_data:
                config.output_file.write_text(output_data["content"])
            else:
                config.output_file.write_text(json.dumps(output_data, indent=2))
        else:
            config.output_file.write_text(str(output_data))
        console.print(f"\n[green]✓[/green] Output saved to {config.output_file}")

    # Save conversation state
    if conversation is None:
        conversation = ConversationState.create_new()
        console.print(f"\n[cyan]Conversation ID:[/cyan] {conversation.id}")

    # Add turn
    conversation.turns.append(
        ConversationTurn(
            role="user",
            content=config.prompt,
            files=[str(f) for f in config.files],
            cli_name=config.cli_name,
        )
    )

    # Extract result text for conversation history
    if isinstance(output_data, dict):
        result_text = output_data.get("result") or output_data.get("content", str(output_data))
    else:
        result_text = str(output_data)

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


async def process_batch(config: CLinkInvokeConfig) -> None:
    """Process batch file."""
    if not config.batch or not config.batch.exists():
        err_console.print(f"[red]Error:[/red] Batch file not found: {config.batch}")
        sys.exit(1)

    results = []
    conversation = None

    for line in config.batch.read_text().splitlines():
        if not line.strip():
            continue

        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            err_console.print(f"[yellow]Warning:[/yellow] Invalid JSON, skipping: {line}")
            continue

        prompt = item.get("prompt", "")
        files = [Path(f) for f in item.get("files", [])]

        console.print(f"\n[cyan]Processing:[/cyan] {prompt[:60]}...")

        # Build prompt with conversation if chaining
        if config.chain and conversation:
            full_prompt, all_files = build_prompt_with_history(prompt, conversation, files)
        else:
            full_prompt = prompt
            all_files = files

        # Execute
        result = await execute_clink(
            cli_name=config.cli_name,
            prompt=full_prompt,
            role=config.role,
            files=all_files,
            verbose=config.verbose,
        )

        output_data = result["output"]

        # Extract result text
        if isinstance(output_data, dict):
            result_text = output_data.get("result") or output_data.get("content", str(output_data))
        else:
            result_text = str(output_data)

        results.append(
            {
                "prompt": prompt,
                "output": result_text,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

        # Update conversation if chaining
        if config.chain:
            if conversation is None:
                conversation = ConversationState.create_new()
            conversation.turns.append(
                ConversationTurn(role="user", content=prompt, files=[str(f) for f in files], cli_name=config.cli_name)
            )
            conversation.turns.append(
                ConversationTurn(
                    role="assistant",
                    content=result_text,
                    cli_name=config.cli_name,
                )
            )

    # Save batch results
    output_file = config.output_file or config.batch.with_suffix(".results.json")
    output_file.write_text(json.dumps(results, indent=2))
    console.print(f"\n[green]✓[/green] Results saved to {output_file}")

    # Save conversation if chaining
    if config.chain and conversation:
        conversation.last_updated = datetime.now(timezone.utc).isoformat()
        conversation.save()
        console.print(f"[cyan]Conversation ID:[/cyan] {conversation.id}")


if __name__ == "__main__":
    asyncio.run(main())
