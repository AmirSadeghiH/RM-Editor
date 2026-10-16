/**
 * Unit tests for the timeline state model (static/js/state.js).
 * Run with: node --test tests/frontend/
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  History,
  LIMITS,
  addClip,
  addTrack,
  clipEnd,
  countClips,
  createEmptyTimeline,
  crossfadeWithNext,
  deleteClip,
  duplicateClip,
  findClip,
  formatTime,
  moveClip,
  projectDuration,
  removeTrack,
  setClipFades,
  setClipVolume,
  splitClip,
  toggleClipMute,
  trimClipIn,
  trimClipOut,
  validateTimelineLight,
} from "../../static/js/state.js";

const ASSET = "11111111-2222-3333-4444-555555555555";

function docWithClip() {
  const doc = createEmptyTimeline();
  const clip = addClip(doc, {
    assetId: ASSET,
    trackId: doc.tracks[0].id,
    start: 2,
    trimIn: 0.5,
    trimOut: 4.5,
  });
  return { doc, clip };
}

test("createEmptyTimeline has schema v1 and one track", () => {
  const doc = createEmptyTimeline();
  assert.equal(doc.schema_version, 1);
  assert.equal(doc.tracks.length, 1);
  assert.equal(countClips(doc), 0);
  assert.equal(projectDuration(doc), 0);
});

test("addClip positions and measures correctly", () => {
  const { doc, clip } = docWithClip();
  assert.equal(clip.start, 2);
  assert.equal(clipEnd(clip), 6);
  assert.equal(projectDuration(doc), 6);
});

test("addClip enforces clip limit", () => {
  const doc = createEmptyTimeline();
  for (let i = 0; i < LIMITS.maxClips; i += 1) {
    addClip(doc, {
      assetId: ASSET,
      trackId: doc.tracks[0].id,
      start: i,
      trimIn: 0,
      trimOut: 0.5,
    });
  }
  assert.throws(
    () =>
      addClip(doc, {
        assetId: ASSET,
        trackId: doc.tracks[0].id,
        start: 99,
        trimIn: 0,
        trimOut: 1,
      }),
    /clip_limit/,
  );
});

test("addClip rejects invalid trims", () => {
  const doc = createEmptyTimeline();
  assert.throws(
    () =>
      addClip(doc, {
        assetId: ASSET,
        trackId: doc.tracks[0].id,
        start: 0,
        trimIn: 5,
        trimOut: 2,
      }),
    /invalid_trim/,
  );
});

test("moveClip clamps at zero and can switch tracks", () => {
  const { doc, clip } = docWithClip();
  const track2 = addTrack(doc, "ترک ۲");

  moveClip(doc, clip.id, -5, track2.id);
  assert.equal(clip.start, 0);
  assert.equal(findClip(doc, clip.id).track.id, track2.id);
});

test("trimClipIn slides the start with the in-point", () => {
  const { doc, clip } = docWithClip();
  // trim_in 0.5 → 1.5 moves start 2 → 3, keeps the right edge at 6.
  trimClipIn(doc, clip.id, 1.5);
  assert.equal(clip.trim_in, 1.5);
  assert.equal(clip.start, 3);
  assert.equal(clipEnd(clip), 6);
});

test("trimClipOut keeps the start edge fixed", () => {
  const { doc, clip } = docWithClip();
  trimClipOut(doc, clip.id, 3.5, 10);
  assert.equal(clip.start, 2);
  assert.equal(clip.trim_out, 3.5);
  assert.equal(clipEnd(clip), 5);
});

test("splitClip divides content at the playhead", () => {
  const { doc, clip } = docWithClip();
  const second = splitClip(doc, clip.id, 4);
  assert.equal(clip.trim_out, 2.5); // 0.5 + (4 - 2)
  assert.equal(clipEnd(clip), 4);
  assert.equal(second.start, 4);
  assert.equal(second.trim_in, 2.5);
  assert.equal(second.trim_out, 4.5);
  assert.equal(countClips(doc), 2);
});

test("deleteClip removes only the target", () => {
  const { doc, clip } = docWithClip();
  const other = addClip(doc, {
    assetId: ASSET,
    trackId: doc.tracks[0].id,
    start: 10,
    trimIn: 0,
    trimOut: 1,
  });
  deleteClip(doc, clip.id);
  assert.equal(countClips(doc), 1);
  assert.ok(findClip(doc, other.id));
});

test("duplicateClip appends a copy after the original", () => {
  const { doc, clip } = docWithClip();
  const copy = duplicateClip(doc, clip.id);
  assert.notEqual(copy.id, clip.id);
  assert.equal(copy.trim_in, clip.trim_in);
  assert.equal(copy.start, clipEnd(clip) + 0.1);
});

test("setClipVolume and toggleClipMute", () => {
  const { doc, clip } = docWithClip();
  setClipVolume(doc, clip.id, 0.5);
  assert.equal(clip.volume, 0.5);
  assert.equal(toggleClipMute(doc, clip.id), true);
  assert.equal(clip.muted, true);
});

test("setClipFades clamps to clip length", () => {
  const { doc, clip } = docWithClip();
  setClipFades(doc, clip.id, 10, 10);
  assert.ok(clip.fade_in + clip.fade_out <= 4 + 1e-9);
  assert.equal(clip.fade_in, 4);
});

test("crossfadeWithNext overlaps and applies fade envelopes", () => {
  const doc = createEmptyTimeline();
  const a = addClip(doc, {
    assetId: ASSET,
    trackId: doc.tracks[0].id,
    start: 0,
    trimIn: 0,
    trimOut: 2,
  });
  const b = addClip(doc, {
    assetId: ASSET,
    trackId: doc.tracks[0].id,
    start: 2.5,
    trimIn: 0,
    trimOut: 2,
  });

  const { overlap } = crossfadeWithNext(doc, a.id, 1);
  assert.equal(overlap, 1);
  assert.equal(b.start, 1); // A ends at 2, overlap 1 → B starts at 1
  assert.equal(a.fade_out, 1);
  assert.equal(b.fade_in, 1);
});

test("crossfadeWithNext without a next clip throws", () => {
  const { doc, clip } = docWithClip();
  assert.throws(() => crossfadeWithNext(doc, clip.id, 1), /no_next_clip/);
});

test("addTrack enforces the track limit", () => {
  const doc = createEmptyTimeline();
  while (doc.tracks.length < LIMITS.maxTracks) addTrack(doc);
  assert.throws(() => addTrack(doc), /track_limit/);
});

test("removeTrack refuses to delete the last track", () => {
  const doc = createEmptyTimeline();
  assert.throws(() => removeTrack(doc, doc.tracks[0].id), /last_track/);
});

test("History undo/redo restores snapshots", () => {
  const history = new History(createEmptyTimeline());

  history.run((d) => {
    addClip(d, {
      assetId: ASSET,
      trackId: d.tracks[0].id,
      start: 0,
      trimIn: 0,
      trimOut: 1,
    });
  });
  assert.equal(countClips(history.current), 1);
  assert.equal(history.canUndo, true);

  history.undo();
  assert.equal(countClips(history.current), 0);
  assert.equal(history.canRedo, true);

  history.redo();
  assert.equal(countClips(history.current), 1);

  // New mutation clears redo.
  history.run((d) => {
    addClip(d, {
      assetId: ASSET,
      trackId: d.tracks[0].id,
      start: 5,
      trimIn: 0,
      trimOut: 1,
    });
  });
  assert.equal(history.canRedo, false);
});

test("History.run isolates snapshots (no aliasing)", () => {
  const history = new History(createEmptyTimeline());
  history.run((d) => {
    d.tracks[0].name = "renamed";
  });
  history.undo();
  assert.notEqual(history.current.tracks[0].name, "renamed");

  // Mutating the returned document must not corrupt older snapshots.
  history.current.tracks[0].name = "mutated-later";
  history.redo();
  assert.equal(history.current.tracks[0].name, "renamed");
});

test("validateTimelineLight reports structural problems", () => {
  const { doc } = docWithClip();
  assert.deepEqual(validateTimelineLight(doc), []);
  assert.ok(validateTimelineLight({ tracks: [] }).length > 0);
});

test("formatTime renders mm:ss.t", () => {
  assert.equal(formatTime(0), "00:00.0");
  assert.equal(formatTime(65.5), "01:05.5");
  assert.equal(formatTime(-3), "00:00.0");
});
