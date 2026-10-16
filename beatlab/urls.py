"""Root URL configuration for BeatLab."""
from __future__ import annotations

from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from accounts.views import me as me_view
from studio.views import editor_page, health_check, home_page

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

if settings.DEBUG:  # pragma: no cover - dev convenience only
    from django.conf.urls.static import static

    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
