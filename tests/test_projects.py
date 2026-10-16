"""Project CRUD, ownership isolation, and timeline save/restore tests."""
from __future__ import annotations

VALID_TIMELINE = {
    "schema_version": 1,
    "settings": {"master_volume": 1.0},
    "tracks": [
        {
            "id": "trk_a",
            "name": "Vocals",
            "order": 0,
            "volume": 1.0,
            "muted": False,
            "clips": [
                {
                    "id": "clp_a",
                    "asset_id": "00000000-0000-0000-0000-000000000000",
                    "start": 0.0,
                    "trim_in": 0.0,
                    "trim_out": 1.0,
                    "volume": 1.0,
                    "muted": False,
                    "fade_in": 0.1,
                    "fade_out": 0.1,
                }
            ],
        }
    ],
}


class TestProjectCRUD:
    def test_create_project(self, auth_client):
        response = auth_client.post("/api/projects/", {"title": "ریمیکس من"}, format="json")
        assert response.status_code == 201
        body = response.json()
        assert body["title"] == "ریمیکس من"
        assert body["clip_count"] == 0
        assert "timeline" not in body  # list/create payloads stay compact

    def test_create_project_default_title(self, auth_client):
        response = auth_client.post("/api/projects/", {}, format="json")
        assert response.status_code == 201
        assert response.json()["title"]

    def test_list_projects_only_own(self, auth_client, other_client):
        auth_client.post("/api/projects/", {"title": "mine"}, format="json")
        other_client.post("/api/projects/", {"title": "theirs"}, format="json")

        response = auth_client.get("/api/projects/")
        assert response.status_code == 200
        results = response.json()["results"]
        assert len(results) == 1
        assert results[0]["title"] == "mine"

    def test_get_project_includes_timeline_and_assets(self, auth_client, project):
        response = auth_client.get(f"/api/projects/{project['id']}/")
        assert response.status_code == 200
        body = response.json()
        assert body["timeline"]["schema_version"] == 1
        assert body["assets"] == []

    def test_rename_project(self, auth_client, project):
        response = auth_client.patch(
            f"/api/projects/{project['id']}/", {"title": "New name"}, format="json"
        )
        assert response.status_code == 200
        assert response.json()["title"] == "New name"

    def test_delete_project(self, auth_client, project):
        response = auth_client.delete(f"/api/projects/{project['id']}/")
        assert response.status_code == 204
        assert auth_client.get(f"/api/projects/{project['id']}/").status_code == 404


class TestOwnershipIsolation:
    def test_cannot_read_others_project(self, auth_client, other_client, project):
        response = other_client.get(f"/api/projects/{project['id']}/")
        assert response.status_code == 404

    def test_cannot_patch_others_project(self, auth_client, other_client, project):
        response = other_client.patch(
            f"/api/projects/{project['id']}/", {"title": "hacked"}, format="json"
        )
        assert response.status_code == 404

    def test_cannot_delete_others_project(self, auth_client, other_client, project):
        response = other_client.delete(f"/api/projects/{project['id']}/")
        assert response.status_code == 404

    def test_unauthenticated_requests_rejected(self, project):
        from rest_framework.test import APIClient

        response = APIClient().get(f"/api/projects/{project['id']}/")
        assert response.status_code == 403


class TestTimelineSaveRestore:
    def _timeline_with_asset(self, asset_id: str) -> dict:
        import copy

        doc = copy.deepcopy(VALID_TIMELINE)
        doc["tracks"][0]["clips"][0]["asset_id"] = asset_id
        return doc

    def test_save_and_reload_timeline(self, auth_client, project, user, wav_file_factory):
        from studio.models import AudioAsset, Project

        wav = wav_file_factory(duration=2.0)
        asset = AudioAsset.objects.create(
            owner=user,
            project_id=project["id"],
            file="assets/test/x.wav",
            original_name="x.wav",
            duration=2.0,
            status=AudioAsset.Status.READY,
        )

        doc = self._timeline_with_asset(str(asset.id))
        response = auth_client.patch(
            f"/api/projects/{project['id']}/", {"timeline": doc}, format="json"
        )
        assert response.status_code == 200, response.content

        reloaded = auth_client.get(f"/api/projects/{project['id']}/").json()
        clip = reloaded["timeline"]["tracks"][0]["clips"][0]
        assert clip["asset_id"] == str(asset.id)
        assert clip["trim_out"] == 1.0

    def test_invalid_asset_reference_rejected(self, auth_client, project):
        response = auth_client.patch(
            f"/api/projects/{project['id']}/", {"timeline": VALID_TIMELINE}, format="json"
        )
        assert response.status_code == 400
        body = response.json()
        assert body["error"]["code"] == "invalid_timeline"
        assert any("unknown or unauthorized" in d for d in body["error"]["details"])

    def test_foreign_asset_rejected(self, auth_client, other_client, project, other_user):
        from studio.models import AudioAsset

        # Another user's asset — id is a real UUID but ownership check must fail.
        other_project = other_client.post("/api/projects/", {"title": "x"}, format="json").json()
        foreign = AudioAsset.objects.create(
            owner=other_user,
            project_id=other_project["id"],
            file="assets/test/y.wav",
            original_name="y.wav",
            duration=3.0,
            status=AudioAsset.Status.READY,
        )

        doc = VALID_TIMELINE.copy()
        doc = {
            **doc,
            "tracks": [
                {
                    **doc["tracks"][0],
                    "clips": [{**doc["tracks"][0]["clips"][0], "asset_id": str(foreign.id)}],
                }
            ],
        }
        response = auth_client.patch(
            f"/api/projects/{project['id']}/", {"timeline": doc}, format="json"
        )
        assert response.status_code == 400
