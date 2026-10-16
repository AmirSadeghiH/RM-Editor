"""FFmpeg-backed audio processing for BeatLab.

Modules:
  * ``ffprobe`` — container/codec inspection of uploaded audio.
  * ``peaks``   — server-side waveform peak extraction.
  * ``filters`` — pure functions building validated FFmpeg filter graphs.
  * ``render``  — subprocess execution of export renders.

All subprocess calls use argument lists with ``shell=False``. User input is
never interpolated into a shell string; numeric filter parameters are
formatted from validated Python floats.
"""
