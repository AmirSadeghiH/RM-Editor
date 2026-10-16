/**
 * Timeline document model, editing operations and undo/redo history.
 *
 * Pure ES module — no DOM, no network — so it can be unit-tested with
 * `node --test`. Mirrors the server-side schema documented in
 * `studio/timeline.py` (schema version 1, times in floating-point seconds).
 */

export const TIMELINE_SCHEMA_VERSION = 1;

export const LIMITS = {
  maxTracks: 8,
  maxClips: 30,
  maxVolume: 4,
  maxFade: 60,
  minClipLength: 0.05,
  maxExportDuration: 600,
};

let _idCounter = 0;

/** Generate a stable, URL-safe unique id for clips/tracks. */
export function makeId(prefix) {
  _idCounter += 1;
  const rand = Math.random().toString(36).slice(2, 8);
  return `${prefix}_${Date.now().toString(36)}${_idCounter.toString(36)}${rand}`;
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

export function createEmptyTimeline() {
  return {
    schema_version: TIMELINE_SCHEMA_VERSION,
    settings: { master_volume: 1 },
    tracks: [
      {
        id: makeId("trk"),
        name: "ترک ۱",
        order: 0,
        volume: 1,
        muted: false,
        clips: [],
      },
    ],
  };
}

/** Duration of the audible arrangement (last clip end), in seconds. */
export function projectDuration(timeline) {
  let max = 0;
  for (const track of timeline.tracks || []) {
    for (const clip of track.clips || []) {
      const end = clip.start + (clip.trim_out - clip.trim_in);
      if (end > max) max = end;
    }
  }
  return max;
}

export function clipContentDuration(clip) {
  return clip.trim_out - clip.trim_in;
}

export function clipEnd(clip) {
  return clip.start + clipContentDuration(clip);
}

export function findClip(timeline, clipId) {
  for (let ti = 0; ti < timeline.tracks.length; ti += 1) {
    const track = timeline.tracks[ti];
    for (let ci = 0; ci < (track.clips || []).length; ci += 1) {
      if (track.clips[ci].id === clipId) {
        return { track, clip: track.clips[ci], trackIndex: ti, clipIndex: ci };
      }
    }
  }
  return null;
}

export function countClips(timeline) {
  return (timeline.tracks || []).reduce(
    (sum, track) => sum + ((track.clips || []).length),
    0,
  );
}

// ---------------------------------------------------------------------------
// Track operations
// ---------------------------------------------------------------------------

export function addTrack(timeline, name) {
  if (timeline.tracks.length >= LIMITS.maxTracks) {
    throw new Error("track_limit");
  }
  const track = {
    id: makeId("trk"),
    name: name || `ترک ${toPersianDigits(timeline.tracks.length + 1)}`,
    order: timeline.tracks.length,
    volume: 1,
    muted: false,
    clips: [],
  };
  timeline.tracks.push(track);
  return track;
}

export function removeTrack(timeline, trackId) {
  const index = timeline.tracks.findIndex((t) => t.id === trackId);
  if (index === -1) return false;
  if (timeline.tracks.length <= 1) {
    throw new Error("last_track");
  }
  timeline.tracks.splice(index, 1);
  return true;
}

export function renameTrack(timeline, trackId, name) {
  const track = timeline.tracks.find((t) => t.id === trackId);
  if (!track) return false;
  track.name = String(name || "").slice(0, 120);
  return true;
}

export function setTrackVolume(timeline, trackId, volume) {
  const track = timeline.tracks.find((t) => t.id === trackId);
  if (!track) return false;
  track.volume = clamp(volume, 0, LIMITS.maxVolume);
  return true;
}

export function toggleTrackMute(timeline, trackId) {
  const track = timeline.tracks.find((t) => t.id === trackId);
  if (!track) return false;
  track.muted = !track.muted;
  return track.muted;
}

// ---------------------------------------------------------------------------
// Clip operations
// ---------------------------------------------------------------------------

function makeClip({ assetId, start, trimIn, trimOut, volume = 1 }) {
  return {
    id: makeId("clp"),
    asset_id: assetId,
    start: round6(Math.max(0, start)),
    trim_in: round6(trimIn),
    trim_out: round6(trimOut),
    volume: clamp(volume, 0, LIMITS.maxVolume),
    muted: false,
    fade_in: 0,
    fade_out: 0,
  };
}

export function addClip(timeline, { assetId, trackId, start, trimIn, trimOut, volume }) {
  if (countClips(timeline) >= LIMITS.maxClips) {
    throw new Error("clip_limit");
  }
  const track = trackId
    ? timeline.tracks.find((t) => t.id === trackId)
    : timeline.tracks[0];
  if (!track) throw new Error("track_not_found");
  if (!(trimOut > trimIn + LIMITS.minClipLength - 1e-9)) {
    throw new Error("invalid_trim");
  }
  const clip = makeClip({ assetId, start, trimIn, trimOut, volume });
  track.clips.push(clip);
  return clip;
}

export function moveClip(timeline, clipId, newStart, newTrackId = null) {
  const found = findClip(timeline, clipId);
  if (!found) return false;
  const target = newTrackId
    ? timeline.tracks.find((t) => t.id === newTrackId)
    : found.track;
  if (!target) throw new Error("track_not_found");

  found.clip.start = round6(Math.max(0, newStart));
  if (target.id !== found.track.id) {
    found.track.clips.splice(found.track.clips.indexOf(found.clip), 1);
    target.clips.push(found.clip);
  }
  return true;
}

/**
 * Trim the in-point of a clip. The clip's right edge stays on the timeline:
 * start slides along with trim_in (standard DAW left-edge behaviour).
 */
export function trimClipIn(timeline, clipId, newTrimIn) {
  const found = findClip(timeline, clipId);
  if (!found) return false;
  const { clip } = found;
  const next = clamp(newTrimIn, 0, clip.trim_out - LIMITS.minClipLength);
  const delta = next - clip.trim_in;
  clip.trim_in = round6(next);
  clip.start = round6(Math.max(0, clip.start + delta));
  if (clip.fade_in > clipContentDuration(clip)) {
    clip.fade_in = round6(clipContentDuration(clip));
  }
  return true;
}

/** Trim the out-point; the clip's start edge stays fixed. */
export function trimClipOut(timeline, clipId, newTrimOut, assetDuration = LIMITS.maxExportDuration) {
  const found = findClip(timeline, clipId);
  if (!found) return false;
  const { clip } = found;
  const maxOut = Math.min(assetDuration, clip.trim_in + (LIMITS.maxExportDuration - clip.start));
  const next = clamp(newTrimOut, clip.trim_in + LIMITS.minClipLength, maxOut);
  clip.trim_out = round6(next);
  if (clip.fade_out > clipContentDuration(clip)) {
    clip.fade_out = round6(clipContentDuration(clip));
  }
  return true;
}

/** Split a clip at an absolute timeline position. Returns the new clip. */
export function splitClip(timeline, clipId, atTime) {
  const found = findClip(timeline, clipId);
  if (!found) throw new Error("clip_not_found");
  const { clip } = found;
  const end = clipEnd(clip);
  const at = clamp(atTime, clip.start + LIMITS.minClipLength, end - LIMITS.minClipLength);

  const splitSource = clip.trim_in + (at - clip.start);
  const originalOut = clip.trim_out;

  clip.trim_out = round6(splitSource);
  clip.fade_out = 0; // fades stay on the outer edges of the original clip

  const second = makeClip({
    assetId: clip.asset_id,
    start: at,
    trimIn: splitSource,
    trimOut: originalOut,
    volume: clip.volume,
  });
  second.fade_in = 0;
  second.fade_out = clip.fade_out;
  second.muted = clip.muted;

  found.track.clips.push(second);
  return second;
}

export function deleteClip(timeline, clipId) {
  const found = findClip(timeline, clipId);
  if (!found) return false;
  found.track.clips.splice(found.clipIndex, 1);
  return true;
}

export function duplicateClip(timeline, clipId) {
  const found = findClip(timeline, clipId);
  if (!found) throw new Error("clip_not_found");
  if (countClips(timeline) >= LIMITS.maxClips) {
    throw new Error("clip_limit");
  }
  const copy = clone(found.clip);
  copy.id = makeId("clp");
  copy.start = round6(clipEnd(found.clip) + 0.1);
  found.track.clips.push(copy);
  return copy;
}

export function setClipVolume(timeline, clipId, volume) {
  const found = findClip(timeline, clipId);
  if (!found) return false;
  found.clip.volume = round6(clamp(volume, 0, LIMITS.maxVolume));
  return true;
}

export function toggleClipMute(timeline, clipId) {
  const found = findClip(timeline, clipId);
  if (!found) return false;
  found.clip.muted = !found.clip.muted;
  return found.clip.muted;
}

export function setClipFades(timeline, clipId, fadeIn, fadeOut) {
  const found = findClip(timeline, clipId);
  if (!found) return false;
  const { clip } = found;
  const len = clipContentDuration(clip);
  clip.fade_in = round6(clamp(fadeIn, 0, Math.min(LIMITS.maxFade, len)));
  clip.fade_out = round6(clamp(fadeOut, 0, Math.min(LIMITS.maxFade, len - clip.fade_in)));
  return true;
}

/**
 * Apply a real crossfade between a clip and the next one on its track:
 * the clips overlap by `duration` seconds while the first fades out and the
 * second fades in. The server render treats the overlap + envelopes as an
 * actual audio crossfade.
 */
export function crossfadeWithNext(timeline, clipId, duration = 1) {
  const found = findClip(timeline, clipId);
  if (!found) throw new Error("clip_not_found");
  const { track, clip } = found;
  // "Next" = the clip with the smallest start that is not this clip and
  // starts at or after this clip's start.
  const candidates = track.clips
    .filter((c) => c.id !== clip.id && c.start >= clip.start - 1e-6)
    .sort((a, b) => a.start - b.start || clipEnd(a) - clipEnd(clip));
  const target = candidates[0];
  if (!target) throw new Error("no_next_clip");

  const overlap = Math.min(
    duration,
    clipContentDuration(clip),
    clipContentDuration(target),
  );
  if (overlap <= 0) throw new Error("invalid_crossfade");

  target.start = round6(Math.max(0, clipEnd(clip) - overlap));
  clip.fade_out = round6(Math.max(clip.fade_out, overlap));
  target.fade_in = round6(Math.max(target.fade_in, overlap));
  return { clip, target, overlap };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

export function clamp(value, lo, hi) {
  const n = Number(value);
  if (!Number.isFinite(n)) return lo;
  return Math.min(hi, Math.max(lo, n));
}

function round6(value) {
  return Math.round(value * 1e6) / 1e6;
}

export function toPersianDigits(value) {
  const digits = "۰۱۲۳۴۵۶۷۸۹";
  return String(value).replace(/\d/g, (d) => digits[Number(d)]);
}

export function formatTime(seconds) {
  const s = Math.max(0, Number(seconds) || 0);
  const mins = Math.floor(s / 60);
  const secs = Math.floor(s % 60);
  const tenths = Math.floor((s % 1) * 10);
  return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}.${tenths}`;
}

/**
 * Light client-side check before saving: mirrors a few server rules so the
 * UI can catch problems early with Persian messages.
 */
export function validateTimelineLight(timeline) {
  const problems = [];
  if (!timeline || !Array.isArray(timeline.tracks) || timeline.tracks.length === 0) {
    return ["تایم‌لاین حداقل به یک ترک نیاز دارد."];
  }
  if (timeline.tracks.length > LIMITS.maxTracks) {
    problems.push(`حداکثر ${toPersianDigits(LIMITS.maxTracks)} ترک مجاز است.`);
  }
  if (countClips(timeline) > LIMITS.maxClips) {
    problems.push(`حداکثر ${toPersianDigits(LIMITS.maxClips)} کلیپ مجاز است.`);
  }
  if (projectDuration(timeline) > LIMITS.maxExportDuration + 0.05) {
    problems.push("طول پروژه از حد مجاز خروجی بیشتر است.");
  }
  return problems;
}

// ---------------------------------------------------------------------------
// Undo / redo
// ---------------------------------------------------------------------------

export class History {
  /** Snapshot-based history. Call `commit()` BEFORE a mutation. */
  constructor(initialTimeline, { limit = 100 } = {}) {
    this._limit = limit;
    this._undoStack = [];
    this._redoStack = [];
    this._current = clone(initialTimeline);
  }

  get current() {
    return this._current;
  }

  /** Record the current state and hand over a mutable working copy. */
  begin() {
    return this._current;
  }

  /** Push a snapshot of `before` (call after mutating `this.current`). */
  commit(before) {
    this._undoStack.push(clone(before));
    if (this._undoStack.length > this._limit) {
      this._undoStack.shift();
    }
    this._redoStack = [];
    // Detach from the caller's object so later mutations stay undoable.
    this._current = clone(this._current);
  }

  /**
   * Run a mutation function with automatic snapshot handling:
   *   history.run((doc) => moveClip(doc, id, 2.5));
   */
  run(mutator) {
    const before = clone(this._current);
    const result = mutator(this._current);
    this.commit(before);
    return result;
  }

  get canUndo() {
    return this._undoStack.length > 0;
  }

  get canRedo() {
    return this._redoStack.length > 0;
  }

  undo() {
    if (!this.canUndo) return null;
    this._redoStack.push(clone(this._current));
    this._current = this._undoStack.pop();
    return this._current;
  }

  redo() {
    if (!this.canRedo) return null;
    this._undoStack.push(clone(this._current));
    this._current = this._redoStack.pop();
    return this._current;
  }

  /** Replace history state after external load (e.g. project reopen). */
  reset(timeline) {
    this._undoStack = [];
    this._redoStack = [];
    this._current = clone(timeline);
  }
}
