"""Production configuration guards: DEBUG, private media, download paths.

These tests re-execute the settings module with a production environment
instead of booting a second Django instance, and they reload the URL
configuration to check which routes really exist.
"""
from __future__ import annotations

import contextlib
import importlib
import os

import pytest
from django.core.files.base import ContentFile
from django.test import override_settings
from django.urls import Resolver404, clear_url_caches, resolve

from beatlab.urls import media_urlpatterns
from studio.models import ExportJob, Project
from studio.timeline import empty_timeline

PRODUCTION_ENV = {
    "DJANGO_ENV": "production",
    "DJANGO_SECRET_KEY": "p" * 48,
    "ALLOWED_HOSTS": "beatlab.example",
    "BEATLAB_DEV_AUTH": "0",
}


@contextlib.contextmanager
def reloaded_settings(**env):
    """Re-execute ``beatlab.settings`` with the given environment overrides.

    Yields the (reloaded) settings module. The environment and the module
    are restored afterwards, so other tests keep the suite's environment.
    """
    import beatlab.settings as settings_module

    previous = {key: os.environ.get(key) for key in env}
    for key, value in env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        importlib.reload(settings_module)
        yield settings_module
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        importlib.reload(settings_module)


@contextlib.contextmanager
def reloaded_urlconf():
    """Rebuild the URL configuration and restore it afterwards."""
    import beatlab.urls

    importlib.reload(beatlab.urls)
    clear_url_caches()
    try:
        yield beatlab.urls
    finally:
        importlib.reload(beatlab.urls)
        clear_url_caches()


def _media_route_matches() -> bool:
    """True when the active URLconf exposes the ``/media/`` route."""
    try:
        resolve("/media/probe.wav")
    except Resolver404:
        return False
    return True


class TestProductionDebugGuard:
    """``DJANGO_ENV=production`` + ``DEBUG=True`` must abort startup."""

    def test_production_refuses_debug_true(self):
        with pytest.raises(RuntimeError) as exc:
            with reloaded_settings(**{**PRODUCTION_ENV, "DJANGO_DEBUG": "1"}):
                pass  # pragma: no cover - the reload must raise
        assert "DJANGO_DEBUG" in str(exc.value)

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
    def test_production_refuses_every_truthy_debug_spelling(self, value):
        with pytest.raises(RuntimeError):
            with reloaded_settings(**{**PRODUCTION_ENV, "DJANGO_DEBUG": value}):
                pass  # pragma: no cover - the reload must raise

    def test_production_accepts_debug_zero(self):
        with reloaded_settings(**{**PRODUCTION_ENV, "DJANGO_DEBUG": "0"}) as module:
            assert module.IS_PRODUCTION is True
            assert module.DEBUG is False

    def test_production_debug_defaults_to_disabled(self):
        with reloaded_settings(**{**PRODUCTION_ENV, "DJANGO_DEBUG": None}) as module:
            assert module.IS_PRODUCTION is True
            assert module.DEBUG is False

    def test_development_still_allows_debug_true(self):
        with reloaded_settings(
            DJANGO_ENV="development", DJANGO_DEBUG="1", DJANGO_SECRET_KEY=None
        ) as module:
            assert module.IS_PRODUCTION is False
            assert module.DEBUG is True

    def test_production_debug_true_fails_before_other_checks(self):
        """The DEBUG guard must fire even with an otherwise valid config."""
        with pytest.raises(RuntimeError) as exc:
            with reloaded_settings(
                DJANGO_ENV="production",
                DJANGO_DEBUG="1",
                DJANGO_SECRET_KEY="p" * 48,
                ALLOWED_HOSTS="beatlab.example",
                BEATLAB_DEV_AUTH="0",
            ):
                pass  # pragma: no cover - the reload must raise
        message = str(exc.value)
        assert "DJANGO_DEBUG" in message
        assert "production" in message


class TestMediaServingConfiguration:
    """Uploaded media and exports must never be served statically in prod."""

    def test_media_urlpatterns_empty_in_production(self):
        with override_settings(DEBUG=True, IS_PRODUCTION=True):
            assert media_urlpatterns() == []

    def test_media_urlpatterns_empty_when_debug_is_off(self):
        with override_settings(DEBUG=False, IS_PRODUCTION=False):
            assert media_urlpatterns() == []

    def test_media_urlpatterns_present_in_development(self):
        with override_settings(DEBUG=True, IS_PRODUCTION=False):
            patterns = media_urlpatterns()
            assert len(patterns) == 1
            assert "media" in str(patterns[0].pattern)

    def test_production_urlconf_has_no_media_route(self):
        with override_settings(DEBUG=True, IS_PRODUCTION=True):
            with reloaded_urlconf():
                assert _media_route_matches() is False

    def test_development_urlconf_keeps_media_route(self):
        with override_settings(DEBUG=True, IS_PRODUCTION=False):
            with reloaded_urlconf():
                assert _media_route_matches() is True

    def test_production_does_not_serve_media_files(self, client, tmp_path):
        media_root = tmp_path / "media"
        media_root.mkdir()
        (media_root / "secret.wav").write_bytes(b"private audio bytes")

        with override_settings(
            DEBUG=True, IS_PRODUCTION=True, MEDIA_ROOT=str(media_root)
        ):
            with reloaded_urlconf():
                response = client.get("/media/secret.wav")
        assert response.status_code == 404
        assert b"private audio bytes" not in response.content

    def test_development_still_serves_media_files(self, client, tmp_path):
        media_root = tmp_path / "media"
        media_root.mkdir()
        (media_root / "secret.wav").write_bytes(b"private audio bytes")

        with override_settings(
            DEBUG=True, IS_PRODUCTION=False, MEDIA_ROOT=str(media_root)
        ):
            with reloaded_urlconf():
                response = client.get("/media/secret.wav")
        assert response.status_code == 200
        assert b"private audio bytes" in b"".join(response.streaming_content)


class TestPrivateDownloadPaths:
    """The only supported download paths are authenticated + owner-scoped."""

    @pytest.fixture
    def export_job(self, user):
        project = Project.objects.create(
            owner=user, title="Exports", timeline=empty_timeline(), schema_version=1
        )
        job = ExportJob.objects.create(
            owner=user,
            project=project,
            status=ExportJob.Status.COMPLETED,
            output_format="mp3",
        )
        job.output_file.save("out.mp3", ContentFile(b"ID3 fake audio"), save=True)
        return job

    def test_export_download_requires_authentication(self, client, export_job):
        response = client.get(f"/api/exports/{export_job.id}/download/")
        assert response.status_code in {401, 403}

    def test_export_download_is_owner_scoped(self, other_client, export_job):
        response = other_client.get(f"/api/exports/{export_job.id}/download/")
        assert response.status_code == 404

    def test_export_download_works_for_owner(self, auth_client, export_job):
        response = auth_client.get(f"/api/exports/{export_job.id}/download/")
        assert response.status_code == 200
        assert response["Content-Type"] == "audio/mpeg"
        assert "attachment" in response["Content-Disposition"]
        assert response["Cache-Control"] == "private, no-store"

    def test_export_file_is_not_reachable_through_media_route(
        self, client, export_job, tmp_path
    ):
        """The raw file must not be downloadable without authentication."""
        relative = export_job.output_file.name  # exports/<project>/<job>.mp3

        with override_settings(DEBUG=True, IS_PRODUCTION=True):
            with reloaded_urlconf():
                response = client.get(f"/media/{relative}")
        assert response.status_code == 404

    def test_asset_audio_requires_authentication(self, client):
        response = client.get("/api/assets/00000000-0000-0000-0000-000000000000/audio/")
        assert response.status_code in {401, 403}
