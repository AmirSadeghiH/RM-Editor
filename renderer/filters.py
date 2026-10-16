"""Pure construction of FFmpeg filter graphs for timeline rendering.

This module contains no subprocess code: it turns validated clip
specifications into an argument list, which makes it cheap to unit test.

Rendering strategy
------------------
Each clip becomes one FFmpeg input, processed independently:

    [i:a] atrim (source in/out) -> asetpts (reset timestamps)
        -> aformat (uniform rate/layout) -> volume (clip x track gain)
        -> afade in/out (relative to the clip) -> adelay (timeline position)
        -> apad

All processed streams are combined with ``amix=duration=longest:
normalize=0`` so overlapping clips are *summed* (mixed), never replaced.
A transparent ``alimiter`` guards against peaks above -0.3 dBFS when many
clips overlap. The mix is trimmed to the exact timeline duration.

Crossfades are encoded in the timeline itself: clip B overlaps clip A by the
crossfade length while A fades out and B fades in — the envelopes above
produce the real audio crossfade in the exported file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

# Number formatting for filter parameters. Values are Python floats already
# validated by timeline validation; the fixed format avoids locale/notation
# surprises and keeps the graph syntactically bounded.
def _num(value: float) -> str:
    return f"{float(value):.6f}"


@dataclass(frozen=True)
class ClipRenderSpec:
    """Everything the renderer needs for a single audible clip."""

    clip_id: str
    input_path: Path
    trim_in: float
    trim_out: float
    start: float
    volume: float
    fade_in: float = 0.0
    fade_out: float = 0.0

    @property
    def content_duration(self) -> float:
        return self.trim_out - self.trim_in

    @property
    def end(self) -> float:
        return self.start + self.content_duration


@dataclass(frozen=True)
class ExportFormat:
    """Validated export encoding settings."""

    container: str  # "mp3" | "wav"
    codec_args: tuple[str, ...]
    extension: str

    @classmethod
    def mp3(cls, bitrate: str) -> "ExportFormat":
        if bitrate not in {"128k", "192k", "256k", "320k"}:
            raise ValueError("invalid bitrate")
        return cls(
            container="mp3",
            codec_args=("-c:a", "libmp3lame", "-b:a", bitrate),
            extension="mp3",
        )

    @classmethod
    def wav(cls, depth: int = 16) -> "ExportFormat":
        codec = {16: "pcm_s16le", 24: "pcm_s24le"}.get(depth)
        if codec is None:
            raise ValueError("invalid bit depth")
        return cls(container="wav", codec_args=("-c:a", codec), extension="wav")


@dataclass
class RenderPlan:
    args: list[str]
    duration: float
    clip_count: int
    filter_graph: str = field(repr=False, default="")


def clip_filter_chain(index: int, clip: ClipRenderSpec, sample_rate: int) -> str:
    """Build the processing chain for one clip (without the [label] prefix)."""
    parts = [
        f"atrim=start={_num(clip.trim_in)}:end={_num(clip.trim_out)}",
        "asetpts=PTS-STARTPTS",
        f"aformat=sample_rates={int(sample_rate)}:channel_layouts=stereo:sample_fmts=fltp",
    ]
    if clip.volume != 1.0:
        parts.append(f"volume={_num(clip.volume)}")
    elif clip.volume == 1.0:
        # Explicit volume keeps the chain deterministic for tests.
        parts.append("volume=1.000000")

    content = clip.content_duration
    if clip.fade_in > 0:
        parts.append(f"afade=t=in:st=0:d={_num(clip.fade_in)}")
    if clip.fade_out > 0:
        fade_start = max(0.0, content - clip.fade_out)
        parts.append(f"afade=t=out:st={_num(fade_start)}:d={_num(clip.fade_out)}")

    delay_ms = int(round(max(0.0, clip.start) * 1000))
    parts.append(f"adelay=delays={delay_ms}:all=1")
    parts.append("apad")
    return ",".join(parts)


def build_filter_graph(
    clips: Sequence[ClipRenderSpec],
    total_duration: float,
    sample_rate: int,
) -> str:
    """Compose the full ``-filter_complex`` graph for a mix."""
    if not clips:
        raise ValueError("no audible clips to render")
    if total_duration <= 0:
        raise ValueError("total duration must be positive")

    chains: list[str] = []
    labels: list[str] = []
    for index, clip in enumerate(clips):
        label = f"c{index}"
        chains.append(f"[{index}:a]{clip_filter_chain(index, clip, sample_rate)}[{label}]")
        labels.append(f"[{label}]")

    mix = (
        f"{''.join(labels)}amix=inputs={len(clips)}:duration=longest:normalize=0,"
        # Safety ceiling only (auto-level disabled); never normalizes loudness.
        "alimiter=limit=0.970000:level=0,"
        f"apad,atrim=0:{_num(total_duration)},asetpts=PTS-STARTPTS[out]"
    )
    chains.append(mix)
    return ";".join(chains)


def build_render_plan(
    clips: Sequence[ClipRenderSpec],
    total_duration: float,
    output_path: Path,
    export_format: ExportFormat,
    *,
    sample_rate: int = 48000,
    ffmpeg_binary: str = "ffmpeg",
    extra_output_args: Sequence[str] = (),
) -> RenderPlan:
    """Build the complete FFmpeg argument list for a mix render."""
    if not clips:
        raise ValueError("no audible clips to render")

    # One input per clip keeps trims/fades independent even when the same
    # asset appears multiple times. Clip count is capped by settings (30).
    #
    # ``-progress pipe:1`` makes FFmpeg write machine-readable progress
    # events (``out_time_us=...``) to stdout, and ``-nostats`` suppresses the
    # human-readable stats line so stdout only carries progress events.
    # Both are global options and must precede the output file.
    args: list[str] = [
        ffmpeg_binary,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        "-nostats",
    ]
    for clip in clips:
        args += ["-i", str(clip.input_path)]

    graph = build_filter_graph(clips, total_duration, sample_rate)

    args += [
        "-filter_complex",
        graph,
        "-map",
        "[out]",
        "-ar",
        str(int(sample_rate)),
        "-ac",
        "2",
    ]
    args += list(export_format.codec_args)
    args += list(extra_output_args)
    args += ["-t", _num(total_duration), str(output_path)]

    return RenderPlan(
        args=args,
        duration=float(total_duration),
        clip_count=len(clips),
        filter_graph=graph,
    )
