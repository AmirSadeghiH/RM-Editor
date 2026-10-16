# BeatLab 🎛️

**BeatLab** is a browser-based music remix studio that opens inside Telegram as a
Mini App. Import audio files, arrange clips on a multitrack timeline, trim and
mix them, preview the arrangement in real time, and render a downloadable
remix on the server with FFmpeg.

* **Backend:** Django 5.2 + Django REST Framework + PostgreSQL + Redis + Celery + FFmpeg
* **Frontend:** HTML5 / CSS3 / vanilla ES modules + Canvas + Web Audio API + Telegram Mini Apps SDK
* **Bot:** aiogram 3
* **UI:** Persian (fa), RTL, mobile-first

---

## Feature overview

| Capability | Status in this MVP |
|---|---|
| Multitrack timeline (drag, trim, split, duplicate, move between tracks) | ✅ implemented |
| Clip volume, mute, fade-in / fade-out | ✅ implemented |
| Overlapping clips mixed together | ✅ implemented (server render + browser preview) |
| Crossfade between adjacent clips | ✅ implemented (real audio transitions) |
| Real-time preview (Web Audio API, synchronized) | ✅ implemented |
| Server-side render (FFmpeg) to MP3 / WAV with progress + download | ✅ implemented |
| Projects: create, save, reopen, delete (per-user, server-persisted) | ✅ implemented |
| Waveforms from real audio data (server-extracted peaks) | ✅ implemented |
| Undo / redo | ✅ implemented |
| Telegram login (signed `initData` verification) + dev-mode login | ✅ implemented |
| Beat replacement (acapella over a new instrumental) | ✅ works via ordinary mixing |
| AI stem separation (vocals/drums/bass/other) | 🚧 **not included** — documented extension point, UI marked «به‌زودی» |

There are **no fake features**: anything not implemented is explicitly marked
as unavailable in the UI and in this README.

---

## Quick start (Docker — recommended)

Prerequisites: Docker + Docker Compose, Git.

```bash
git clone <repo-url> RM-Editor && cd RM-Editor
cp .env.example .env          # defaults work for local development
docker compose up --build
```

That's it:

* Web app + Mini App UI → http://localhost:8000/
* Health check → http://localhost:8000/health/
* Admin (create with `docker compose exec web python manage.py createsuperuser`) → http://localhost:8000/admin/
* Export worker runs in the `worker` container (Celery + Redis).
* The `bot` container idles with setup instructions until you configure
  Telegram credentials — everything else works without them.

Stop with `Ctrl+C` or `docker compose down`. Data survives restarts in the
`pgdata` and `media` volumes.

### Cleanup of old exports

```bash
docker compose exec web python manage.py cleanup_exports --days 7
# preview first:
docker compose exec web python manage.py cleanup_exports --days 7 --dry-run
```

---

## Quick start (without Docker)

Prerequisites: Python 3.11+, FFmpeg + ffprobe on `PATH`, Node.js 18+ (frontend
tests only).

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# Zero-config local run (SQLite, synchronous export jobs, dev login):
export BEATLAB_CELERY_EAGER=1        # run renders inline instead of needing Redis
python manage.py migrate
python manage.py runserver
```

Open http://localhost:8000/ — see “Testing in a normal browser” below.

With PostgreSQL + Redis available, point `DB_URL` and `CELERY_BROKER_URL` at
them and drop `BEATLAB_CELERY_EAGER` (see `.env.example`).

---

## Testing in a normal browser (no Telegram needed)

1. Start the app (Docker or `runserver`).
2. Open http://localhost:8000/ in a desktop or mobile browser.
3. If no bot token is configured, the development login
   (`POST /api/auth/dev/`) is enabled automatically — the page shows
   **«ورود آزمایشی (حالت توسعه)»**. Click it (or just create a project and
   the app logs you in).
4. Create a project, upload audio (≤ 25 MiB, ≤ 10 min per file), drag clips,
   preview with ▶, then «خروجی» → «شروع رندر» → «دانلود خروجی».

Development auth is **hard-disabled in production**: settings refuse to start
if `DJANGO_ENV=production` is combined with `BEATLAB_DEV_AUTH=1`.

### Sample remix walkthrough

1. **پروژه جدید** → title e.g. «ریمیکس آزمایشی».
2. **افزودن فایل** → upload two short MP3/WAV files (e.g. an acapella and a beat).
   Each file appears in the tray and is placed on the timeline at the playhead.
3. Drag the second clip so it overlaps the first — the render will *mix* them.
   Or place the vocal on track 1 and the beat on track 2 starting at `0` —
   that's a beat replacement.
4. Select a clip (tap) → open properties (double-tap / Enter) → set volume and
   fades, or **«کراس‌فید با کلیپ بعدی»**.
5. Press ▶ (or Space) to preview the synchronized mix.
6. The title/time autosaves (status in the header). Reopen the project from
   the home page any time.
7. **خروجی** → pick MP3/WAV → **شروع رندر** → wait for «خروجی آماده دانلود است»
   → **دانلود خروجی**.

---

## Connecting a real Telegram bot

1. In Telegram, talk to **@BotFather** → `/newbot` → choose a name and username.
   Copy the **bot token**.
2. Deploy the app behind an **HTTPS** URL (Telegram requires HTTPS for Mini
   Apps). Set in `.env`:
   ```ini
   TELEGRAM_BOT_TOKEN=123456:ABC-your-token
   TELEGRAM_WEBAPP_URL=https://your-domain.example/
   DJANGO_ENV=production
   DJANGO_DEBUG=0            # required — see "Production deployment" below
   DJANGO_SECRET_KEY=<long random string>
   ALLOWED_HOSTS=your-domain.example
   CSRF_TRUSTED_ORIGINS=https://your-domain.example
   BEATLAB_DEV_AUTH=0        # dev login must stay disabled
   ```
3. `docker compose up --build` (or restart). The bot container starts long
   polling and answers `/start` and `/help` with a **«باز کردن استودیو»**
   keyboard button that opens the Mini App.
4. Optional: with BotFather `/setmenubutton`, point the menu button at the
   same `TELEGRAM_WEBAPP_URL`.
5. Open the bot in Telegram → tap the button → the Mini App logs you in via
   signed `initData` (verified server-side against the bot token; `auth_date`
   max age is `TELEGRAM_AUTH_MAX_AGE`, default 24 h).

Never commit real tokens. `.env.example` contains placeholders only.

---

## Production deployment

The production profile is *strict*: the application refuses to start rather
than running with unsafe defaults.

```ini
DJANGO_ENV=production
DJANGO_DEBUG=0                 # REQUIRED — startup fails with DEBUG=1
DJANGO_SECRET_KEY=<64+ random chars, not "change-me...">
ALLOWED_HOSTS=your-domain.example            # explicit hosts, no "*", no ".domain"
CSRF_TRUSTED_ORIGINS=https://your-domain.example
BEATLAB_DEV_AUTH=0             # dev login must stay disabled
```

Startup aborts with a `RuntimeError` when:

| Condition | Why it is fatal |
|---|---|
| `DJANGO_ENV=production` **and** `DJANGO_DEBUG=1` | debug mode leaks settings, stack traces and SQL |
| `DJANGO_ENV=production` without a strong `DJANGO_SECRET_KEY` | sessions would be forgeable |
| `DJANGO_ENV=production` with `ALLOWED_HOSTS` empty / `*` / `.domain` | host header poisoning |
| `DJANGO_ENV=production` with `BEATLAB_DEV_AUTH=1` | bypasses Telegram verification |

**Private media.** Uploaded audio and rendered exports live under
`BEATLAB_MEDIA_ROOT` and are *never* served as static files in production:
the development static-media route is hard-disabled whenever
`DJANGO_ENV=production` (and it is skipped whenever `DEBUG` is off). The
only supported download paths are the authenticated, owner-scoped API
endpoints — `GET /api/assets/<uuid>/audio/` and
`GET /api/exports/<uuid>/download/` — which return `404` for other users'
resources and set `Cache-Control: private, no-store`.

In development (`DJANGO_ENV=development`, `DJANGO_DEBUG=1`) the usual
convenience route `/media/<path>` is still served by Django so you can open
uploaded files directly while working locally.

---

## API surface (summary)

Session-cookie auth + CSRF (`X-CSRFToken`) for state-changing requests.
Errors always look like `{"error": {"code", "message", "details": []}}`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/auth/telegram/` | verify `init_data`, open a session |
| POST | `/api/auth/dev/` | dev-only mock login |
| POST | `/api/auth/logout/` | end session |
| GET | `/api/auth/config/` | which login flows are available |
| GET | `/api/me/` | current profile |
| GET/POST | `/api/projects/` | list / create projects |
| GET/PATCH/DELETE | `/api/projects/<uuid>/` | detail (incl. timeline + assets) / update title+timeline / delete |
| POST | `/api/projects/<uuid>/assets/` | upload audio (multipart `file`) |
| DELETE | `/api/assets/<uuid>/` | delete an unused asset |
| GET | `/api/assets/<uuid>/audio/` | stream asset audio (owner only) |
| POST | `/api/projects/<uuid>/exports/` | enqueue a render job |
| GET | `/api/exports/<uuid>/` | job status / progress |
| GET | `/api/exports/<uuid>/download/` | download completed render |
| GET | `/health/` | liveness |

Validation rules (timeline schema v1, asset ownership, trim/fade bounds,
limits) are documented in `studio/timeline.py` and `ARCHITECTURE.md`.

### Configurable limits (env vars)

| Variable | Default |
|---|---|
| `BEATLAB_MAX_UPLOAD_BYTES` | 25 MiB |
| `BEATLAB_MAX_SOURCE_DURATION` | 600 s |
| `BEATLAB_MAX_ASSETS_PER_PROJECT` | 8 |
| `BEATLAB_MAX_CLIPS_PER_PROJECT` | 30 |
| `BEATLAB_MAX_TRACKS` | 8 |
| `BEATLAB_MAX_EXPORT_DURATION` | 600 s |

---

## Running the tests

Backend (153 tests — API, auth, validation, production configuration guards,
renderer unit + real FFmpeg integration). FFmpeg-dependent tests are skipped
automatically with a reason if the binaries are missing.

```bash
. .venv/bin/activate
python -m pytest tests/
```

Frontend timeline-state tests (20 tests, plain `node --test`):

```bash
node --test "tests/frontend/*.test.mjs"     # or: npm test
```

What the integration tests actually verify (with synthetic sine-wave fixtures,
no copyrighted audio): single-clip render, trim ranges, delayed clips with
silent gaps, concatenation, overlapping-track mixing (overlap region is
louder than either solo), crossfades (continuous energy through the overlap),
fade envelopes, output duration within tolerance, and that outputs contain
real samples and are decodable.

---

## Project structure

```
beatlab/            Django project (settings, urls, celery app)
accounts/           Telegram user model + initData verification + auth API
studio/             Project / AudioAsset / ExportJob models, timeline schema
                    validation, REST API, Celery render task, cleanup command
renderer/           FFmpeg layer: probe, waveform peaks, filter-graph builder,
                    bounded subprocess runner
ai_tools/           Provider interface for future AI tools (stem separation)
bot/                aiogram bot (python -m bot)
templates/          Mini App pages (home + editor), RTL Persian
static/             css/app.css + js/ (api, auth, state, audio-engine,
                    waveform, timeline-ui, tg, home, editor)
tests/              pytest suite + node --test frontend suite
docs/               AI extension guide
Dockerfile, docker-compose.yml, .env.example
```

More detail in [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## Security notes

* Telegram auth verifies the HMAC-SHA256 signature of `initData` with the bot
  token and rejects stale `auth_date`. `initDataUnsafe` is never trusted.
* All project/asset/export queries are scoped to the authenticated user.
* Uploads are validated with **ffprobe** (real container/codec checks), size
  and duration limited, and stored under generated UUID paths — original
  filenames are display-only.
* FFmpeg runs as `shell=False` argument lists; filter parameters are formatted
  from validated floats. No client data ever reaches a shell.
* Exports are private (authenticated download, `no-store`), rate-limited, and
  deduplicated while a job is active.
* Production settings require a real `SECRET_KEY`, explicit `ALLOWED_HOSTS`,
  secure cookies, HSTS; there is no fallback production secret.
* Production startup fails loudly when `DEBUG` is enabled; uploaded media and
  rendered exports are never exposed through Django's static-media route in
  production (see "Production deployment").

**Copyright notice:** only upload audio you have the rights to process.
BeatLab does not download music from streaming links.

---

## Known limitations (honest list)

* Browser preview decodes assets fully into memory (fine for the enforced
  limits, heavy for many full-length tracks on low-RAM phones).
* Preview fidelity can differ subtly from the FFmpeg render (resampling /
  codec behavior); the render is authoritative.
* Export progress is derived from FFmpeg's `-progress pipe:1` events (`out_time`)
  and updates in ~5% steps; 100% is only reported once the rendered file has
  been probed and validated.
* Rate limiting uses DRF throttling (per-process cache) — adequate for a
  single-instance MVP, not a distributed guard.
* No realtime collaboration, no MIDI, no plugin effects (reverb/EQ) yet.
* AI stem separation is not included; see `docs/AI_EXTENSIONS.md`.
* Automated *browser* E2E (Playwright etc.) is not included in this repo;
  the manual flow above plus the API/integration tests cover the same path.

## License / rights

Code provided as-is for this MVP. You are responsible for the rights to any
audio you upload.
