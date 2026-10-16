"""Execute FFmpeg render plans as bounded subprocesses.

Progress is read from ``-progress pipe:1`` and reported through a callback so
the export job can expose meaningful percentages.

The FFmpeg process is supervised against a *wall-clock* deadline: the timeout
does not depend on FFmpeg writing another line to stdout, so a stalled or
silently hung render is still killed. On timeout the whole process group is
terminated, waited for, and the incomplete output file is removed. Failures
never produce a silent/empty file: the output is probed and checked before it
is accepted, and completion is only reported after that validation succeeded.
"""
from __future__ import annotations

import logging
import os
import queue
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from renderer.errors import FFmpegNotFoundError, RenderError, RenderTimeoutError
from renderer.ffprobe import probe_audio
from renderer.filters import RenderPlan

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float], None]

#: Progress is reported in ~5% steps; ``1.0`` is reserved for a render whose
#: artifact has been validated successfully.
PROGRESS_STEP = 0.05
#: Upper bound for in-flight progress values (never report "done" early).
PROGRESS_CEILING = 0.99
#: How long to wait for the pipe reader threads to finish.
_JOIN_TIMEOUT = 5.0
#: Grace period between SIGTERM and SIGKILL when a render times out.
_KILL_GRACE = 5.0
#: Poll interval of the supervision loop.
_POLL_INTERVAL = 0.05


def _clamp_progress(value: float) -> float:
    """Clamp a raw progress fraction into the reportable range."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    if value != value:  # NaN
        return 0.0
    return max(0.0, min(PROGRESS_CEILING, value))


def _parse_progress_line(line: str, duration: float) -> float | None:
    """Return a 0..1 fraction from one FFmpeg ``-progress`` line.

    Returns ``None`` for lines that carry no progress information so the
    caller can ignore them.
    """
    line = line.strip()
    if not line or "=" not in line:
        return None
    key, _, value = line.partition("=")
    key = key.strip()
    value = value.strip()
    if not value or value == "N/A":
        return None
    if duration <= 0:
        return None

    if key in {"out_time_us", "out_time_ms"}:
        # Note: FFmpeg's out_time_ms is historically reported in microseconds.
        try:
            seconds = float(value) / 1_000_000.0
        except ValueError:
            return None
        return _clamp_progress(seconds / duration)

    if key == "out_time":
        # "HH:MM:SS.ffffff"
        try:
            hours, minutes, seconds = value.split(":")
            total = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        except ValueError:
            return None
        return _clamp_progress(total / duration)

    return None


class _ProgressReporter:
    """Delivers monotonic, bounded progress values to the export callback."""

    def __init__(
        self,
        callback: ProgressCallback | None,
        *,
        step: float = PROGRESS_STEP,
        ceiling: float = PROGRESS_CEILING,
    ) -> None:
        self._callback = callback
        self._step = step
        self._ceiling = ceiling
        self._last = 0.0
        self._delivered = False

    @property
    def last(self) -> float:
        """The most recent value handed to the callback."""
        return self._last

    def update(self, fraction: float) -> None:
        """Report ``fraction`` unless that would move the progress backwards."""
        if self._callback is None:
            return
        value = _clamp_progress(fraction)
        if value <= 0.0:
            return
        if self._delivered:
            if value <= self._last:  # progress never moves backwards
                return
            if value - self._last < self._step:
                return
        self._last = value
        self._delivered = True
        self._callback(value)

    def complete(self) -> None:
        """Report completion — only called once the artifact was validated."""
        if self._callback is None:
            return
        self._last = 1.0
        self._delivered = True
        self._callback(1.0)


def _drain_stderr(stream, sink: list[str]) -> None:
    try:
        for line in stream:
            sink.append(line.rstrip("\n"))
            if len(sink) > 200:
                del sink[:100]
    except Exception:  # pragma: no cover - stream closed mid-read
        pass


def _read_progress(stream, duration: float, sink: "queue.Queue[float]") -> None:
    """Parse ``-progress`` events from FFmpeg's stdout pipe.

    The parsed fractions are handed to the calling thread through ``sink`` so
    the progress callback (which usually touches the database) never runs on
    a helper thread.
    """
    try:
        for line in stream:
            fraction = _parse_progress_line(line, duration)
            if fraction is not None:
                sink.put(fraction)
    except Exception:  # pragma: no cover - stream closed mid-read
        pass


def _drain_progress(events: "queue.Queue[float]", reporter: _ProgressReporter) -> None:
    """Deliver every queued progress event to the reporter."""
    while True:
        try:
            fraction = events.get_nowait()
        except queue.Empty:
            return
        reporter.update(fraction)


def _terminate_process(proc: subprocess.Popen, *, grace: float = _KILL_GRACE) -> None:
    """Stop a runaway FFmpeg process and wait until it is really gone.

    The child is started in its own session, so the whole process group can
    be signalled without touching the server process. ``SIGTERM`` is tried
    first, ``SIGKILL`` if the process ignores it. The call only returns once
    the process has been reaped.
    """
    if proc.poll() is not None:
        return

    def _send(sig: int) -> None:
        try:
            pgid = os.getpgid(proc.pid)
        except (OSError, AttributeError, ValueError):
            pgid = None
        if pgid is not None and pgid != os.getpgid(0):
            try:
                os.killpg(pgid, sig)
                return
            except OSError:
                pass
        # Fall back to signalling the child directly.
        try:
            if sig == signal.SIGKILL:
                proc.kill()
            else:
                proc.terminate()
        except OSError:  # pragma: no cover - already gone
            pass

    _send(signal.SIGTERM)
    try:
        proc.wait(timeout=grace)
        return
    except subprocess.TimeoutExpired:
        pass

    logger.warning("ffmpeg ignored SIGTERM; sending SIGKILL")
    _send(signal.SIGKILL)
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:  # pragma: no cover - unkillable process
        logger.error("ffmpeg process %s could not be killed", proc.pid)


def run_render_plan(
    plan: RenderPlan,
    *,
    timeout_seconds: int,
    on_progress: ProgressCallback | None = None,
) -> dict:
    """Run FFmpeg for ``plan`` and validate the produced file.

    Returns ``{"duration": float, "size_bytes": int}``.
    Raises ``RenderError`` subclasses on any failure; partial outputs are
    removed before raising.
    """
    output_path = Path(plan.args[-1])
    started = time.monotonic()

    try:
        proc = subprocess.Popen(
            plan.args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            text=True,
            errors="replace",
            # Own session: the whole process group can be killed on timeout.
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise FFmpegNotFoundError("ffmpeg is not installed on the server.") from exc
    except OSError as exc:
        raise RenderError("Audio rendering failed.", code="render_failed") from exc

    stderr_tail: list[str] = []
    stderr_thread = threading.Thread(
        target=_drain_stderr, args=(proc.stderr, stderr_tail), daemon=True
    )
    stderr_thread.start()

    progress_events: queue.Queue[float] = queue.Queue()
    stdout_thread = threading.Thread(
        target=_read_progress, args=(proc.stdout, progress_events, plan.duration), daemon=True
    )
    stdout_thread.start()

    reporter = _ProgressReporter(on_progress)
    deadline = started + max(0.0, float(timeout_seconds))
    timed_out = False
    returncode: int | None = None

    try:
        # Supervision loop. The deadline is wall-clock based: it does not
        # depend on FFmpeg writing another progress line to stdout, so a
        # stalled render is still detected and killed.
        while True:
            _drain_progress(progress_events, reporter)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if proc.poll() is not None:
                    # The process finished right at the deadline.
                    returncode = proc.returncode
                    break
                timed_out = True
                break
            try:
                proc.wait(timeout=min(_POLL_INTERVAL, remaining))
            except subprocess.TimeoutExpired:
                continue
            returncode = proc.returncode
            break

        if timed_out:
            logger.error("ffmpeg render timed out after %.1fs", timeout_seconds)
            _terminate_process(proc)
            returncode = proc.returncode
    finally:
        # Safety net: never leave an FFmpeg process behind.
        if proc.poll() is None:
            _terminate_process(proc)
        stdout_thread.join(timeout=_JOIN_TIMEOUT)
        stderr_thread.join(timeout=_JOIN_TIMEOUT)
        _drain_progress(progress_events, reporter)
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:  # pragma: no cover
                pass

    if timed_out:
        _cleanup(output_path)
        raise RenderTimeoutError("Audio rendering timed out.")

    if returncode != 0:
        _cleanup(output_path)
        logger.error("ffmpeg failed rc=%s tail=%s", returncode, " | ".join(stderr_tail[-10:]))
        raise RenderError("Audio rendering failed.", code="render_failed")

    if not output_path.exists() or output_path.stat().st_size == 0:
        _cleanup(output_path)
        raise RenderError("Renderer produced an empty file.", code="render_empty")

    # Validate the artifact: must be decodable audio of the expected length.
    try:
        info = probe_audio(output_path)
    except RenderError:
        _cleanup(output_path)
        raise RenderError("Renderer produced an unreadable file.", code="render_invalid")

    tolerance = max(0.35, plan.duration * 0.02)
    if abs(info.duration - plan.duration) > tolerance:
        _cleanup(output_path)
        logger.error(
            "render duration mismatch: got=%.3f expected=%.3f", info.duration, plan.duration
        )
        raise RenderError("Rendered file has the wrong duration.", code="render_invalid")

    # The render is only "done" once the artifact has been validated.
    reporter.complete()

    return {"duration": info.duration, "size_bytes": info.size_bytes}


def _cleanup(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:  # pragma: no cover - best effort
        logger.warning("could not remove partial output %s", path.name)
