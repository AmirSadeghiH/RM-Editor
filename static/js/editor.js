/** Main audio editor page controller. */

import { api, ApiError } from "./api.js";
import { ensureAuth, devLogin } from "./auth.js";
import { confirmAction, haptic, initTelegram } from "./tg.js";
import {
  History,
  addClip,
  clipContentDuration,
  clipEnd,
  countClips,
  createEmptyTimeline,
  crossfadeWithNext,
  deleteClip as deleteClipOp,
  duplicateClip,
  findClip,
  formatTime,
  moveClip,
  projectDuration,
  removeTrack,
  setClipFades,
  setClipVolume,
  splitClip,
  toPersianDigits,
  toggleClipMute,
  validateTimelineLight,
} from "./state.js";
import { PreviewEngine } from "./audio-engine.js";
import { TimelineView } from "./timeline-ui.js";

// ---------------------------------------------------------------------------
// Elements
// ---------------------------------------------------------------------------
const $ = (id) => document.getElementById(id);
const els = {
  title: $("project-title"),
  saveText: $("save-text"),
  saveDot: document.querySelector("#save-status .status-dot"),
  btnBack: $("btn-back"),
  btnUndo: $("btn-undo"),
  btnRedo: $("btn-redo"),
  btnExport: $("btn-export"),
  btnPlay: $("btn-play"),
  iconPlay: $("icon-play"),
  iconPause: $("icon-pause"),
  btnStop: $("btn-stop"),
  timeCurrent: $("time-current"),
  timeTotal: $("time-total"),
  speed: $("speed-select"),
  btnZoomIn: $("btn-zoom-in"),
  btnZoomOut: $("btn-zoom-out"),
  btnZoomFit: $("btn-zoom-fit"),
  btnAi: $("btn-ai"),
  btnAddAudio: $("btn-add-audio"),
  fileInput: $("file-input"),
  editorLoading: $("editor-loading"),
  assetTray: $("asset-tray"),
  trayEmpty: $("tray-empty"),
  assetCount: $("asset-count"),
  uploadStatus: $("upload-status"),
  uploadStatusText: $("upload-status-text"),
  uploadProgress: $("upload-progress"),
  clipSheet: $("clip-sheet"),
  clipVolume: $("clip-volume"),
  clipVolumeLabel: $("clip-volume-label"),
  clipFadeIn: $("clip-fade-in"),
  clipFadeInLabel: $("clip-fade-in-label"),
  clipFadeOut: $("clip-fade-out"),
  clipFadeOutLabel: $("clip-fade-out-label"),
  clipTiming: $("clip-timing"),
  clipSplit: $("clip-split"),
  clipDuplicate: $("clip-duplicate"),
  clipCrossfade: $("clip-crossfade"),
  clipMute: $("clip-mute"),
  clipDelete: $("clip-delete"),
  exportSheet: $("export-sheet"),
  exportFormat: $("export-format"),
  exportBitrate: $("export-bitrate"),
  exportDepth: $("export-depth"),
  exportBitrateRow: $("export-bitrate-row"),
  exportDepthRow: $("export-depth-row"),
  exportProgressBox: $("export-progress-box"),
  exportStatusDot: $("export-status-dot"),
  exportStatusText: $("export-status-text"),
  exportProgress: $("export-progress"),
  btnStartExport: $("btn-start-export"),
  btnDownload: $("btn-download"),
  btnExportClose: $("btn-export-close"),
  aiSheet: $("ai-sheet"),
  btnAiClose: $("btn-ai-close"),
};

// ---------------------------------------------------------------------------
// App state
// ---------------------------------------------------------------------------
const projectId = new URLSearchParams(window.location.search).get("p");
const engine = new PreviewEngine();
let history = new History(createEmptyTimeline());
let assets = {}; // assetId -> asset payload
let dirty = false;
let saving = false;
let saveTimer = null;
let pollTimer = null;
let activeExportId = null;
let selectedClipId = null;

const view = new TimelineView(
  {
    scroll: $("timeline-scroll"),
    inner: $("timeline-inner"),
    ruler: $("ruler"),
    lanes: $("lanes"),
    playhead: $("playhead"),
    trackHeads: $("track-heads"),
  },
  {
    getTimeline: () => history.current,
    getAssets: () => assets,
    engine,
    callbacks: {
      onSelect: (clipId) => {
        selectedClipId = clipId;
      },
      onGestureBegin: () => {
        gestureBefore = structuredClone(history.current);
      },
      onGestureCommit: () => {
        if (gestureBefore) {
          history.commit(gestureBefore);
          gestureBefore = null;
          markDirty();
          updateUndoButtons();
        }
      },
      onTimelineChanged: () => {
        markDirty();
        updateTimeDisplay();
      },
      onSeek: (time) => {
        if (engine.playing) {
          engine.seek(history.current, time);
        }
      },
      onOpenClipSheet: (clipId) => openClipSheet(clipId),
      onDeleteClip: (clipId) => removeClip(clipId),
      onDeleteTrack: (trackId) => removeTrackById(trackId),
    },
  },
);
let gestureBefore = null;

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------
function toast(message, type = "") {
  const wrap = $("toast-wrap");
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = message;
  wrap.appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

function setSaveStatus(state) {
  // state: saved | saving | dirty | error
  const map = {
    saved: ["ok", "ذخیره‌شده"],
    saving: ["busy", "در حال ذخیره..."],
    dirty: ["busy", "تغییرات ذخیره‌نشده"],
    error: ["err", "ذخیره ناموفق؛ دوباره تلاش کنید"],
  };
  const [dotClass, text] = map[state] || map.saved;
  els.saveDot.className = `status-dot ${dotClass}`;
  els.saveText.textContent = text;
}

function markDirty() {
  dirty = true;
  setSaveStatus("dirty");
  scheduleSave();
}

function scheduleSave() {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(saveProject, 1500);
}

async function saveProject() {
  if (saving) {
    scheduleSave();
    return;
  }
  const problems = validateTimelineLight(history.current);
  if (problems.length) {
    setSaveStatus("error");
    toast(problems[0], "error");
    return;
  }
  saving = true;
  setSaveStatus("saving");
  try {
    await api.patchProject(projectId, {
      title: els.title.value.trim() || "پروژه بدون عنوان",
      timeline: history.current,
    });
    dirty = false;
    setSaveStatus("saved");
  } catch (err) {
    setSaveStatus("error");
    if (err instanceof ApiError && err.details && err.details.length) {
      toast(`ذخیره ناموفق: ${err.details[0]}`, "error");
    } else {
      toast(err.message || "ذخیره ناموفق بود.", "error");
    }
  } finally {
    saving = false;
    if (dirty) scheduleSave();
  }
}

function updateUndoButtons() {
  els.btnUndo.disabled = !history.canUndo;
  els.btnRedo.disabled = !history.canRedo;
}

function updateTimeDisplay() {
  els.timeCurrent.textContent = formatTime(engine.currentTime);
  els.timeTotal.textContent = formatTime(projectDuration(history.current));
}

function runMutation(mutator) {
  history.run(mutator);
  markDirty();
  updateUndoButtons();
  updateTimeDisplay();
  view.render();
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
async function boot() {
  initTelegram();
  els.editorLoading.classList.remove("hidden");

  const auth = await ensureAuth();
  if (!auth.user) {
    if (auth.devEnabled) {
      try {
        await devLogin("devuser");
      } catch (err) {
        toast(err.message || "ورود ناموفق بود.", "error");
      }
    } else {
      els.editorLoading.classList.add("hidden");
      toast("ابتدا وارد تلگرام شوید یا حالت توسعه را فعال کنید.", "error");
      return;
    }
  }

  if (!projectId) {
    window.location.href = "/";
    return;
  }

  try {
    const project = await api.getProject(projectId);
    els.title.value = project.title;
    history.reset(project.timeline && project.timeline.tracks ? project.timeline : createEmptyTimeline());
    assets = {};
    for (const asset of project.assets || []) {
      assets[asset.id] = asset;
    }
    renderTray();
    view.render();
    view.zoomToFit();
    updateTimeDisplay();
    updateUndoButtons();
    setSaveStatus("saved");
    preloadAudio();
  } catch (err) {
    toast(err.message || "بارگذاری پروژه ناموفق بود.", "error");
  } finally {
    els.editorLoading.classList.add("hidden");
  }
}

async function preloadAudio() {
  // Decode assets in the background so preview starts instantly.
  for (const asset of Object.values(assets)) {
    try {
      await engine.loadAsset(asset.id, api.assetAudioUrl(asset.id));
    } catch {
      /* decoding failure is surfaced at playback time */
    }
  }
}

// ---------------------------------------------------------------------------
// Transport
// ---------------------------------------------------------------------------
async function togglePlay() {
  haptic("light");
  if (engine.playing) {
    engine.pause();
    els.iconPlay.classList.remove("hidden");
    els.iconPause.classList.add("hidden");
    return;
  }
  engine.ensureContext();
  engine.setMasterVolume(history.current.settings?.master_volume ?? 1);

  // Make sure required assets are decoded before scheduling.
  for (const track of history.current.tracks) {
    for (const clip of track.clips) {
      if (!engine.hasAsset(clip.asset_id)) {
        try {
          await engine.loadAsset(clip.asset_id, api.assetAudioUrl(clip.asset_id));
        } catch {
          toast("فایل صوتی این کلیپ در دسترس نیست.", "error");
          return;
        }
      }
    }
  }

  const from = engine.currentTime >= projectDuration(history.current) ? 0 : engine.currentTime;
  engine.onEnded = () => {
    els.iconPlay.classList.remove("hidden");
    els.iconPause.classList.add("hidden");
  };
  engine.play(history.current, from);
  els.iconPlay.classList.add("hidden");
  els.iconPause.classList.remove("hidden");
}

function stopPlayback() {
  engine.stop();
  els.iconPlay.classList.remove("hidden");
  els.iconPause.classList.add("hidden");
  updateTimeDisplay();
  view.setPlayhead(0);
}

function animationLoop() {
  if (engine.playing) {
    const t = engine.currentTime;
    view.setPlayhead(t);
    view.ensurePlayheadVisible();
    els.timeCurrent.textContent = formatTime(t);
  }
  requestAnimationFrame(animationLoop);
}

// ---------------------------------------------------------------------------
// Import / tray
// ---------------------------------------------------------------------------
function renderTray() {
  els.assetTray.innerHTML = "";
  const list = Object.values(assets);
  els.assetCount.textContent = `${toPersianDigits(list.length)} فایل`;
  els.trayEmpty.classList.toggle("hidden", list.length > 0);
  if (list.length === 0) {
    els.assetTray.appendChild(els.trayEmpty);
    return;
  }

  for (const asset of list) {
    const chip = document.createElement("div");
    chip.className = "asset-chip";

    const nm = document.createElement("span");
    nm.className = "nm";
    nm.textContent = asset.original_name;
    nm.dir = "rtl";

    const dur = document.createElement("span");
    dur.className = "dur";
    dur.textContent = formatTime(asset.duration);

    const addBtn = document.createElement("button");
    addBtn.className = "btn btn-accent";
    addBtn.textContent = "افزودن به تایم‌لاین";
    addBtn.addEventListener("click", () => addAssetToTimeline(asset.id));

    const delBtn = document.createElement("button");
    delBtn.className = "btn btn-danger";
    delBtn.textContent = "حذف";
    delBtn.addEventListener("click", async () => {
      const ok = await confirmAction(
        `فایل «${asset.original_name}» حذف شود؟ کلیپ‌های آن ابتدا باید از تایم‌لاین برداشته شوند.`,
      );
      if (!ok) return;
      try {
        await api.deleteAsset(asset.id);
        delete assets[asset.id];
        renderTray();
        view.render();
        toast("فایل حذف شد.", "success");
      } catch (err) {
        toast(err.message || "حذف فایل ناموفق بود.", "error");
      }
    });

    chip.append(nm, dur, addBtn, delBtn);
    els.assetTray.appendChild(chip);
  }
}

function addAssetToTimeline(assetId) {
  const asset = assets[assetId];
  if (!asset) return;
  try {
    runMutation((doc) => {
      // Drop the clip at the playhead on the selected clip's track (or first).
      let trackId = null;
      if (selectedClipId) {
        const found = findClip(doc, selectedClipId);
        if (found) trackId = found.track.id;
      }
      const start = Math.max(0, engine.currentTime);
      addClip(doc, {
        assetId,
        trackId: trackId || doc.tracks[0].id,
        start,
        trimIn: 0,
        trimOut: asset.duration,
      });
    });
    haptic("light");
    toast("کلیپ به تایم‌لاین اضافه شد.", "success");
  } catch (err) {
    toast(persifyError(err), "error");
  }
}

function persifyError(err) {
  const map = {
    clip_limit: "حداکثر تعداد کلیپ‌ها رسیده است.",
    track_limit: "حداکثر تعداد ترک‌ها رسیده است.",
    last_track: "آخرین ترک قابل حذف نیست.",
    invalid_trim: "مقادیر برش نامعتبر است.",
    clip_not_found: "کلیپ پیدا نشد.",
    track_not_found: "ترک پیدا نشد.",
    no_next_clip: "کلیپ بعدی روی این ترک وجود ندارد.",
    invalid_crossfade: "کراس‌فید با این کلیپ ممکن نیست.",
    asset_limit: "به سقف تعداد فایل‌های پروژه رسیده‌اید.",
  };
  return map[err && err.message] || (err && err.message) || "خطای ناشناخته.";
}

async function handleFiles(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) return;

  for (const file of files) {
    els.uploadStatus.classList.remove("hidden");
    els.uploadStatusText.textContent = "آپلود فایل...";
    els.uploadProgress.style.width = "0%";
    try {
      const asset = await api.uploadAsset(projectId, file, (frac) => {
        els.uploadProgress.style.width = `${Math.round(frac * 100)}%`;
        els.uploadStatusText.textContent =
          frac < 1 ? "آپلود فایل..." : "در حال بررسی فایل صوتی...";
      });
      assets[asset.id] = asset;
      renderTray();
      toast(`فایل «${asset.original_name}» آماده است.`, "success");
      // Convenience: place it on the timeline right away.
      addAssetToTimeline(asset.id);
    } catch (err) {
      const messages = {
        file_too_large: "حجم فایل بیش از حد مجاز است.",
        file_too_long: "مدت فایل بیش از حد مجاز است.",
        unsupported_audio: "فرمت فایل پشتیبانی نمی‌شود.",
        invalid_audio: "فایل صوتی معتبر نیست.",
        asset_limit: "فضای کافی برای این فایل وجود ندارد.",
        empty_file: "فایل خالی است.",
      };
      toast(messages[err.code] || err.message || "آپلود ناموفق بود.", "error");
    }
  }
  els.uploadStatus.classList.add("hidden");
  els.fileInput.value = "";
}

// ---------------------------------------------------------------------------
// Clip sheet
// ---------------------------------------------------------------------------
function openClipSheet(clipId) {
  const found = findClip(history.current, clipId);
  if (!found) return;
  selectedClipId = clipId;
  const { clip } = found;

  els.clipVolume.value = String(clip.volume);
  els.clipVolumeLabel.textContent = `${toPersianDigits(Math.round(clip.volume * 100))}٪`;
  els.clipFadeIn.value = String(clip.fade_in);
  els.clipFadeInLabel.textContent = `${clip.fade_in.toFixed(1)}s`;
  els.clipFadeOut.value = String(clip.fade_out);
  els.clipFadeOutLabel.textContent = `${clip.fade_out.toFixed(1)}s`;
  els.clipTiming.textContent = `${formatTime(clip.start)} ← ${formatTime(clipEnd(clip))}`;
  els.clipMute.textContent = clip.muted ? "فعال‌کردن صدا" : "بی‌صدا";

  els.clipSheet.classList.add("open");
}

function closeSheet(sheet) {
  sheet.classList.remove("open");
}

function bindSheetControls() {
  const bindRange = (input, label, formatter, apply) => {
    let before = null;
    input.addEventListener("pointerdown", () => {
      before = structuredClone(history.current);
    });
    input.addEventListener("input", () => {
      const value = Number(input.value);
      label.textContent = formatter(value);
      if (selectedClipId) {
        apply(value);
        view.render();
      }
    });
    input.addEventListener("change", () => {
      if (before) {
        history.commit(before);
        before = null;
        markDirty();
        updateUndoButtons();
      }
    });
  };

  bindRange(
    els.clipVolume,
    els.clipVolumeLabel,
    (v) => `${toPersianDigits(Math.round(v * 100))}٪`,
    (v) => setClipVolume(history.current, selectedClipId, v),
  );
  bindRange(
    els.clipFadeIn,
    els.clipFadeInLabel,
    (v) => `${v.toFixed(1)}s`,
    (v) => {
      const found = findClip(history.current, selectedClipId);
      if (found) setClipFades(history.current, selectedClipId, v, found.clip.fade_out);
    },
  );
  bindRange(
    els.clipFadeOut,
    els.clipFadeOutLabel,
    (v) => `${v.toFixed(1)}s`,
    (v) => {
      const found = findClip(history.current, selectedClipId);
      if (found) setClipFades(history.current, selectedClipId, found.clip.fade_in, v);
    },
  );

  els.clipSplit.addEventListener("click", () => {
    if (!selectedClipId) return;
    try {
      runMutation((doc) => splitClip(doc, selectedClipId, engine.currentTime));
      toast("کلیپ در موقعیت هد پخش بریده شد.", "success");
    } catch (err) {
      toast(persifyError(err), "error");
    }
    closeSheet(els.clipSheet);
  });

  els.clipDuplicate.addEventListener("click", () => {
    if (!selectedClipId) return;
    try {
      runMutation((doc) => duplicateClip(doc, selectedClipId));
    } catch (err) {
      toast(persifyError(err), "error");
    }
  });

  els.clipCrossfade.addEventListener("click", () => {
    if (!selectedClipId) return;
    try {
      runMutation((doc) => crossfadeWithNext(doc, selectedClipId, 1.0));
      toast("کراس‌فید ۱ ثانیه‌ای اعمال شد.", "success");
    } catch (err) {
      toast(persifyError(err), "error");
    }
    closeSheet(els.clipSheet);
  });

  els.clipMute.addEventListener("click", () => {
    if (!selectedClipId) return;
    runMutation((doc) => toggleClipMute(doc, selectedClipId));
    closeSheet(els.clipSheet);
  });

  els.clipDelete.addEventListener("click", () => {
    if (!selectedClipId) return;
    removeClip(selectedClipId);
    closeSheet(els.clipSheet);
  });

  // Close on backdrop click.
  for (const sheet of [els.clipSheet, els.exportSheet, els.aiSheet]) {
    sheet.addEventListener("click", (event) => {
      if (event.target === sheet && sheet !== els.exportSheet) {
        closeSheet(sheet);
      }
    });
  }
}

function removeClip(clipId) {
  runMutation((doc) => deleteClipOp(doc, clipId));
  if (selectedClipId === clipId) selectedClipId = null;
}

async function removeTrackById(trackId) {
  const ok = await confirmAction("این ترک و همه کلیپ‌های آن حذف شود؟");
  if (!ok) return;
  try {
    runMutation((doc) => removeTrack(doc, trackId));
  } catch (err) {
    toast(persifyError(err), "error");
  }
}

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------
function openExportSheet() {
  els.exportSheet.classList.add("open");
  els.btnDownload.classList.add("hidden");
  els.btnExportClose.classList.add("hidden");
  els.exportProgressBox.classList.add("hidden");
  els.btnStartExport.disabled = false;
}

async function startExport() {
  els.btnStartExport.disabled = true;
  els.exportProgressBox.classList.remove("hidden");
  els.exportStatusDot.className = "status-dot busy";
  els.exportStatusText.textContent = "در صف پردازش...";
  els.exportProgress.style.width = "5%";

  // Persist the latest edits before rendering.
  await saveProject();

  try {
    const job = await api.createExport(projectId, {
      format: els.exportFormat.value,
      bitrate: els.exportBitrate.value,
      depth: Number(els.exportDepth.value),
    });
    activeExportId = job.id;
    pollExport(job.id);
  } catch (err) {
    els.btnStartExport.disabled = false;
    els.exportStatusDot.className = "status-dot err";
    els.exportStatusText.textContent =
      err.code === "no_audible_clips"
        ? "ابتدا یک فایل صوتی به تایم‌لاین اضافه کنید."
        : err.message || "پردازش ناموفق بود؛ دوباره تلاش کنید.";
  }
}

function pollExport(jobId) {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const job = await api.getExport(jobId);
      els.exportProgress.style.width = `${job.progress || 0}%`;

      if (job.status === "queued") {
        els.exportStatusText.textContent = "در صف پردازش...";
      } else if (job.status === "processing") {
        els.exportStatusText.textContent = "در حال ساخت خروجی...";
      } else if (job.status === "completed") {
        clearInterval(pollTimer);
        pollTimer = null;
        els.exportStatusDot.className = "status-dot ok";
        els.exportStatusText.textContent = "خروجی آماده دانلود است.";
        els.exportProgress.style.width = "100%";
        els.btnDownload.href = api.exportDownloadUrl(job.id);
        els.btnDownload.classList.remove("hidden");
        els.btnExportClose.classList.remove("hidden");
        els.btnStartExport.disabled = false;
        haptic("heavy");
      } else if (job.status === "failed") {
        clearInterval(pollTimer);
        pollTimer = null;
        els.exportStatusDot.className = "status-dot err";
        els.exportStatusText.textContent = "پردازش ناموفق بود؛ دوباره تلاش کنید.";
        els.btnStartExport.disabled = false;
      }
    } catch {
      /* transient polling errors are ignored */
    }
  }, 1200);
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------
els.btnPlay.addEventListener("click", togglePlay);
els.btnStop.addEventListener("click", stopPlayback);
els.speed.addEventListener("change", () => {
  engine.setPlaybackRate(Number(els.speed.value));
});
els.btnZoomIn.addEventListener("click", () => view.zoomBy(1.35));
els.btnZoomOut.addEventListener("click", () => view.zoomBy(1 / 1.35));
els.btnZoomFit.addEventListener("click", () => view.zoomToFit());

els.btnUndo.addEventListener("click", () => {
  const doc = history.undo();
  if (doc) {
    view.render();
    updateUndoButtons();
    markDirty();
    updateTimeDisplay();
  }
});
els.btnRedo.addEventListener("click", () => {
  const doc = history.redo();
  if (doc) {
    view.render();
    updateUndoButtons();
    markDirty();
    updateTimeDisplay();
  }
});

els.title.addEventListener("input", () => markDirty());
els.title.addEventListener("change", () => saveProject());

els.btnBack.addEventListener("click", async () => {
  if (dirty) {
    const ok = await confirmAction("تغییرات ذخیره‌نشده است. ذخیره و خروج؟");
    if (!ok) return;
    await saveProject();
  }
  window.location.href = "/";
});

els.btnExport.addEventListener("click", openExportSheet);
els.btnStartExport.addEventListener("click", startExport);
els.btnExportClose.addEventListener("click", () => closeSheet(els.exportSheet));
els.exportFormat.addEventListener("change", () => {
  const isMp3 = els.exportFormat.value === "mp3";
  els.exportBitrateRow.classList.toggle("hidden", !isMp3);
  els.exportDepthRow.classList.toggle("hidden", isMp3);
});

els.btnAi.addEventListener("click", () => els.aiSheet.classList.add("open"));
els.btnAiClose.addEventListener("click", () => closeSheet(els.aiSheet));

els.btnAddAudio.addEventListener("click", () => els.fileInput.click());
els.fileInput.addEventListener("change", () => handleFiles(els.fileInput.files));

// Prevent duplicate uploads from double-taps.
els.btnAddAudio.addEventListener("pointerdown", () => {
  if (els.btnAddAudio.disabled) return;
});

bindSheetControls();

window.addEventListener("beforeunload", (event) => {
  if (dirty || saving) {
    event.preventDefault();
    event.returnValue = "";
  }
});

// Keyboard shortcuts (desktop convenience).
window.addEventListener("keydown", (event) => {
  const tag = document.activeElement && document.activeElement.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
  if (event.code === "Space") {
    event.preventDefault();
    togglePlay();
  } else if (event.key === "s" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    saveProject();
  } else if (event.key === "z" && (event.ctrlKey || event.metaKey) && !event.shiftKey) {
    event.preventDefault();
    els.btnUndo.click();
  } else if (
    (event.key === "y" && (event.ctrlKey || event.metaKey)) ||
    (event.key === "z" && (event.ctrlKey || event.metaKey) && event.shiftKey)
  ) {
    event.preventDefault();
    els.btnRedo.click();
  }
});

boot();
requestAnimationFrame(animationLoop);
