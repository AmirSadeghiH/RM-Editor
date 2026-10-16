"""Export job API: creation, status flow, downloads, authorization."""
from __future__ import annotations

import pytest

from studio.models import ExportJob
from tests.conftest import requires_ffmpeg


def timeline_for(asset_id: str) -> dict:
    return {
        "schema_version": 1,
        "settings": {"master_volume": 1.0},
        "tracks": [
            {
                "id": "trk_1",
                "name": "T",
                "order": 0,
                "volume": 1.0,
                "muted": False,
                "clips": [
                    {
                        "id": "clp_1",
                        "asset_id": str(asset_id),
                        "start": 0.0,
                        "trim_in": 0.0,
                        "trim_out": 1.0,
                        "volume": 1.0,
                        "muted": False,
                        "fade_in": 0.0,
                        "fade_out": 0.0,
                    }
                ],
            }
        ],
    }


@pytest.fixture
def ready_project(auth_client, project, wav_file_factory):
    """A project with one uploaded asset and a valid timeline (sync render)."""
    wav = wav_file_factory(duration=2.0)
    with open(wav, "rb") as fh:
        asset = auth_client.post(
            f"/api/projects/{project['id']}/assets/",
            {"file": (wav.name, fh, "audio/wav")},
            format="multipart",
        ).json()
    patch = auth_client.patch(
        f"/api/projects/{project['id']}/",
        {"timeline": timeline_for(asset["id"])},
        format="json",
    )
    assert patch.status_code == 200, patch.content
    return {**project, "asset": asset}


class TestExportAPI:
    def test_export_requires_audible_clips(self, auth_client, project):
        response = auth_client.post(
            f"/api/projects/{project['id']}/exports/", {"format": "mp3"}, format="json"
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "no_audible_clips"

    def test_export_rejects_invalid_settings(self, auth_client, ready_project):
        response = auth_client.post(
            f"/api/projects/{ready_project['id']}/exports/",
            {"format": "flac"},
            format="json",
        )
        assert response.status_code == 400

    def test_export_duplicate_blocked_while_active(self, auth_client, ready_project):
        first = auth_client.post(
            f"/api/projects/{ready_project['id']}/exports/", {"format": "mp3"}, format="json"
        )
        # With eager Celery the job finishes immediately; simulate an active job.
        job = ExportJob.objects.get(pk=first.json()["id"])
        job.status = ExportJob.Status.PROCESSING
        job.save(update_fields=["status"])

        second = auth_client.post(
            f"/api/projects/{ready_project['id']}/exports/", {"format": "mp3"}, format="json"
        )
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "export_in_progress"
        assert second.json()["job"]["id"] == first.json()["id"]

    def test_cannot_export_others_project(self, other_client, ready_project):
        response = other_client.post(
            f"/api/projects/{ready_project['id']}/exports/", {"format": "mp3"}, format="json"
        )
        assert response.status_code == 404

    def test_status_and_download_isolation(self, auth_client, other_client, ready_project):
        response = auth_client.post(
            f"/api/projects/{ready_project['id']}/exports/", {"format": "mp3"}, format="json"
        )
        assert response.status_code == 202
        job_id = response.json()["id"]

        assert other_client.get(f"/api/exports/{job_id}/").status_code == 404
        assert other_client.get(f"/api/exports/{job_id}/download/").status_code == 404


@requires_ffmpeg
class TestExportEndToEnd:
    def test_full_render_download_flow(self, auth_client, ready_project, settings):
        """Create → render (eager Celery) → poll → download a real MP3."""
        settings.BEATLAB_CELERY_EAGER = True
        settings.CELERY_TASK_ALWAYS_EAGER = True

        response = auth_client.post(
            f"/api/projects/{ready_project['id']}/exports/",
            {"format": "mp3", "bitrate": "192k"},
            format="json",
        )
        assert response.status_code == 202, response.content
        job_id = response.json()["id"]

        status = auth_client.get(f"/api/exports/{job_id}/").json()
        assert status["status"] == "completed", status
        assert status["progress"] == 100
        assert status["output_duration"] == pytest.approx(1.0, abs=0.35)

        download = auth_client.get(f"/api/exports/{job_id}/download/")
        assert download.status_code == 200
        assert download["Content-Type"] == "audio/mpeg"
        assert "attachment" in download["Content-Disposition"]

        body = b"".join(download.streaming_content)
        assert len(body) > 1000  # a real encoded file, not an empty stub

    def test_wav_export(self, auth_client, ready_project, settings):
        settings.BEATLAB_CELERY_EAGER = True
        settings.CELERY_TASK_ALWAYS_EAGER = True

        response = auth_client.post(
            f"/api/projects/{ready_project['id']}/exports/",
            {"format": "wav", "depth": 16},
            format="json",
        )
        assert response.status_code == 202
        status = auth_client.get(f"/api/exports/{response.json()['id']}/").json()
        assert status["status"] == "completed"
        assert status["output_format"] == "wav"

    def test_failed_render_marks_job_failed(self, auth_client, ready_project, settings, monkeypatch):
        settings.BEATLAB_CELERY_EAGER = True
        settings.CELERY_TASK_ALWAYS_EAGER = True

        from renderer import render as render_mod

        def boom(*args, **kwargs):
            from renderer.errors import RenderError

            raise RenderError("Audio rendering failed.", code="render_failed")

        monkeypatch.setattr(render_mod, "run_render_plan", boom)

        # The task imports run_render_plan into its own namespace — patch there.
        from studio import tasks as tasks_mod

        monkeypatch.setattr(tasks_mod, "run_render_plan", boom)

        response = auth_client.post(
            f"/api/projects/{ready_project['id']}/exports/", {"format": "mp3"}, format="json"
        )
        job_id = response.json()["id"]
        status = auth_client.get(f"/api/exports/{job_id}/").json()
        assert status["status"] == "failed"
        assert status["error_message"]
        # Download of a failed job must be refused.
        assert auth_client.get(f"/api/exports/{job_id}/download/").status_code == 400
