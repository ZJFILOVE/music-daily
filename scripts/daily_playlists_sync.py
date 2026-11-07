#!/usr/bin/env python3
"""Publish dated Daily 5 playlists using cloud credentials or the existing local session."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import urllib.parse
import apple_music_sync as common

PREFIX = 'ChatGPT Daily 5 · '

def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')
        name = f.name
    os.chmod(name, 0o600)
    os.replace(name, path)

def plans(repo, client, date=None):
    days = []
    for p in sorted((repo / 'recommendations').glob('*/*/*.json')):
        if date is not None and p.stem != date:
            continue
        day = json.loads(p.read_text())
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', p.stem):
            raise RuntimeError('Invalid date')
        tracks = sorted(day['tracks'], key=lambda t: t['position'])
        expected = [1, 2, 3, 4, 5]
        if p.stem == '2026-11-06':
            from recording_withdrawal import positions
            expected = positions(repo, p.stem, p.read_bytes())
        if len(tracks) != len(expected):
            raise RuntimeError(f'{p.stem}: expected standard five or exact authorized withdrawal')
        for t in tracks:
            am = t['apple_music']
            if am['storefront'] != 'cn' or not re.fullmatch(r'\d+', str(am['song_id'])):
                raise RuntimeError('Invalid catalog identity')
        days.append((p.stem, tracks))
    ids = list(dict.fromkeys(t['apple_music']['song_id'] for _, ts in days for t in ts))
    available = set()
    for i in range(0, len(ids), 100):
        _, data = client.request('/v1/catalog/cn/songs?' + urllib.parse.urlencode({'ids': ','.join(ids[i:i+100])}))
        if not isinstance(data.get('data'), list):
            raise RuntimeError('Catalog preflight failed')
        available.update(t['id'] for t in data['data'])
    return [(date, tracks, [t['apple_music']['song_id'] for t in tracks if t['apple_music']['song_id'] in available]) for date, tracks in days]

def sync(args):
    from publication_gate import authorize_args, recheck
    authorization = authorize_args(args) if args.execute else None
    auth = common.load_auth(args.no_wrapper)
    client = common.Client(args.api_host, auth)
    days = plans(args.repo, client, authorization['date'] if authorization else None)
    if authorization:
        if len(days) != 1 or days[0][2] != authorization['song_ids']:
            raise RuntimeError('Authorized daily selection unavailable or changed')
    playlists = client.collection('/v1/me/library/playlists?limit=100')
    state = json.loads(args.state_file.read_text()) if args.state_file.exists() else {'days': {}}
    state.setdefault('days', {})
    index = []
    for date, tracks, ids in days:
        name = PREFIX + date
        matches = [p for p in playlists if p.get('attributes', {}).get('name') == name]
        if len(matches) > 1:
            raise RuntimeError(f'{date}: duplicate playlist names; stopped')
        playlist = matches[0] if matches else None
        checkpoint = state['days'].get(date, {})
        if checkpoint.get('playlist_id') and playlist and checkpoint['playlist_id'] != playlist['id']:
            raise RuntimeError(f'{date}: playlist identity changed')
        unavailable = [{'id': t['apple_music']['song_id'], 'title': t['title'], 'artist': t['artist']} for t in tracks if t['apple_music']['song_id'] not in ids]
        note = f'{date} 每日五首，按导听顺序排列。'
        if unavailable:
            note += ' 国区暂不可用：' + '；'.join(t['artist'] + '《' + t['title'] + '》' for t in unavailable) + '。'
        note += f' 导听：https://github.com/ZJFILOVE/obsidian-vault/blob/main/Music/Daily/{date}.md'
        if not args.execute:
            print(f'{date}: {"exists" if playlist else "create"}, {len(ids)}/{len(tracks)} available', flush=True)
            continue
        if playlist is None:
            if checkpoint.get('creation_pending') or checkpoint.get('playlist_id'):
                raise RuntimeError(f'{date}: earlier creation is unresolved or playlist missing; refusing duplicate')
            state['days'][date] = {'creation_pending': True}
            save(args.state_file, state)
            payload = {'attributes': {'name': name, 'description': note},
                       'relationships': {'tracks': {'data': [{'id': sid, 'type': 'songs'} for sid in ids]}}}
            recheck(authorization)
            _, response = client.request('/v1/me/library/playlists', 'POST', payload)
            playlist = response['data'][0]
            playlists.append(playlist)
            state['days'][date] = {'creation_pending': False, 'playlist_id': playlist['id']}
            save(args.state_file, state)
            if ids:
                common.verify(client, playlist['id'], set(ids))
        else:
            actual_before = client.playlist_tracks(playlist['id'])
            if authorization:
                ordered = common.ordered_catalog_ids(actual_before)
                if ordered != ids[:len(ordered)]:
                    raise RuntimeError('Existing dated playlist is not an exact prefix; manual repair required')
            existing = common.catalog_ids(actual_before)
            missing = [sid for sid in ids if sid not in existing]
            if missing:
                recheck(authorization)
                client.request(f'/v1/me/library/playlists/{playlist["id"]}/tracks', 'POST',
                               {'data': [{'id': sid, 'type': 'songs'} for sid in missing]})
                common.verify(client, playlist['id'], set(ids))
        actual = client.playlist_tracks(playlist['id'])
        actual_ids = common.catalog_ids(actual)
        if authorization and common.ordered_catalog_ids(actual) != ids:
            raise RuntimeError('Dated playlist exact count/order/IDs verification failed')
        if not set(ids) <= actual_ids:
            raise RuntimeError(f'{date}: readback does not match recommendations')
        record = {'date': date, 'name': name, 'playlist_id': playlist['id'], 'verified_count': len(set(ids) & actual_ids),
                  'expected_count': 5, 'unavailable': unavailable,
                  'url': f'https://music.apple.com/cn/library/playlist/{playlist["id"]}'}
        state['days'][date] = {**record, 'creation_pending': False}
        save(args.state_file, state)
        index.append(record)
        print(f'{date}: verified {record["verified_count"]}/5', flush=True)
    if args.execute:
        save(args.report, {'date_count': len(index), 'latest_date': index[-1]['date'] if index else None,
                           'playlists': list(reversed(index))})
        print(f'Verified {len(index)} dated playlists', flush=True)

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--state-file', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--api-host', choices=sorted(common.ALLOWED_HOSTS), default='amp-api.music.apple.com')
    p.add_argument('--no-wrapper', action='store_true')
    p.add_argument('--execute', action='store_true')
    from publication_gate import add_arguments
    add_arguments(p)
    args = p.parse_args(argv)
    args.repo=args.repo.resolve();args.state_file=args.state_file.resolve();args.report=args.report.resolve()
    if args.state_file.is_relative_to(args.repo):
        raise RuntimeError('Checkpoint must be outside repository')
    with common.state_lock(args.state_file):
        sync(args)

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        message=str(e) if isinstance(e,(RuntimeError,common.ApiError)) else 'Check credentials, network and private checkpoint'
        print(f'Stopped: {type(e).__name__}: {message}',file=sys.stderr,flush=True)
        sys.exit(1)
