#!/usr/bin/env python3
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REC = ROOT / "recommendations"
STATE = ROOT / "state" / "tracks.json"
SONG_URL_RE = re.compile(
    r"^https://music\.apple\.com/[A-Za-z]{2}/song/(?:[^/]+/)?([0-9]+)(?:\?.*)?$"
)


def fail(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(1)


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        fail(f"{path}: {e}")


def validate_manifest(path):
    data = load_json(path)
    if data.get("provider") != "apple_music":
        fail(f"{path}: provider must be apple_music")
    tracks = data.get("tracks")
    if not isinstance(tracks, list) or len(tracks) != 5:
        fail(f"{path}: tracks must contain exactly 5 items")
    ids, positions = [], []
    for t in tracks:
        for k in ("position", "title", "artist", "canonical_id", "apple_music"):
            if k not in t:
                fail(f"{path}: missing track field {k}")
        am = t["apple_music"]
        if not isinstance(am, dict):
            fail(f"{path}: apple_music must be an object")
        url = am.get("url")
        song_id = am.get("song_id")
        if not isinstance(url, str):
            fail(f"{path}: apple_music.url is required")
        match = SONG_URL_RE.fullmatch(url)
        if not match:
            fail(
                f"{path}: apple_music.url must be a direct Apple Music song URL, "
                "not a search/album/artist/playlist URL"
            )
        if not isinstance(song_id, str) or not song_id.isdigit():
            fail(f"{path}: apple_music.song_id must be a numeric string")
        if match.group(1) != song_id:
            fail(
                f"{path}: apple_music.song_id {song_id} does not match URL id "
                f"{match.group(1)}"
            )
        ids.append(t["canonical_id"])
        positions.append(t["position"])
    if len(ids) != len(set(ids)):
        fail(f"{path}: duplicate canonical_id within manifest")
    if sorted(positions) != [1, 2, 3, 4, 5]:
        fail(f"{path}: positions must be 1..5")
    return ids


def main():
    seen = set()
    for path in sorted(REC.rglob("*.json")) if REC.exists() else []:
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

    print(f"OK: {len(seen)} unique recommended tracks validated")


if __name__ == "__main__":
    main()
