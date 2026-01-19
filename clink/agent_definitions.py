"""Agent definition file handling for clink agent roles."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from utils.env import get_env

logger = logging.getLogger("clink.agent_definitions")

# Frontmatter pattern: matches YAML frontmatter between --- delimiters
FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL | re.MULTILINE)

# Default agent directory prefix (can be overridden via AGENTS_PREFIX env var)
DEFAULT_AGENTS_PREFIX = ".claude/agents"


def get_agents_prefix() -> str:
    """Get the agents directory prefix from environment or default.

    Returns:
        Agent directory prefix (e.g., ".claude/agents")
    """
    return get_env("AGENTS_PREFIX") or DEFAULT_AGENTS_PREFIX


class AgentDefinitionError(RuntimeError):
    """Raised when agent definition files cannot be loaded or parsed."""


class AgentDefinition:
    """Represents a loaded agent definition with frontmatter and content."""

    def __init__(
        self,
        name: str,
        path: Path,
        content: str,
        frontmatter: dict[str, Any] | None = None,
    ):
        self.name = name
        self.path = path
        self.content = content
        self.frontmatter = frontmatter or {}

    def get_system_prompt(self) -> str:
        """Get system prompt with @ imports resolved and appended."""
        content = self._strip_frontmatter()
        imports = self._extract_imports(content)
        resolved_blocks = self._resolve_imports_to_blocks(imports)

        if resolved_blocks:
            # Append resolved imports AFTER the agent definition (Claude Code style)
            return f"{content}\n\n\n{resolved_blocks}"
        return content

    def _strip_frontmatter(self) -> str:
        """Extract content without frontmatter."""
        match = FRONTMATTER_PATTERN.match(self.content)
        if match:
            return self.content[match.end() :].strip()
        return self.content.strip()

    def _extract_imports(self, content: str) -> list[tuple[str, Path | None]]:
        """Extract @path references and resolve their paths."""
        # Match @path.md, @~/path.md, @./path.md
        pattern = r"@(~?\.?/?[\w./\-]+\.md)\b"
        imports = []
        seen: set[str] = set()  # Deduplicate
        for match in re.finditer(pattern, content):
            path_str = match.group(1)
            if path_str not in seen:
                seen.add(path_str)
                resolved = self._resolve_import_path(path_str)
                imports.append((path_str, resolved))
        return imports

    def _resolve_imports_to_blocks(self, imports: list[tuple[str, Path | None]]) -> str:
        """Format resolved imports as Claude-style content blocks."""
        blocks = []
        for _, resolved_path in imports:
            if resolved_path and resolved_path.exists():
                try:
                    file_content = resolved_path.read_text(encoding="utf-8")
                    block = f"Contents of {resolved_path} (agent imported context):\n\n{file_content}"
                    blocks.append(block)
                except Exception:
                    pass  # Skip unreadable files
        return "\n\n".join(blocks)

    def _resolve_import_path(self, path_str: str) -> Path | None:
        """Resolve import path relative to agent file location."""
        if path_str.startswith("~"):
            return Path(path_str).expanduser()

        path = Path(path_str)
        if path.is_absolute():
            return path

        # Relative to agent file's directory
        if self.path:
            return (self.path.parent / path).resolve()

        return None

    def __repr__(self) -> str:
        return f"AgentDefinition(name={self.name!r}, path={self.path})"


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from markdown content.

    Args:
        content: Raw file content that may contain frontmatter

    Returns:
        Tuple of (frontmatter dict, remaining content)
    """
    match = FRONTMATTER_PATTERN.match(content)
    if not match:
        return {}, content

    frontmatter_text = match.group(1)
    remaining_content = content[match.end() :]

    # Simple YAML parsing for common key: value patterns
    # This handles basic cases without requiring PyYAML dependency
    frontmatter: dict[str, Any] = {}
    for line in frontmatter_text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()

            # Remove quotes if present
            if value.startswith(("'", '"')) and value.endswith(("'", '"')):
                value = value[1:-1]

            frontmatter[key] = value

    return frontmatter, remaining_content


def find_agent_file_by_name(agent_name: str, project_dir: Path | None = None) -> Path | None:
    """Find agent definition file by searching frontmatter.

    Search order:
    1. Project level: {project_dir}/{AGENTS_PREFIX}/*.md
    2. User level: ~/{AGENTS_PREFIX}/*.md

    The AGENTS_PREFIX defaults to ".claude/agents" but can be configured
    via the AGENTS_PREFIX environment variable.

    Args:
        agent_name: Name to match in frontmatter 'name' field
        project_dir: Optional project directory to search first

    Returns:
        Path to matching agent file, or None if not found
    """
    agents_prefix = get_agents_prefix()
    search_paths: list[Path] = []

    # Project level
    if project_dir:
        project_agents_dir = project_dir / agents_prefix
        if project_agents_dir.is_dir():
            search_paths.append(project_agents_dir)

    # User level
    user_agents_dir = Path.home() / agents_prefix
    if user_agents_dir.is_dir():
        search_paths.append(user_agents_dir)

    logger.debug("Searching for agent '%s' in: %s (prefix: %s)", agent_name, search_paths, agents_prefix)

    for search_dir in search_paths:
        for agent_file in search_dir.glob("*.md"):
            try:
                content = agent_file.read_text(encoding="utf-8")
                frontmatter, _ = parse_frontmatter(content)

                # Check if frontmatter contains matching name
                if frontmatter.get("name") == agent_name:
                    logger.debug("Found agent '%s' at %s", agent_name, agent_file)
                    return agent_file

            except Exception as exc:
                logger.debug("Failed to read agent file %s: %s", agent_file, exc)
                continue

    logger.debug("Agent '%s' not found in search paths", agent_name)
    return None


def load_agent_definition(
    definition: str,
    project_dir: Path | None = None,
) -> AgentDefinition:
    """Load agent definition from name or path.

    Args:
        definition: Agent name or absolute path to agent file
        project_dir: Optional project directory for relative path resolution

    Returns:
        AgentDefinition instance

    Raises:
        AgentDefinitionError: If agent file cannot be found or loaded
    """
    path = Path(definition)

    # Check if absolute path
    if path.is_absolute():
        if not path.exists():
            raise AgentDefinitionError(f"Agent definition file not found: {path}")
        if not path.is_file():
            raise AgentDefinitionError(f"Agent definition path is not a file: {path}")

        logger.debug("Loading agent definition from absolute path: %s", path)

    else:
        # Treat as agent name - search by frontmatter
        found_path = find_agent_file_by_name(definition, project_dir)
        if not found_path:
            agents_prefix = get_agents_prefix()
            raise AgentDefinitionError(
                f"Agent '{definition}' not found. Searched project {agents_prefix}/ "
                f"and user ~/{agents_prefix}/ for matching frontmatter."
            )
        path = found_path

    # Load and parse the file
    try:
        content = path.read_text(encoding="utf-8")
    except Exception as exc:
        raise AgentDefinitionError(f"Failed to read agent definition {path}: {exc}") from exc

    frontmatter, _ = parse_frontmatter(content)
    agent_name = frontmatter.get("name", path.stem)

    logger.info("Loaded agent definition '%s' from %s", agent_name, path)
    return AgentDefinition(
        name=agent_name,
        path=path,
        content=content,
        frontmatter=frontmatter,
    )


def is_agent_role(role: str | None) -> bool:
    """Check if role is an agent role pattern.

    Args:
        role: Role string to check

    Returns:
        True if role is "agent" or "agent:<definition>"
    """
    if not role:
        return False
    return role == "agent" or role.startswith("agent:")


def parse_agent_role(role: str) -> str | None:
    """Extract definition from agent role string.

    Args:
        role: Role string (e.g., "agent", "agent:researcher", "agent:/path/to/agent.md")

    Returns:
        Agent definition string (name or path), or None for plain "agent"
    """
    if role == "agent":
        return None

    if role.startswith("agent:"):
        definition = role[6:]  # Remove "agent:" prefix
        return definition.strip() if definition.strip() else None

    return None
