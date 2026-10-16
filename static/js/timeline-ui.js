/**
 * Timeline view: ruler, track lanes, clips with real waveforms, playhead.
 *
 * Time flows left → right inside `.timeline-scroll` (dir="ltr" is applied by
 * CSS even though the surrounding UI is RTL). Clips are DOM nodes (easy hit
 * testing with Pointer Events); waveforms and the ruler are canvas.
 *
 * The view mutates the shared timeline document during gestures and reports
 * `onGestureBegin` / `onGestureCommit` so the editor can record exactly one
 * undo entry per drag.
 */

import { drawClipWaveform } from "./waveform.js";
import {
  clipContentDuration,
  clipEnd,
  findClip,
  formatTime,
  moveClip,
  projectDuration,
  trimClipIn,
  trimClipOut,
} from "./state.js";

const MIN_PPS = 8; // pixels per second
const MAX_PPS = 400;
const DEFAULT_PPS = 64;
const TRACK_H = 84;
const TRACK_GAP = 8;
const SNAP = 0.05;

export class TimelineView {
  /**
   * @param {object} els {scroll, inner, ruler, lanes, playhead, trackHeads}
   * @param {object} opts {getTimeline, engine, getAssets, callbacks}
   */
  constructor(els, opts) {
    this.els = els;
    this.getTimeline = opts.getTimeline;
    this.engine = opts.engine;
    this.getAssets = opts.getAssets;
    this.cb = opts.callbacks || {};
    this.pps = DEFAULT_PPS;
    this.selectedClipId = null;
    this._playheadTime = 0;
    this._raf = 0;

    this._bindRuler();
    this._bindLanes();
    window.addEventListener("resize", () => this.render());
  }

  // -------------------------------------------------------------------------
  // Public API
  // -------------------------------------------------------------------------

  setZoom(pps, anchorTime = null) {
    const scroll = this.els.scroll;
    const anchor =
      anchorTime !== null
        ? anchorTime
        : (scroll.scrollLeft + scroll.clientWidth / 2) / this.pps;
    this.pps = Math.max(MIN_PPS, Math.min(MAX_PPS, pps));
    this.render();
    scroll.scrollLeft = Math.max(0, anchor * this.pps - scroll.clientWidth / 2);
  }

  zoomBy(factor) {
    this.setZoom(this.pps * factor);
  }

  zoomToFit() {
    const duration = Math.max(projectDuration(this.getTimeline()), 10);
    const target = (this.els.scroll.clientWidth - 24) / duration;
    this.setZoom(target, 0);
    this.els.scroll.scrollLeft = 0;
  }

  select(clipId) {
    this.selectedClipId = clipId;
    this.render();
    if (this.cb.onSelect) this.cb.onSelect(clipId);
  }

  setPlayhead(time) {
    this._playheadTime = Math.max(0, time);
    this.els.playhead.style.left = `${this._playheadTime * this.pps}px`;
  }

  /** Scroll the viewport to keep the playhead visible (during playback). */
  ensurePlayheadVisible() {
    const x = this._playheadTime * this.pps;
    const scroll = this.els.scroll;
    const margin = 40;
    if (x < scroll.scrollLeft + margin || x > scroll.scrollLeft + scroll.clientWidth - margin) {
      scroll.scrollLeft = Math.max(0, x - scroll.clientWidth / 3);
    }
  }

  render() {
    cancelAnimationFrame(this._raf);
    this._raf = requestAnimationFrame(() => this._renderNow());
  }

  // -------------------------------------------------------------------------
  // Rendering
  // -------------------------------------------------------------------------

  _renderNow() {
    const timeline = this.getTimeline();
    const duration = Math.max(projectDuration(timeline), 30);
    const width = Math.max(
      duration * this.pps + 240,
      this.els.scroll.clientWidth,
    );

    this.els.inner.style.width = `${width}px`;
    this.els.lanes.style.setProperty("--grid-px", `${this.pps}px`);

    this._renderRuler(width, duration);
    this._renderTrackHeads(timeline);
    this._renderLanes(timeline);
    this.setPlayhead(this._playheadTime);
  }

  _renderRuler(width, duration) {
    const ruler = this.els.ruler;
    const dpr = window.devicePixelRatio || 1;
    const height = ruler.clientHeight || 34;
    ruler.width = Math.round(width * dpr);
    ruler.height = Math.round(height * dpr);

    const ctx = ruler.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    // Choose tick spacing: aim for ≥70px between major ticks.
    const candidates = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300];
    const major = candidates.find((c) => c * this.pps >= 70) || 300;
    const minor = major / 5;

    ctx.strokeStyle = "rgba(255,255,255,0.14)";
    ctx.fillStyle = "rgba(255,255,255,0.55)";
    ctx.font = "10px Vazirmatn, Tahoma, sans-serif";
    ctx.textBaseline = "top";

    for (let t = 0; t <= duration + major; t += minor) {
      const x = Math.round(t * this.pps) + 0.5;
      const isMajor = Math.abs(t / major - Math.round(t / major)) < 1e-6;
      ctx.beginPath();
      ctx.moveTo(x, isMajor ? height - 14 : height - 6);
      ctx.lineTo(x, height);
      ctx.stroke();
      if (isMajor) {
        ctx.fillText(formatTime(t), x + 3, 4);
      }
    }
  }

  _renderTrackHeads(timeline) {
    const container = this.els.trackHeads;
    container.innerHTML = "";
    timeline.tracks.forEach((track, index) => {
      const head = document.createElement("div");
      head.className = "track-head";
      head.dataset.trackId = track.id;

      const row1 = document.createElement("div");
      row1.className = "row1";

      const nameInput = document.createElement("input");
      nameInput.className = "name";
      nameInput.value = track.name;
      nameInput.setAttribute("aria-label", "نام ترک");
      nameInput.addEventListener("change", () => {
        if (this.cb.onGestureBegin) this.cb.onGestureBegin();
        track.name = nameInput.value;
        if (this.cb.onGestureCommit) this.cb.onGestureCommit();
      });
      // Keep RTL Persian names readable inside the LTR timeline context.
      nameInput.dir = "rtl";

      const delBtn = document.createElement("button");
      delBtn.className = "mini";
      delBtn.title = "حذف ترک";
      delBtn.innerHTML =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h16M9 7V5h6v2M7 7l1 12h8l1-12"/></svg>';
      delBtn.addEventListener("click", () => {
        if (this.cb.onDeleteTrack) this.cb.onDeleteTrack(track.id);
      });

      row1.append(nameInput, delBtn);

      const row2 = document.createElement("div");
      row2.className = "row2";

      const muteBtn = document.createElement("button");
      muteBtn.className = `mini${track.muted ? " active" : ""}`;
      muteBtn.title = "بی‌صدا";
      muteBtn.textContent = "M";
      muteBtn.addEventListener("click", () => {
        if (this.cb.onGestureBegin) this.cb.onGestureBegin();
        track.muted = !track.muted;
        if (this.cb.onGestureCommit) this.cb.onGestureCommit();
        this.render();
      });

      const vol = document.createElement("input");
      vol.type = "range";
      vol.min = "0";
      vol.max = "2";
      vol.step = "0.01";
      vol.value = String(track.volume ?? 1);
      vol.title = "حجم صدای ترک";
      vol.setAttribute("aria-label", "حجم صدای ترک");
      vol.addEventListener("input", () => {
        track.volume = Number(vol.value);
      });
      vol.addEventListener("change", () => {
        if (this.cb.onGestureCommit) this.cb.onGestureCommit();
        if (this.cb.onTimelineChanged) this.cb.onTimelineChanged();
      });
      vol.addEventListener("pointerdown", () => {
        if (this.cb.onGestureBegin) this.cb.onGestureBegin();
      });

      row2.append(muteBtn, vol);
      head.append(row1, row2);
      container.append(head);

      // Keep heads aligned with lanes (same heights).
      head.style.marginTop = index === 0 ? "0" : `${TRACK_GAP}px`;
    });
  }

  _renderLanes(timeline) {
    const lanes = this.els.lanes;
    lanes.innerHTML = "";
    const assets = this.getAssets();

    timeline.tracks.forEach((track, index) => {
      const lane = document.createElement("div");
      lane.className = `track-lane${track.muted ? " muted" : ""}`;
      lane.dataset.trackId = track.id;
      lane.style.marginTop = index === 0 ? "0" : `${TRACK_GAP}px`;

      for (const clip of track.clips || []) {
        lane.appendChild(this._buildClipEl(clip, track, assets));
      }
      lanes.appendChild(lane);
    });
  }

  _buildClipEl(clip, track, assets) {
    const el = document.createElement("div");
    const contentLen = clipContentDuration(clip);
    el.className = [
      "clip",
      clip.id === this.selectedClipId ? "selected" : "",
      clip.muted ? "muted-clip" : "",
      (assets[clip.asset_id] && assets[clip.asset_id].alt) ? "alt" : "",
    ]
      .filter(Boolean)
      .join(" ");
    el.dataset.clipId = clip.id;
    el.style.left = `${clip.start * this.pps}px`;
    el.style.width = `${Math.max(8, contentLen * this.pps)}px`;
    el.setAttribute("role", "button");
    el.setAttribute("tabindex", "0");
    el.setAttribute("aria-label", `کلیپ ${assets[clip.asset_id] ? assets[clip.asset_id].original_name : ""}`);

    const canvas = document.createElement("canvas");
    el.appendChild(canvas);

    const label = document.createElement("div");
    label.className = "clip-label";
    const asset = assets[clip.asset_id];
    label.textContent = asset ? asset.original_name : "کلیپ";
    el.appendChild(label);

    // Fade visualization wedges.
    if (clip.fade_in > 0) {
      const fade = document.createElement("div");
      fade.className = "fade-viz in";
      fade.style.width = `${clip.fade_in * this.pps}px`;
      el.appendChild(fade);
    }
    if (clip.fade_out > 0) {
      const fade = document.createElement("div");
      fade.className = "fade-viz out";
      fade.style.width = `${clip.fade_out * this.pps}px`;
      el.appendChild(fade);
    }

    const trimL = document.createElement("div");
    trimL.className = "trim-handle l";
    trimL.dataset.trim = "in";
    const trimR = document.createElement("div");
    trimR.className = "trim-handle r";
    trimR.dataset.trim = "out";
    el.append(trimL, trimR);

    this._bindClip(el, clip, track);

    // Draw waveform after layout.
    requestAnimationFrame(() => this._drawClipWave(canvas, clip, asset));
    return el;
  }

  _drawClipWave(canvas, clip, asset) {
    if (!asset) return;
    const peaks = this.engine.getPeaks(clip.asset_id, asset.waveform);
    if (!peaks) return;
    drawClipWaveform(canvas, peaks, {
      trimIn: clip.trim_in,
      trimOut: clip.trim_out,
      duration: asset.duration,
      muted: clip.muted,
    });
  }

  // -------------------------------------------------------------------------
  // Interaction
  // -------------------------------------------------------------------------

  _bindRuler() {
    const ruler = this.els.ruler;
    const seek = (event) => {
      const rect = ruler.getBoundingClientRect();
      const time = Math.max(0, (event.clientX - rect.left) / this.pps);
      this.setPlayhead(time);
      if (this.cb.onSeek) this.cb.onSeek(time);
    };
    ruler.addEventListener("pointerdown", (event) => {
      ruler.setPointerCapture(event.pointerId);
      seek(event);
      const move = (e) => seek(e);
      const up = () => {
        ruler.removeEventListener("pointermove", move);
        ruler.removeEventListener("pointerup", up);
      };
      ruler.addEventListener("pointermove", move);
      ruler.addEventListener("pointerup", up);
    });
  }

  _bindLanes() {
    // Click on empty lane space seeks (feels natural on mobile).
    this.els.lanes.addEventListener("pointerdown", (event) => {
      if (event.target.closest(".clip")) return;
      const rect = this.els.lanes.getBoundingClientRect();
      const time = Math.max(0, (event.clientX - rect.left) / this.pps);
      this.setPlayhead(time);
      this.select(null);
      if (this.cb.onSeek) this.cb.onSeek(time);
    });
  }

  _bindClip(el, clip, track) {
    let mode = null; // "move" | "trim-in" | "trim-out"
    let originX = 0;
    let originY = 0;
    let before = null;
    let moved = false;

    const timeline = this.getTimeline;

    el.addEventListener("pointerdown", (event) => {
      event.stopPropagation();
      el.setPointerCapture(event.pointerId);
      originX = event.clientX;
      originY = event.clientY;
      moved = false;
      before = {
        start: clip.start,
        trim_in: clip.trim_in,
        trim_out: clip.trim_out,
        trackId: track.id,
      };

      const trim = event.target.dataset && event.target.dataset.trim;
      mode = trim === "in" ? "trim-in" : trim === "out" ? "trim-out" : "move";

      if (this.selectedClipId !== clip.id) {
        this.selectedClipId = clip.id;
        this.render();
        if (this.cb.onSelect) this.cb.onSelect(clip.id);
      }
    });

    el.addEventListener("pointermove", (event) => {
      if (!mode) return;
      const dx = event.clientX - originX;
      const dy = event.clientY - originY;
      if (!moved && Math.abs(dx) < 3 && Math.abs(dy) < 3) return;
      if (!moved) {
        moved = true;
        if (this.cb.onGestureBegin) this.cb.onGestureBegin();
      }

      const dt = dx / this.pps;

      if (mode === "move") {
        let newStart = Math.max(0, before.start + dt);
        newStart = snap(newStart);
        // Vertical drag moves the clip across tracks.
        const lanesRect = this.els.lanes.getBoundingClientRect();
        const y = event.clientY - lanesRect.top;
        const targetIndex = Math.max(
          0,
          Math.min(
            timeline().tracks.length - 1,
            Math.floor(y / (TRACK_H + TRACK_GAP)),
          ),
        );
        const targetTrack = timeline().tracks[targetIndex];
        moveClip(timeline(), clip.id, newStart, targetTrack ? targetTrack.id : null);
      } else if (mode === "trim-in") {
        trimClipIn(timeline(), clip.id, snap(before.trim_in + dt));
      } else {
        const asset = this.getAssets()[clip.asset_id];
        trimClipOut(
          timeline(),
          clip.id,
          snap(before.trim_out + dt),
          asset ? asset.duration : undefined,
        );
      }
      this.render();
    });

    const finish = () => {
      if (!mode) return;
      const wasMoved = moved;
      mode = null;
      if (wasMoved && this.cb.onGestureCommit) {
        this.cb.onGestureCommit();
        if (this.cb.onTimelineChanged) this.cb.onTimelineChanged();
      }
    };
    el.addEventListener("pointerup", finish);
    el.addEventListener("pointercancel", finish);

    // Double-click / double-tap opens the clip properties sheet.
    el.addEventListener("dblclick", () => {
      if (this.cb.onOpenClipSheet) this.cb.onOpenClipSheet(clip.id);
    });

    el.addEventListener("keydown", (event) => {
      if (event.key === "Delete" || event.key === "Backspace") {
        event.preventDefault();
        if (this.cb.onDeleteClip) this.cb.onDeleteClip(clip.id);
      } else if (event.key === "Enter") {
        if (this.cb.onOpenClipSheet) this.cb.onOpenClipSheet(clip.id);
      }
    });
  }
}

function snap(value) {
  return Math.round(value / SNAP) * SNAP;
}
