#!/usr/bin/env python3
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REC = ROOT / "recommendations"
STATE = ROOT / "state" / "tracks.json"
STRICT_FROM = "2026-09-11"
SONG_URL_RE = re.compile(
    r"^https://music\.apple\.com/cn/song/(?:[^/]+/)?([0-9]+)(?:\?.*)?$"
)


def fail(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(1)


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        fail(f"{path}: {e}")


def require_text(path, track, key, min_len=1):
    value = track.get(key)
    if not isinstance(value, str) or len(value.strip()) < min_len:
        fail(f"{path}: {key} must be a non-empty string of at least {min_len} characters")


def validate_manifest(path):
    data = load_json(path)
    date = data.get("date")
    if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        fail(f"{path}: invalid or missing date")
    strict = date >= STRICT_FROM

    if data.get("provider") != "apple_music":
        fail(f"{path}: provider must be apple_music")
    from recording_withdrawal import positions as withdrawal_positions
    expected_positions = withdrawal_positions(ROOT, date, path.read_bytes())
    tracks = data.get("tracks")
    if not isinstance(tracks, list) or len(tracks) != len(expected_positions):
        fail(f"{path}: tracks must match the standard five or exact authorized withdrawal")

    ids, positions = [], []
    for t in tracks:
        for k in ("position", "title", "artist", "canonical_id", "apple_music"):
            if k not in t:
                fail(f"{path}: missing track field {k}")

        require_text(path, t, "title")
        require_text(path, t, "artist")
        require_text(path, t, "canonical_id")

        if strict:
            require_text(path, t, "album")
            require_text(path, t, "analysis_summary", 35)
            require_text(path, t, "listening_cue", 20)

        am = t["apple_music"]
        if not isinstance(am, dict):
            fail(f"{path}: apple_music must be an object")
        url = am.get("url")
        song_id = am.get("song_id")
        storefront = am.get("storefront")
        if not isinstance(url, str):
            fail(f"{path}: apple_music.url is required")
        match = SONG_URL_RE.fullmatch(url)
        if not match:
            fail(
                f"{path}: apple_music.url must be a direct China Apple Music song URL "
                "under https://music.apple.com/cn/song/..."
            )
        if not isinstance(song_id, str) or not song_id.isdigit():
            fail(f"{path}: apple_music.song_id must be a numeric string")
        if storefront != "cn":
            fail(f"{path}: apple_music.storefront must be cn")
        if match.group(1) != song_id:
            fail(
                f"{path}: apple_music.song_id {song_id} does not match URL id "
                f"{match.group(1)}"
            )
        ids.append(t["canonical_id"])
        positions.append(t["position"])

    if len(ids) != len(set(ids)):
        fail(f"{path}: duplicate canonical_id within manifest")
    if sorted(positions) != expected_positions:
        fail(f"{path}: positions must be 1..5")
    return ids


def main():
    seen = set()
    manifests = sorted(REC.rglob("*.json")) if REC.exists() else []
    if not manifests:
        fail("no recommendation manifests found")

    for path in manifests:
        ids = validate_manifest(path)
        dup = seen.intersection(ids)
        if dup:
            fail(f"{path}: canonical_id already appeared earlier: {sorted(dup)}")
        seen.update(ids)

    if STATE.exists():
        state = load_json(STATE)
        rec = state.get("recommended", {})
        if not isinstance(rec, dict):
            fail("state/tracks.json: recommended must be an object")
        missing = seen.difference(rec.keys())
        if missing:
            fail(f"state/tracks.json missing manifest ids: {sorted(missing)}")
    else:
        fail("state/tracks.json is required")

    from recording_history import audit_history
    try:
        history = audit_history(ROOT)
    except Exception as exc:
        fail(str(exc))
    print(json.dumps(history, ensure_ascii=False))
    print(f"OK: {len(seen)} unique recommended tracks validated for China storefront")


if __name__ == "__main__":
    main()
