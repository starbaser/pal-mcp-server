import json
from pathlib import Path

import pytest

from clink import get_registry
from clink.agents import AgentOutput
from clink.parsers.base import ParsedCLIResponse
from config import MAX_MCP_OUTPUT_TOKENS
from tools.clink import CLinkTool


@pytest.mark.asyncio
async def test_clink_tool_execute(monkeypatch):
    tool = CLinkTool()

    async def fake_run(**kwargs):
        return AgentOutput(
            parsed=ParsedCLIResponse(content="Hello from Gemini", metadata={"model_used": "gemini-2.5-pro"}),
            sanitized_command=["gemini", "-o", "json"],
            returncode=0,
            stdout='{"response": "Hello from Gemini"}',
            stderr="",
            duration_seconds=0.42,
            parser_name="gemini_json",
            output_file_content=None,
        )

    class DummyAgent:
        async def run(self, **kwargs):
            return await fake_run(**kwargs)

    def fake_create_agent(client):
        return DummyAgent()

    monkeypatch.setattr("tools.clink.create_agent", fake_create_agent)

    arguments = {
        "prompt": "Summarize the project",
        "cwd": "/tmp",
        "cli_name": "gemini",
        "role": "default",
        "absolute_file_paths": [],
        "images": [],
    }

    results = await tool.execute(arguments)
    assert len(results) == 1

    payload = json.loads(results[0].text)
    assert payload["status"] in {"success", "continuation_available"}
    assert "Hello from Gemini" in payload["content"]
    metadata = payload.get("metadata", {})
    assert metadata.get("cli_name") == "gemini"
    assert metadata.get("command") == ["gemini", "-o", "json"]


def test_registry_lists_roles():
    registry = get_registry()
    clients = registry.list_clients()
    assert {"codex", "gemini"}.issubset(set(clients))
    roles = registry.list_roles("gemini")
    assert "default" in roles
    assert "default" in registry.list_roles("codex")
    codex_client = registry.get_client("codex")
    # Verify codex uses --enable web_search_request (not --search which is unsupported by exec)
    assert codex_client.config_args == [
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
        "--enable",
        "web_search_request",
    ]


@pytest.mark.asyncio
async def test_clink_tool_defaults_to_first_cli(monkeypatch):
    tool = CLinkTool()

    async def fake_run(**kwargs):
        return AgentOutput(
            parsed=ParsedCLIResponse(content="Default CLI response", metadata={"events": ["foo"]}),
            sanitized_command=["gemini"],
            returncode=0,
            stdout='{"response": "Default CLI response"}',
            stderr="",
            duration_seconds=0.1,
            parser_name="gemini_json",
            output_file_content=None,
        )

    class DummyAgent:
        async def run(self, **kwargs):
            return await fake_run(**kwargs)

    monkeypatch.setattr("tools.clink.create_agent", lambda client: DummyAgent())

    arguments = {
        "prompt": "Hello",
        "cwd": "/tmp",
        "absolute_file_paths": [],
        "images": [],
    }

    result = await tool.execute(arguments)
    payload = json.loads(result[0].text)
    metadata = payload.get("metadata", {})
    assert metadata.get("cli_name") == tool._default_cli_name
    assert metadata.get("events_removed_for_normal") is True


@pytest.mark.asyncio
async def test_clink_tool_offloads_large_output(monkeypatch, tmp_path):
    tool = CLinkTool()

    # Generate text that exceeds the token limit (~4 chars per token)
    long_text = "word " * (MAX_MCP_OUTPUT_TOKENS + 1000)

    async def fake_run(**kwargs):
        return AgentOutput(
            parsed=ParsedCLIResponse(
                content=long_text,
                metadata={"events": ["event1", "event2"], "session_id": "test-session-123"},
            ),
            sanitized_command=["codex"],
            returncode=0,
            stdout="{}",
            stderr="",
            duration_seconds=0.2,
            parser_name="codex_jsonl",
            output_file_content=None,
        )

    class DummyAgent:
        async def run(self, **kwargs):
            return await fake_run(**kwargs)

    monkeypatch.setattr("tools.clink.create_agent", lambda client: DummyAgent())

    arguments = {
        "prompt": "Summarize",
        "cwd": str(tmp_path),
        "cli_name": tool._default_cli_name,
        "absolute_file_paths": [],
        "images": [],
    }

    result = await tool.execute(arguments)
    payload = json.loads(result[0].text)
    assert payload["status"] in {"success", "continuation_available"}
    assert "exceeded the MCP output token limit" in payload["content"]

    metadata = payload.get("metadata", {})
    assert metadata.get("output_offloaded") is True
    assert metadata.get("output_limit") == MAX_MCP_OUTPUT_TOKENS

    # Verify file was written with correct content
    output_file = Path(metadata["output_file"])
    assert output_file.exists()
    assert output_file.read_text(encoding="utf-8") == long_text
    assert output_file.suffix == ".md"
    assert output_file.parent == tmp_path / ".claude" / "output"


@pytest.mark.asyncio
async def test_clink_tool_with_json_schema(monkeypatch):
    """Verify json_schema parameter is passed to agent."""
    tool = CLinkTool()
    received_schema = None

    async def fake_run(**kwargs):
        nonlocal received_schema
        received_schema = kwargs.get("json_schema")
        return AgentOutput(
            parsed=ParsedCLIResponse(content="Response with schema", metadata={"schema_used": True}),
            sanitized_command=["claude", "--json-schema", '{"type": "object"}'],
            returncode=0,
            stdout='{"response": "Success"}',
            stderr="",
            duration_seconds=0.1,
            parser_name="claude_json",
            output_file_content=None,
        )

    class DummyAgent:
        async def run(self, **kwargs):
            return await fake_run(**kwargs)

    monkeypatch.setattr("tools.clink.create_agent", lambda client: DummyAgent())

    schema = {"type": "object", "properties": {"result": {"type": "string"}}}
    arguments = {
        "prompt": "Test prompt",
        "cwd": "/tmp",
        "cli_name": "claude",
        "json_schema": schema,
        "absolute_file_paths": [],
        "images": [],
    }

    result = await tool.execute(arguments)
    assert len(result) == 1
    assert received_schema == schema


@pytest.mark.asyncio
async def test_clink_tool_with_empty_json_schema(monkeypatch):
    """Test empty dict {} is handled gracefully."""
    tool = CLinkTool()

    async def fake_run(**kwargs):
        return AgentOutput(
            parsed=ParsedCLIResponse(content="Response", metadata={}),
            sanitized_command=["claude", "--json-schema", "{}"],
            returncode=0,
            stdout='{"response": "Success"}',
            stderr="",
            duration_seconds=0.1,
            parser_name="claude_json",
            output_file_content=None,
        )

    class DummyAgent:
        async def run(self, **kwargs):
            return await fake_run(**kwargs)

    monkeypatch.setattr("tools.clink.create_agent", lambda client: DummyAgent())

    arguments = {
        "prompt": "Test",
        "cwd": "/tmp",
        "cli_name": "claude",
        "json_schema": {},  # Empty schema
        "absolute_file_paths": [],
        "images": [],
    }

    result = await tool.execute(arguments)
    assert len(result) == 1


@pytest.mark.asyncio
async def test_clink_tool_json_schema_backwards_compat(monkeypatch):
    """Test calls without json_schema continue to work."""
    tool = CLinkTool()

    async def fake_run(**kwargs):
        # Verify json_schema is None or not present
        assert kwargs.get("json_schema") is None
        return AgentOutput(
            parsed=ParsedCLIResponse(content="Normal response", metadata={}),
            sanitized_command=["claude"],
            returncode=0,
            stdout='{"response": "Success"}',
            stderr="",
            duration_seconds=0.1,
            parser_name="claude_json",
            output_file_content=None,
        )

    class DummyAgent:
        async def run(self, **kwargs):
            return await fake_run(**kwargs)

    monkeypatch.setattr("tools.clink.create_agent", lambda client: DummyAgent())

    arguments = {
        "prompt": "Test",
        "cwd": "/tmp",
        "cli_name": "claude",
        # No json_schema parameter
        "absolute_file_paths": [],
        "images": [],
    }

    result = await tool.execute(arguments)
    assert len(result) == 1
