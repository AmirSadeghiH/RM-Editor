"""DRF serializers for request validation and response shaping."""
from __future__ import annotations

from rest_framework import serializers

from studio.models import AudioAsset, ExportJob, Project


class ProjectSerializer(serializers.ModelSerializer):
    clip_count = serializers.SerializerMethodField()
    asset_count = serializers.IntegerField(source="assets.count", read_only=True)

    class Meta:
        model = Project
        fields = [
            "id",
            "title",
            "schema_version",
            "created_at",
            "updated_at",
            "asset_count",
            "clip_count",
        ]
        read_only_fields = fields

    def get_clip_count(self, obj: Project) -> int:
        timeline = obj.timeline or {}
        total = 0
        for track in timeline.get("tracks", []) or []:
            if isinstance(track, dict):
                total += len(track.get("clips", []) or [])
        return total


class ProjectCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")

    def validate_title(self, value: str) -> str:
        value = (value or "").strip()
        return value or "پروژه بدون عنوان"


class ProjectUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200, required=False)
    timeline = serializers.DictField(required=False)

    def validate_title(self, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Title must not be empty.")
        return value


class AudioAssetSerializer(serializers.ModelSerializer):
    class Meta:
        model = AudioAsset
        fields = [
            "id",
            "original_name",
            "file_size",
            "duration",
            "format_name",
            "codec_name",
            "sample_rate",
            "channels",
            "status",
            "error_message",
            "waveform",
            "created_at",
        ]
        read_only_fields = fields


class ExportSettingsSerializer(serializers.Serializer):
    format = serializers.ChoiceField(choices=["mp3", "wav"], default="mp3")
    bitrate = serializers.ChoiceField(
        choices=["128k", "192k", "256k", "320k"], default="192k", required=False
    )
    depth = serializers.ChoiceField(choices=[16, 24], default=16, required=False)

    def validate(self, attrs: dict) -> dict:
        fmt = attrs.get("format", "mp3")
        if fmt == "wav":
            attrs.setdefault("depth", 16)
        else:
            attrs.setdefault("bitrate", "192k")
        return attrs


class ExportJobSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = ExportJob
        fields = [
            "id",
            "project",
            "settings_json",
            "status",
            "progress",
            "output_size",
            "output_duration",
            "output_format",
            "error_message",
            "download_url",
            "created_at",
            "started_at",
            "completed_at",
        ]
        read_only_fields = fields

    def get_download_url(self, obj: ExportJob) -> str | None:
        if obj.status != ExportJob.Status.COMPLETED:
            return None
        return f"/api/exports/{obj.id}/download/"
