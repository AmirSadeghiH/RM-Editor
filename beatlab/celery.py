"""Celery application for BeatLab.

Importing this package guarantees the Celery app is registered whenever
Django starts (see ``beatlab/__init__.py``).
"""
from __future__ import annotations

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "beatlab.settings")

app = Celery("beatlab")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
