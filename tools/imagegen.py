"""Image generation tool — native AI image creation and editing."""

import logging
from typing import TYPE_CHECKING, Any, Optional

from pydantic import Field

if TYPE_CHECKING:
    from tools.models import ToolModelCategory

from systemprompts import IMAGEGEN_PROMPT
from tools.shared.base_models import ToolRequest

from .simple.base import SimpleTool

logger = logging.getLogger(__name__)

IMAGEGEN_FIELD_DESCRIPTIONS = {
    "prompt": (
        "Describe the image to generate. Be as specific or abstract as you like — "
        "the model expands terse descriptions into rich visual prompts. "
        "For editing, describe the desired changes to the reference image."
    ),
    "media": "Optional reference images (absolute paths or base64) for style transfer or editing.",
}


class ImageGenRequest(ToolRequest):
    prompt: str = Field(..., description=IMAGEGEN_FIELD_DESCRIPTIONS["prompt"])
    media: Optional[list[str]] = Field(
        default_factory=list,
        description=IMAGEGEN_FIELD_DESCRIPTIONS["media"],
    )


class ImageGenTool(SimpleTool):
    def get_name(self) -> str:
        return "imagegen"

    def get_description(self) -> str:
        return (
            "Generate images using AI models with native image generation capability. "
            "Describe what you want and receive generated images. Supports iterative "
            "refinement via continuation_id and image editing via reference images in media."
        )

    def get_annotations(self) -> dict[str, Any]:
        return {"readOnlyHint": True}

    def get_system_prompt(self) -> str:
        return IMAGEGEN_PROMPT

    def get_default_temperature(self) -> float:
        from config import TEMPERATURE_CREATIVE

        return TEMPERATURE_CREATIVE

    def get_model_category(self) -> "ToolModelCategory":
        from tools.models import ToolModelCategory

        return ToolModelCategory.IMAGE_GENERATION

    def get_request_model(self):
        return ImageGenRequest

    def get_tool_fields(self) -> dict[str, dict[str, Any]]:
        return {
            "prompt": {
                "type": "string",
                "description": IMAGEGEN_FIELD_DESCRIPTIONS["prompt"],
            },
            "media": {
                "type": "array",
                "items": {"type": "string"},
                "description": IMAGEGEN_FIELD_DESCRIPTIONS["media"],
            },
        }

    def get_required_fields(self) -> list[str]:
        return ["prompt"]

    def _pre_execute_validate(self) -> Optional[dict]:
        """Verify the resolved model supports image generation."""
        ctx = getattr(self, "_model_context", None)
        if ctx is None:
            return None
        caps = getattr(ctx, "capabilities", None)
        if caps is None:
            return None
        if not caps.supports_image_generation:
            model_name = getattr(ctx, "model_name", "unknown")
            return {
                "status": "error",
                "content": (
                    f"Image generation requires a Gemini model with native image generation support "
                    f"(e.g. 'gemini-3-pro-image-preview', 'gemini-3.1-flash-image-preview', or 'gemini-2.0-flash'). "
                    f"Model '{model_name}' does not support image generation. "
                    f"Specify a capable model explicitly using the model parameter."
                ),
                "content_type": "text",
                "metadata": {
                    "error_type": "capability_error",
                    "model_name": model_name,
                    "supports_image_generation": False,
                },
            }
        return None

    async def prepare_prompt(self, request) -> str:
        return self.get_request_prompt(request)

    def format_response(self, response: str, request, model_info=None) -> str:
        if not response or not response.strip():
            return "Image generated successfully based on your description."
        return response
