"""REST API views.

All querysets are scoped to ``request.user``: users can never read or mutate
another user's projects, assets, or exports.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction
from django.http import FileResponse, Http404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from studio import tasks
from studio.assets import UploadRejected, create_audio_asset
from studio.models import AudioAsset, ExportJob, Project
from studio.serializers import (
    AudioAssetSerializer,
    ExportJobSerializer,
    ExportSettingsSerializer,
    ProjectCreateSerializer,
    ProjectSerializer,
    ProjectUpdateSerializer,
)
from studio.timeline import (
    AssetInfo,
    TimelineValidationError,
    ValidatedTimeline,
    empty_timeline,
    validate_timeline,
)

logger = logging.getLogger(__name__)


def _error(code: str, message: str, http_status: int, details: list[str] | None = None) -> Response:
    return Response(
        {"error": {"code": code, "message": message, "details": details or []}},
        status=http_status,
    )


def _asset_info_map(project: Project, *, ready_only: bool = True) -> dict[str, AssetInfo]:
    rows = project.assets.all()
    if ready_only:
        rows = rows.filter(status=AudioAsset.Status.READY)
    return {
        str(a.id): AssetInfo(
            id=str(a.id), duration=a.duration, status=a.status, project_id=str(project.id)
        )
        for a in rows
    }


@api_view(["GET"])
@permission_classes([])
def health_check(_request: Request) -> Response:
    """Liveness probe used by Docker healthchecks."""
    return Response({"status": "ok", "time": timezone.now().isoformat()})


# ---------------------------------------------------------------------------
# Mini App pages (served by Django so API + UI share one origin)
# ---------------------------------------------------------------------------
from django.shortcuts import render as django_render  # noqa: E402
from django.views.decorators.csrf import ensure_csrf_cookie  # noqa: E402


@ensure_csrf_cookie
def home_page(request):
    """Project home. Sets the CSRF cookie for the SPA."""
    return django_render(request, "webapp/home.html")


@ensure_csrf_cookie
def editor_page(request):
    """Multitrack editor. Sets the CSRF cookie for the SPA."""
    return django_render(request, "webapp/editor.html")


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
class ProjectListCreate(generics.ListCreateAPIView):
    """GET lists the caller's projects; POST creates one."""

    permission_classes = [IsAuthenticated]
    parsers = [JSONParser]

    def get_queryset(self):
        return Project.objects.filter(owner=self.request.user)

    def get_serializer_class(self):
        return ProjectCreateSerializer if self.request.method == "POST" else ProjectSerializer

    def create(self, request: Request, *args, **kwargs) -> Response:
        serializer = ProjectCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        project = Project.objects.create(
            owner=request.user,
            title=serializer.validated_data["title"],
            timeline=empty_timeline(),
            schema_version=1,
        )
        return Response(ProjectSerializer(project).data, status=status.HTTP_201_CREATED)


class ProjectDetail(APIView):
    permission_classes = [IsAuthenticated]
    parsers = [JSONParser]

    def get_object(self, request: Request, project_id: str) -> Project:
        try:
            return Project.objects.prefetch_related("assets").get(pk=project_id, owner=request.user)
        except (Project.DoesNotExist, ValueError):
            raise Http404("Project not found.")

    def get(self, request: Request, project_id: str) -> Response:
        project = self.get_object(request, project_id)
        payload = ProjectSerializer(project).data
        payload["timeline"] = project.timeline
        payload["assets"] = AudioAssetSerializer(project.assets.all(), many=True).data
        return Response(payload)

    def patch(self, request: Request, project_id: str) -> Response:
        project = self.get_object(request, project_id)
        serializer = ProjectUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        if "title" in serializer.validated_data:
            project.title = serializer.validated_data["title"]

        if "timeline" in serializer.validated_data:
            timeline = serializer.validated_data["timeline"]
            try:
                validated = validate_timeline(timeline, _asset_info_map(project, ready_only=False))
            except TimelineValidationError as exc:
                return _error(
                    "invalid_timeline",
                    "Timeline validation failed.",
                    status.HTTP_400_BAD_REQUEST,
                    exc.errors,
                )
            project.timeline = _normalize_timeline(timeline, validated)
            project.schema_version = validated.schema_version

        project.save()
        payload = ProjectSerializer(project).data
        payload["timeline"] = project.timeline
        return Response(payload)

    def delete(self, request: Request, project_id: str) -> Response:
        project = self.get_object(request, project_id)
        _delete_project_files(project)
        project.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def _normalize_timeline(document: dict, validated: ValidatedTimeline) -> dict:
    """Return a canonical copy of ``document`` with validated numbers."""
    clips_by_track: dict[str, list] = {}
    for clip in validated.clips:
        clips_by_track.setdefault(clip.track_id, []).append(
            {
                "id": clip.id,
                "asset_id": clip.asset_id,
                "start": round(clip.start, 6),
                "trim_in": round(clip.trim_in, 6),
                "trim_out": round(clip.trim_out, 6),
                "volume": round(clip.volume, 6),
                "muted": clip.muted,
                "fade_in": round(clip.fade_in, 6),
                "fade_out": round(clip.fade_out, 6),
            }
        )
    # Preserve original clip order within each track where possible.
    original_order = {
        track.get("id"): [c.get("id") for c in (track.get("clips") or []) if isinstance(c, dict)]
        for track in (document.get("tracks") or [])
        if isinstance(track, dict)
    }
    tracks_out = []
    for track in validated.tracks:
        clips = clips_by_track.get(track["id"], [])
        order = original_order.get(track["id"], [])
        rank = {cid: i for i, cid in enumerate(order)}
        clips.sort(key=lambda c: rank.get(c["id"], len(order)))
        tracks_out.append({**track, "clips": clips})
    return {
        "schema_version": validated.schema_version,
        "settings": {"master_volume": validated.master_volume},
        "tracks": tracks_out,
    }


def _delete_project_files(project: Project) -> None:
    """Best-effort removal of managed files when a project is deleted."""
    for asset in project.assets.all():
        asset.file.delete(save=False)
    for job in project.export_jobs.all():
        job.output_file.delete(save=False)


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------
class ProjectAssetUpload(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "upload"

    def post(self, request: Request, project_id: str) -> Response:
        try:
            project = Project.objects.get(pk=project_id, owner=request.user)
        except (Project.DoesNotExist, ValueError):
            raise Http404("Project not found.")

        uploaded = request.FILES.get("file")
        if uploaded is None:
            return _error("file_missing", "Attach a file in the 'file' field.", 400)

        try:
            asset = create_audio_asset(project, uploaded)
        except UploadRejected as exc:
            return _error(exc.code, exc.message, status.HTTP_400_BAD_REQUEST)

        return Response(AudioAssetSerializer(asset).data, status=status.HTTP_201_CREATED)


class AssetDetail(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, request: Request, asset_id: str) -> AudioAsset:
        try:
            return AudioAsset.objects.select_related("project").get(pk=asset_id, owner=request.user)
        except (AudioAsset.DoesNotExist, ValueError):
            raise Http404("Asset not found.")

    def delete(self, request: Request, asset_id: str) -> Response:
        asset = self.get_object(request, asset_id)
        project = asset.project

        # Refuse to delete an asset that is still referenced by the timeline.
        for track in (project.timeline or {}).get("tracks", []) or []:
            if not isinstance(track, dict):
                continue
            for clip in track.get("clips") or []:
                if isinstance(clip, dict) and clip.get("asset_id") == str(asset.id):
                    return _error(
                        "asset_in_use",
                        "This file is still used on the timeline. Remove its clips first.",
                        status.HTTP_400_BAD_REQUEST,
                    )

        asset.file.delete(save=False)
        asset.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AssetAudio(APIView):
    """Stream an asset's audio back to its owner (preview / waveform decode)."""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request, asset_id: str) -> Response:
        try:
            asset = AudioAsset.objects.get(pk=asset_id, owner=request.user)
        except (AudioAsset.DoesNotExist, ValueError):
            raise Http404("Asset not found.")
        if not asset.file:
            raise Http404("Asset file missing.")

        response = FileResponse(
            asset.file.open("rb"),
            content_type=_audio_content_type(asset),
            as_attachment=False,
        )
        response["Cache-Control"] = "private, max-age=3600"
        response["X-Content-Type-Options"] = "nosniff"
        return response


def _audio_content_type(asset: AudioAsset) -> str:
    return {
        "mp3": "audio/mpeg",
        "wav": "audio/wav",
        "flac": "audio/flac",
        "ogg": "audio/ogg",
        "opus": "audio/ogg",
        "m4a": "audio/mp4",
        "aac": "audio/aac",
    }.get(asset.file_extension, "application/octet-stream")


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------
class ProjectExportCreate(APIView):
    permission_classes = [IsAuthenticated]
    parsers = [JSONParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "export"

    def post(self, request: Request, project_id: str) -> Response:
        try:
            project = Project.objects.get(pk=project_id, owner=request.user)
        except (Project.DoesNotExist, ValueError):
            raise Http404("Project not found.")

        # Prevent accidental duplicate jobs from repeated taps.
        active = (
            project.export_jobs.filter(
                status__in=[ExportJob.Status.QUEUED, ExportJob.Status.PROCESSING]
            )
            .order_by("-created_at")
            .first()
        )
        if active is not None:
            return Response(
                {
                    "error": {
                        "code": "export_in_progress",
                        "message": "An export is already running for this project.",
                        "details": [],
                    },
                    "job": ExportJobSerializer(active).data,
                },
                status=status.HTTP_409_CONFLICT,
            )

        settings_serializer = ExportSettingsSerializer(data=request.data or {})
        settings_serializer.is_valid(raise_exception=True)

        # Validate the timeline now so bad requests fail fast with details.
        try:
            validated = validate_timeline(project.timeline, _asset_info_map(project))
        except TimelineValidationError as exc:
            return _error(
                "invalid_timeline",
                "Timeline validation failed.",
                status.HTTP_400_BAD_REQUEST,
                exc.errors,
            )
        if not validated.audible_clips():
            return _error(
                "no_audible_clips",
                "The timeline has no audible clips to export.",
                status.HTTP_400_BAD_REQUEST,
            )

        job = ExportJob.objects.create(
            owner=request.user,
            project=project,
            settings_json=settings_serializer.validated_data,
            output_format=settings_serializer.validated_data.get("format", "mp3"),
            status=ExportJob.Status.QUEUED,
        )
        if settings.BEATLAB_CELERY_EAGER:
            # Synchronous dev/test fallback (no Redis in the environment).
            tasks.render_export_task.delay(str(job.id))
        else:
            # Enqueue only after the transaction commits so the worker can
            # always see the job row.
            transaction.on_commit(lambda: tasks.render_export_task.delay(str(job.id)))

        return Response(ExportJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class ExportDetail(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, export_id: str) -> Response:
        try:
            job = ExportJob.objects.get(pk=export_id, owner=request.user)
        except (ExportJob.DoesNotExist, ValueError):
            raise Http404("Export not found.")
        return Response(ExportJobSerializer(job).data)


class ExportDownload(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, export_id: str) -> Response:
        try:
            job = ExportJob.objects.get(pk=export_id, owner=request.user)
        except (ExportJob.DoesNotExist, ValueError):
            raise Http404("Export not found.")

        if job.status != ExportJob.Status.COMPLETED or not job.output_file:
            return _error(
                "export_not_ready",
                "Export is not ready for download.",
                status.HTTP_400_BAD_REQUEST,
            )

        content_type = {
            "mp3": "audio/mpeg",
            "wav": "audio/wav",
        }.get(job.output_format, "application/octet-stream")

        response = FileResponse(
            job.output_file.open("rb"),
            content_type=content_type,
            as_attachment=True,
            filename=f"beatlab-export-{job.id}.{job.output_format or 'bin'}",
        )
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response
