"""Unit tests for FFmpeg filter-graph construction (no subprocess needed)."""
from __future__ import annotations

from pathlib import Path

import pytest

from renderer.filters import (
    ClipRenderSpec,
    ExportFormat,
    build_filter_graph,
    build_render_plan,
    clip_filter_chain,
)


def spec(**overrides) -> ClipRenderSpec:
    base = dict(
        clip_id="clp_1",
        input_path=Path("/tmp/a.wav"),
        trim_in=0.5,
        trim_out=2.5,
        start=1.0,
        volume=0.8,
        fade_in=0.25,
        fade_out=0.5,
    )
    base.update(overrides)
    return ClipRenderSpec(**base)


class TestClipChain:
    def test_basic_chain(self):
        chain = clip_filter_chain(0, spec(), 48000)
        assert chain.startswith("atrim=start=0.500000:end=2.500000")
        assert "asetpts=PTS-STARTPTS" in chain
        assert "aformat=sample_rates=48000:channel_layouts=stereo:sample_fmts=fltp" in chain
        assert "volume=0.800000" in chain
        assert "afade=t=in:st=0:d=0.250000" in chain
        # fade-out starts at content_len - fade_out = 2.0 - 0.5 = 1.5
        assert "afade=t=out:st=1.500000:d=0.500000" in chain
        assert "adelay=delays=1000:all=1" in chain
        assert chain.endswith("apad")

    def test_delay_in_milliseconds(self):
        chain = clip_filter_chain(0, spec(start=2.345), 48000)
        assert "adelay=delays=2345:all=1" in chain

    def test_no_fades_when_zero(self):
        chain = clip_filter_chain(0, spec(fade_in=0, fade_out=0), 48000)
        assert "afade" not in chain

    def test_volume_one_still_explicit(self):
        chain = clip_filter_chain(0, spec(volume=1.0), 48000)
        assert "volume=1.000000" in chain

    def test_values_are_fixed_precision(self):
        chain = clip_filter_chain(0, spec(trim_in=0.1, trim_out=0.3, volume=0.5), 48000)
        assert "atrim=start=0.100000:end=0.300000" in chain
        assert "volume=0.500000" in chain


class TestFilterGraph:
    def test_single_clip(self):
        graph = build_filter_graph([spec()], total_duration=3.0, sample_rate=48000)
        assert graph.count("[0:a]") == 1
        assert "amix=inputs=1:duration=longest:normalize=0" in graph
        assert "alimiter=limit=0.970000" in graph
        assert "atrim=0:3.000000" in graph
        assert graph.endswith("[out]")

    def test_multi_clip_mix(self):
        clips = [spec(clip_id=f"c{i}", input_path=Path(f"/tmp/{i}.wav")) for i in range(3)]
        graph = build_filter_graph(clips, total_duration=5.0, sample_rate=48000)
        for i in range(3):
            assert f"[{i}:a]" in graph
            assert f"[c{i}]" in graph
        assert "amix=inputs=3" in graph

    def test_overlapping_clips_are_summed_not_replaced(self):
        # Both clips at the same timeline position must land in one amix.
        clips = [
            spec(clip_id="a", start=0.0, trim_in=0, trim_out=2),
            spec(clip_id="b", start=0.5, trim_in=0, trim_out=2),
        ]
        graph = build_filter_graph(clips, total_duration=2.5, sample_rate=48000)
        assert "amix=inputs=2:duration=longest:normalize=0" in graph

    def test_empty_clips_rejected(self):
        with pytest.raises(ValueError):
            build_filter_graph([], total_duration=1.0, sample_rate=48000)

    def test_nonpositive_duration_rejected(self):
        with pytest.raises(ValueError):
            build_filter_graph([spec()], total_duration=0.0, sample_rate=48000)

    def test_no_shell_injection_through_ids(self):
        # clip ids never enter the graph; numeric-only parameters do.
        evil = spec(clip_id="; rm -rf /")
        graph = build_filter_graph([evil], total_duration=2.0, sample_rate=48000)
        assert "; rm" not in graph


class TestRenderPlan:
    def test_argument_list_shape(self):
        plan = build_render_plan(
            [spec(), spec(clip_id="x", input_path=Path("/tmp/b.wav"))],
            total_duration=4.0,
            output_path=Path("/tmp/out.mp3"),
            export_format=ExportFormat.mp3("192k"),
            ffmpeg_binary="/usr/bin/ffmpeg",
        )
        args = plan.args
        assert args[0] == "/usr/bin/ffmpeg"
        assert "-filter_complex" in args
        assert args[args.index("-map") + 1] == "[out]"
        assert args[args.index("-ar") + 1] == "48000"
        assert args[args.index("-ac") + 1] == "2"
        assert args[args.index("-c:a") + 1] == "libmp3lame"
        assert args[args.index("-b:a") + 1] == "192k"
        assert args[args.index("-t") + 1] == "4.000000"
        assert args[-1] == "/tmp/out.mp3"
        # Two inputs, in order.
        input_paths = [args[i + 1] for i, a in enumerate(args) if a == "-i"]
        assert input_paths == ["/tmp/a.wav", "/tmp/b.wav"]
        assert plan.clip_count == 2

    def test_wav_format(self):
        plan = build_render_plan(
            [spec()],
            total_duration=2.0,
            output_path=Path("/tmp/out.wav"),
            export_format=ExportFormat.wav(24),
        )
        assert "pcm_s24le" in plan.args

    def test_invalid_bitrate(self):
        with pytest.raises(ValueError):
            ExportFormat.mp3("1megabit")

    def test_invalid_depth(self):
        with pytest.raises(ValueError):
            ExportFormat.wav(32)
