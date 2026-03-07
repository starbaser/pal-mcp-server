"""Tests for agent definition loading and @ import resolution."""

from pathlib import Path

import pytest

from clink.agent_definitions import (
    AgentDefinition,
    AgentDefinitionError,
    _is_relative_path,
    load_agent_definition,
)


def load_agent_definition_from_path(path: Path) -> AgentDefinition:
    """Helper to load agent definition from a file path."""
    content = path.read_text(encoding="utf-8")
    return AgentDefinition(
        name=path.stem,
        path=path,
        content=content,
        frontmatter={},
    )


class TestAgentImportResolution:
    """Tests for @ import resolution in agent definitions."""

    def test_resolve_import_appends_context(self, tmp_path):
        """@ imports resolved and APPENDED after agent body (Claude Code style)."""
        imported = tmp_path / "standards.md"
        imported.write_text("# Python Standards\nUse type hints.")

        agent = tmp_path / "agent.md"
        agent.write_text("---\nname: test\n---\n\n# Test Agent\n\n@standards.md")

        defn = load_agent_definition_from_path(agent)
        prompt = defn.get_system_prompt()

        # Context block appended
        assert "Contents of" in prompt
        assert "(agent imported context)" in prompt
        assert "# Python Standards" in prompt
        assert "Use type hints." in prompt

        # Original @reference preserved in body
        assert "@standards.md" in prompt

        # Context comes AFTER agent body (Claude Code behavior)
        body_pos = prompt.find("# Test Agent")
        context_pos = prompt.find("Contents of")
        assert body_pos < context_pos

    def test_resolve_import_home_path(self, tmp_path, monkeypatch):
        """@ imports with ~ expand to home directory."""
        mock_home = tmp_path / "home"
        mock_home.mkdir()
        (mock_home / "test.md").write_text("# Home Content")
        monkeypatch.setenv("HOME", str(mock_home))

        agent = tmp_path / "agent.md"
        agent.write_text("---\nname: test\n---\n\n@~/test.md")

        defn = load_agent_definition_from_path(agent)
        prompt = defn.get_system_prompt()

        assert "# Home Content" in prompt
        assert "Contents of" in prompt

    def test_resolve_import_missing_graceful(self, tmp_path):
        """Missing imports silently skipped, original reference preserved."""
        agent = tmp_path / "agent.md"
        agent.write_text("---\nname: test\n---\n\n# Agent\n\n@nonexistent.md")

        defn = load_agent_definition_from_path(agent)
        prompt = defn.get_system_prompt()

        # No context block for missing file
        assert "Contents of" not in prompt
        # Original reference still there
        assert "@nonexistent.md" in prompt
        # Agent body intact
        assert "# Agent" in prompt

    def test_resolve_multiple_imports_sequential(self, tmp_path):
        """Multiple @ imports resolved sequentially in order of appearance."""
        (tmp_path / "a.md").write_text("# File A")
        (tmp_path / "b.md").write_text("# File B")

        agent = tmp_path / "agent.md"
        agent.write_text("---\nname: test\n---\n\n# Agent\n@a.md\n@b.md")

        defn = load_agent_definition_from_path(agent)
        prompt = defn.get_system_prompt()

        assert "# File A" in prompt
        assert "# File B" in prompt
        assert prompt.count("(agent imported context)") == 2

        # A comes before B (order preserved)
        a_pos = prompt.find("# File A")
        b_pos = prompt.find("# File B")
        assert a_pos < b_pos

    def test_resolve_import_deduplication(self, tmp_path):
        """Same file referenced multiple times only resolved once."""
        (tmp_path / "shared.md").write_text("# Shared Content")

        agent = tmp_path / "agent.md"
        agent.write_text("---\nname: test\n---\n\n@shared.md\nMore text\n@shared.md")

        defn = load_agent_definition_from_path(agent)
        prompt = defn.get_system_prompt()

        # Only one content block despite two references
        assert prompt.count("(agent imported context)") == 1
        assert prompt.count("# Shared Content") == 1

    def test_no_imports_unchanged(self, tmp_path):
        """Agent definition without @ imports returns content unchanged."""
        agent = tmp_path / "agent.md"
        agent.write_text("---\nname: test\n---\n\n# Simple Agent\n\nNo imports here.")

        defn = load_agent_definition_from_path(agent)
        prompt = defn.get_system_prompt()

        assert "# Simple Agent" in prompt
        assert "No imports here." in prompt
        assert "Contents of" not in prompt

    def test_absolute_path_import(self, tmp_path):
        """@ imports with absolute paths are resolved."""
        imported = tmp_path / "absolute.md"
        imported.write_text("# Absolute Import")

        agent = tmp_path / "agent.md"
        agent.write_text(f"---\nname: test\n---\n\n@{imported}")

        defn = load_agent_definition_from_path(agent)
        prompt = defn.get_system_prompt()

        assert "# Absolute Import" in prompt
        assert "Contents of" in prompt


class TestIsRelativePath:
    """Tests for _is_relative_path helper."""

    def test_dot_slash_prefix(self):
        assert _is_relative_path("./agents/custom.md") is True

    def test_dot_dot_slash_prefix(self):
        assert _is_relative_path("../shared/agent.md") is True

    def test_contains_slash(self):
        assert _is_relative_path("subdir/agent.md") is True

    def test_plain_name(self):
        assert _is_relative_path("researcher") is False

    def test_name_with_extension(self):
        assert _is_relative_path("custom.md") is False

    def test_name_with_dots(self):
        assert _is_relative_path("my.agent.v2") is False


class TestLoadAgentDefinitionRelativePath:
    """Tests for load_agent_definition with relative file paths."""

    def test_dot_slash_relative(self, tmp_path):
        """./path resolves relative to project_dir."""
        agent_file = tmp_path / "my-agent.md"
        agent_file.write_text("---\nname: myagent\n---\n\n# My Agent")

        defn = load_agent_definition("./my-agent.md", project_dir=tmp_path)
        assert defn.name == "myagent"
        assert "# My Agent" in defn.content

    def test_dot_dot_slash_relative(self, tmp_path):
        """../path resolves relative to project_dir."""
        agent_file = tmp_path / "shared" / "agent.md"
        agent_file.parent.mkdir()
        agent_file.write_text("---\nname: shared\n---\n\n# Shared Agent")

        subdir = tmp_path / "project"
        subdir.mkdir()

        defn = load_agent_definition("../shared/agent.md", project_dir=subdir)
        assert defn.name == "shared"

    def test_subdir_slash_relative(self, tmp_path):
        """subdir/file resolves relative to project_dir."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        agent_file = agents_dir / "custom.md"
        agent_file.write_text("---\nname: custom\n---\n\n# Custom Agent")

        defn = load_agent_definition("agents/custom.md", project_dir=tmp_path)
        assert defn.name == "custom"

    def test_relative_path_not_found(self, tmp_path):
        """Relative path to nonexistent file raises error."""
        with pytest.raises(AgentDefinitionError, match="not found"):
            load_agent_definition("./nonexistent.md", project_dir=tmp_path)

    def test_relative_path_no_project_dir(self):
        """Relative path without project_dir raises error."""
        with pytest.raises(AgentDefinitionError, match="no cwd provided"):
            load_agent_definition("./agent.md", project_dir=None)

    def test_relative_path_is_directory(self, tmp_path):
        """Relative path pointing to a directory raises error."""
        (tmp_path / "not-a-file").mkdir()
        with pytest.raises(AgentDefinitionError, match="not a file"):
            load_agent_definition("./not-a-file", project_dir=tmp_path)

    def test_plain_name_still_searches_by_name(self, tmp_path):
        """Plain names without slashes still use frontmatter search."""
        agents_dir = tmp_path / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        agent_file = agents_dir / "my-agent.md"
        agent_file.write_text("---\nname: researcher\n---\n\n# Researcher Agent")

        defn = load_agent_definition("researcher", project_dir=tmp_path)
        assert defn.name == "researcher"
