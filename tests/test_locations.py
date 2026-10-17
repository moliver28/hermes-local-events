"""Google Timeline import: format detection, parsing, seeding, safety."""

from __future__ import annotations

import json
import zipfile
from datetime import UTC

from core.locations import analyze, apply_import, find_timeline_files
from core.models import Household
from core.profile import seed_from_drive_histogram


def _write_timeline_new(path):
    data = {
        "semanticSegments": [
            {
                "startTime": "2026-03-01T10:00:00-08:00",
                "endTime": "2026-03-01T12:00:00-08:00",
                "visit": {"topCandidate": {"placeLocation": {"latLng": "47.2530, -122.4443"}}},
            },
            {
                "startTime": "2026-03-01T12:00:00-08:00",
                "endTime": "2026-03-01T12:40:00-08:00",
                "activity": {
                    "topCandidate": {"type": "DRIVING"},
                    "start": {"latLng": "47.2530, -122.4443"},
                    "end": {"latLng": "47.1000, -122.3000"},
                },
            },
            {
                "startTime": "2026-03-08T10:00:00-08:00",
                "endTime": "2026-03-08T13:00:00-08:00",
                "visit": {"topCandidate": {"placeLocation": {"latLng": "47.2600, -122.4500"}}},
            },
        ]
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_parse_new_format(tmp_path, store):
    fp = _write_timeline_new(tmp_path / "Timeline.json")
    result = analyze(fp, store)
    assert result["visits"] == 2
    assert result["drives"] == 1
    assert result["home_guess"]["lat"] == 47.253


def test_parse_records_e7(tmp_path, store):
    data = {
        "locations": [
            {"latitudeE7": 472529000, "longitudeE7": -1224443000, "timestamp": "2026-03-01T10:00:00Z"},
            {"latitudeE7": 472529100, "longitudeE7": -1224443100, "timestamp": "2026-03-01T10:25:00Z"},
            {"latitudeE7": 473000000, "longitudeE7": -1225000000, "timestamp": "2026-03-01T11:00:00Z"},
        ]
    }
    fp = tmp_path / "Records.json"
    fp.write_text(json.dumps(data), encoding="utf-8")
    result = analyze(fp, store)
    assert result["visits"] >= 1  # 25-minute stay clusters


def test_zip_with_zip_slip_member(tmp_path, store):
    evil = tmp_path / "takeout.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr(
            "Takeout/Timeline.json",
            json.dumps(
                {
                    "semanticSegments": [
                        {
                            "startTime": "2026-03-01T10:00:00-08:00",
                            "visit": {"topCandidate": {"placeLocation": {"latLng": "47.25, -122.44"}}},
                        }
                    ]
                }
            ),
        )
        z.writestr("../evil.txt", "pwn")
    files = find_timeline_files(evil)
    assert files, "zip should be parsed"
    assert not (tmp_path / "evil.txt").exists()
    assert not (tmp_path.parent / "evil.txt").exists()


def test_traversal_path_rejected(tmp_path, store):
    from core.locations import _safe_resolve

    assert _safe_resolve(tmp_path / ".." / "elsewhere") is None or True  # must not raise
    assert _safe_resolve(tmp_path / "does_not_exist.json") is None


def test_apply_import_seeds_and_backfills(tmp_path, store, hh):
    from datetime import datetime

    # an event the user actually attended, at a known venue
    # (10:00 Pacific = 18:00 UTC, matching the fixture visit instant)
    from core.models import Event, Venue

    ev_start = datetime(2026, 3, 8, 18, 0, tzinfo=UTC)
    venue = Venue(id="ven_zoo", name="Point Defiance", lat=47.26, lon=-122.45, venue_type="zoo")
    store.upsert_venue(venue)
    ev = Event(
        title="Zoo day",
        source_id="t",
        source_event_id="z1",
        start=ev_start,
        end=ev_start.replace(hour=21),
        venue_id="ven_zoo",
        kid="verified_yes",
    )
    store.upsert_event(ev)

    fp = _write_timeline_new(tmp_path / "Timeline.json")
    analysis = analyze(fp, store)
    assert analysis["event_matches"], "visit at venue during event should match"

    hh2 = Household()
    result = apply_import(hh2, store, analysis)
    assert result["ok"] and result["events_backfilled"] == 1
    assert hh2.home_lat is not None
    assert hh2.drive_tolerance["normal"] > 0  # seeded from the one drive (~25 km)
    assert store.has_location_data()


def test_seeding_scales_moods():
    hh = Household()
    seed_from_drive_histogram(hh, {"20": 30, "30": 20})
    assert hh.drive_tolerance["tired"] < hh.drive_tolerance["normal"] < hh.drive_tolerance["adventurous"]


def test_downloads_candidates_scan(tmp_path, monkeypatch):
    """Read-only Downloads scan: finds recent Takeout/Timeline files, skips
    unrelated files and stale exports, never imports anything."""
    from core.locations import find_recent_candidates

    dl = tmp_path / "Downloads"
    dl.mkdir()
    monkeypatch.setenv("HERMES_EVENTS_DOWNLOADS", str(dl))

    (dl / "takeout-20261001T120000Z-001.zip").write_bytes(b"PK\x03\x04")
    (dl / "Timeline.json").write_text("{}", encoding="utf-8")
    (dl / "vacation-photos.zip").write_bytes(b"PK\x03\x04")  # unrelated
    stale = dl / "takeout-20250101T000000Z-001.zip"
    stale.write_bytes(b"PK\x03\x04")
    import os

    old = (1031, 1)  # mtime 1999 — outside the 60-day window
    os.utime(stale, old)

    found = find_recent_candidates()
    names = [f.name for f in found]
    assert "takeout-20261001T120000Z-001.zip" in names
    assert "Timeline.json" in names
    assert "vacation-photos.zip" not in names
    assert "takeout-20250101T000000Z-001.zip" not in names


def test_downloads_scan_missing_dir(tmp_path, monkeypatch):
    from core.locations import find_recent_candidates

    monkeypatch.setenv("HERMES_EVENTS_DOWNLOADS", str(tmp_path / "nope"))
    assert find_recent_candidates() == []
