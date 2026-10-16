"""Persistent models: projects, uploaded audio assets, export jobs.

Filesystem names are always generated (UUID-based). Client-supplied
filenames are kept only as display metadata and never used as paths.
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


def asset_upload_path(instance: "AudioAsset", filename: str) -> str:
    """Storage path for an uploaded asset. ``filename`` is intentionally
    ignored: only the generated UUID and a validated extension are used."""
    ext = (instance.file_extension or "bin").lower().lstrip(".")
    return f"assets/{instance.project_id}/{instance.id}.{ext}"


def export_upload_path(instance: "ExportJob", filename: str) -> str:
    """Storage path for a rendered export (again: generated name only)."""
    ext = (instance.output_format or "bin").lower().lstrip(".")
    return f"exports/{instance.project_id}/{instance.id}.{ext}"


class Project(models.Model):
    """One remix project owned by a single user."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="projects",
    )
    title = models.CharField(max_length=200)
    timeline = models.JSONField(default=dict, blank=True)
    schema_version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["owner", "-updated_at"]),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.title} ({self.id})"


class AudioAsset(models.Model):
    """An uploaded audio file, inspected and ready for the timeline."""

    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="audio_assets",
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="assets",
    )
    file = models.FileField(upload_to=asset_upload_path, max_length=255)
    original_name = models.CharField(max_length=255, blank=True, default="")
    file_size = models.BigIntegerField(default=0)
    duration = models.FloatField(default=0.0)
    format_name = models.CharField(max_length=32, blank=True, default="")
    codec_name = models.CharField(max_length=32, blank=True, default="")
    sample_rate = models.IntegerField(default=0)
    channels = models.IntegerField(default=0)
    file_extension = models.CharField(max_length=16, blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PROCESSING,
    )
    error_message = models.CharField(max_length=255, blank=True, default="")
    waveform = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["project", "created_at"]),
            models.Index(fields=["owner"]),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.original_name or self.id} ({self.status})"


class ExportJob(models.Model):
    """A queued/completed server-side render of a project timeline."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="export_jobs",
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="export_jobs",
    )
    settings_json = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
    )
    progress = models.PositiveSmallIntegerField(default=0)
    output_file = models.FileField(upload_to=export_upload_path, max_length=255, blank=True)
    output_size = models.BigIntegerField(default=0)
    output_duration = models.FloatField(default=0.0)
    output_format = models.CharField(max_length=8, blank=True, default="")
    error_message = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "-created_at"]),
            models.Index(fields=["owner", "-created_at"]),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"export {self.id} ({self.status})"

    @property
    def is_active(self) -> bool:
        return self.status in {self.Status.QUEUED, self.Status.PROCESSING}
