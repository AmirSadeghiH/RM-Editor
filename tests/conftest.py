"""Shared pytest fixtures.

Environment is pinned here *before* Django loads settings:
  * SQLite is used for tests so the suite runs without a PostgreSQL server
    (the Docker deployment still uses PostgreSQL — same ORM code paths).
  * Development auth is enabled so API tests can log users in.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

# Must happen before pytest-django imports settings.
os.environ.setdefault("DJANGO_ENV", "development")
os.environ.setdefault("DJANGO_DEBUG", "0")
os.environ.setdefault("BEATLAB_DEV_AUTH", "1")
os.environ.setdefault(
    "DB_URL", f"sqlite:///{tempfile.mkdtemp(prefix='beatlab-test-')}/test.sqlite3"
)
os.environ.setdefault("BEATLAB_MEDIA_ROOT", tempfile.mkdtemp(prefix="beatlab-media-"))

import pytest  # noqa: E402
from django.conf import settings  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from accounts.models import TelegramUser  # noqa: E402


def ffmpeg_available() -> bool:
    return shutil.which(settings.FFMPEG_BINARY) is not None and (
        shutil.which(settings.FFPROBE_BINARY) is not None
    )


requires_ffmpeg = pytest.mark.skipif(
    not ffmpeg_available(),
    reason="ffmpeg/ffprobe not available in this environment",
)


@pytest.fixture
def user(db) -> TelegramUser:
    return TelegramUser.objects.create_user(username="alice", first_name="Alice")


@pytest.fixture
def other_user(db) -> TelegramUser:
    return TelegramUser.objects.create_user(username="mallory", first_name="Mallory")


@pytest.fixture
def auth_client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def other_client(other_user) -> APIClient:
    client = APIClient()
    client.force_authenticate(other_user)
    return client


@pytest.fixture
def project(auth_client) -> dict:
    response = auth_client.post("/api/projects/", {"title": "Test project"}, format="json")
    assert response.status_code == 201, response.content
    return response.json()


@pytest.fixture
def wav_file_factory():
    """Generate short synthetic WAV files with FFmpeg (sine tones)."""
    created: list[Path] = []

    def factory(duration: float = 1.0, frequency: int = 440, name: str = "tone.wav") -> Path:
        tmp_dir = Path(tempfile.mkdtemp(prefix="beatlab-fixtures-"))
        path = tmp_dir / name
        cmd = [
            settings.FFMPEG_BINARY,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:duration={duration}",
            "-ac",
            "2",
            "-ar",
            "44100",
            str(path),
        ]
        subprocess.run(cmd, check=True, shell=False, timeout=60)
        created.append(path)
        return path

    yield factory

    for path in created:
        shutil.rmtree(path.parent, ignore_errors=True)
