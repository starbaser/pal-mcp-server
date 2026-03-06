"""Utility helpers for validating image and video media inputs."""

import base64
import binascii
import os

from utils.file_types import (
    AUDIO_EXTENSIONS,
    MEDIA_EXTENSIONS,
    MEDIA_MIME_TYPES,
    VIDEO_EXTENSIONS,
)

DEFAULT_MAX_IMAGE_SIZE_MB = 2048.0

__all__ = [
    "DEFAULT_MAX_IMAGE_SIZE_MB",
    "is_audio_file",
    "is_video_file",
    "validate_image",
    "validate_media",
]


def _valid_media_mime_types() -> set[str]:
    """Return the MIME types permitted by the MEDIA_EXTENSIONS whitelist."""
    return set(MEDIA_MIME_TYPES.values())


def validate_media(media_path: str, max_size_mb: float = None) -> tuple[bytes, str]:
    """Validate a user-supplied image or video path or data URL.

    Args:
        media_path: Either a filesystem path or a data URL (image/* or video/*).
        max_size_mb: Optional size limit (defaults to ``DEFAULT_MAX_IMAGE_SIZE_MB``).

    Returns:
        A tuple ``(media_bytes, mime_type)`` ready for upstream providers.

    Raises:
        ValueError: When the media is missing, malformed, or exceeds limits.
    """
    if max_size_mb is None:
        max_size_mb = DEFAULT_MAX_IMAGE_SIZE_MB

    if media_path.startswith("data:"):
        return _validate_data_url(media_path, max_size_mb)

    return _validate_file_path(media_path, max_size_mb)


def validate_image(image_path: str, max_size_mb: float = None) -> tuple[bytes, str]:
    """Deprecated alias for ``validate_media``. Use ``validate_media`` instead."""
    return validate_media(image_path, max_size_mb)


def is_video_file(path_or_data_url: str) -> bool:
    """Return True if the input is a video file or video data URL.

    Args:
        path_or_data_url: Either a filesystem path or a data URL.

    Returns:
        True when the input is identified as a video by extension or MIME type.
    """
    if path_or_data_url.startswith("data:"):
        try:
            header, _ = path_or_data_url.split(",", 1)
            mime_type = header.split(";")[0].split(":")[1]
        except (ValueError, IndexError):
            return False
        return mime_type.startswith("video/")

    ext = os.path.splitext(path_or_data_url)[1].lower()
    return ext in VIDEO_EXTENSIONS


def is_audio_file(path_or_data_url: str) -> bool:
    """Return True if the input is an audio file or audio data URL."""
    if path_or_data_url.startswith("data:"):
        try:
            header, _ = path_or_data_url.split(",", 1)
            mime_type = header.split(";")[0].split(":")[1]
        except (ValueError, IndexError):
            return False
        return mime_type.startswith("audio/")

    ext = os.path.splitext(path_or_data_url)[1].lower()
    return ext in AUDIO_EXTENSIONS


def _validate_data_url(media_data_url: str, max_size_mb: float) -> tuple[bytes, str]:
    """Validate a data URL and return media bytes plus MIME type."""
    try:
        header, data = media_data_url.split(",", 1)
        mime_type = header.split(";")[0].split(":")[1]
    except (ValueError, IndexError) as exc:
        raise ValueError(f"Invalid data URL format: {exc}")

    valid_mime_types = _valid_media_mime_types()
    if mime_type not in valid_mime_types:
        raise ValueError(
            "Unsupported media type: {mime}. Supported types: {supported}".format(
                mime=mime_type, supported=", ".join(sorted(valid_mime_types))
            )
        )

    try:
        media_bytes = base64.b64decode(data)
    except binascii.Error as exc:
        raise ValueError(f"Invalid base64 data: {exc}")

    _validate_media_limits(media_bytes, max_size_mb)
    return media_bytes, mime_type


def _validate_file_path(file_path: str, max_size_mb: float) -> tuple[bytes, str]:
    """Validate a media file loaded from the filesystem."""
    try:
        with open(file_path, "rb") as handle:
            media_bytes = handle.read()
    except FileNotFoundError:
        raise ValueError(f"Media file not found: {file_path}")
    except OSError as exc:
        raise ValueError(f"Failed to read media file: {exc}")

    ext = os.path.splitext(file_path)[1].lower()
    if ext not in MEDIA_EXTENSIONS:
        raise ValueError(
            "Unsupported media format: {ext}. Supported formats: {supported}".format(
                ext=ext, supported=", ".join(sorted(MEDIA_EXTENSIONS))
            )
        )

    mime_type = MEDIA_MIME_TYPES.get(ext, "application/octet-stream")
    _validate_media_limits(media_bytes, max_size_mb)
    return media_bytes, mime_type


def _validate_media_limits(media_bytes: bytes, max_size_mb: float) -> None:
    """Ensure the media does not exceed the configured size limit."""
    size_mb = len(media_bytes) / (1024 * 1024)
    if size_mb > max_size_mb:
        raise ValueError(f"Media too large: {size_mb:.1f}MB (max: {max_size_mb}MB)")
