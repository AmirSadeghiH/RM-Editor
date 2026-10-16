from django.urls import path

from accounts import views

urlpatterns = [
    path("telegram/", views.telegram_login, name="auth-telegram"),
    path("dev/", views.dev_login, name="auth-dev"),
    path("logout/", views.logout_view, name="auth-logout"),
    path("config/", views.auth_config, name="auth-config"),
]
