"""Upload handling for audio assets.

Untrusted files are spooled to a temporary path, inspected with ffprobe,
validated against configured limits, and only then moved into managed
storage under a generated name.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
import uuid
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.db import transaction

from renderer.errors import LimitExceededError, RenderError
from renderer.ffprobe import probe_audio
from renderer.peaks import compute_peaks
from studio.models import AudioAsset, Project

logger = logging.getLogger(__name__)


class UploadRejected(Exception):
    """Upload refused with a safe client-facing message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


_EXTENSION_BY_CODEC = {
    "mp3": "mp3",
    "aac": "m4a",
    "aac_latm": "m4a",
    "flac": "flac",
    "vorbis": "ogg",
    "opus": "opus",
    "pcm_s16le": "wav",
    "pcm_s24le": "wav",
    "pcm_s32le": "wav",
    "pcm_f32le": "wav",
    "pcm_u8": "wav",
    "pcm_s16be": "wav",
    "pcm_s24be": "wav",
    "mp3float": "mp3",
}


def _safe_display_name(name: str) -> str:
    """Keep a human-readable original name without ever using it as a path."""
    name = (name or "").replace("\x00", "").replace("/", " ").replace("\\", " ").strip()
    return name[:255] or "audio"


@transaction.atomic
def create_audio_asset(project: Project, uploaded_file) -> AudioAsset:
    """Validate and store one uploaded audio file for ``project``.

    Raises ``UploadRejected`` on any problem. On success the asset is in
    ``ready`` status with probed metadata and waveform peaks.
    """
    limits = settings.BEATLAB_LIMITS

    size = getattr(uploaded_file, "size", 0) or 0
    if size <= 0:
        raise UploadRejected("empty_file", "Uploaded file is empty.")
    if size > limits["max_upload_bytes"]:
        raise UploadRejected(
            "file_too_large",
            f"File exceeds the {limits['max_upload_bytes'] // (1024 * 1024)} MiB upload limit.",
        )

    current_assets = project.assets.count()
    if current_assets >= limits["max_assets_per_project"]:
        raise UploadRejected(
            "asset_limit",
            f"Project already has the maximum of {limits['max_assets_per_project']} audio files.",
        )

    asset = AudioAsset(
        owner=project.owner,
        project=project,
        original_name=_safe_display_name(getattr(uploaded_file, "name", "")),
    )

    tmp = tempfile.NamedTemporaryFile(prefix="beatlab-upload-", suffix=".bin", delete=False)
    tmp_path = Path(tmp.name)
    try:
        for chunk in uploaded_file.chunks():
            tmp.write(chunk)
        tmp.close()

        try:
            info = probe_audio(tmp_path)
        except LimitExceededError as exc:
            raise UploadRejected("file_too_long", exc.message) from exc
        except RenderError as exc:
            raise UploadRejected(exc.code, exc.message) from exc

        ext = _EXTENSION_BY_CODEC.get(info.codec_name, "bin")
        asset.file_size = info.size_bytes
        asset.duration = info.duration
        asset.format_name = info.format_name
        asset.codec_name = info.codec_name
        asset.sample_rate = info.sample_rate
        asset.channels = info.channels
        asset.file_extension = ext

        with open(tmp_path, "rb") as fh:
            generated_name = f"{asset.id}.{ext}"
            asset.file.save(generated_name, File(fh), save=False)

        try:
            asset.waveform = compute_peaks(tmp_path)
        except RenderError:
            # Peaks are a display optimization; the asset itself is valid.
            logger.warning("waveform extraction failed for asset %s", asset.id)
            asset.waveform = None

        asset.status = AudioAsset.Status.READY
        asset.save()
        return asset
    except UploadRejected:
        raise
    except Exception:
        logger.exception("unexpected failure storing upload for project %s", project.id)
        raise UploadRejected("upload_failed", "Upload failed; please try again.") from None
    finally:
        try:
            tmp.close()
        except Exception:
            pass
        tmp_path.unlink(missing_ok=True)
