"""Timeline document schema (v1) and server-side validation.

The timeline JSON is the single source of truth for the arrangement. The
client may send it back on save/export, but the server re-validates every
value against real asset metadata and configured limits — client-supplied
asset ids, durations and time ranges are never trusted.

Schema (version 1)
------------------
::

    {
      "schema_version": 1,
      "settings": {"master_volume": 1.0},
      "tracks": [
        {
          "id": "trk_x", "name": "Track 1", "order": 0,
          "volume": 1.0, "muted": false,
          "clips": [
            {
              "id": "clp_x", "asset_id": "<uuid>",
              "start": 0.0,        # timeline position (seconds)
              "trim_in": 0.0,      # source in-point (seconds)
              "trim_out": 5.0,     # source out-point (seconds, exclusive)
              "volume": 1.0, "muted": false,
              "fade_in": 0.0, "fade_out": 0.0
            }
          ]
        }
      ]
    }

Time unit: floating-point seconds. Durations of clips are derived
(``trim_out - trim_in``); a clip occupies ``[start, start + duration)``.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings

SCHEMA_VERSION = 1

# Conservative numeric ranges for timeline values.
MIN_TIME = 0.0
MAX_TIME = 24 * 3600.0  # absolute guard against absurd values
MAX_VOLUME = 4.0
MAX_FADE = 60.0
TIME_EPSILON = 0.05  # seconds of tolerance for float trim boundaries
MAX_NAME_LENGTH = 120

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class TimelineValidationError(Exception):
    """Raised when a timeline document is structurally or semantically invalid."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors) if errors else "invalid timeline")


@dataclass
class AssetInfo:
    """Minimal trusted asset metadata used for validation."""

    id: str
    duration: float
    status: str = "ready"
    project_id: str = ""


@dataclass
class ValidatedClip:
    id: str
    asset_id: str
    track_id: str
    start: float
    trim_in: float
    trim_out: float
    volume: float
    muted: bool
    fade_in: float
    fade_out: float

    @property
    def content_duration(self) -> float:
        return self.trim_out - self.trim_in

    @property
    def end(self) -> float:
        return self.start + self.content_duration


@dataclass
class ValidatedTimeline:
    schema_version: int
    master_volume: float
    tracks: list[dict[str, Any]] = field(default_factory=list)
    clips: list[ValidatedClip] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max((c.end for c in self.clips), default=0.0)

    def audible_clips(self) -> list[ValidatedClip]:
        muted_track_ids = {t["id"] for t in self.tracks if t["muted"]}
        return [
            c
            for c in self.clips
            if not c.muted and c.track_id not in muted_track_ids and c.volume > 0
        ]


def empty_timeline() -> dict[str, Any]:
    """A fresh timeline with one empty track."""
    return {
        "schema_version": SCHEMA_VERSION,
        "settings": {"master_volume": 1.0},
        "tracks": [
            {
                "id": "trk_1",
                "name": "ترک ۱",
                "order": 0,
                "volume": 1.0,
                "muted": False,
                "clips": [],
            }
        ],
    }


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_number(
    errors: list[str],
    value: Any,
    label: str,
    *,
    lo: float,
    hi: float,
) -> float | None:
    if not _is_number(value):
        errors.append(f"{label} must be a number")
        return None
    number = float(value)
    if number < lo - 1e-9 or number > hi + 1e-9:
        errors.append(f"{label} must be between {lo} and {hi}")
        return None
    return number


def validate_timeline(
    document: Any,
    assets: dict[str, AssetInfo],
    limits: dict[str, Any] | None = None,
) -> ValidatedTimeline:
    """Validate a timeline document. Returns a normalized structure or raises
    ``TimelineValidationError`` listing every problem found.

    ``assets`` maps asset UUID strings to trusted server-side metadata.
    """
    limits = limits or settings.BEATLAB_LIMITS
    errors: list[str] = []

    if not isinstance(document, dict):
        raise TimelineValidationError(["timeline must be an object"])

    version = document.get("schema_version")
    if version != SCHEMA_VERSION:
        errors.append(f"unsupported schema_version (expected {SCHEMA_VERSION})")

    settings_doc = document.get("settings")
    if settings_doc is None:
        settings_doc = {}
    if not isinstance(settings_doc, dict):
        errors.append("settings must be an object")
        settings_doc = {}
    master_volume = _check_number(
        errors, settings_doc.get("master_volume", 1.0), "settings.master_volume", lo=0.0, hi=MAX_VOLUME
    )
    if master_volume is None:
        master_volume = 1.0

    tracks_raw = document.get("tracks")
    if not isinstance(tracks_raw, list):
        raise TimelineValidationError(errors + ["tracks must be a list"])
    if not tracks_raw:
        errors.append("timeline must contain at least one track")
    if len(tracks_raw) > limits["max_tracks"]:
        errors.append(f"too many tracks (max {limits['max_tracks']})")

    max_export_duration = float(limits["max_export_duration"])
    max_clips = int(limits["max_clips_per_project"])

    validated_tracks: list[dict[str, Any]] = []
    validated_clips: list[ValidatedClip] = []
    seen_clip_ids: set[str] = set()
    seen_track_ids: set[str] = set()

    for track_index, track_raw in enumerate(tracks_raw):
        tlabel = f"tracks[{track_index}]"
        if not isinstance(track_raw, dict):
            errors.append(f"{tlabel} must be an object")
            continue

        track_id = track_raw.get("id")
        if not isinstance(track_id, str) or not _ID_RE.match(track_id):
            errors.append(f"{tlabel}.id is invalid")
            track_id = f"{tlabel}"
        if track_id in seen_track_ids:
            errors.append(f"duplicate track id '{track_id}'")
        seen_track_ids.add(track_id)

        name = track_raw.get("name", "")
        if not isinstance(name, str) or len(name) > MAX_NAME_LENGTH:
            errors.append(f"{tlabel}.name is invalid")
            name = str(name)[:MAX_NAME_LENGTH] if name else ""

        volume = _check_number(errors, track_raw.get("volume", 1.0), f"{tlabel}.volume", lo=0.0, hi=MAX_VOLUME)
        muted = track_raw.get("muted", False)
        if not isinstance(muted, bool):
            errors.append(f"{tlabel}.muted must be a boolean")
            muted = False
        order = track_raw.get("order", track_index)
        if not isinstance(order, int) or isinstance(order, bool):
            order = track_index

        clips_raw = track_raw.get("clips", [])
        if not isinstance(clips_raw, list):
            errors.append(f"{tlabel}.clips must be a list")
            clips_raw = []

        validated_tracks.append(
            {
                "id": track_id,
                "name": name,
                "order": order,
                "volume": volume if volume is not None else 1.0,
                "muted": muted,
            }
        )

        for clip_index, clip_raw in enumerate(clips_raw):
            clabel = f"{tlabel}.clips[{clip_index}]"
            if not isinstance(clip_raw, dict):
                errors.append(f"{clabel} must be an object")
                continue

            clip_id = clip_raw.get("id")
            if not isinstance(clip_id, str) or not _ID_RE.match(clip_id):
                errors.append(f"{clabel}.id is invalid")
                clip_id = f"bad_{clip_index}"
            if clip_id in seen_clip_ids:
                errors.append(f"duplicate clip id '{clip_id}'")
            seen_clip_ids.add(clip_id)

            asset_id_raw = clip_raw.get("asset_id")
            asset: AssetInfo | None = None
            try:
                asset_id = str(uuid.UUID(str(asset_id_raw)))
            except (ValueError, TypeError, AttributeError):
                errors.append(f"{clabel}.asset_id is not a valid asset reference")
                asset_id = str(asset_id_raw)
            else:
                asset = assets.get(asset_id)
                if asset is None:
                    errors.append(f"{clabel}.asset_id references an unknown or unauthorized asset")
                elif asset.status != "ready":
                    errors.append(f"{clabel}.asset_id references an asset that is not ready")

            start = _check_number(errors, clip_raw.get("start"), f"{clabel}.start", lo=MIN_TIME, hi=MAX_TIME)
            trim_in = _check_number(errors, clip_raw.get("trim_in"), f"{clabel}.trim_in", lo=MIN_TIME, hi=MAX_TIME)
            trim_out = _check_number(errors, clip_raw.get("trim_out"), f"{clabel}.trim_out", lo=MIN_TIME, hi=MAX_TIME)
            volume = _check_number(errors, clip_raw.get("volume", 1.0), f"{clabel}.volume", lo=0.0, hi=MAX_VOLUME)
            fade_in = _check_number(errors, clip_raw.get("fade_in", 0.0), f"{clabel}.fade_in", lo=0.0, hi=MAX_FADE)
            fade_out = _check_number(errors, clip_raw.get("fade_out", 0.0), f"{clabel}.fade_out", lo=0.0, hi=MAX_FADE)
            muted = clip_raw.get("muted", False)
            if not isinstance(muted, bool):
                errors.append(f"{clabel}.muted must be a boolean")
                muted = False

            # Trim range checks against *trusted* asset duration.
            if trim_in is not None and trim_out is not None:
                if trim_out <= trim_in:
                    errors.append(f"{clabel}: trim_out must be greater than trim_in")
                if asset is not None:
                    if trim_out > asset.duration + TIME_EPSILON:
                        errors.append(f"{clabel}: trim_out exceeds the asset duration")
                    if trim_in > asset.duration + TIME_EPSILON:
                        errors.append(f"{clabel}: trim_in exceeds the asset duration")

            clip_len = None
            if trim_in is not None and trim_out is not None and trim_out > trim_in:
                clip_len = trim_out - trim_in

            if clip_len is not None and fade_in is not None and fade_out is not None:
                if fade_in + fade_out > clip_len + 1e-6:
                    errors.append(f"{clabel}: fades are longer than the clip")

            if start is not None and clip_len is not None:
                if start + clip_len > max_export_duration + TIME_EPSILON:
                    errors.append(
                        f"{clabel}: clip ends beyond the maximum export duration "
                        f"({int(max_export_duration)}s)"
                    )

            validated_clips.append(
                ValidatedClip(
                    id=clip_id,
                    asset_id=asset_id,
                    track_id=track_id,
                    start=start if start is not None else 0.0,
                    trim_in=trim_in if trim_in is not None else 0.0,
                    trim_out=trim_out if trim_out is not None else 0.0,
                    volume=volume if volume is not None else 1.0,
                    muted=muted,
                    fade_in=fade_in if fade_in is not None else 0.0,
                    fade_out=fade_out if fade_out is not None else 0.0,
                )
            )

    if len(validated_clips) > max_clips:
        errors.append(f"too many clips (max {max_clips})")

    if errors:
        raise TimelineValidationError(errors)

    return ValidatedTimeline(
        schema_version=SCHEMA_VERSION,
        master_volume=master_volume,
        tracks=validated_tracks,
        clips=validated_clips,
    )
