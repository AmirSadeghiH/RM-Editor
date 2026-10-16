"""Server-side waveform peak extraction.

Decodes audio to a low-rate mono PCM stream through FFmpeg and reduces it to
min/max pairs per bucket. Peaks are stored as JSON on the asset and used by
the timeline waveform renderer (so the browser does not need to download the
whole file just to draw a waveform).
"""
from __future__ import annotations

import logging
import math
import struct
import subprocess
from pathlib import Path

from django.conf import settings

from renderer.errors import FFmpegNotFoundError, InvalidAudioError

logger = logging.getLogger(__name__)

# Mono analysis rate: enough resolution for visual peaks at low memory cost.
ANALYSIS_RATE = 8000
DEFAULT_BUCKETS = 800


def compute_peaks(path: str | Path, buckets: int = DEFAULT_BUCKETS) -> dict:
    """Return ``{"version": 1, "num_buckets": N, "peaks": [[min, max], ...]}``.

    Values are floats in [-1, 1], rounded to 3 decimals.
    """
    path = Path(path)
    buckets = max(16, min(4096, int(buckets)))
    cmd = [
        settings.FFMPEG_BINARY,
        "-v",
        "error",
        "-i",
        str(path),
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-ar",
        str(ANALYSIS_RATE),
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=120, shell=False, check=False)
    except FileNotFoundError as exc:
        raise FFmpegNotFoundError("ffmpeg is not installed on the server.") from exc
    except subprocess.TimeoutExpired as exc:
        raise InvalidAudioError("Waveform analysis timed out.") from exc
    if proc.returncode != 0 or not proc.stdout:
        raise InvalidAudioError("Could not analyze audio for waveform display.")

    raw = proc.stdout
    sample_count = len(raw) // 2
    if sample_count == 0:
        raise InvalidAudioError("Audio contains no samples.")

    samples = struct.unpack(f"<{sample_count}h", raw[: sample_count * 2])
    per_bucket = max(1, math.ceil(sample_count / buckets))

    peaks: list[list[float]] = []
    for start in range(0, sample_count, per_bucket):
        chunk = samples[start : start + per_bucket]
        lo = min(chunk) / 32768.0
        hi = max(chunk) / 32768.0
        peaks.append([round(lo, 3), round(hi, 3)])

    return {"version": 1, "num_buckets": len(peaks), "peaks": peaks}
