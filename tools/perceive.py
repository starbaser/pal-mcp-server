"""Media intelligence extraction — structured analysis of images, video, and audio."""

import logging
from typing import TYPE_CHECKING, Any, Optional

from pydantic import Field

if TYPE_CHECKING:
    from tools.models import ToolModelCategory

from systemprompts import PERCEIVE_PROMPT
from tools.shared.base_models import ToolRequest
from utils.media_utils import is_audio_file, is_video_file

from .simple.base import SimpleTool

logger = logging.getLogger(__name__)

PERCEIVE_FIELD_DESCRIPTIONS = {
    "media": (
        "Media to analyze — absolute file paths or base64 data URLs. "
        "Accepts images (JPEG, PNG, WebP, GIF), video (MP4, MOV, WebM), "
        "and audio (MP3, WAV, OGG, FLAC). At least one item is required."
    ),
    "prompt": (
        "Optional focus prompt. Describe what to look for, extract, or analyze. "
        "When omitted, the model performs comprehensive analysis of all media."
    ),
}


class PerceiveRequest(ToolRequest):
    media: list[str] = Field(..., min_length=1, description=PERCEIVE_FIELD_DESCRIPTIONS["media"])
    prompt: str = Field(default="", description=PERCEIVE_FIELD_DESCRIPTIONS["prompt"])


class PerceiveTool(SimpleTool):
    def get_name(self) -> str:
        return "perceive"

    def get_description(self) -> str:
        return (
            "Extract structured intelligence from images, video, and audio using multimodal AI. "
            "Analyze visual content, transcribe speech, describe scenes, identify objects, "
            "extract text, and answer questions about media. Supports continuation_id for "
            "multi-turn analysis sessions."
        )

    def get_annotations(self) -> dict[str, Any]:
        return {"readOnlyHint": True}

    def get_system_prompt(self) -> str:
        return PERCEIVE_PROMPT

    def get_model_category(self) -> "ToolModelCategory":
        from tools.models import ToolModelCategory

        return ToolModelCategory.BALANCED

    def get_request_model(self):
        return PerceiveRequest

    def get_tool_fields(self) -> dict[str, dict[str, Any]]:
        return {
            "media": {
                "type": "array",
                "items": {"type": "string"},
                "description": PERCEIVE_FIELD_DESCRIPTIONS["media"],
                "minItems": 1,
            },
            "prompt": {
                "type": "string",
                "description": PERCEIVE_FIELD_DESCRIPTIONS["prompt"],
            },
        }

    def get_required_fields(self) -> list[str]:
        return ["media"]

    def _capability_error(self, model_name: str, media_type: str) -> dict:
        """Return a structured capability error dict for unsupported media types."""
        return {
            "status": "error",
            "content": (
                f"Model '{model_name}' does not support {media_type} inputs. "
                f"Use the `listmodels` tool to find a model that supports {media_type}, "
                f"then specify it explicitly via the model parameter."
            ),
            "content_type": "text",
            "metadata": {
                "error_type": "capability_error",
                "model_name": model_name,
                f"supports_{media_type}": False,
            },
        }

    def _pre_execute_validate(self) -> Optional[dict]:
        """Verify the resolved model supports the media types present in the request."""
        ctx = getattr(self, "_model_context", None)
        if ctx is None:
            return None
        caps = getattr(ctx, "capabilities", None)
        if caps is None:
            return None

        media_items = self._current_arguments.get("media", [])
        if not media_items:
            return {
                "status": "error",
                "content": "The `media` field is required and must contain at least one item.",
                "content_type": "text",
                "metadata": {"error_type": "validation_error"},
            }

        model_name = getattr(ctx, "model_name", "unknown")

        has_video = any(is_video_file(m) for m in media_items)
        has_audio = any(is_audio_file(m) for m in media_items)
        has_image = any(not is_video_file(m) and not is_audio_file(m) for m in media_items)

        if has_video and not caps.supports_video:
            return self._capability_error(model_name, "video")
        if has_audio and not caps.supports_audio:
            return self._capability_error(model_name, "audio")
        if has_image and not caps.supports_images:
            return self._capability_error(model_name, "image")

        return None

    async def prepare_prompt(self, request) -> str:
        if request.prompt:
            return request.prompt
        return (
            "Perform a comprehensive analysis of all provided media. "
            "Describe visual content, identify objects and scenes, extract any text, "
            "transcribe speech if present, and surface all noteworthy details."
        )

    def format_response(self, response: str, request, model_info=None) -> str:
        if not response or not response.strip():
            return "Media analysis complete. No textual output was returned by the model."
        return response
