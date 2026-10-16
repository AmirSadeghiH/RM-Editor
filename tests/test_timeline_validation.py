"""Unit tests for timeline document validation (schema v1)."""
from __future__ import annotations

import copy

import pytest

from studio.timeline import (
    AssetInfo,
    TimelineValidationError,
    empty_timeline,
    validate_timeline,
)

ASSET_ID = "11111111-2222-3333-4444-555555555555"
ASSETS = {ASSET_ID: AssetInfo(id=ASSET_ID, duration=10.0, status="ready")}

LIMITS = {
    "max_upload_bytes": 25 * 1024 * 1024,
    "max_source_duration": 600,
    "max_assets_per_project": 8,
    "max_clips_per_project": 30,
    "max_tracks": 8,
    "max_export_duration": 600,
}


def make_doc(**clip_overrides) -> dict:
    clip = {
        "id": "clp_1",
        "asset_id": ASSET_ID,
        "start": 1.5,
        "trim_in": 0.5,
        "trim_out": 3.5,
        "volume": 1.0,
        "muted": False,
        "fade_in": 0.25,
        "fade_out": 0.25,
    }
    clip.update(clip_overrides)
    return {
        "schema_version": 1,
        "settings": {"master_volume": 1.0},
        "tracks": [
            {
                "id": "trk_1",
                "name": "Track",
                "order": 0,
                "volume": 1.0,
                "muted": False,
                "clips": [clip],
            }
        ],
    }


def validate(doc, assets=ASSETS):
    return validate_timeline(doc, assets, LIMITS)


class TestValidDocuments:
    def test_empty_timeline_is_valid(self):
        result = validate(empty_timeline())
        assert result.duration == 0.0
        assert result.clips == []

    def test_valid_clip(self):
        result = validate(make_doc())
        assert result.duration == pytest.approx(1.5 + 3.0)
        assert result.clips[0].trim_out == 3.5
        assert len(result.audible_clips()) == 1

    def test_muted_clip_not_audible(self):
        result = validate(make_doc(muted=True))
        assert result.audible_clips() == []

    def test_muted_track_not_audible(self):
        doc = make_doc()
        doc["tracks"][0]["muted"] = True
        result = validate(doc)
        assert result.audible_clips() == []

    def test_zero_volume_not_audible(self):
        result = validate(make_doc(volume=0))
        assert result.audible_clips() == []

    def test_clip_at_zero(self):
        result = validate(make_doc(start=0, trim_in=0, trim_out=0.05, fade_in=0, fade_out=0))
        assert result.duration == pytest.approx(0.05)


class TestInvalidDocuments:
    def test_wrong_schema_version(self):
        doc = make_doc()
        doc["schema_version"] = 2
        with pytest.raises(TimelineValidationError) as exc:
            validate(doc)
        assert any("schema_version" in e for e in exc.value.errors)

    def test_not_an_object(self):
        with pytest.raises(TimelineValidationError):
            validate("nope")

    def test_tracks_must_be_list(self):
        with pytest.raises(TimelineValidationError):
            validate({"schema_version": 1, "tracks": {}})

    def test_empty_tracks(self):
        with pytest.raises(TimelineValidationError):
            validate({"schema_version": 1, "settings": {}, "tracks": []})

    def test_too_many_tracks(self):
        doc = make_doc()
        doc["tracks"] = [
            {"id": f"trk_{i}", "name": "x", "order": i, "volume": 1, "muted": False, "clips": []}
            for i in range(9)
        ]
        with pytest.raises(TimelineValidationError) as exc:
            validate(doc)
        assert any("too many tracks" in e for e in exc.value.errors)

    def test_unknown_asset(self):
        doc = make_doc(asset_id="99999999-8888-7777-6666-555555555555")
        with pytest.raises(TimelineValidationError) as exc:
            validate(doc)
        assert any("unknown or unauthorized" in e for e in exc.value.errors)

    def test_malformed_asset_id(self):
        with pytest.raises(TimelineValidationError) as exc:
            validate(make_doc(asset_id="not-a-uuid"))
        assert any("asset_id" in e for e in exc.value.errors)

    def test_negative_start(self):
        with pytest.raises(TimelineValidationError):
            validate(make_doc(start=-1))

    def test_trim_out_not_greater_than_trim_in(self):
        with pytest.raises(TimelineValidationError) as exc:
            validate(make_doc(trim_in=3.0, trim_out=3.0))
        assert any("trim_out must be greater" in e for e in exc.value.errors)

    def test_trim_out_beyond_asset_duration(self):
        with pytest.raises(TimelineValidationError) as exc:
            validate(make_doc(trim_in=0, trim_out=12.0))
        assert any("exceeds the asset duration" in e for e in exc.value.errors)

    def test_fades_longer_than_clip(self):
        with pytest.raises(TimelineValidationError) as exc:
            validate(make_doc(trim_in=0, trim_out=1.0, fade_in=0.7, fade_out=0.7))
        assert any("fades are longer" in e for e in exc.value.errors)

    def test_volume_out_of_range(self):
        with pytest.raises(TimelineValidationError):
            validate(make_doc(volume=99))

    def test_volume_must_be_number(self):
        with pytest.raises(TimelineValidationError):
            validate(make_doc(volume="loud"))

    def test_muted_must_be_boolean(self):
        with pytest.raises(TimelineValidationError):
            validate(make_doc(muted="yes"))

    def test_duplicate_clip_ids(self):
        doc = make_doc()
        doc["tracks"][0]["clips"].append(copy.deepcopy(doc["tracks"][0]["clips"][0]))
        with pytest.raises(TimelineValidationError) as exc:
            validate(doc)
        assert any("duplicate clip id" in e for e in exc.value.errors)

    def test_too_many_clips(self):
        doc = make_doc()
        clips = []
        for i in range(31):
            clips.append(
                {
                    "id": f"clp_{i}",
                    "asset_id": ASSET_ID,
                    "start": float(i),
                    "trim_in": 0.0,
                    "trim_out": 0.2,
                    "volume": 1.0,
                    "muted": False,
                    "fade_in": 0,
                    "fade_out": 0,
                }
            )
        doc["tracks"][0]["clips"] = clips
        with pytest.raises(TimelineValidationError) as exc:
            validate(doc)
        assert any("too many clips" in e for e in exc.value.errors)

    def test_clip_beyond_max_duration(self):
        with pytest.raises(TimelineValidationError) as exc:
            validate(make_doc(start=599, trim_in=0, trim_out=5))
        assert any("maximum export duration" in e for e in exc.value.errors)

    def test_invalid_clip_id_format(self):
        with pytest.raises(TimelineValidationError):
            validate(make_doc(id="bad id with spaces!"))

    def test_asset_not_ready(self):
        assets = {ASSET_ID: AssetInfo(id=ASSET_ID, duration=10.0, status="processing")}
        with pytest.raises(TimelineValidationError) as exc:
            validate(make_doc(), assets)
        assert any("not ready" in e for e in exc.value.errors)

    def test_multiple_errors_reported(self):
        doc = make_doc(start=-5, trim_in=8, trim_out=2)
        with pytest.raises(TimelineValidationError) as exc:
            validate(doc)
        assert len(exc.value.errors) >= 2
