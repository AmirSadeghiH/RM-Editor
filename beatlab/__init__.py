"""BeatLab Django project package.

Exposes the Celery application so that ``celery -A beatlab worker`` and
Django management commands share one app instance.
"""
from beatlab.celery import app as celery_app

__all__ = ("celery_app",)
