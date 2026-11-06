#!/usr/bin/env python3
"""Read-only history and mirror checks. Success is structural, not live-source QA."""
from __future__ import annotations
import argparse
from datetime import date
import json
from pathlib import Path
import re
import subprocess
from typing import Any

class AuditError(ValueError):
    pass

def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditError(message)

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON key: ' + key)
        result[key] = value
    return result

def loads(text: str | bytes) -> Any:
    def nonfinite(value):
        raise AuditError('Non-finite JSON number: ' + value)
    return json.loads(text, object_pairs_hook=unique_object, parse_constant=nonfinite)

def iso_day(value: Any) -> str:
    require(isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) is not None, 'Invalid date format')
    require(date.fromisoformat(value).isoformat() == value, 'Invalid calendar date')
    return value

def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE)

def exposures(data: Any) -> set[str]:
    require(isinstance(data, dict), 'History object must be a mapping')
    result = set(data.get('recommended', {}))
    for track in data.get('tracks', []):
        require(isinstance(track, dict) and isinstance(track.get('canonical_id'), str), 'Historical track lacks canonical_id')
        result.add(track['canonical_id'])
    return result

def history_ids(root: Path, snapshot: Path | None = None) -> set[str]:
    """Include retired manifests AND states; unparseable history fails closed."""
    result = set()
    if snapshot is not None:
        rows = loads((snapshot / 'history/index.json').read_bytes())
        for row in rows:
            path = row['path']
            require(re.fullmatch(r'[0-9a-f]{40}', row['sha']) is not None, 'Invalid historical blob SHA')
            if path.endswith('.json') and (path.startswith('recommendations/') or path == 'state/tracks.json'):
                result.update(exposures(loads((snapshot / 'history/blobs' / row['sha']).read_bytes())))
        require(bool(result), 'No music historical evidence in supplied snapshot')
        return result
    require(git(root, 'rev-parse', '--is-shallow-repository').strip() == b'false', 'Full Git history required; fetch-depth: 0')
    objects = git(root, 'rev-list', '--objects', 'HEAD', '--', 'recommendations', 'state/tracks.json').decode().splitlines()
    for item in objects:
        parts = item.split(' ', 1)
        if len(parts) != 2 or not parts[1].endswith('.json'):
            continue
        sha, path = parts
        if git(root, 'cat-file', '-t', sha).strip() == b'blob':
            result.update(exposures(loads(git(root, 'cat-file', 'blob', sha))))
    require(bool(result), 'No music historical evidence found')
    return result

def preserve_state(old: dict, new: dict) -> None:
    require(all(key in new and new[key] == value for key, value in old.items()), 'Existing state entries were deleted or changed')

def source_url(note: str) -> str | None:
    front = re.match(r'\A---\n(.*?)\n---(?:\n|$)', note, re.S)
    if front:
        match = re.search(r'^source_url:\s*[\"\']?(https?://[^\s\"\']+)', front.group(1), re.M)
        if match:
            return match.group(1)
    for line in note.splitlines():
        plain = line.replace('**', '').strip().removeprefix('- ').strip()
        if re.match(r'^(?:source(?: url)?|official page \+ transcript|official video|official episode page)\s*:', plain, re.I):
            match = re.search(r'https?://[^\s<>]+', plain)
            if match:
                return match.group(0).rstrip(').,;\"')
    return None

def audit(root: Path, vault: Path | None, ever: set[str], base_ref: str | None = None) -> dict:
    state_doc = loads((root / 'state/tracks.json').read_bytes())
    require(isinstance(state_doc, dict), 'State root must be an object')
    require(state_doc.get('schema_version') == 1 and state_doc.get('provider') == 'apple_music', 'Wrong state schema/provider')
    state = state_doc.get('recommended')
    require(isinstance(state, dict) and bool(state), 'State must be a non-empty mapping')
    for cid, value in state.items():
        require(isinstance(cid, str) and '::' in cid and all(cid.split('::', 1)), 'Invalid canonical ID')
        if type(value) is int:
            require(value >= 1, 'Invalid legacy count')
        else:
            require(isinstance(value, dict) and type(value.get('count')) is int and value['count'] >= 1, 'Invalid history count')
            require(iso_day(value.get('first_recommended')) <= iso_day(value.get('last_recommended')), 'Reversed history dates')
    require(ever <= state.keys(), 'Historical exposures missing from state: ' + repr(sorted(ever - state.keys())))
    if base_ref:
        require(re.fullmatch(r'[0-9a-f]{40}', base_ref) is not None, 'base-ref must be a full commit SHA')
        old = loads(git(root, 'show', base_ref + ':state/tracks.json'))['recommended']
        preserve_state(old, state)
    paths = sorted((root / 'recommendations').rglob('*.json'))
    require(bool(paths), 'No manifests found')
    canonical, song_ids, names = set(), set(), set()
    legacy_urls = []
    recording_rows = []
    from recording_history import occurrences, check_rows, incident
    for path in paths:
        recording_rows.extend(occurrences(path.read_bytes(), str(path)))
        doc = loads(path.read_bytes()); require(isinstance(doc, dict), 'Manifest root must be an object')
        day = iso_day(doc.get('date'))
        require(path.relative_to(root).as_posix() == f'recommendations/{day[:4]}/{day[5:7]}/{day}.json', 'Manifest date/path mismatch')
        require(doc.get('schema_version') == 1 and doc.get('provider') == 'apple_music', 'Wrong manifest schema/provider')
        tracks = doc.get('tracks'); require(isinstance(tracks, list) and len(tracks) == 5, 'Every manifest must contain exactly five tracks')
        require(all(isinstance(t, dict) and type(t.get('position')) is int for t in tracks), 'Invalid track/position type')
        require([t['position'] for t in tracks] == [1, 2, 3, 4, 5], 'Wrong position order')
        today_ids = []
        for track in tracks:
            cid = track['canonical_id']; am = track['apple_music']; sid = am.get('song_id')
            require(isinstance(sid, str) and re.fullmatch(r'[0-9]+', sid) is not None, 'song_id must be a decimal string')
            require(cid == track['artist'] + '::' + track['title'], 'canonical_id mismatch')
            require(cid in state, 'Manifest not registered in state')
            require(cid not in canonical, 'Repeated canonical ID')
            match = re.fullmatch(r'https://music\.apple\.com/cn/song/(?:[^/\s]+/)?([0-9]+)', str(am.get('url', '')))
            require(match is not None and match.group(1) == sid and am.get('storefront') == 'cn', f'{path.name}: wrong CN URL/ID/storefront for {sid}')
            if am['url'] != 'https://music.apple.com/cn/song/' + sid:
                legacy_urls.append({'manifest': path.name, 'song_id': sid, 'issue': 'schema-valid slug URL; not requested shortest /cn/song/ID form'})
            canonical.add(cid); song_ids.add(sid); today_ids.append(sid)
        names.add(path.name)
        if vault:
            mirror = vault / 'Music/Daily/manifests' / path.name
            require(mirror.is_file() and mirror.read_bytes() == path.read_bytes(), 'Mirror differs: ' + path.name)
            note_path = vault / 'Music/Daily' / (day + '.md')
            require(note_path.is_file(), 'Missing music note: ' + day)
            ids = re.findall(r'https://music\.apple\.com/cn/song/(?:[^/\s]+/)?([0-9]+)', note_path.read_text(encoding='utf-8'))
            require(ids == today_ids, 'Music note IDs/order differ: ' + day)
    known_incidents = check_rows(recording_rows, incident(root))
    result = {'known_incidents': known_incidents, 'publication_authorized': False, 'status': 'passed_structural_with_url_warnings' if legacy_urls else 'passed_structural_checks', 'legacy_url_warnings': legacy_urls, 'manifests': len(paths), 'current_tracks': len(canonical), 'state_entries': len(state), 'historical_exposures_checked': len(ever), 'retired_entries_retained': len(state.keys() - canonical), 'cross_repository': 'not_checked', 'unverified': ['live source availability and metadata', 'semantic quality and artist diversity', 'local Obsidian sync', 'task scheduling and delivery']}
    if vault:
        require((vault / 'Music/Daily/state/tracks.json').read_bytes() == (root / 'state/tracks.json').read_bytes(), 'State mirrors differ')
        require({p.name for p in (vault / 'Music/Daily/manifests').glob('*.json')} == names, 'Orphan or missing manifest mirror')
        result['cross_repository'] = 'passed_byte_exact'
        english = [loads(x) for x in (vault / '.recommendation/history/english.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
        pairs = [(iso_day(r.get('date')), r['source_url']) for r in english]
        require(len(pairs) == len(set(pairs)), 'Duplicate English append')
        enroot = vault / '50-courses/English'; notes = list((enroot / 'Daily').glob('????-??-??.md')) + list((enroot / 'Weekly').rglob('????-??-??.md'))
        for path in notes:
            url = source_url(path.read_text(encoding='utf-8'))
            require(url is not None and (iso_day(path.stem), url) in pairs, 'Published English source missing history: ' + str(path.relative_to(vault)))
            if path.parent.name == 'Daily':
                for component in ('Translation', 'Vocabulary'):
                    require((enroot / component / path.name).is_file(), 'Incomplete English bundle: ' + path.stem + '/' + component)
        result['english_notes_with_history'] = len(notes)
    return result

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--music-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--vault-root', type=Path)
    parser.add_argument('--history-snapshot', type=Path, help='Offline audit snapshot with history/index.json and blobs; otherwise use full Git HEAD history')
    parser.add_argument('--base-ref', help='Optional full commit SHA for exact preservation of existing state values')
    args = parser.parse_args()
    try:
        from recording_history import audit_history
        recording_result = audit_history(args.music_root, args.vault_root)
        result = audit(args.music_root, args.vault_root, history_ids(args.music_root, args.history_snapshot), args.base_ref)
    except (AuditError, OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__, 'error': str(exc)}, ensure_ascii=False, indent=2))
        return 1
    result['recording_history'] = recording_result
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
