"""Root URL configuration for BeatLab."""
from __future__ import annotations

from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from accounts.views import me as me_view
from studio.views import editor_page, health_check, home_page


def media_urlpatterns() -> list:
    """URL patterns that expose ``MEDIA_ROOT`` directly.

    Uploaded audio and rendered exports are private user data: the only
    supported download paths are the authenticated API endpoints
    (``/api/assets/<uuid>/audio/`` and ``/api/exports/<uuid>/download/``),
    which are scoped to the resource owner.

    Django's static-file helper is a *development* convenience only. It is
    skipped when ``DEBUG`` is off and hard-disabled in production, so a
    misconfigured deployment can never turn ``/media/`` into an anonymous
    file server for other users' uploads and exports.
    """
    if settings.IS_PRODUCTION or not settings.DEBUG:
        return []

    from django.conf.urls.static import static

    return static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health_check, name="health"),
    path("api/auth/", include("accounts.urls")),
    path("api/me/", me_view, name="api-me"),
    path("api/", include("studio.urls")),
]

# The Mini App pages (project home + editor) are served by Django itself so
# the app runs on a single origin — required for Telegram session cookies.
urlpatterns += [
    path("", home_page, name="webapp-home"),
    path("editor/", editor_page, name="webapp-editor"),
]

# Development convenience only — never exposes private media in production.
urlpatterns += media_urlpatterns()
