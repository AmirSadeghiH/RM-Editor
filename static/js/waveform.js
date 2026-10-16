/**
 * Waveform rendering on canvas from peak data.
 *
 * Peaks are [min, max] pairs in [-1, 1] per bucket — either extracted
 * server-side (asset.waveform) or computed locally from a decoded
 * AudioBuffer as a fallback. Rendering is resolution-independent (DPR aware)
 * and only redraws when asked by the timeline view.
 */

/** Compute peaks from a decoded AudioBuffer (fallback path). */
export function peaksFromAudioBuffer(audioBuffer, buckets = 800) {
  const channel = audioBuffer.getChannelData(0);
  const perBucket = Math.max(1, Math.ceil(channel.length / buckets));
  const peaks = [];
  for (let i = 0; i < channel.length; i += perBucket) {
    let lo = 1;
    let hi = -1;
    const end = Math.min(channel.length, i + perBucket);
    for (let j = i; j < end; j += 1) {
      const s = channel[j];
      if (s < lo) lo = s;
      if (s > hi) hi = s;
    }
    peaks.push([Math.round(lo * 1000) / 1000, Math.round(hi * 1000) / 1000]);
  }
  return { version: 1, num_buckets: peaks.length, peaks };
}

/**
 * Draw clip waveform: maps visible pixels to source-time buckets in
 * [trimIn, trimOut] of the source.
 *
 * @param {HTMLCanvasElement} canvas
 * @param {{peaks: number[][]}} waveform
 * @param {{trimIn:number, trimOut:number, duration:number, color?:string, muted?:boolean}} opts
 */
export function drawClipWaveform(canvas, waveform, opts) {
  const { trimIn, trimOut, duration, color = "rgba(255,255,255,0.85)", muted = false } = opts;
  const dpr = window.devicePixelRatio || 1;
  const cssWidth = canvas.clientWidth || canvas.width;
  const cssHeight = canvas.clientHeight || canvas.height;
  if (cssWidth <= 0 || cssHeight <= 0) return;

  const targetW = Math.round(cssWidth * dpr);
  const targetH = Math.round(cssHeight * dpr);
  if (canvas.width !== targetW || canvas.height !== targetH) {
    canvas.width = targetW;
    canvas.height = targetH;
  }

  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, targetW, targetH);
  if (!waveform || !waveform.peaks || !waveform.peaks.length) return;

  const peaks = waveform.peaks;
  const sourceLen = Math.max(trimOut - trimIn, 1e-6);
  const mid = targetH / 2;
  const scale = (targetH * 0.42);

  ctx.fillStyle = muted ? "rgba(255,255,255,0.28)" : color;

  // One vertical min/max line per device pixel column.
  for (let x = 0; x < targetW; x += 1) {
    const t0 = trimIn + (x / targetW) * sourceLen;
    const t1 = trimIn + ((x + 1) / targetW) * sourceLen;
    const b0 = Math.floor((t0 / duration) * peaks.length);
    const b1 = Math.max(b0 + 1, Math.ceil((t1 / duration) * peaks.length));

    let lo = 0;
    let hi = 0;
    for (let b = Math.max(0, b0); b < Math.min(peaks.length, b1); b += 1) {
      if (peaks[b][0] < lo) lo = peaks[b][0];
      if (peaks[b][1] > hi) hi = peaks[b][1];
    }
    const yTop = mid - hi * scale;
    const yBot = mid - lo * scale;
    ctx.fillRect(x, yTop, 1, Math.max(1, yBot - yTop));
  }
}
