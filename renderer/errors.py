"""Errors raised by the audio rendering pipeline."""
from __future__ import annotations


class RenderError(Exception):
    """Base class for rendering failures with a safe, user-facing message."""

    code = "render_failed"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class InvalidAudioError(RenderError):
    """The input file is not usable audio."""

    code = "invalid_audio"


class UnsupportedAudioError(RenderError):
    """The input is audio but its codec/container is not supported."""

    code = "unsupported_audio"


class LimitExceededError(RenderError):
    """A configured limit (size, duration, count) would be exceeded."""

    code = "limit_exceeded"


class FFmpegNotFoundError(RenderError):
    """ffmpeg/ffprobe binaries are missing from the environment."""

    code = "ffmpeg_missing"


class RenderTimeoutError(RenderError):
    """The FFmpeg process exceeded its time budget."""

    code = "render_timeout"
