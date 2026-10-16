# Adding real AI audio tools later

The MVP deliberately does **not** ship AI stem separation. The UI shows these
features as «به‌زودی» — no fake progress bars, no simulated output. What *is*
shipped is the seam they plug into.

## What works today without AI

**Beat replacement.** Users who already have an acapella/vocal file put it on
one track and a new instrumental on another; the mixer sums them with volume,
fades and crossfades. That is real, tested audio mixing — not AI.

## The provider interface (`ai_tools/providers.py`)

```python
class StemSeparationProvider(ABC):
    name: str

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def separate(self, source_path: Path, output_dir: Path) -> StemResult: ...
```

`StemResult` carries real output files (vocals / drums / bass / other). The
default `UnavailableProvider` raises `StemSeparationUnavailable` — callers can
never mistake “not implemented” for success.

Register a real provider via env var (import path):

```ini
BEATLAB_STEM_PROVIDER=myapp.separation.DemucsProvider
```

## Suggested integration path (e.g. Demucs)

1. **Model + license.** Demucs (MIT) or similar; check the model weights'
   license and the music-rights implications for your users' uploads. Do not
   bundle weights in the Docker image — mount them or download at first run.
2. **Resources.** CPU-only Demucs separates a 4-minute track in several
   minutes; GPU (CUDA) is much faster. Plan a dedicated worker queue with its
   own concurrency (1 job per GPU) — reuse the existing Celery app with a
   named queue (`ai`) and route the task with `task_routes`.
3. **Pipeline.** New model e.g. `AIJob` mirroring `ExportJob`
   (queued/processing/completed/failed + progress + safe errors). Task flow:
   authorized asset → temp dir → `provider.separate()` → probe each stem with
   `renderer.ffprobe` → store as new `AudioAsset` rows (`kind="stem"`) →
   user drags them onto the timeline like any other clip.
4. **Limits.** Source duration cap, one active AI job per user, hard
   `time_limit`, temp cleanup in `finally` — same patterns as
   `studio/tasks.py`.
5. **API/UI.** `POST /api/projects/<id>/ai/separate/` + status polling (copy
   the export panel pattern). Keep Persian status strings honest.
6. **Honesty.** Only mark the UI feature active when a provider reports
   `is_available()`. Keep «به‌زودی» otherwise.

## Other planned intelligence (same seam)

* Tempo / key detection → metadata on assets (`AudioAnalysis` table),
  suggested grid snap in the timeline.
* Beat alignment / auto-arrangement → timeline transforms in `state.js` or
  server-side suggestions applied explicitly by the user.
* Remix suggestions → proposal objects the user previews and accepts.

None of these require changing the timeline schema (v1) — extensions should
add metadata side-tables, not overload the clip document.
