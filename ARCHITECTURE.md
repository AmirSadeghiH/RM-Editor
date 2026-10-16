# BeatLab Architecture

## 1. High-level view

```
Telegram (Mini App WebView / any browser)
   │  HTTPS: pages + REST + session cookie + CSRF
   ▼
Django (gunicorn) ──────────────► PostgreSQL   (users, projects, assets, jobs)
   │  │
   │  └─ static: HTML/CSS/JS Mini App (same origin — required for cookies)
   │
   ├─ enqueue render job (transaction.on_commit)
   ▼
Redis  ──►  Celery worker  ──►  FFmpeg/ffprobe  ──►  media storage (private)
   ▲              │
   └── job state ─┘   (status/progress polled by the client)

aiogram bot (separate process): /start, /help, Mini App launcher button.
```

One Django image serves three roles (`web`, `worker`, `bot`) via the Compose
entrypoint. The bot and worker are independent processes/containers; the web
app works even when Telegram is unconfigured.

## 2. Timeline document (schema v1)

Saved as `Project.timeline` (JSONField) and re-validated on every save and
export. Time unit: floating-point seconds.

```jsonc
{
  "schema_version": 1,
  "settings": { "master_volume": 1.0 },
  "tracks": [
    {
      "id": "trk_1", "name": "ترک ۱", "order": 0,
      "volume": 1.0, "muted": false,
      "clips": [
        {
          "id": "clp_1", "asset_id": "<uuid>",
          "start": 0.0,            // timeline position
          "trim_in": 0.0,          // source in-point
          "trim_out": 5.0,         // source out-point (exclusive)
          "volume": 1.0, "muted": false,
          "fade_in": 0.0, "fade_out": 0.0
        }
      ]
    }
  ]
}
```

Rules enforced server-side (`studio/timeline.py`):

* `schema_version` must be 1; clip/track ids must match `[A-Za-z0-9_-]{1,64}` and be unique.
* `asset_id` must reference a **ready asset owned by the project owner**; trims
  are checked against the *probed* asset duration (±50 ms tolerance).
* `0 ≤ start`, `trim_out > trim_in`, clip end ≤ `max_export_duration`.
* `volume ∈ [0, 4]`, `fade_* ≥ 0`, `fade_in + fade_out ≤ clip length`.
* Track count ≤ 8, clip count ≤ 30 (configurable).
* Clip duration is derived (`trim_out − trim_in`); a clip occupies
  `[start, start + duration)`.

The same schema is modeled in `static/js/state.js` for the editor, including
operations (move/trim/split/duplicate/crossfade) and undo/redo snapshots.

## 3. Audio rendering (renderer/)

* `ffprobe.py` — probe uploads: allowlisted containers/codecs, duration/size
  limits, structured `AudioInfo`.
* `peaks.py` — decode to mono 8 kHz PCM via FFmpeg, reduce to min/max buckets,
  stored as JSON on the asset (waveforms without shipping the file).
* `filters.py` — **pure** graph builder (unit-tested without subprocesses):

  ```
  [i:a] atrim → asetpts → aformat(48k stereo fltp) → volume
      → afade in/out → adelay(timeline offset) → apad
  …
  amix=inputs=N:duration=longest:normalize=0   ← overlaps SUM
  alimiter=limit=0.97:level=0                  ← safety ceiling
  apad → atrim=0:total → asetpts → [out]
  ```

  Crossfade = overlap + `afade` envelopes on both clips (real transitions in
  the rendered audio, not cosmetics). All numeric parameters are printed from
  validated floats with fixed precision; ids never enter the graph.

* `render.py` — runs the plan with `subshell=False`, `-progress pipe:1`
  progress reporting, hard timeout, partial-output cleanup, and a final probe
  (decodable + duration within tolerance) before accepting a file.

## 4. Export lifecycle (studio/tasks.py)

1. `POST /api/projects/<id>/exports/` — ownership, settings validation,
   full timeline re-validation, duplicate-job guard (409 with existing job).
2. `ExportJob(queued)` created; task enqueued after commit (eager inline in
   dev without Redis).
3. `render_export_task` — statuses `queued → processing → completed | failed`,
   progress 5→100%, safe error messages only (no paths/tracebacks).
4. Client polls `GET /api/exports/<uuid>/`, downloads via the authenticated
   `…/download/` endpoint (`private, no-store`).
5. `manage.py cleanup_exports` removes expired outputs/failed jobs/temp files.

Deterministic failures (invalid timeline/settings) never retry; transient OS
errors retry at most twice; Celery `soft_time_limit` bounds workers.

## 5. Authentication

* `POST /api/auth/telegram/` verifies `Telegram.WebApp.initData` with the
  official HMAC-SHA256 algorithm (`secret = HMAC("WebAppData", bot_token)`),
  constant-time compare, `auth_date` freshness window, and the `user` JSON.
  Verified identity → Django session login. `initDataUnsafe` is never trusted.
* Dev login (`/api/auth/dev/`) auto-enabled only when not production and no
  bot token; settings **raise** if enabled with `DJANGO_ENV=production`.
* DRF `SessionAuthentication` enforces CSRF on unsafe methods; the SPA reads
  the `csrftoken` cookie and sends `X-CSRFToken`.
* Every queryset is scoped `owner=request.user`; 404 (not 403) for foreign
  resources to avoid leaking existence.

## 6. Frontend (static/js)

| Module | Responsibility |
|---|---|
| `api.js` | REST client, CSRF, XHR upload progress |
| `auth.js` / `tg.js` | login flow, Telegram WebApp glue, popups, haptics |
| `state.js` | timeline document ops + History (pure, node-tested) |
| `audio-engine.js` | Web Audio preview: `AudioBufferSourceNode` + `GainNode` per clip, one AudioContext clock, fade automation, playback rate |
| `waveform.js` | canvas peaks rendering (server peaks or local fallback) |
| `timeline-ui.js` | ruler canvas, lanes/clips DOM, Pointer Events (drag/trim/seek/zoom), playhead |
| `home.js` / `editor.js` | page controllers: projects, transport, sheets, autosave, export polling |

The page is RTL Persian; the timeline scroller is LTR because time flows left
to right. Waveforms and ruler are Canvas; clips are DOM with canvas children.

## 7. Data model

* `accounts.TelegramUser` — custom user; `telegram_id` unique/nullable (dev
  accounts have none).
* `studio.Project` — UUID pk, owner FK, title, `timeline` JSON, `schema_version`, timestamps.
* `studio.AudioAsset` — UUID pk, owner + project FKs, generated storage path
  (`assets/<project>/<asset>.<ext>`), display name, probed metadata,
  processing status, waveform JSON.
* `studio.ExportJob` — UUID pk, owner + project FKs, settings JSON, status,
  progress, output file/size/duration/format, safe error, timestamps.

Deletes remove managed files (project delete cascades rows + best-effort file
cleanup).

## 8. Deployment / ops

* `Dockerfile` — python:3.12-slim + distro ffmpeg; non-root user.
* `docker-compose.yml` — `db` (Postgres 16, volume + healthcheck), `redis`,
  `web` (gunicorn, migrations + collectstatic in entrypoint), `worker`
  (Celery), `bot` (optional). Persistent `pgdata`/`media` volumes; health
  checks gate dependents instead of sleep scripts.
* Settings: env-driven (`settings.py`); production hardening (secret key
  required, explicit hosts, secure cookies, HSTS, SSL redirect).
* Logging: single console formatter, error-level detail for render failures
  (FFmpeg stderr tail) without secrets.

## 9. Deliberate deviations from the prompt

* **SQLite fallback** for development/tests when no `DB_URL`/`POSTGRES_*` is
  set — lets the suite and browser dev run without a database server. The
  canonical deployment (Compose) always uses PostgreSQL.
* **`BEATLAB_CELERY_EAGER`** synchronous fallback for environments without
  Redis (used by the sandbox verification here). Never enable in production.
* **Browser E2E automation** not shipped (no Playwright in the pipeline); the
  same flow is covered by API + FFmpeg integration tests and the documented
  manual walkthrough.
* The “production + dev-auth” guard is tested by re-executing the settings
  module rather than booting a second Django instance.
