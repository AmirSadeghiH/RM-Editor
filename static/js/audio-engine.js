/**
 * Real-time multitrack preview with the Web Audio API.
 *
 * Clips are scheduled as `AudioBufferSourceNode`s on a shared AudioContext
 * clock: every clip starts at its exact timeline position, trims are source
 * offsets, and clip volume + fades are GainNode automations. One master
 * gain feeds the output. This keeps all tracks sample-synchronized —
 * unlike playing separate HTML audio elements.
 *
 * The browser preview is only for monitoring: the server-side FFmpeg render
 * remains the authoritative final mix.
 */

import { peaksFromAudioBuffer } from "./waveform.js";
import { clipContentDuration, clipEnd } from "./state.js";

const LOOKAHEAD = 0.08; // seconds of scheduling lead

export class PreviewEngine {
  constructor() {
    this.ctx = null;
    this.masterGain = null;
    this.buffers = new Map(); // assetId -> AudioBuffer
    this.peaks = new Map(); // assetId -> peaks object
    this.sources = [];
    this.playing = false;
    this.playbackRate = 1;
    this._startCtxTime = 0;
    this._startOffset = 0; // timeline seconds at playback start
    this._pausedAt = 0;
    this._endTimer = null;
    this.onEnded = null;
  }

  /** Must be called from a user gesture (mobile autoplay policies). */
  ensureContext() {
    if (!this.ctx) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      this.ctx = new Ctx();
      this.masterGain = this.ctx.createGain();
      this.masterGain.gain.value = 1;
      this.masterGain.connect(this.ctx.destination);
    }
    if (this.ctx.state === "suspended") {
      this.ctx.resume();
    }
    return this.ctx;
  }

  /** Fetch + decode one asset. Safe to call repeatedly (cached). */
  async loadAsset(assetId, url) {
    if (this.buffers.has(assetId)) return this.buffers.get(assetId);
    this.ensureContext();
    const response = await fetch(url, { credentials: "same-origin" });
    if (!response.ok) throw new Error("asset_fetch_failed");
    const raw = await response.arrayBuffer();
    const buffer = await this.ctx.decodeAudioData(raw);
    this.buffers.set(assetId, buffer);
    return buffer;
  }

  hasAsset(assetId) {
    return this.buffers.has(assetId);
  }

  /** Peaks for waveform drawing: server peaks win; else compute locally. */
  getPeaks(assetId, serverWaveform) {
    if (this.peaks.has(assetId)) return this.peaks.get(assetId);
    if (serverWaveform && serverWaveform.peaks) {
      this.peaks.set(assetId, serverWaveform);
      return serverWaveform;
    }
    const buffer = this.buffers.get(assetId);
    if (!buffer) return null;
    const peaks = peaksFromAudioBuffer(buffer);
    this.peaks.set(assetId, peaks);
    return peaks;
  }

  setMasterVolume(volume) {
    if (this.masterGain) {
      this.masterGain.gain.value = Math.max(0, Math.min(4, volume));
    }
  }

  setPlaybackRate(rate) {
    this.playbackRate = rate;
    if (this.playing) {
      // Restart scheduling at the current position with the new rate.
      const t = this.currentTime;
      this.stopSources();
      this._schedule(t);
    }
  }

  get currentTime() {
    if (!this.playing || !this.ctx) return this._pausedAt;
    const elapsed = (this.ctx.currentTime - this._startCtxTime) * this.playbackRate;
    return this._startOffset + elapsed;
  }

  /** Start (or resume) preview from timeline position `fromTime`. */
  play(timeline, fromTime = 0) {
    this.ensureContext();
    this.stopSources();
    this._schedule(timeline, fromTime);
    this.playing = true;
    this._pausedAt = fromTime;
  }

  pause() {
    if (!this.playing) return;
    this._pausedAt = this.currentTime;
    this.stopSources();
    this.playing = false;
    if (this._endTimer) clearTimeout(this._endTimer);
  }

  stop() {
    this.stopSources();
    this.playing = false;
    this._pausedAt = 0;
    if (this._endTimer) clearTimeout(this._endTimer);
  }

  seek(timeline, time) {
    const wasPlaying = this.playing;
    this.stopSources();
    this.playing = false;
    this._pausedAt = Math.max(0, time);
    if (wasPlaying) this.play(timeline, this._pausedAt);
  }

  stopSources() {
    for (const source of this.sources) {
      try {
        source.node.stop();
      } catch {
        /* already stopped */
      }
      try {
        source.node.disconnect();
        source.gain.disconnect();
      } catch {
        /* ignore */
      }
    }
    this.sources = [];
    if (this._endTimer) {
      clearTimeout(this._endTimer);
      this._endTimer = null;
    }
  }

  /** Drop cached audio (project switch / asset delete). */
  reset() {
    this.stop();
    this.buffers.clear();
    this.peaks.clear();
    this._pausedAt = 0;
  }

  // -------------------------------------------------------------------------

  _schedule(timeline, fromTime) {
    const ctx = this.ensureContext();
    const rate = this.playbackRate;
    const startCtx = ctx.currentTime + LOOKAHEAD;
    this._startCtxTime = startCtx;
    this._startOffset = fromTime;

    let endAt = fromTime;

    for (const track of timeline.tracks || []) {
      if (track.muted) continue;
      for (const clip of track.clips || []) {
        const contentLen = clipContentDuration(clip);
        const clipStart = clip.start;
        const clipEndT = clipEnd(clip);
        if (clipEndT <= fromTime) continue;
        if (clip.muted || clip.volume <= 0) continue;

        const buffer = this.buffers.get(clip.asset_id);
        if (!buffer) continue;

        const audibleStart = Math.max(clipStart, fromTime);
        const when = startCtx + (audibleStart - fromTime) / rate;
        const contentOffset = audibleStart - clipStart; // seconds into clip content
        const remaining = clipEndT - audibleStart;

        const source = ctx.createBufferSource();
        source.buffer = buffer;
        source.playbackRate.value = rate;

        const gain = ctx.createGain();
        const peakVolume = (clip.volume || 1) * (track.volume ?? 1);

        this._applyGainAutomation(gain.gain, clip, peakVolume, contentLen, when, contentOffset, rate);

        source.connect(gain);
        gain.connect(this.masterGain);
        source.start(when, clip.trim_in + contentOffset, remaining);

        this.sources.push({ node: source, gain });
        endAt = Math.max(endAt, clipEndT);
      }
    }

    // Auto-stop at the end of the project.
    const wallRemaining = Math.max(0, (endAt - fromTime) / rate) * 1000 + 150;
    this._endTimer = setTimeout(() => {
      if (this.playing) {
        this.stopSources();
        this.playing = false;
        this._pausedAt = endAt;
        if (this.onEnded) this.onEnded();
      }
    }, wallRemaining);
  }

  /**
   * Gain breakpoints for a clip: 0 → volume over fade-in, flat, then
   * volume → 0 over fade-out. Adjusted when playback joins mid-clip.
   */
  _applyGainAutomation(param, clip, volume, contentLen, when, contentOffset, rate) {
    const fadeOut = Math.min(clip.fade_out || 0, contentLen);
    const fadeIn = Math.min(clip.fade_in || 0, contentLen - fadeOut);
    const outStart = contentLen - fadeOut;

    // Breakpoints in content-seconds → (ctxTime, gain).
    const points = [];
    if (fadeIn > 0) {
      points.push([0, 0], [fadeIn, volume]);
    } else {
      points.push([0, volume]);
    }
    if (fadeOut > 0) {
      points.push([outStart, volume], [contentLen, 0]);
    } else {
      points.push([contentLen, volume]);
    }

    // Drop points before the join point; insert the current gain value.
    const toCtx = (contentSec) => when + (contentSec - contentOffset) / rate;
    const active = points.filter(([sec]) => sec >= contentOffset - 1e-9);

    if (active.length === 0) {
      param.setValueAtTime(0, when);
      return;
    }
    const first = active[0];
    if (first[0] > contentOffset + 1e-9) {
      // Interpolate gain at the join point.
      const prev = points[points.indexOf(first) - 1] || first;
      const span = first[0] - prev[0] || 1;
      const frac = (contentOffset - prev[0]) / span;
      const g = prev[1] + (first[1] - prev[1]) * Math.max(0, Math.min(1, frac));
      param.setValueAtTime(g, when);
    } else {
      param.setValueAtTime(first[1], toCtx(first[0]));
    }
    for (const [sec, g] of active.slice(1)) {
      param.linearRampToValueAtTime(g, toCtx(sec));
    }
  }
}
