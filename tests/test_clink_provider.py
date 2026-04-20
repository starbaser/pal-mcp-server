"""Tests for ClinkProvider and stable headless CLI configuration."""

import json
from pathlib import Path

import pytest

from clink.agents.base import BaseCLIAgent
from clink.constants import INTERNAL_DEFAULTS, PASSTHROUGH_ARGS
from clink.models import ResolvedCLIClient, ResolvedCLIRole
from clink.registry import get_registry
from providers.clink_provider import (
    PASSTHROUGH_SYSTEM_PROMPT,
    ClinkProvider,
)


# ---------------------------------------------------------------------------
# PASSTHROUGH_ARGS completeness
# ---------------------------------------------------------------------------


class TestPassthroughArgs:
    """Verify PASSTHROUGH_ARGS contain the full headless flag set per CLI."""

    def test_claude_has_print_and_json_output(self):
        args = PASSTHROUGH_ARGS["claude"]
        assert "--print" in args
        assert "--output-format" in args
        idx = args.index("--output-format")
        assert args[idx + 1] == "json"

    def test_claude_has_bypass_permissions(self):
        args = PASSTHROUGH_ARGS["claude"]
        assert "--permission-mode" in args
        idx = args.index("--permission-mode")
        assert args[idx + 1] == "bypassPermissions"

    def test_gemini_has_yolo(self):
        assert "--yolo" in PASSTHROUGH_ARGS["gemini"]

    def test_gemini_has_json_output(self):
        args = PASSTHROUGH_ARGS["gemini"]
        assert "-o" in args
        idx = args.index("-o")
        assert args[idx + 1] == "json"

    def test_codex_has_exec(self):
        assert PASSTHROUGH_ARGS["codex"][0] == "exec"

    def test_codex_has_skip_git_repo_check(self):
        assert "--skip-git-repo-check" in PASSTHROUGH_ARGS["codex"]

    def test_codex_has_json(self):
        assert "--json" in PASSTHROUGH_ARGS["codex"]

    def test_codex_has_bypass_sandbox(self):
        assert "--dangerously-bypass-approvals-and-sandbox" in PASSTHROUGH_ARGS["codex"]


# ---------------------------------------------------------------------------
# INTERNAL_DEFAULTS
# ---------------------------------------------------------------------------


class TestInternalDefaults:
    """Verify INTERNAL_DEFAULTS carry stable headless flags."""

    def test_claude_internal_has_print_and_json(self):
        args = INTERNAL_DEFAULTS["claude"].additional_args
        assert "--print" in args
        assert "--output-format" in args
        idx = args.index("--output-format")
        assert args[idx + 1] == "json"


# ---------------------------------------------------------------------------
# CLAUDECODE env stripping
# ---------------------------------------------------------------------------


class TestClaudecodeEnvStripping:
    """Verify BaseCLIAgent._build_environment strips CLAUDECODE."""

    def _make_agent(self) -> BaseCLIAgent:
        role = ResolvedCLIRole(name="default", prompt_path=Path(__file__), role_args=[])
        client = ResolvedCLIClient(
            name="test",
            executable=["echo"],
            internal_args=[],
            config_args=[],
            env={},
            timeout_seconds=10,
            parser="claude_json",
            runner="claude",
            roles={"default": role},
            output_to_file=None,
            working_dir=None,
        )
        return BaseCLIAgent(client)

    def test_claudecode_stripped_from_env(self, monkeypatch):
        monkeypatch.setenv("CLAUDECODE", "1")
        agent = self._make_agent()
        env = agent._build_environment()
        assert "CLAUDECODE" not in env

    def test_pal_mcp_clink_still_set(self, monkeypatch):
        monkeypatch.delenv("CLAUDECODE", raising=False)
        agent = self._make_agent()
        env = agent._build_environment()
        assert env["PAL_MCP_CLINK"] == "1"

    def test_claudecode_absent_no_error(self, monkeypatch):
        monkeypatch.delenv("CLAUDECODE", raising=False)
        agent = self._make_agent()
        env = agent._build_environment()
        assert "CLAUDECODE" not in env


# ---------------------------------------------------------------------------
# ClinkProvider passthrough system prompt
# ---------------------------------------------------------------------------


class TestPassthroughSystemPrompt:
    """Verify PASSTHROUGH_SYSTEM_PROMPT content."""

    def test_prompt_prohibits_file_writes(self):
        assert "Do NOT create, modify" in PASSTHROUGH_SYSTEM_PROMPT

    def test_prompt_allows_read_tools(self):
        assert "read-only tools" in PASSTHROUGH_SYSTEM_PROMPT

    def test_prompt_instructs_text_output(self):
        assert "fenced code blocks" in PASSTHROUGH_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# ClinkProvider passthrough client building
# ---------------------------------------------------------------------------


class TestClinkProviderPassthrough:
    """Verify ClinkProvider._build_passthrough_client replaces args correctly."""

    def _make_client(self, runner: str) -> ResolvedCLIClient:
        role = ResolvedCLIRole(name="default", prompt_path=Path(__file__), role_args=[])
        return ResolvedCLIClient(
            name=runner,
            executable=[runner],
            internal_args=["--old-internal"],
            config_args=["--old-config"],
            env={},
            timeout_seconds=30,
            parser="claude_json",
            runner=runner,
            roles={"default": role},
            output_to_file=None,
            working_dir=None,
        )

    def test_passthrough_replaces_internal_args_claude(self):
        provider = ClinkProvider.__new__(ClinkProvider)
        client = self._make_client("claude")
        result = provider._build_passthrough_client(client)
        assert result.internal_args == PASSTHROUGH_ARGS["claude"]
        assert result.config_args == []

    def test_passthrough_replaces_internal_args_gemini(self):
        provider = ClinkProvider.__new__(ClinkProvider)
        client = self._make_client("gemini")
        result = provider._build_passthrough_client(client)
        assert result.internal_args == PASSTHROUGH_ARGS["gemini"]
        assert result.config_args == []

    def test_passthrough_replaces_internal_args_codex(self):
        provider = ClinkProvider.__new__(ClinkProvider)
        client = self._make_client("codex")
        result = provider._build_passthrough_client(client)
        assert result.internal_args == PASSTHROUGH_ARGS["codex"]
        assert result.config_args == []

    def test_unknown_runner_falls_back_to_original_internal_args(self):
        provider = ClinkProvider.__new__(ClinkProvider)
        client = self._make_client("unknown-cli")
        result = provider._build_passthrough_client(client)
        assert result.internal_args == ["--old-internal"]
        assert result.config_args == []


# ---------------------------------------------------------------------------
# ClinkProvider slug-based model validation
# ---------------------------------------------------------------------------


class TestClinkProviderModelValidation:
    """Verify slug-based model validation via conf/cli_clients/*.json."""

    @pytest.fixture()
    def provider(self):
        provider = ClinkProvider.__new__(ClinkProvider)
        provider._capabilities_cache = {}
        provider._registry = get_registry()
        return provider

    def test_accepts_cli_default(self, provider):
        assert provider.validate_model_name("gemini")

    def test_accepts_model_specific_slug(self, provider):
        assert provider.validate_model_name("sonnet")

    def test_accepts_bare_model_name(self, provider):
        assert provider.validate_model_name("gemini-2.5-flash")

    def test_rejects_unknown_slug(self, provider):
        assert not provider.validate_model_name("nonexistent-model-xyz")

    def test_rejects_colon_prefix(self, provider):
        assert not provider.validate_model_name("clink:gemini")


# ---------------------------------------------------------------------------
# Registry model slug resolution
# ---------------------------------------------------------------------------


class TestRegistryModelSlugs:
    """Verify the registry resolves model slugs to clients and real model names."""

    def test_claude_resolves_to_default(self):
        registry = get_registry()
        client, real_model = registry.resolve_model_slug("claude")
        assert client.name == "claude"
        assert real_model is None

    def test_sonnet_resolves_to_sonnet(self):
        registry = get_registry()
        client, real_model = registry.resolve_model_slug("sonnet")
        assert client.name == "claude"
        assert real_model == "sonnet"

    def test_gemini_resolves_to_default(self):
        registry = get_registry()
        client, real_model = registry.resolve_model_slug("gemini")
        assert client.name == "gemini"
        assert real_model is None

    def test_gemini_specific_model(self):
        registry = get_registry()
        client, real_model = registry.resolve_model_slug("gemini-2.5-flash")
        assert client.name == "gemini"
        assert real_model == "gemini-2.5-flash"

    def test_list_model_slugs_includes_all_clients(self):
        registry = get_registry()
        slugs = registry.list_model_slugs()
        client_names = set(slugs.values())
        assert "claude" in client_names
        assert "gemini" in client_names
        assert "codex" in client_names

    def test_unknown_slug_raises_key_error(self):
        registry = get_registry()
        with pytest.raises(KeyError):
            registry.resolve_model_slug("nonexistent-slug")


# ---------------------------------------------------------------------------
# CLI client configs
# ---------------------------------------------------------------------------


class TestCliClientConfigs:
    """Verify conf/cli_clients/*.json have the required stable flags."""

    @staticmethod
    def _load_config(name: str) -> dict:
        config_path = Path(__file__).resolve().parent.parent / "conf" / "cli_clients" / f"{name}.json"
        return json.loads(config_path.read_text())

    def test_gemini_config_has_yolo(self):
        config = self._load_config("gemini")
        assert "--yolo" in config["additional_args"]

    def test_codex_config_has_skip_git_repo_check(self):
        config = self._load_config("codex")
        assert "--skip-git-repo-check" in config["additional_args"]

    def test_codex_config_has_json(self):
        config = self._load_config("codex")
        assert "--json" in config["additional_args"]

    def test_codex_config_has_bypass_sandbox(self):
        config = self._load_config("codex")
        assert "--dangerously-bypass-approvals-and-sandbox" in config["additional_args"]

    def test_claude_config_has_permission_mode(self):
        config = self._load_config("claude")
        args = config["additional_args"]
        assert "--permission-mode" in args
        idx = args.index("--permission-mode")
        assert args[idx + 1] == "acceptEdits"

    def test_claude_config_has_models(self):
        config = self._load_config("claude")
        assert "claude" in config["models"]
        assert "sonnet" in config["models"]

    def test_gemini_config_has_models(self):
        config = self._load_config("gemini")
        assert "gemini" in config["models"]
        assert "gemini-2.5-flash" in config["models"]

    def test_codex_config_has_models(self):
        config = self._load_config("codex")
        assert "codex" in config["models"]
