import asyncio
import json
import shutil
from pathlib import Path

import pytest

from clink.agents.base import CLIAgentError
from clink.agents.claude import ClaudeAgent
from clink.models import ResolvedCLIClient, ResolvedCLIRole


class DummyProcess:
    def __init__(self, *, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.stdin_data: bytes | None = None

    async def communicate(self, input_data):
        self.stdin_data = input_data
        return self._stdout, self._stderr


@pytest.fixture()
def claude_agent():
    prompt_path = Path("systemprompts/clink/default.txt").resolve()
    role = ResolvedCLIRole(name="default", prompt_path=prompt_path, role_args=[])
    client = ResolvedCLIClient(
        name="claude",
        executable=["claude"],
        internal_args=["--print", "--output-format", "json"],
        config_args=["--permission-mode", "acceptEdits"],
        env={},
        timeout_seconds=30,
        parser="claude_json",
        runner="claude",
        roles={"default": role},
        output_to_file=None,
        working_dir=None,
    )
    return ClaudeAgent(client), role


async def _run_agent_with_process(monkeypatch, agent, role, process, *, system_prompt="System prompt"):
    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return process

    def fake_which(executable_name):
        return f"/usr/bin/{executable_name}"

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(shutil, "which", fake_which)

    return await agent.run(
        role=role,
        prompt="Respond with 42",
        system_prompt=system_prompt,
        files=[],
        images=[],
    )


@pytest.mark.asyncio
async def test_claude_agent_injects_system_prompt(monkeypatch, claude_agent):
    agent, role = claude_agent
    stdout_payload = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "42",
        }
    ).encode()
    process = DummyProcess(stdout=stdout_payload)

    result = await _run_agent_with_process(monkeypatch, agent, role, process)

    assert "--append-system-prompt" in result.sanitized_command
    idx = result.sanitized_command.index("--append-system-prompt")
    assert result.sanitized_command[idx + 1] == "System prompt"
    assert process.stdin_data.decode().startswith("Respond with 42")


@pytest.mark.asyncio
async def test_claude_agent_recovers_error_payload(monkeypatch, claude_agent):
    agent, role = claude_agent
    stdout_payload = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": True,
            "result": "API Error",
        }
    ).encode()
    process = DummyProcess(stdout=stdout_payload, returncode=2)

    result = await _run_agent_with_process(monkeypatch, agent, role, process)

    assert result.returncode == 2
    assert result.parsed.content == "API Error"
    assert result.parsed.metadata["is_error"] is True


@pytest.mark.asyncio
async def test_claude_agent_propagates_unparseable_output(monkeypatch, claude_agent):
    agent, role = claude_agent
    process = DummyProcess(stdout=b"", returncode=1)

    with pytest.raises(CLIAgentError):
        await _run_agent_with_process(monkeypatch, agent, role, process)


@pytest.mark.asyncio
async def test_claude_agent_rejects_non_serializable_schema(monkeypatch, claude_agent):
    """Verify TypeError handling for non-serializable json_schema."""
    agent, role = claude_agent

    # Mock subprocess
    def fake_which(executable_name):
        return f"/usr/bin/{executable_name}"

    monkeypatch.setattr(shutil, "which", fake_which)

    # Non-serializable object
    invalid_schema = {"func": lambda x: x}

    with pytest.raises(CLIAgentError, match="Failed to serialize json_schema"):
        await agent.run(
            role=role,
            prompt="Test prompt",
            system_prompt="System prompt",
            files=[],
            images=[],
            json_schema=invalid_schema,
        )


@pytest.mark.asyncio
async def test_claude_agent_parses_structured_output(monkeypatch, claude_agent):
    """Parser extracts structured_output when result is empty (json-schema mode)."""
    agent, role = claude_agent
    stdout_payload = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "",
            "structured_output": {"status": "ok", "data": [1, 2, 3]},
        }
    ).encode()
    process = DummyProcess(stdout=stdout_payload)

    result = await _run_agent_with_process(monkeypatch, agent, role, process)

    assert '"status": "ok"' in result.parsed.content
    assert '"data"' in result.parsed.content
    assert result.parsed.metadata["is_error"] is False
