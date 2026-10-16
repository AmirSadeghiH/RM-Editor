#!/bin/sh
# BeatLab container entrypoint: applies migrations reliably (the web role
# only), then hands over to the requested process. No fixed sleep loops —
# PostgreSQL is awaited by Docker Compose healthchecks and Django retries
# the initial connection through CONN_MAX_AGE reconnects.
set -e

role="${1:-web}"

wait_for_db() {
    echo "Waiting for database..."
    python - <<'PY'
import os, sys, time
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "beatlab.settings")
django.setup()
from django.db import connections
from django.db.utils import OperationalError

for attempt in range(60):
    try:
        connections["default"].ensure_connection()
        print("Database is available.")
        sys.exit(0)
    except OperationalError:
        time.sleep(1)
print("Database did not become available in time.", file=sys.stderr)
sys.exit(1)
PY
}

case "$role" in
    web)
        wait_for_db
        python manage.py migrate --noinput
        python manage.py collectstatic --noinput
        exec gunicorn beatlab.wsgi:application \
            --bind 0.0.0.0:8000 \
            --workers "${WEB_CONCURRENCY:-2}" \
            --timeout 120 \
            --access-logfile - \
            --error-logfile -
        ;;
    worker)
        wait_for_db
        exec celery -A beatlab worker --loglevel=INFO --concurrency="${WORKER_CONCURRENCY:-1}"
        ;;
    bot)
        exec python -m bot
        ;;
    manage)
        shift
        exec python manage.py "$@"
        ;;
    *)
        exec "$@"
        ;;
esac
