"""Audio inspection with ffprobe.

Uploaded files are untrusted. Every upload passes through ``probe_audio``
which validates that the file really is audio in an allowed container/codec
and returns machine-readable metadata.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from renderer.errors import FFmpegNotFoundError, InvalidAudioError, UnsupportedAudioError

logger = logging.getLogger(__name__)

# Containers we accept from users. Codecs are checked separately below.
ALLOWED_CONTAINERS = {"mp3", "wav", "ogg", "flac", "mov", "mp4", "m4a", "webm", "aac"}

# Audio codecs that the render pipeline can decode reliably.
ALLOWED_CODECS = {
    "mp3",
    "mp3float",
    "aac",
    "aac_latm",
    "flac",
    "vorbis",
    "opus",
    "pcm_s16le",
    "pcm_s24le",
    "pcm_s32le",
    "pcm_f32le",
    "pcm_u8",
    "pcm_s16be",
    "pcm_s24be",
}


@dataclass(frozen=True)
class AudioInfo:
    duration: float
    format_name: str
    codec_name: str
    sample_rate: int
    channels: int
    size_bytes: int


def _binary() -> str:
    return settings.FFPROBE_BINARY


def probe_audio(path: str | Path) -> AudioInfo:
    """Inspect ``path`` with ffprobe and validate it is acceptable audio.

    Raises ``InvalidAudioError`` / ``UnsupportedAudioError`` with safe
    messages; never echoes filesystem paths back to users.
    """
    path = Path(path)
    cmd = [
        _binary(),
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-select_streams",
        "a:0",
        "-of",
        "json",
        str(path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=60,
            shell=False,
            check=False,
        )
    except FileNotFoundError as exc:
        raise FFmpegNotFoundError("ffprobe is not installed on the server.") from exc
    except subprocess.TimeoutExpired as exc:
        raise InvalidAudioError("Audio inspection timed out.") from exc

    if proc.returncode != 0:
        logger.info("ffprobe rejected upload: rc=%s", proc.returncode)
        raise InvalidAudioError("File is not a valid audio file.")

    try:
        payload = json.loads(proc.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise InvalidAudioError("Could not read audio metadata.") from exc

    streams = payload.get("streams") or []
    if not streams:
        raise InvalidAudioError("No audio stream found in the file.")

    stream = streams[0]
    fmt = payload.get("format") or {}

    codec = str(stream.get("codec_name", "")).lower()
    format_name = str(fmt.get("format_name", "")).lower()
    container_tokens = set(format_name.split(","))

    if codec not in ALLOWED_CODECS:
        raise UnsupportedAudioError(f"Audio codec '{codec or 'unknown'}' is not supported.")
    if not container_tokens & ALLOWED_CONTAINERS:
        raise UnsupportedAudioError("Audio container is not supported.")

    try:
        duration = float(fmt.get("duration") or stream.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        raise InvalidAudioError("Audio duration could not be determined.")
    if duration > settings.BEATLAB_LIMITS["max_source_duration"] + 0.5:
        from renderer.errors import LimitExceededError

        raise LimitExceededError("Audio file is longer than the allowed maximum.")

    try:
        sample_rate = int(stream.get("sample_rate") or 0)
        channels = int(stream.get("channels") or 0)
    except (TypeError, ValueError):
        sample_rate, channels = 0, 0
    if sample_rate <= 0 or channels <= 0:
        raise InvalidAudioError("Audio stream has invalid format parameters.")

    try:
        size_bytes = int(fmt.get("size") or path.stat().st_size)
    except (TypeError, ValueError, OSError):
        size_bytes = path.stat().st_size

    return AudioInfo(
        duration=round(duration, 6),
        format_name=format_name.split(",")[0],
        codec_name=codec,
        sample_rate=sample_rate,
        channels=channels,
        size_bytes=size_bytes,
    )
