"""Django settings for the BeatLab project.

Configuration is driven by environment variables (see ``.env.example``).
Development may fall back to SQLite and insecure defaults; production is
strict: missing secrets or wildcard hosts abort startup.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse, unquote

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


# ---------------------------------------------------------------------------
# Environment / safety mode
# ---------------------------------------------------------------------------
DJANGO_ENV = os.environ.get("DJANGO_ENV", "development").strip().lower()
IS_PRODUCTION = DJANGO_ENV == "production"

DEBUG = env_bool("DJANGO_DEBUG", default=not IS_PRODUCTION)

# Production must never run with DEBUG enabled. DEBUG=True leaks settings,
# stack traces and SQL, disables the security middleware defaults, and turns
# Django's static-file helper into a public file server. Refuse to start
# instead of silently serving a debug build in production.
if IS_PRODUCTION and DEBUG:
    raise RuntimeError(
        "DJANGO_DEBUG must be 0 (disabled) when DJANGO_ENV=production. "
        "DEBUG=True is never allowed in production."
    )

# Production must supply a real secret key; there is no insecure fallback.
_secret_from_env = os.environ.get("DJANGO_SECRET_KEY", "").strip()
if IS_PRODUCTION:
    if not _secret_from_env:
        raise RuntimeError("DJANGO_SECRET_KEY must be set in production.")
    if _secret_from_env.startswith("change-me") or len(_secret_from_env) < 32:
        raise RuntimeError("DJANGO_SECRET_KEY must be a strong random value.")
    SECRET_KEY = _secret_from_env
else:
    SECRET_KEY = _secret_from_env or "insecure-dev-only-secret-key-do-not-use-in-production"

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",")
    if h.strip()
]
if IS_PRODUCTION:
    if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS or any(h.startswith(".") for h in ALLOWED_HOSTS):
        raise RuntimeError("ALLOWED_HOSTS must list explicit hostnames in production.")

CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
    if o.strip()
]

# ---------------------------------------------------------------------------
# Application definition
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "accounts",
    "studio",
    "renderer",
    "ai_tools",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "beatlab.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "beatlab.wsgi.application"
ASGI_APPLICATION = "beatlab.asgi.application"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
# Priority:
#   1. DB_URL (postgresql://... or sqlite:///...)
#   2. POSTGRES_* variables
#   3. SQLite file in the repository (development / tests convenience)
_db_url = os.environ.get("DB_URL", "").strip()
if _db_url:
    _parsed = urlparse(_db_url)
    if _parsed.scheme in {"postgres", "postgresql"}:
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": unquote(_parsed.path.lstrip("/")),
                "USER": unquote(_parsed.username or ""),
                "PASSWORD": unquote(_parsed.password or ""),
                "HOST": _parsed.hostname or "localhost",
                "PORT": str(_parsed.port or 5432),
                "CONN_MAX_AGE": 60,
            }
        }
    elif _parsed.scheme == "sqlite":
        _sqlite_path = unquote(_parsed.path) or str(BASE_DIR / "db.sqlite3")
        if _sqlite_path in {"", "/"}:
            _sqlite_path = str(BASE_DIR / "db.sqlite3")
        DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": _sqlite_path}}
    else:
        raise RuntimeError(f"Unsupported DB_URL scheme: {_parsed.scheme!r}")
elif os.environ.get("POSTGRES_HOST"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_NAME", "beatlab"),
            "USER": os.environ.get("POSTGRES_USER", "beatlab"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "beatlab"),
            "HOST": os.environ["POSTGRES_HOST"],
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": 60,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.TelegramUser"

# ---------------------------------------------------------------------------
# Password validation (kept for Django admin; Telegram users have no password)
# ---------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "fa-ir"
TIME_ZONE = os.environ.get("TZ", "UTC")
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static files (frontend assets are served by Django, same origin)
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Uploaded audio and rendered exports. Never served directly by URL in
# production: downloads go through the authenticated API endpoints
# (``/api/assets/<id>/audio/`` and ``/api/exports/<id>/download/``), both of
# which are scoped to the resource owner. The development static-media route
# in ``beatlab/urls.py`` is hard-disabled when ``DJANGO_ENV=production``.
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.environ.get("BEATLAB_MEDIA_ROOT", BASE_DIR / "media"))

# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "EXCEPTION_HANDLER": "studio.api_errors.beatlab_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "auth": "30/minute",
        "upload": "30/hour",
        "export": "12/hour",
        "default": "600/minute",
    },
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_WEBAPP_URL = os.environ.get("TELEGRAM_WEBAPP_URL", "").strip()
TELEGRAM_AUTH_MAX_AGE = env_int("TELEGRAM_AUTH_MAX_AGE", 86400)

# Development-only mock login (POST /api/auth/dev/). Automatically available
# for local work when no bot token is configured, but *hard-refused* whenever
# the app runs in production mode.
_allow_dev_default = (not IS_PRODUCTION) and not TELEGRAM_BOT_TOKEN
ALLOW_DEV_AUTH = env_bool("BEATLAB_DEV_AUTH", default=_allow_dev_default)
if IS_PRODUCTION and ALLOW_DEV_AUTH:
    raise RuntimeError(
        "BEATLAB_DEV_AUTH must be disabled when DJANGO_ENV=production. "
        "Development auth bypasses Telegram verification and is never safe in production."
    )

# ---------------------------------------------------------------------------
# Celery
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = env_int("BEATLAB_RENDER_TIMEOUT", 600) + 120
CELERY_TASK_SOFT_TIME_LIMIT = env_int("BEATLAB_RENDER_TIMEOUT", 600)
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_ACKS_LATE = True

# Synchronous fallback for environments without Redis (local sandbox/tests).
BEATLAB_CELERY_EAGER = env_bool("BEATLAB_CELERY_EAGER", default=False)
if BEATLAB_CELERY_EAGER:
    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_TASK_EAGER_PROPAGATES = True

# ---------------------------------------------------------------------------
# Audio pipeline
# ---------------------------------------------------------------------------
# Locate ffmpeg/ffprobe: explicit env wins, then PATH. Docker images install
# the distribution packages; local sandboxes may point these at static builds.
FFMPEG_BINARY = os.environ.get("FFMPEG_BINARY", "ffmpeg")
FFPROBE_BINARY = os.environ.get("FFPROBE_BINARY", "ffprobe")
RENDER_TIMEOUT_SECONDS = env_int("BEATLAB_RENDER_TIMEOUT", 600)
RENDER_SAMPLE_RATE = 48000

BEATLAB_LIMITS = {
    "max_upload_bytes": env_int("BEATLAB_MAX_UPLOAD_BYTES", 25 * 1024 * 1024),
    "max_source_duration": float(env_int("BEATLAB_MAX_SOURCE_DURATION", 600)),
    "max_assets_per_project": env_int("BEATLAB_MAX_ASSETS_PER_PROJECT", 8),
    "max_clips_per_project": env_int("BEATLAB_MAX_CLIPS_PER_PROJECT", 30),
    "max_tracks": env_int("BEATLAB_MAX_TRACKS", 8),
    "max_export_duration": float(env_int("BEATLAB_MAX_EXPORT_DURATION", 600)),
}

# ---------------------------------------------------------------------------
# Logging — structured enough to diagnose errors without leaking secrets
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "beatlab": {
            "format": "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "beatlab",
        }
    },
    "root": {
        "handlers": ["console"],
        "level": os.environ.get("LOG_LEVEL", "INFO"),
    },
    "loggers": {
        "django.server": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}

# ---------------------------------------------------------------------------
# Production security posture
# ---------------------------------------------------------------------------
if IS_PRODUCTION:
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = env_int("DJANGO_SECURE_HSTS_SECONDS", 3600)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = False

X_FRAME_OPTIONS = "SAMEORIGIN"
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False  # frontend reads the cookie to send X-CSRFToken
