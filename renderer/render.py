"""Execute FFmpeg render plans as bounded subprocesses.

Progress is read from ``-progress pipe:1`` and reported through a callback so
the export job can expose meaningful percentages. The process is killed on
timeout; failures never produce a silent/empty file: the output is probed and
checked before it is accepted.
"""
from __future__ import annotations

import logging
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


def _parse_progress_line(line: str, duration: float) -> float | None:
    """Return a 0..1 fraction from one FFmpeg ``-progress`` line."""
    line = line.strip()
    if not line or "=" not in line:
        return None
    key, _, value = line.partition("=")
    if key in {"out_time_us", "out_time_ms"} and value not in {"N/A", ""}:
        # Note: FFmpeg's out_time_ms is historically reported in microseconds.
        try:
            if duration <= 0:
                return None
            return min(0.99, (int(value) / 1_000_000.0) / duration)
        except ValueError:
            return None
    return None


def _drain_stderr(stream, sink: list[str]) -> None:
    try:
        for line in stream:
            sink.append(line.rstrip("\n"))
            if len(sink) > 200:
                del sink[:100]
    except Exception:  # pragma: no cover - stream closed mid-read
        pass


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
        )
    except FileNotFoundError as exc:
        raise FFmpegNotFoundError("ffmpeg is not installed on the server.") from exc

    stderr_tail: list[str] = []
    stderr_thread = threading.Thread(
        target=_drain_stderr, args=(proc.stderr, stderr_tail), daemon=True
    )
    stderr_thread.start()

    try:
        assert proc.stdout is not None
        last_reported = -1.0
        for line in proc.stdout:
            if time.monotonic() - started > timeout_seconds:
                raise RenderTimeoutError("Audio rendering timed out.")
            if on_progress:
                frac = _parse_progress_line(line, plan.duration)
                if frac is not None and frac - last_reported >= 0.05:
                    last_reported = frac
                    on_progress(frac)
        returncode = proc.wait(timeout=30)
    except RenderTimeoutError:
        proc.kill()
        proc.wait()
        _cleanup(output_path)
        raise
    except Exception:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        _cleanup(output_path)
        raise
    finally:
        if proc.stdout is not None:
            try:
                proc.stdout.close()
            except Exception:  # pragma: no cover
                pass
        stderr_thread.join(timeout=5)

    if time.monotonic() - started > timeout_seconds and returncode != 0:
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

    if on_progress:
        on_progress(1.0)

    return {"duration": info.duration, "size_bytes": info.size_bytes}


def _cleanup(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:  # pragma: no cover - best effort
        logger.warning("could not remove partial output %s", path.name)
