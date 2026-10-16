"""Audio upload validation tests (require ffmpeg for real probing)."""
from __future__ import annotations

import pytest

from tests.conftest import requires_ffmpeg


@requires_ffmpeg
class TestUploadValidation:
    def _upload(self, client, project_id, path, name=None):
        with open(path, "rb") as fh:
            return client.post(
                f"/api/projects/{project_id}/assets/",
                {"file": (name or path.name, fh, "audio/wav")},
                format="multipart",
            )

    def test_upload_wav_succeeds(self, auth_client, project, wav_file_factory):
        wav = wav_file_factory(duration=1.5)
        response = self._upload(auth_client, project["id"], wav)
        assert response.status_code == 201, response.content
        body = response.json()
        assert body["status"] == "ready"
        assert body["duration"] == pytest.approx(1.5, abs=0.1)
        assert body["waveform"] and body["waveform"]["peaks"]

    def test_upload_rejects_non_audio(self, auth_client, project, tmp_path):
        junk = tmp_path / "not-audio.wav"
        junk.write_bytes(b"this is definitely not audio data" * 10)
        response = self._upload(auth_client, project["id"], junk)
        assert response.status_code == 400
        assert response.json()["error"]["code"] in {"invalid_audio", "unsupported_audio"}

    def test_upload_rejects_oversized_file(self, auth_client, project, wav_file_factory, monkeypatch):
        from django.conf import settings

        monkeypatch.setitem(settings.BEATLAB_LIMITS, "max_upload_bytes", 1024)
        wav = wav_file_factory(duration=1.0)
        response = self._upload(auth_client, project["id"], wav)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "file_too_large"

    def test_upload_rejects_overlong_duration(self, auth_client, project, wav_file_factory, monkeypatch):
        from django.conf import settings

        monkeypatch.setitem(settings.BEATLAB_LIMITS, "max_source_duration", 1)
        wav = wav_file_factory(duration=5.0)
        response = self._upload(auth_client, project["id"], wav)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "file_too_long"

    def test_upload_respects_asset_limit(self, auth_client, project, wav_file_factory, monkeypatch):
        from django.conf import settings

        monkeypatch.setitem(settings.BEATLAB_LIMITS, "max_assets_per_project", 1)
        wav1 = wav_file_factory(duration=0.5, frequency=440, name="a.wav")
        wav2 = wav_file_factory(duration=0.5, frequency=880, name="b.wav")
        assert self._upload(auth_client, project["id"], wav1).status_code == 201
        response = self._upload(auth_client, project["id"], wav2)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "asset_limit"

    def test_upload_missing_file_field(self, auth_client, project):
        response = auth_client.post(
            f"/api/projects/{project['id']}/assets/", {}, format="multipart"
        )
        assert response.status_code == 400

    def test_cannot_upload_to_others_project(self, other_client, project, wav_file_factory):
        wav = wav_file_factory(duration=0.5)
        response = self._upload(other_client, project["id"], wav)
        assert response.status_code == 404

    def test_asset_delete_and_isolation(self, auth_client, other_client, project, wav_file_factory):
        wav = wav_file_factory(duration=0.5)
        asset = self._upload(auth_client, project["id"], wav).json()

        # Cross-user delete must 404.
        assert other_client.delete(f"/api/assets/{asset['id']}/").status_code == 404
        # Cross-user audio fetch must 404.
        assert other_client.get(f"/api/assets/{asset['id']}/audio/").status_code == 404

        # Owner can stream the audio for preview.
        audio = auth_client.get(f"/api/assets/{asset['id']}/audio/")
        assert audio.status_code == 200
        assert audio["Content-Type"].startswith("audio/")

        # Owner can delete.
        assert auth_client.delete(f"/api/assets/{asset['id']}/").status_code == 204

    def test_delete_asset_referenced_by_timeline_fails(
        self, auth_client, project, wav_file_factory
    ):
        wav = wav_file_factory(duration=2.0)
        asset = self._upload(auth_client, project["id"], wav).json()

        timeline = {
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
                            "asset_id": asset["id"],
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
        patch = auth_client.patch(
            f"/api/projects/{project['id']}/", {"timeline": timeline}, format="json"
        )
        assert patch.status_code == 200

        response = auth_client.delete(f"/api/assets/{asset['id']}/")
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "asset_in_use"
