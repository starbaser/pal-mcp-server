"""Unit tests for ImageGenTool."""

import pytest
from pydantic import ValidationError

from config import TEMPERATURE_CREATIVE
from tools.imagegen import ImageGenRequest, ImageGenTool
from tools.models import ToolModelCategory

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tool() -> ImageGenTool:
    return ImageGenTool()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_imagegen_tool_name(tool: ImageGenTool) -> None:
    assert tool.get_name() == "imagegen"


def test_imagegen_model_category(tool: ImageGenTool) -> None:
    assert tool.get_model_category() == ToolModelCategory.IMAGE_GENERATION


def test_imagegen_format_response_empty(tool: ImageGenTool) -> None:
    request = ImageGenRequest(prompt="a mountain at sunset")
    result = tool.format_response("", request)
    assert result == "Image generated successfully based on your description."


def test_imagegen_format_response_whitespace_only(tool: ImageGenTool) -> None:
    request = ImageGenRequest(prompt="a mountain at sunset")
    result = tool.format_response("   \n  ", request)
    assert result == "Image generated successfully based on your description."


def test_imagegen_format_response_passthrough(tool: ImageGenTool) -> None:
    request = ImageGenRequest(prompt="a mountain at sunset")
    content = "Here is your generated image of a mountain at sunset."
    result = tool.format_response(content, request)
    assert result == content


def test_imagegen_request_model_prompt_required() -> None:
    request = ImageGenRequest(prompt="a serene lake")
    assert request.prompt == "a serene lake"
    assert request.media == []


def test_imagegen_request_model_media_defaults_empty() -> None:
    request = ImageGenRequest(prompt="test prompt")
    assert isinstance(request.media, list)
    assert len(request.media) == 0


def test_imagegen_request_model_media_accepts_list() -> None:
    request = ImageGenRequest(prompt="edit this image", media=["/tmp/ref.png"])
    assert request.media == ["/tmp/ref.png"]


def test_imagegen_request_model_missing_prompt_raises() -> None:
    with pytest.raises(ValidationError):
        ImageGenRequest()


def test_imagegen_default_temperature(tool: ImageGenTool) -> None:
    assert tool.get_default_temperature() == TEMPERATURE_CREATIVE


def test_imagegen_system_prompt(tool: ImageGenTool) -> None:
    prompt = tool.get_system_prompt()
    assert prompt
    assert isinstance(prompt, str)
    assert len(prompt.strip()) > 0
