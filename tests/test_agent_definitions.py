"""Tests for agent definition @ import resolution."""

from pathlib import Path

from clink.agent_definitions import AgentDefinition


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
