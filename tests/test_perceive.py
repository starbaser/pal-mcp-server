"""Unit tests for PerceiveTool — capability validation, schema, and prompt logic."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tools.perceive import PerceiveTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(
    *,
    supports_images: bool = True,
    supports_video: bool = False,
    supports_audio: bool = False,
    model_name: str = "test-model",
) -> SimpleNamespace:
    """Build a minimal model context with capability flags."""
    caps = SimpleNamespace(
        supports_images=supports_images,
        supports_video=supports_video,
        supports_audio=supports_audio,
    )
    return SimpleNamespace(capabilities=caps, model_name=model_name)


def _tool_with_context(media: list[str], ctx) -> PerceiveTool:
    """Return a PerceiveTool with _model_context and _current_arguments pre-set."""
    tool = PerceiveTool()
    tool._model_context = ctx
    tool._current_arguments = {"media": media}
    return tool


def _make_request(prompt: str = "") -> SimpleNamespace:
    """Build a minimal request-like object with a prompt attribute."""
    return SimpleNamespace(prompt=prompt)


# ---------------------------------------------------------------------------
# TestPerceiveToolValidation
# ---------------------------------------------------------------------------


class TestPerceiveToolValidation:
    """Test _pre_execute_validate() for media capability checks."""

    def test_no_media_returns_error(self) -> None:
        """Empty media list must return a validation error, not None."""
        ctx = _make_context()
        tool = PerceiveTool()
        tool._model_context = ctx
        tool._current_arguments = {"media": []}

        result = tool._pre_execute_validate()

        assert result is not None
        assert result["status"] == "error"
        assert result["metadata"]["error_type"] == "validation_error"

    def test_image_without_supports_images_returns_error(self) -> None:
        """Image item against a model with supports_images=False must return capability error."""
        ctx = _make_context(supports_images=False, supports_video=True, supports_audio=True)
        tool = _tool_with_context(["/path/to/image.png"], ctx)

        result = tool._pre_execute_validate()

        assert result is not None
        assert result["status"] == "error"
        assert result["metadata"]["error_type"] == "capability_error"
        assert result["metadata"]["supports_image"] is False
        assert "image" in result["content"]

    def test_video_without_supports_video_returns_error(self) -> None:
        """Video item against a model with supports_video=False must return capability error."""
        ctx = _make_context(supports_images=True, supports_video=False, supports_audio=True)
        tool = _tool_with_context(["/path/to/video.mp4"], ctx)

        result = tool._pre_execute_validate()

        assert result is not None
        assert result["status"] == "error"
        assert result["metadata"]["error_type"] == "capability_error"
        assert result["metadata"]["supports_video"] is False
        assert "video" in result["content"]

    def test_audio_without_supports_audio_returns_error(self) -> None:
        """Audio item against a model with supports_audio=False must return capability error."""
        ctx = _make_context(supports_images=True, supports_video=True, supports_audio=False)
        tool = _tool_with_context(["/path/to/audio.mp3"], ctx)

        result = tool._pre_execute_validate()

        assert result is not None
        assert result["status"] == "error"
        assert result["metadata"]["error_type"] == "capability_error"
        assert result["metadata"]["supports_audio"] is False
        assert "audio" in result["content"]

    def test_image_with_capable_model_passes(self) -> None:
        """Image item with supports_images=True must return None."""
        ctx = _make_context(supports_images=True, supports_video=False, supports_audio=False)
        tool = _tool_with_context(["/path/to/photo.jpg"], ctx)

        result = tool._pre_execute_validate()

        assert result is None

    def test_video_with_capable_model_passes(self) -> None:
        """Video item with supports_video=True must return None."""
        ctx = _make_context(supports_images=True, supports_video=True, supports_audio=False)
        tool = _tool_with_context(["/path/to/clip.webm"], ctx)

        result = tool._pre_execute_validate()

        assert result is None

    def test_audio_with_capable_model_passes(self) -> None:
        """Audio item with supports_audio=True must return None."""
        ctx = _make_context(supports_images=True, supports_video=False, supports_audio=True)
        tool = _tool_with_context(["/path/to/recording.wav"], ctx)

        result = tool._pre_execute_validate()

        assert result is None

    def test_mixed_media_checks_all_types(self) -> None:
        """Mixed image + audio list must fail when audio is unsupported."""
        ctx = _make_context(supports_images=True, supports_video=False, supports_audio=False)
        # Image passes, audio must be caught
        tool = _tool_with_context(["/path/to/image.png", "/path/to/audio.mp3"], ctx)

        result = tool._pre_execute_validate()

        assert result is not None
        assert result["status"] == "error"
        assert result["metadata"]["supports_audio"] is False

    def test_mixed_media_video_checked_before_audio(self) -> None:
        """When both video and audio are unsupported, the video error is returned first."""
        ctx = _make_context(supports_images=True, supports_video=False, supports_audio=False)
        tool = _tool_with_context(["/path/to/video.mp4", "/path/to/audio.mp3"], ctx)

        result = tool._pre_execute_validate()

        assert result is not None
        assert result["status"] == "error"
        # Video check fires before audio check
        assert result["metadata"]["supports_video"] is False

    def test_no_model_context_passes(self) -> None:
        """Missing _model_context must return None — no context, no enforcement."""
        tool = PerceiveTool()
        tool._model_context = None
        tool._current_arguments = {"media": ["/path/to/image.png"]}

        result = tool._pre_execute_validate()

        assert result is None

    def test_no_capabilities_on_context_passes(self) -> None:
        """Missing capabilities attribute on context must return None gracefully."""
        ctx = SimpleNamespace(model_name="test-model")  # no .capabilities
        tool = PerceiveTool()
        tool._model_context = ctx
        tool._current_arguments = {"media": ["/path/to/image.png"]}

        result = tool._pre_execute_validate()

        assert result is None

    def test_error_content_names_model(self) -> None:
        """Capability error content must include the model name."""
        ctx = _make_context(supports_images=False, model_name="gpt-4o-mini")
        tool = _tool_with_context(["/path/to/image.png"], ctx)

        result = tool._pre_execute_validate()

        assert result is not None
        assert "gpt-4o-mini" in result["content"]

    def test_error_content_mentions_listmodels(self) -> None:
        """Capability error must guide the user toward listmodels for discovery."""
        ctx = _make_context(supports_images=False)
        tool = _tool_with_context(["/path/to/image.png"], ctx)

        result = tool._pre_execute_validate()

        assert result is not None
        assert "listmodels" in result["content"]

    def test_metadata_model_name_matches_context(self) -> None:
        """Error metadata model_name must reflect the context model_name."""
        ctx = _make_context(supports_video=False, model_name="claude-haiku-3-5")
        tool = _tool_with_context(["/path/to/video.mp4"], ctx)

        result = tool._pre_execute_validate()

        assert result is not None
        assert result["metadata"]["model_name"] == "claude-haiku-3-5"

    def test_all_capable_model_passes_mixed_media(self) -> None:
        """A fully capable model must pass validation for image + video + audio together."""
        ctx = _make_context(supports_images=True, supports_video=True, supports_audio=True)
        media = [
            "/path/to/image.png",
            "/path/to/video.mp4",
            "/path/to/audio.mp3",
        ]
        tool = _tool_with_context(media, ctx)

        result = tool._pre_execute_validate()

        assert result is None


# ---------------------------------------------------------------------------
# TestPerceiveToolSchema
# ---------------------------------------------------------------------------


class TestPerceiveToolSchema:
    """Test get_tool_fields, get_required_fields, get_name, and get_description."""

    @pytest.fixture
    def tool(self) -> PerceiveTool:
        return PerceiveTool()

    def test_media_is_required(self, tool: PerceiveTool) -> None:
        """media must appear in get_required_fields()."""
        assert "media" in tool.get_required_fields()

    def test_prompt_is_optional(self, tool: PerceiveTool) -> None:
        """prompt must not appear in get_required_fields()."""
        assert "prompt" not in tool.get_required_fields()

    def test_tool_name(self, tool: PerceiveTool) -> None:
        """Tool name must be 'perceive'."""
        assert tool.get_name() == "perceive"

    def test_tool_description_is_non_empty(self, tool: PerceiveTool) -> None:
        """Description must be a non-empty string."""
        desc = tool.get_description()
        assert isinstance(desc, str)
        assert len(desc) > 0

    def test_tool_description_mentions_media_types(self, tool: PerceiveTool) -> None:
        """Description must reference at least one supported media type."""
        desc = tool.get_description().lower()
        assert any(word in desc for word in ("image", "video", "audio", "media"))

    def test_annotations_readonly(self, tool: PerceiveTool) -> None:
        """get_annotations() must declare readOnlyHint=True."""
        assert tool.get_annotations().get("readOnlyHint") is True

    def test_tool_fields_contains_media(self, tool: PerceiveTool) -> None:
        """get_tool_fields() must define the media field."""
        fields = tool.get_tool_fields()
        assert "media" in fields
        assert fields["media"]["type"] == "array"
        assert fields["media"]["items"]["type"] == "string"

    def test_tool_fields_contains_prompt(self, tool: PerceiveTool) -> None:
        """get_tool_fields() must define the prompt field."""
        fields = tool.get_tool_fields()
        assert "prompt" in fields
        assert fields["prompt"]["type"] == "string"

    def test_media_field_has_min_items(self, tool: PerceiveTool) -> None:
        """media field schema must declare minItems: 1."""
        fields = tool.get_tool_fields()
        assert fields["media"].get("minItems") == 1

    def test_model_category_is_balanced(self, tool: PerceiveTool) -> None:
        """get_model_category() must return ToolModelCategory.BALANCED."""
        from tools.models import ToolModelCategory

        assert tool.get_model_category() == ToolModelCategory.BALANCED


# ---------------------------------------------------------------------------
# TestPerceivePrompt
# ---------------------------------------------------------------------------


class TestPerceivePrompt:
    """Test prepare_prompt() behavior."""

    @pytest.mark.asyncio
    async def test_with_focus_prompt(self) -> None:
        """Non-empty prompt must be returned verbatim as the prompt string."""
        tool = PerceiveTool()
        request = _make_request(prompt="Focus your analysis on: text extraction")

        result = await tool.prepare_prompt(request)

        assert result == "Focus your analysis on: text extraction"

    @pytest.mark.asyncio
    async def test_without_prompt_uses_comprehensive_fallback(self) -> None:
        """Missing prompt (empty string) must trigger the comprehensive analysis fallback."""
        tool = PerceiveTool()
        request = _make_request(prompt="")

        result = await tool.prepare_prompt(request)

        assert "comprehensive" in result.lower() or "analysis" in result.lower()
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_empty_prompt_differs_from_non_empty(self) -> None:
        """Empty prompt must not return the same string as a non-empty prompt."""
        tool = PerceiveTool()
        user_prompt = "Identify all faces in the image."
        request_with = _make_request(prompt=user_prompt)
        request_without = _make_request(prompt="")

        result_with = await tool.prepare_prompt(request_with)
        result_without = await tool.prepare_prompt(request_without)

        assert result_with == user_prompt
        assert result_with != result_without

    @pytest.mark.asyncio
    async def test_prompt_passed_through_unchanged(self) -> None:
        """prepare_prompt must return the user prompt without modification."""
        tool = PerceiveTool()
        prompt = "List all objects visible in this photo and their approximate positions."
        request = _make_request(prompt=prompt)

        result = await tool.prepare_prompt(request)

        assert result == prompt


# ---------------------------------------------------------------------------
# TestPerceiveFormatResponse
# ---------------------------------------------------------------------------


class TestPerceiveFormatResponse:
    """Test format_response() fallback behavior."""

    @pytest.fixture
    def tool(self) -> PerceiveTool:
        return PerceiveTool()

    def test_non_empty_response_returned_unchanged(self, tool: PerceiveTool) -> None:
        """A non-empty model response must pass through format_response unmodified."""
        request = _make_request()
        response = "The image shows a red barn against a cloudy sky."

        result = tool.format_response(response, request)

        assert result == response

    def test_empty_response_returns_fallback(self, tool: PerceiveTool) -> None:
        """An empty response string must trigger the fallback message."""
        request = _make_request()

        result = tool.format_response("", request)

        assert len(result) > 0
        assert result != ""

    def test_whitespace_only_response_returns_fallback(self, tool: PerceiveTool) -> None:
        """A whitespace-only response must also trigger the fallback message."""
        request = _make_request()

        result = tool.format_response("   \n\t  ", request)

        assert len(result.strip()) > 0

    def test_fallback_message_mentions_analysis(self, tool: PerceiveTool) -> None:
        """Fallback message must indicate that analysis was attempted."""
        request = _make_request()

        result = tool.format_response("", request)

        assert "analysis" in result.lower() or "media" in result.lower()
