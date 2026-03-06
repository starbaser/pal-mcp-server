"""Tests for multimodal capability enforcement — explicit errors, no silent fallbacks."""

import tempfile
import os
from unittest.mock import MagicMock, Mock, patch

import pytest

from providers.shared.model_capabilities import ModelCapabilities
from providers.shared import ProviderType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_capabilities(
    *,
    supports_images: bool = True,
    supports_video: bool = False,
    supports_audio: bool = False,
    supports_image_generation: bool = False,
    provider: ProviderType = ProviderType.OPENAI,
    model_name: str = "test-model",
) -> ModelCapabilities:
    """Build a real ModelCapabilities instance with the given flags."""
    return ModelCapabilities(
        provider=provider,
        model_name=model_name,
        friendly_name=model_name,
        supports_images=supports_images,
        supports_video=supports_video,
        supports_audio=supports_audio,
        supports_image_generation=supports_image_generation,
    )


# ---------------------------------------------------------------------------
# Section A: OpenAI-compatible provider raises ValueError for unsupported media
# ---------------------------------------------------------------------------


class TestOpenAICompatibleMediaErrors:
    """OpenAI-compatible provider must raise ValueError for audio or video inputs."""

    def _make_provider(self):
        from providers.xai import XAIModelProvider

        return XAIModelProvider(api_key="test-key")

    def test_raises_on_audio_input(self) -> None:
        """Audio files must raise ValueError pointing to Gemini."""
        provider = self._make_provider()

        caps_mock = MagicMock()
        caps_mock.supports_images = True
        caps_mock.get_effective_temperature.return_value = 0.3
        caps_mock.use_openai_response_api = False

        with patch.object(provider, "validate_model_name", return_value=True), \
             patch.object(provider, "get_capabilities", return_value=caps_mock), \
             patch.object(provider, "_resolve_model_name", return_value="grok-2-vision"), \
             patch.object(provider, "validate_parameters"):

            with pytest.raises(ValueError, match="[Aa]udio"):
                provider.generate_content(
                    prompt="describe this",
                    model_name="grok-2-vision",
                    media=["/path/to/clip.mp3"],
                )

    def test_raises_on_video_input(self) -> None:
        """Video files must raise ValueError pointing to Gemini."""
        provider = self._make_provider()

        caps_mock = MagicMock()
        caps_mock.supports_images = True
        caps_mock.get_effective_temperature.return_value = 0.3
        caps_mock.use_openai_response_api = False

        with patch.object(provider, "validate_model_name", return_value=True), \
             patch.object(provider, "get_capabilities", return_value=caps_mock), \
             patch.object(provider, "_resolve_model_name", return_value="grok-2-vision"), \
             patch.object(provider, "validate_parameters"):

            with pytest.raises(ValueError, match="[Vv]ideo"):
                provider.generate_content(
                    prompt="describe this",
                    model_name="grok-2-vision",
                    media=["/path/to/clip.mp4"],
                )

    def test_raises_when_images_not_supported(self) -> None:
        """Media provided to a model without supports_images must raise ValueError."""
        provider = self._make_provider()

        caps_mock = MagicMock()
        caps_mock.supports_images = False
        caps_mock.get_effective_temperature.return_value = 0.3
        caps_mock.use_openai_response_api = False

        with patch.object(provider, "validate_model_name", return_value=True), \
             patch.object(provider, "get_capabilities", return_value=caps_mock), \
             patch.object(provider, "_resolve_model_name", return_value="grok-text-only"), \
             patch.object(provider, "validate_parameters"):

            with pytest.raises(ValueError):
                provider.generate_content(
                    prompt="describe this",
                    model_name="grok-text-only",
                    media=["/path/to/image.jpg"],
                )

    def test_raises_when_capabilities_none_and_media_provided(self) -> None:
        """When capabilities cannot be resolved and media is provided, ValueError must be raised."""
        provider = self._make_provider()

        with patch.object(provider, "validate_model_name", return_value=True), \
             patch.object(provider, "get_capabilities", side_effect=Exception("not found")), \
             patch.object(provider, "_resolve_model_name", return_value="unknown-model"), \
             patch.object(provider, "validate_parameters"):

            with pytest.raises(ValueError):
                provider.generate_content(
                    prompt="describe this",
                    model_name="unknown-model",
                    media=["/path/to/image.jpg"],
                )

    def test_no_error_when_media_is_none(self) -> None:
        """No media validation should occur when media is None."""
        from providers.xai import XAIModelProvider

        caps_mock = MagicMock()
        caps_mock.supports_images = False
        caps_mock.get_effective_temperature.return_value = 0.3
        caps_mock.use_openai_response_api = False
        caps_mock.default_reasoning_effort = None

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "hello"
        mock_response.choices[0].finish_reason = "stop"
        mock_response.usage.prompt_tokens = 5
        mock_response.usage.completion_tokens = 3
        mock_response.usage.total_tokens = 8
        mock_response.model = "grok-text"

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        provider = self._make_provider()

        with patch.object(provider, "validate_model_name", return_value=True), \
             patch.object(provider, "get_capabilities", return_value=caps_mock), \
             patch.object(provider, "_resolve_model_name", return_value="grok-text"), \
             patch.object(provider, "validate_parameters"), \
             patch.object(XAIModelProvider, "client", new_callable=lambda: property(lambda self: mock_client)):

            # Should NOT raise — no media provided
            result = provider.generate_content(
                prompt="hello",
                model_name="grok-text",
                media=None,
            )
            assert result.content == "hello"

    def test_audio_error_message_mentions_gemini(self) -> None:
        """The audio ValueError message must guide the user toward a Gemini model."""
        provider = self._make_provider()

        caps_mock = MagicMock()
        caps_mock.supports_images = True
        caps_mock.get_effective_temperature.return_value = 0.3

        with patch.object(provider, "validate_model_name", return_value=True), \
             patch.object(provider, "get_capabilities", return_value=caps_mock), \
             patch.object(provider, "_resolve_model_name", return_value="grok-2-vision"), \
             patch.object(provider, "validate_parameters"):

            with pytest.raises(ValueError) as exc_info:
                provider.generate_content(
                    prompt="transcribe",
                    model_name="grok-2-vision",
                    media=["interview.mp3"],
                )

        assert "Gemini" in str(exc_info.value) or "gemini" in str(exc_info.value).lower()

    def test_video_error_message_mentions_gemini(self) -> None:
        """The video ValueError message must guide the user toward a Gemini model."""
        provider = self._make_provider()

        caps_mock = MagicMock()
        caps_mock.supports_images = True
        caps_mock.get_effective_temperature.return_value = 0.3

        with patch.object(provider, "validate_model_name", return_value=True), \
             patch.object(provider, "get_capabilities", return_value=caps_mock), \
             patch.object(provider, "_resolve_model_name", return_value="grok-2-vision"), \
             patch.object(provider, "validate_parameters"):

            with pytest.raises(ValueError) as exc_info:
                provider.generate_content(
                    prompt="describe this clip",
                    model_name="grok-2-vision",
                    media=["demo.mp4"],
                )

        assert "Gemini" in str(exc_info.value) or "gemini" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Section B: ImageGenTool._pre_execute_validate() capability guard
# ---------------------------------------------------------------------------


class TestImageGenCapabilityGuard:
    """ImageGenTool._pre_execute_validate() must block non-image-gen models early."""

    def test_errors_without_image_gen_model(self) -> None:
        """Must return an error dict when model lacks supports_image_generation."""
        from tools.imagegen import ImageGenTool

        tool = ImageGenTool()
        mock_ctx = Mock()
        mock_ctx.capabilities = _make_capabilities(supports_image_generation=False)
        mock_ctx.model_name = "gpt-5.2"
        tool._model_context = mock_ctx

        result = tool._pre_execute_validate()

        assert result is not None
        assert result["status"] == "error"
        assert "image generation" in result["content"].lower()
        assert result["metadata"]["supports_image_generation"] is False
        assert result["metadata"]["model_name"] == "gpt-5.2"

    def test_error_metadata_contains_error_type(self) -> None:
        """Error metadata must include error_type for downstream consumers."""
        from tools.imagegen import ImageGenTool

        tool = ImageGenTool()
        mock_ctx = Mock()
        mock_ctx.capabilities = _make_capabilities(supports_image_generation=False)
        mock_ctx.model_name = "o3"
        tool._model_context = mock_ctx

        result = tool._pre_execute_validate()

        assert result["metadata"]["error_type"] == "capability_error"

    def test_passes_with_image_gen_model(self) -> None:
        """Must return None when the model supports image generation."""
        from tools.imagegen import ImageGenTool

        tool = ImageGenTool()
        mock_ctx = Mock()
        mock_ctx.capabilities = _make_capabilities(supports_image_generation=True)
        tool._model_context = mock_ctx

        result = tool._pre_execute_validate()

        assert result is None

    def test_passes_when_no_context(self) -> None:
        """Must return None gracefully when _model_context is not set."""
        from tools.imagegen import ImageGenTool

        tool = ImageGenTool()
        tool._model_context = None

        result = tool._pre_execute_validate()

        assert result is None

    def test_passes_when_no_capabilities_on_context(self) -> None:
        """Must return None gracefully when capabilities attribute is absent."""
        from tools.imagegen import ImageGenTool

        tool = ImageGenTool()
        mock_ctx = Mock(spec=[])  # No attributes by spec
        tool._model_context = mock_ctx

        result = tool._pre_execute_validate()

        assert result is None

    def test_error_content_names_capable_models(self) -> None:
        """Error message must mention at least one capable model for user guidance."""
        from tools.imagegen import ImageGenTool

        tool = ImageGenTool()
        mock_ctx = Mock()
        mock_ctx.capabilities = _make_capabilities(supports_image_generation=False)
        mock_ctx.model_name = "gpt-4.1"
        tool._model_context = mock_ctx

        result = tool._pre_execute_validate()

        # Must mention something about Gemini image models
        content = result["content"]
        assert "gemini" in content.lower() or "imagen" in content.lower()


# ---------------------------------------------------------------------------
# Section C: Gemini provider raises ValueError for unsupported media types
# ---------------------------------------------------------------------------


class TestGeminiMediaEnforcement:
    """Gemini provider must raise ValueError for unsupported media types on the model."""

    def _make_provider(self):
        from providers.gemini import GeminiModelProvider

        return GeminiModelProvider(api_key="test-key")

    def test_raises_on_audio_when_not_supported(self) -> None:
        """Audio on a model without supports_audio must raise ValueError."""
        provider = self._make_provider()

        caps_mock = MagicMock()
        caps_mock.supports_images = True
        caps_mock.supports_video = False
        caps_mock.supports_audio = False
        caps_mock.supports_image_generation = False
        caps_mock.get_effective_temperature.return_value = 1.0

        with patch.object(provider, "validate_parameters"), \
             patch.object(provider, "get_capabilities", return_value=caps_mock), \
             patch.object(provider, "get_all_model_capabilities", return_value={}), \
             patch.object(provider, "_resolve_model_name", return_value="gemini-flash-lite"):

            with pytest.raises(ValueError, match="[Aa]udio"):
                provider.generate_content(
                    prompt="transcribe this",
                    model_name="gemini-flash-lite",
                    media=["speech.mp3"],
                )

    def test_raises_on_video_when_not_supported(self) -> None:
        """Video on a model without supports_video must raise ValueError."""
        provider = self._make_provider()

        caps_mock = MagicMock()
        caps_mock.supports_images = True
        caps_mock.supports_video = False
        caps_mock.supports_audio = False
        caps_mock.supports_image_generation = False
        caps_mock.get_effective_temperature.return_value = 1.0

        with patch.object(provider, "validate_parameters"), \
             patch.object(provider, "get_capabilities", return_value=caps_mock), \
             patch.object(provider, "get_all_model_capabilities", return_value={}), \
             patch.object(provider, "_resolve_model_name", return_value="gemini-no-video"):

            with pytest.raises(ValueError, match="[Vv]ideo"):
                provider.generate_content(
                    prompt="describe this video",
                    model_name="gemini-no-video",
                    media=["clip.mp4"],
                )

    def test_raises_when_images_not_supported_and_media_provided(self) -> None:
        """Media on a model that does not support images must raise ValueError."""
        provider = self._make_provider()

        caps_mock = MagicMock()
        caps_mock.supports_images = False
        caps_mock.supports_video = False
        caps_mock.supports_audio = False
        caps_mock.supports_image_generation = False
        caps_mock.get_effective_temperature.return_value = 1.0

        with patch.object(provider, "validate_parameters"), \
             patch.object(provider, "get_capabilities", return_value=caps_mock), \
             patch.object(provider, "get_all_model_capabilities", return_value={}), \
             patch.object(provider, "_resolve_model_name", return_value="gemini-text-only"):

            with pytest.raises(ValueError):
                provider.generate_content(
                    prompt="describe this",
                    model_name="gemini-text-only",
                    media=["photo.jpg"],
                )

    def test_audio_passes_when_supports_audio(self) -> None:
        """Audio media must not raise when the model declares supports_audio."""
        from providers.gemini import GeminiModelProvider

        caps_mock = MagicMock()
        caps_mock.supports_images = True
        caps_mock.supports_video = True
        caps_mock.supports_audio = True
        caps_mock.supports_image_generation = False
        caps_mock.get_effective_temperature.return_value = 1.0

        mock_audio_part = {"inline_data": {"mime_type": "audio/mpeg", "data": "abc=="}}

        mock_resp = MagicMock()
        mock_resp.candidates = [MagicMock()]
        mock_resp.candidates[0].content.parts = [MagicMock(text="OK")]
        mock_resp.candidates[0].finish_reason = "STOP"
        mock_resp.usage_metadata.prompt_token_count = 10
        mock_resp.usage_metadata.candidates_token_count = 5
        mock_resp.usage_metadata.total_token_count = 15

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_resp

        provider = self._make_provider()

        with patch.object(provider, "validate_parameters"), \
             patch.object(provider, "get_capabilities", return_value=caps_mock), \
             patch.object(provider, "get_all_model_capabilities", return_value={}), \
             patch.object(provider, "_resolve_model_name", return_value="gemini-2.5-flash"), \
             patch.object(provider, "_process_audio", return_value=mock_audio_part), \
             patch.object(GeminiModelProvider, "client", new_callable=lambda: property(lambda self: mock_client)):

            # Must not raise
            result = provider.generate_content(
                prompt="transcribe",
                model_name="gemini-2.5-flash",
                media=["speech.mp3"],
            )
            assert result is not None

    def test_no_media_validation_when_media_list_empty(self) -> None:
        """Empty media list must skip all media-related validation."""
        from providers.gemini import GeminiModelProvider

        caps_mock = MagicMock()
        caps_mock.supports_images = False
        caps_mock.supports_video = False
        caps_mock.supports_audio = False
        caps_mock.supports_image_generation = False
        caps_mock.get_effective_temperature.return_value = 1.0

        mock_resp = MagicMock()
        mock_resp.candidates = [MagicMock()]
        mock_resp.candidates[0].content.parts = [MagicMock(text="hello")]
        mock_resp.candidates[0].finish_reason = "STOP"
        mock_resp.usage_metadata.prompt_token_count = 3
        mock_resp.usage_metadata.candidates_token_count = 2
        mock_resp.usage_metadata.total_token_count = 5

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_resp

        provider = self._make_provider()

        with patch.object(provider, "validate_parameters"), \
             patch.object(provider, "get_capabilities", return_value=caps_mock), \
             patch.object(provider, "get_all_model_capabilities", return_value={}), \
             patch.object(provider, "_resolve_model_name", return_value="gemini-text"), \
             patch.object(GeminiModelProvider, "client", new_callable=lambda: property(lambda self: mock_client)):

            # Should NOT raise — no media at all
            result = provider.generate_content(
                prompt="hello",
                model_name="gemini-text",
                media=None,
            )
            assert result is not None


# ---------------------------------------------------------------------------
# Section D: ModelCapabilities.supports_audio field
# ---------------------------------------------------------------------------


class TestModelCapabilitiesAudioField:
    """ModelCapabilities must expose supports_audio with correct defaults."""

    def test_default_supports_audio_is_false(self) -> None:
        """A freshly constructed ModelCapabilities must not claim audio support."""
        caps = ModelCapabilities(
            provider=ProviderType.GOOGLE,
            model_name="gemini-test",
            friendly_name="Gemini Test",
        )

        assert caps.supports_audio is False

    def test_supports_audio_can_be_enabled(self) -> None:
        """supports_audio must be settable to True."""
        caps = ModelCapabilities(
            provider=ProviderType.GOOGLE,
            model_name="gemini-2.5-flash",
            friendly_name="Gemini 2.5 Flash",
            supports_audio=True,
        )

        assert caps.supports_audio is True

    def test_supports_audio_independent_of_supports_images(self) -> None:
        """supports_audio and supports_images are independent flags."""
        caps_images_no_audio = ModelCapabilities(
            provider=ProviderType.GOOGLE,
            model_name="gemini-vis",
            friendly_name="Gemini Vision",
            supports_images=True,
            supports_audio=False,
        )
        caps_audio_no_images = ModelCapabilities(
            provider=ProviderType.GOOGLE,
            model_name="gemini-audio",
            friendly_name="Gemini Audio",
            supports_images=False,
            supports_audio=True,
        )

        assert caps_images_no_audio.supports_images is True
        assert caps_images_no_audio.supports_audio is False

        assert caps_audio_no_images.supports_images is False
        assert caps_audio_no_images.supports_audio is True
