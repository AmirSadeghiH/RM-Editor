"""Celery tasks for asynchronous export rendering."""
from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.core.files import File
from django.utils import timezone

from renderer.errors import FFmpegNotFoundError, RenderError, RenderTimeoutError
from renderer.filters import ClipRenderSpec, ExportFormat, build_render_plan
from renderer.render import run_render_plan
from studio.models import AudioAsset, ExportJob
from studio.timeline import AssetInfo, TimelineValidationError, validate_timeline

logger = logging.getLogger(__name__)


def build_render_inputs(job: ExportJob) -> tuple[list[ClipRenderSpec], float]:
    """Turn the saved timeline + stored assets into renderer inputs.

    Re-validates the timeline against trusted asset metadata — never trusts
    values that may have been tampered with after the last save.
    """
    project = job.project
    asset_map: dict[str, AssetInfo] = {}
    asset_paths: dict[str, Path] = {}
    for row in AudioAsset.objects.filter(project=project, status=AudioAsset.Status.READY):
        asset_map[str(row.id)] = AssetInfo(
            id=str(row.id), duration=row.duration, status=row.status, project_id=str(project.id)
        )
        asset_paths[str(row.id)] = Path(row.file.path)

    validated = validate_timeline(project.timeline, asset_map)

    audible = validated.audible_clips()
    if not audible:
        raise TimelineValidationError(["timeline has no audible clips to export"])

    track_volumes = {t["id"]: t["volume"] for t in validated.tracks}
    specs: list[ClipRenderSpec] = []
    for clip in audible:
        specs.append(
            ClipRenderSpec(
                clip_id=clip.id,
                input_path=asset_paths[clip.asset_id],
                trim_in=clip.trim_in,
                trim_out=clip.trim_out,
                start=clip.start,
                volume=clip.volume * track_volumes.get(clip.track_id, 1.0) * validated.master_volume,
                fade_in=clip.fade_in,
                fade_out=clip.fade_out,
            )
        )
    specs.sort(key=lambda s: (s.start, s.clip_id))
    return specs, validated.duration


@shared_task(
    bind=True,
    name="studio.render_export",
    acks_late=True,
    max_retries=2,
    soft_time_limit=settings.RENDER_TIMEOUT_SECONDS,
    time_limit=settings.RENDER_TIMEOUT_SECONDS + 120,
)
def render_export_task(self, export_id: str) -> dict:
    """Render one export job to a real audio file.

    * Deterministic failures (invalid timeline/settings) fail immediately —
      they are never retried.
    * Transient infrastructure failures (storage/OS errors) are retried at
      most twice with a short delay.
    * Temporary files are always removed, partial outputs never accepted.
    """
    try:
        job = ExportJob.objects.select_related("project", "owner").get(pk=export_id)
    except ExportJob.DoesNotExist:
        logger.error("export job %s vanished before rendering", export_id)
        return {"status": "missing"}

    if job.status not in {ExportJob.Status.QUEUED, ExportJob.Status.PROCESSING}:
        return {"status": job.status}

    job.status = ExportJob.Status.PROCESSING
    job.started_at = timezone.now()
    job.progress = 5
    job.error_message = ""
    job.save(update_fields=["status", "started_at", "progress", "error_message"])

    settings_json = job.settings_json or {}
    export_format_name = settings_json.get("format", "mp3")
    bitrate = settings_json.get("bitrate", "192k")
    depth = int(settings_json.get("depth", 16))

    tmp_dir = Path(tempfile.mkdtemp(prefix="beatlab-export-"))
    tmp_out = tmp_dir / f"render.{export_format_name}"

    try:
        try:
            export_format = (
                ExportFormat.mp3(bitrate)
                if export_format_name == "mp3"
                else ExportFormat.wav(depth)
            )
        except ValueError as exc:
            raise TimelineValidationError([f"invalid export settings: {exc}"]) from exc

        specs, duration = build_render_inputs(job)

        plan = build_render_plan(
            specs,
            duration,
            tmp_out,
            export_format,
            sample_rate=settings.RENDER_SAMPLE_RATE,
            ffmpeg_binary=settings.FFMPEG_BINARY,
        )

        def on_progress(fraction: float) -> None:
            pct = int(5 + fraction * 90)
            ExportJob.objects.filter(pk=job.pk).update(progress=min(99, pct))

        result = run_render_plan(
            plan,
            timeout_seconds=settings.RENDER_TIMEOUT_SECONDS,
            on_progress=on_progress,
        )

        with open(tmp_out, "rb") as fh:
            job.output_file.save(f"{job.id}.{export_format.extension}", File(fh), save=False)
        job.status = ExportJob.Status.COMPLETED
        job.progress = 100
        job.output_size = result["size_bytes"]
        job.output_duration = result["duration"]
        job.output_format = export_format.extension
        job.completed_at = timezone.now()
        job.error_message = ""
        job.save()
        logger.info("export %s completed (%s bytes)", job.id, result["size_bytes"])
        return {"status": "completed"}

    except TimelineValidationError as exc:
        _fail(job, exc.errors[0][:255] if exc.errors else "Invalid timeline.")
        logger.info("export %s rejected: %s", job.id, job.error_message)
        return {"status": "failed", "reason": "invalid"}

    except (RenderError, FFmpegNotFoundError, RenderTimeoutError) as exc:
        _fail(job, exc.message[:255])
        logger.error("export %s render error: %s", job.id, exc.code)
        return {"status": "failed", "reason": exc.code}

    except OSError:
        logger.exception("export %s hit a transient OS error", job.id)
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=15)
        _fail(job, "Storage error while saving the rendered file.")
        return {"status": "failed", "reason": "storage"}

    except Exception:
        logger.exception("export %s crashed", job.id)
        _fail(job, "Internal rendering error.")
        return {"status": "failed", "reason": "internal"}

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _fail(job: ExportJob, message: str) -> None:
    job.status = ExportJob.Status.FAILED
    job.error_message = message
    job.completed_at = timezone.now()
    job.save(update_fields=["status", "error_message", "completed_at"])
