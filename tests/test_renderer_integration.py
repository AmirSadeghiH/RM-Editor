"""FFmpeg integration tests with synthetic audio fixtures.

These verify that the renderer produces *real* audio: correct duration,
non-silent samples, crossfades and overlaps that actually mix.
"""
from __future__ import annotations

import struct
import subprocess
from pathlib import Path

import pytest

from django.conf import settings

from renderer.filters import ClipRenderSpec, ExportFormat, build_render_plan
from renderer.render import run_render_plan
from tests.conftest import requires_ffmpeg

pytestmark = requires_ffmpeg


def make_sine(tmp_path: Path, name: str, duration: float, freq: int = 440) -> Path:
    path = tmp_path / name
    subprocess.run(
        [
            settings.FFMPEG_BINARY,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={freq}:duration={duration}",
            "-ac",
            "2",
            "-ar",
            "44100",
            str(path),
        ],
        check=True,
        shell=False,
        timeout=60,
    )
    return path


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        [
            settings.FFPROBE_BINARY,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        shell=False,
        timeout=30,
    )
    return float(out.stdout.strip())


def read_pcm_samples(path: Path, limit_seconds: float = 30) -> list[int]:
    """Decode output to mono s16le and return raw samples."""
    out = subprocess.run(
        [
            settings.FFMPEG_BINARY,
            "-v",
            "error",
            "-i",
            str(path),
            "-ac",
            "1",
            "-ar",
            "8000",
            "-f",
            "s16le",
            "-t",
            str(limit_seconds),
            "-",
        ],
        capture_output=True,
        shell=False,
        timeout=60,
    )
    raw = out.stdout
    count = len(raw) // 2
    return list(struct.unpack(f"<{count}h", raw[: count * 2]))


def render(tmp_path: Path, clips, duration, name="out.mp3", fmt=None) -> Path:
    out = tmp_path / name
    fmt = fmt or ExportFormat.mp3("192k")
    plan = build_render_plan(
        clips,
        duration,
        out,
        fmt,
        sample_rate=48000,
        ffmpeg_binary=settings.FFMPEG_BINARY,
    )
    result = run_render_plan(plan, timeout_seconds=120)
    assert result["size_bytes"] > 0
    return out


class TestSingleClip:
    def test_single_clip_duration_and_content(self, tmp_path):
        src = make_sine(tmp_path, "a.wav", 2.0)
        out = render(
            tmp_path,
            [ClipRenderSpec("c1", src, trim_in=0, trim_out=2.0, start=0, volume=1.0)],
            duration=2.0,
        )
        assert probe_duration(out) == pytest.approx(2.0, abs=0.2)
        samples = read_pcm_samples(out)
        assert max(abs(s) for s in samples) > 1000  # real audio, not silence


class TestTrimAndPosition:
    def test_trim_range_respected(self, tmp_path):
        """A 4s source trimmed to [1, 3] must render ~2s."""
        src = make_sine(tmp_path, "a.wav", 4.0)
        out = render(
            tmp_path,
            [ClipRenderSpec("c1", src, trim_in=1.0, trim_out=3.0, start=0, volume=1.0)],
            duration=2.0,
            name="trim.mp3",
        )
        assert probe_duration(out) == pytest.approx(2.0, abs=0.2)

    def test_delayed_clip_leaves_silence(self, tmp_path):
        """A clip starting at 1.5s must yield silence before it."""
        src = make_sine(tmp_path, "a.wav", 1.0, freq=880)
        out = render(
            tmp_path,
            [ClipRenderSpec("c1", src, trim_in=0, trim_out=1.0, start=1.5, volume=1.0)],
            duration=2.5,
            name="delay.mp3",
        )
        assert probe_duration(out) == pytest.approx(2.5, abs=0.25)
        samples = read_pcm_samples(out)
        # First 0.75s (at 8kHz → 6000 samples) should be near silent.
        head = samples[:6000]
        tail = samples[12000:18000]  # inside the tone region
        assert max(abs(s) for s in head) < 800
        assert max(abs(s) for s in tail) > 1000


class TestMixing:
    def test_two_clips_concatenated(self, tmp_path):
        a = make_sine(tmp_path, "a.wav", 1.0, 440)
        b = make_sine(tmp_path, "b.wav", 1.0, 880)
        out = render(
            tmp_path,
            [
                ClipRenderSpec("c1", a, 0, 1.0, start=0, volume=1.0),
                ClipRenderSpec("c2", b, 0, 1.0, start=1.0, volume=1.0),
            ],
            duration=2.0,
            name="concat.mp3",
        )
        assert probe_duration(out) == pytest.approx(2.0, abs=0.2)
        samples = read_pcm_samples(out)
        assert max(abs(s) for s in samples[:6000]) > 1000
        assert max(abs(s) for s in samples[10000:15000]) > 1000

    def test_overlapping_clips_mix(self, tmp_path):
        """Overlaps must SUM: overlap region louder than either solo region."""
        a = make_sine(tmp_path, "a.wav", 2.0, 440)
        out_solo = render(
            tmp_path,
            [ClipRenderSpec("c1", a, 0, 2.0, start=0, volume=0.4)],
            duration=2.0,
            name="solo.mp3",
        )
        out_mix = render(
            tmp_path,
            [
                ClipRenderSpec("c1", a, 0, 2.0, start=0, volume=0.4),
                ClipRenderSpec("c2", a, 0, 2.0, start=0, volume=0.4),
            ],
            duration=2.0,
            name="mix.mp3",
        )
        solo = read_pcm_samples(out_solo)
        mixed = read_pcm_samples(out_mix)
        mid = slice(6000, 10000)

        def peak(samples):
            return max(abs(s) for s in samples[mid])

        # Summing two identical signals roughly doubles amplitude
        # (limiter may shave the very top, so allow a generous margin).
        assert peak(mixed) > peak(solo) * 1.4

    def test_crossfade_region_is_continuous(self, tmp_path):
        """Crossfade: A fades out while B fades in on an overlap."""
        a = make_sine(tmp_path, "a.wav", 2.0, 440)
        b = make_sine(tmp_path, "b.wav", 2.0, 440)
        overlap = 1.0
        out = render(
            tmp_path,
            [
                ClipRenderSpec("c1", a, 0, 2.0, start=0, volume=1.0, fade_out=overlap),
                ClipRenderSpec("c2", b, 0, 2.0, start=2.0 - overlap, volume=1.0, fade_in=overlap),
            ],
            duration=3.0,
            name="xfade.mp3",
        )
        assert probe_duration(out) == pytest.approx(3.0, abs=0.25)
        samples = read_pcm_samples(out)
        # No hard silence exactly in the middle of the crossfade.
        mid_start = int(1.5 * 8000)
        window = samples[mid_start : mid_start + 2000]
        assert max(abs(s) for s in window) > 800

    def test_fades_reduce_edges(self, tmp_path):
        a = make_sine(tmp_path, "a.wav", 2.0, 440)
        out = render(
            tmp_path,
            [ClipRenderSpec("c1", a, 0, 2.0, start=0, volume=1.0, fade_in=0.5, fade_out=0.5)],
            duration=2.0,
            name="fade.mp3",
        )
        samples = read_pcm_samples(out)
        # Very start should be much quieter than the middle.
        head = max(abs(s) for s in samples[:800])
        mid = max(abs(s) for s in samples[6000:10000])
        assert head < mid * 0.6

    def test_master_volume_scale(self, tmp_path):
        a = make_sine(tmp_path, "a.wav", 1.0, 440)
        quiet = render(
            tmp_path,
            [ClipRenderSpec("c1", a, 0, 1.0, start=0, volume=0.1)],
            duration=1.0,
            name="quiet.mp3",
        )
        loud = render(
            tmp_path,
            [ClipRenderSpec("c1", a, 0, 1.0, start=0, volume=1.0)],
            duration=1.0,
            name="loud.mp3",
        )
        quiet_peak = max(abs(s) for s in read_pcm_samples(quiet))
        loud_peak = max(abs(s) for s in read_pcm_samples(loud))
        assert quiet_peak < loud_peak * 0.5


class TestFailureHandling:
    def test_invalid_input_rejected(self, tmp_path):
        junk = tmp_path / "junk.wav"
        junk.write_bytes(b"not audio" * 100)
        from renderer.errors import RenderError

        with pytest.raises(RenderError):
            render(
                tmp_path,
                [ClipRenderSpec("c1", junk, 0, 1.0, start=0, volume=1.0)],
                duration=1.0,
                name="bad.mp3",
            )
        assert not (tmp_path / "bad.mp3").exists()  # no partial output left

    def test_missing_ffmpeg_binary(self, tmp_path):
        from renderer.errors import FFmpegNotFoundError

        src = make_sine(tmp_path, "a.wav", 1.0)
        out = tmp_path / "x.mp3"
        plan = build_render_plan(
            [ClipRenderSpec("c1", src, 0, 1.0, 0, 1.0)],
            1.0,
            out,
            ExportFormat.mp3("192k"),
            ffmpeg_binary="definitely-not-ffmpeg-binary",
        )
        with pytest.raises(FFmpegNotFoundError):
            run_render_plan(plan, timeout_seconds=30)
