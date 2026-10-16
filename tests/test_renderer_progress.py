"""FFmpeg progress reporting and subprocess supervision.

Covers the render plan's progress options, the ``-progress`` parser, the
monotonic/bounded progress callback and — most importantly — the timeout
behaviour: a stalled FFmpeg process must be killed even when it never writes
another line to stdout, and its partial output must be removed.
"""
from __future__ import annotations

import os
import subprocess
import time
import wave
from pathlib import Path

import pytest

from django.conf import settings

from renderer.errors import RenderError, RenderTimeoutError
from renderer.filters import ClipRenderSpec, ExportFormat, build_render_plan
from renderer.render import (
    PROGRESS_CEILING,
    _parse_progress_line,
    _ProgressReporter,
    run_render_plan,
)
from tests.conftest import requires_ffmpeg

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def spec(path: Path) -> ClipRenderSpec:
    return ClipRenderSpec(
        clip_id="clp_1",
        input_path=path,
        trim_in=0.0,
        trim_out=1.0,
        start=0.0,
        volume=1.0,
    )


def write_wav(path: Path, seconds: float = 1.0, rate: int = 8000) -> Path:
    """Write a tiny but perfectly valid PCM WAV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(b"\x00\x00" * int(rate * seconds))
    return path


def write_fake_ffmpeg(tmp_path: Path, body: str) -> Path:
    """Create an executable stand-in for the ffmpeg binary."""
    script = tmp_path / "fake_ffmpeg.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys, time\n"
        "from pathlib import Path\n" + body
    )
    script.chmod(0o755)
    return script


def plan_for(tmp_path: Path, binary: str, *, duration: float = 1.0) -> object:
    """Build a render plan that renders to ``tmp_path/render.mp3``."""
    source = write_wav(tmp_path / "source.wav", seconds=1.0)
    return build_render_plan(
        [spec(source)],
        duration,
        tmp_path / "render.mp3",
        ExportFormat.mp3("192k"),
        ffmpeg_binary=binary,
    )


# --------------------------------------------------------------------------
# Progress options
# --------------------------------------------------------------------------


class TestProgressOptions:
    def test_plan_requests_progress_events(self, tmp_path):
        plan = plan_for(tmp_path, "ffmpeg")
        assert "-progress" in plan.args
        assert plan.args[plan.args.index("-progress") + 1] == "pipe:1"

    def test_plan_disables_human_readable_stats(self, tmp_path):
        plan = plan_for(tmp_path, "ffmpeg")
        assert "-nostats" in plan.args

    def test_progress_options_precede_inputs_and_output(self, tmp_path):
        """``-progress``/``-nostats`` are global options: they must come first."""
        plan = plan_for(tmp_path, "ffmpeg")
        first_input = plan.args.index("-i")
        assert plan.args.index("-progress") < first_input
        assert plan.args.index("-nostats") < first_input
        # The output file stays the last argument (the renderer relies on it).
        assert plan.args[-1].endswith("render.mp3")

    def test_progress_options_do_not_disturb_the_filter_graph(self, tmp_path):
        plan = plan_for(tmp_path, "ffmpeg")
        assert "amix=inputs=1:duration=longest:normalize=0" in plan.filter_graph
        assert plan.args[plan.args.index("-filter_complex") + 1] == plan.filter_graph
        assert plan.args[plan.args.index("-map") + 1] == "[out]"


# --------------------------------------------------------------------------
# Progress parsing
# --------------------------------------------------------------------------


class TestProgressParsing:
    def test_out_time_us_is_microseconds(self):
        assert _parse_progress_line("out_time_us=500000", 10.0) == pytest.approx(0.05)

    def test_out_time_ms_is_microseconds(self):
        # FFmpeg has reported out_time_ms in microseconds since 4.3.
        assert _parse_progress_line("out_time_ms=2000000", 4.0) == pytest.approx(0.5)

    def test_out_time_clock_value(self):
        assert _parse_progress_line("out_time=00:00:05.000000", 10.0) == pytest.approx(0.5)

    def test_out_time_hours(self):
        assert _parse_progress_line("out_time=01:00:00.000000", 7200.0) == pytest.approx(0.5)

    def test_progress_marker_is_ignored(self):
        assert _parse_progress_line("progress=continue", 10.0) is None
        assert _parse_progress_line("progress=end", 10.0) is None

    def test_non_progress_keys_are_ignored(self):
        assert _parse_progress_line("bitrate=  66.1kbits/s", 10.0) is None
        assert _parse_progress_line("total_size=16526", 10.0) is None
        assert _parse_progress_line("speed=134x", 10.0) is None

    def test_not_available_values_are_ignored(self):
        assert _parse_progress_line("out_time_us=N/A", 10.0) is None
        assert _parse_progress_line("out_time_ms=", 10.0) is None

    def test_malformed_values_are_ignored(self):
        assert _parse_progress_line("out_time_us=abc", 10.0) is None
        assert _parse_progress_line("out_time=not-a-time", 10.0) is None
        assert _parse_progress_line("no-equals-sign", 10.0) is None
        assert _parse_progress_line("   ", 10.0) is None

    def test_values_are_bounded(self):
        # A render that reports more than the expected duration is clamped.
        assert _parse_progress_line("out_time_us=20000000", 10.0) == PROGRESS_CEILING
        assert _parse_progress_line("out_time_us=-500000", 10.0) == 0.0

    def test_zero_duration_is_ignored(self):
        assert _parse_progress_line("out_time_us=500000", 0.0) is None


# --------------------------------------------------------------------------
# Progress callback
# --------------------------------------------------------------------------


class TestProgressReporter:
    def test_values_are_monotonic(self):
        seen: list[float] = []
        reporter = _ProgressReporter(seen.append)
        reporter.update(0.5)
        reporter.update(0.2)  # a late/rewound value must be dropped
        reporter.update(0.4)
        assert seen == [0.5]
        assert reporter.last == 0.5

    def test_values_are_bounded_below_one(self):
        seen: list[float] = []
        reporter = _ProgressReporter(seen.append)
        reporter.update(5.0)
        assert seen == [PROGRESS_CEILING]

    def test_negative_values_are_dropped(self):
        seen: list[float] = []
        reporter = _ProgressReporter(seen.append)
        reporter.update(-1.0)
        assert seen == []

    def test_small_steps_are_coalesced(self):
        seen: list[float] = []
        reporter = _ProgressReporter(seen.append)
        reporter.update(0.10)
        reporter.update(0.11)
        reporter.update(0.12)
        assert seen == [pytest.approx(0.10)]

    def test_completion_reports_one(self):
        seen: list[float] = []
        reporter = _ProgressReporter(seen.append)
        reporter.update(0.5)
        reporter.complete()
        assert seen[-1] == 1.0

    def test_callback_is_optional(self):
        reporter = _ProgressReporter(None)
        reporter.update(0.5)
        reporter.complete()


# --------------------------------------------------------------------------
# Subprocess supervision
# --------------------------------------------------------------------------


STALL_BODY = """
pid_file = Path(os.environ["FAKE_PID_FILE"])
pid_file.write_text(str(os.getpid()))
Path(sys.argv[-1]).write_bytes(b"partial audio data")
sys.stdout.write("out_time_us=1000000\\n")
sys.stdout.flush()
time.sleep(600)
"""

SILENT_STALL_BODY = """
pid_file = Path(os.environ["FAKE_PID_FILE"])
pid_file.write_text(str(os.getpid()))
Path(sys.argv[-1]).write_bytes(b"partial audio data")
time.sleep(600)
"""

FAIL_BODY = """
sys.stderr.write("fake ffmpeg: invalid argument\\n")
sys.exit(1)
"""

EMPTY_OUTPUT_BODY = """
Path(sys.argv[-1]).write_bytes(b"")
sys.exit(0)
"""

GARBAGE_OUTPUT_BODY = """
Path(sys.argv[-1]).write_bytes(b"this is not audio at all")
sys.exit(0)
"""

SUCCESS_BODY = """
source = Path(os.environ["FAKE_WAV"])
Path(sys.argv[-1]).write_bytes(source.read_bytes())
sys.stdout.write("out_time_us=500000\\n")
sys.stdout.write("out_time_us=250000\\n")
sys.stdout.write("out_time_us=1000000\\n")
sys.stdout.flush()
sys.exit(0)
"""


class TestTimeoutHandling:
    def test_stalled_process_times_out(self, tmp_path, monkeypatch):
        pid_file = tmp_path / "pid"
        monkeypatch.setenv("FAKE_PID_FILE", str(pid_file))
        script = write_fake_ffmpeg(tmp_path, STALL_BODY)
        plan = plan_for(tmp_path, str(script))

        started = time.monotonic()
        with pytest.raises(RenderTimeoutError):
            run_render_plan(plan, timeout_seconds=2)
        elapsed = time.monotonic() - started

        # The timeout must not depend on another stdout line arriving.
        assert elapsed < 20, "the render did not time out promptly"

    def test_silent_stalled_process_times_out(self, tmp_path, monkeypatch):
        """No progress output at all must not stall the supervision loop."""
        pid_file = tmp_path / "pid"
        monkeypatch.setenv("FAKE_PID_FILE", str(pid_file))
        script = write_fake_ffmpeg(tmp_path, SILENT_STALL_BODY)
        plan = plan_for(tmp_path, str(script))

        started = time.monotonic()
        with pytest.raises(RenderTimeoutError):
            run_render_plan(plan, timeout_seconds=2)
        assert time.monotonic() - started < 20

    def test_timeout_kills_the_process(self, tmp_path, monkeypatch):
        pid_file = tmp_path / "pid"
        monkeypatch.setenv("FAKE_PID_FILE", str(pid_file))
        script = write_fake_ffmpeg(tmp_path, STALL_BODY)
        plan = plan_for(tmp_path, str(script))

        with pytest.raises(RenderTimeoutError):
            run_render_plan(plan, timeout_seconds=2)

        pid = int(pid_file.read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)  # the process is gone (and reaped)

    def test_timeout_removes_partial_output(self, tmp_path, monkeypatch):
        pid_file = tmp_path / "pid"
        monkeypatch.setenv("FAKE_PID_FILE", str(pid_file))
        script = write_fake_ffmpeg(tmp_path, STALL_BODY)
        plan = plan_for(tmp_path, str(script))
        output = Path(plan.args[-1])

        with pytest.raises(RenderTimeoutError):
            run_render_plan(plan, timeout_seconds=2)

        assert not output.exists(), "the partial output was left behind"

    def test_timeout_is_distinguishable_from_a_render_error(self, tmp_path, monkeypatch):
        pid_file = tmp_path / "pid"
        monkeypatch.setenv("FAKE_PID_FILE", str(pid_file))
        script = write_fake_ffmpeg(tmp_path, STALL_BODY)
        plan = plan_for(tmp_path, str(script))

        with pytest.raises(RenderTimeoutError) as exc:
            run_render_plan(plan, timeout_seconds=2)
        assert exc.value.code == "render_timeout"
        assert exc.value.code != "render_failed"


class TestFailureModes:
    def test_non_zero_exit_is_a_render_error(self, tmp_path):
        script = write_fake_ffmpeg(tmp_path, FAIL_BODY)
        plan = plan_for(tmp_path, str(script))
        output = Path(plan.args[-1])

        with pytest.raises(RenderError) as exc:
            run_render_plan(plan, timeout_seconds=30)
        assert exc.value.code == "render_failed"
        assert not isinstance(exc.value, RenderTimeoutError)
        assert not output.exists()

    def test_empty_output_is_rejected(self, tmp_path):
        script = write_fake_ffmpeg(tmp_path, EMPTY_OUTPUT_BODY)
        plan = plan_for(tmp_path, str(script))
        output = Path(plan.args[-1])

        with pytest.raises(RenderError) as exc:
            run_render_plan(plan, timeout_seconds=30)
        assert exc.value.code == "render_empty"
        assert not output.exists()

    def test_undecodable_output_is_rejected_and_removed(self, tmp_path):
        script = write_fake_ffmpeg(tmp_path, GARBAGE_OUTPUT_BODY)
        plan = plan_for(tmp_path, str(script))
        output = Path(plan.args[-1])

        with pytest.raises(RenderError) as exc:
            run_render_plan(plan, timeout_seconds=30)
        assert exc.value.code == "render_invalid"
        assert not output.exists()

    def test_completion_is_reported_only_after_validation(self, tmp_path):
        """A failed render must never report 100% progress."""
        script = write_fake_ffmpeg(tmp_path, GARBAGE_OUTPUT_BODY)
        plan = plan_for(tmp_path, str(script))
        seen: list[float] = []

        with pytest.raises(RenderError):
            run_render_plan(plan, timeout_seconds=30, on_progress=seen.append)

        assert 1.0 not in seen
        assert all(value < 1.0 for value in seen)

    def test_successful_render_reports_monotonic_progress(self, tmp_path, monkeypatch):
        wav = write_wav(tmp_path / "valid.wav", seconds=1.0)
        monkeypatch.setenv("FAKE_WAV", str(wav))
        script = write_fake_ffmpeg(tmp_path, SUCCESS_BODY)
        plan = plan_for(tmp_path, str(script), duration=1.0)
        seen: list[float] = []

        result = run_render_plan(plan, timeout_seconds=30, on_progress=seen.append)

        assert result["duration"] == pytest.approx(1.0, abs=0.35)
        assert result["size_bytes"] > 0
        assert seen, "no progress was reported"
        assert seen == sorted(seen), "progress must be monotonic"
        assert seen[-1] == 1.0
        assert all(0.0 < value < 1.0 for value in seen[:-1])

    def test_missing_binary_is_reported(self, tmp_path):
        plan = plan_for(tmp_path, "definitely-not-ffmpeg-binary")
        from renderer.errors import FFmpegNotFoundError

        with pytest.raises(FFmpegNotFoundError):
            run_render_plan(plan, timeout_seconds=30)


# --------------------------------------------------------------------------
# Real FFmpeg
# --------------------------------------------------------------------------


@requires_ffmpeg
class TestRealFfmpegProgress:
    def _render(self, tmp_path, duration: float = 1.0):
        source = write_wav(tmp_path / "source.wav", seconds=duration)
        plan = build_render_plan(
            [spec(source)],
            duration,
            tmp_path / "out.mp3",
            ExportFormat.mp3("192k"),
            sample_rate=48000,
            ffmpeg_binary=settings.FFMPEG_BINARY,
        )
        return plan

    def test_progress_events_are_delivered(self, tmp_path):
        plan = self._render(tmp_path)
        seen: list[float] = []
        result = run_render_plan(plan, timeout_seconds=120, on_progress=seen.append)

        assert result["duration"] == pytest.approx(1.0, abs=0.35)
        assert seen, "ffmpeg did not report any progress"
        assert seen == sorted(seen), "progress must be monotonic"
        assert all(0.0 < value < 1.0 for value in seen[:-1])
        assert seen[-1] == 1.0

    def test_render_without_callback_still_works(self, tmp_path):
        plan = self._render(tmp_path)
        result = run_render_plan(plan, timeout_seconds=120)
        assert result["size_bytes"] > 0

    def test_output_is_decodable_with_the_expected_duration(self, tmp_path):
        plan = self._render(tmp_path, duration=2.0)
        result = run_render_plan(plan, timeout_seconds=120)
        output = Path(plan.args[-1])

        probe = subprocess.run(
            [
                settings.FFPROBE_BINARY,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(output),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        assert float(probe.stdout.strip()) == pytest.approx(2.0, abs=0.2)
