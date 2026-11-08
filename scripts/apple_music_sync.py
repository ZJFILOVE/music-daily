#!/usr/bin/env python3
"""Sync Daily 5 manifests to Apple Music, locally or in a headless cloud job."""
import argparse
from contextlib import contextmanager
import fcntl
import re
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SCRIPT_REPO = Path(__file__).resolve().parents[1]
DEFAULT_REPO = SCRIPT_REPO if (SCRIPT_REPO / 'recommendations').is_dir() else Path.home() / 'Music/music-daily'
NAME = 'ChatGPT Daily 5'
ALLOWED_HOSTS = {'api.music.apple.com', 'amp-api.music.apple.com'}
STATE = Path.home() / '.local/state/daily5/apple-music-playlist.json'


def load_auth(no_wrapper=False):
    developer = os.environ.get('APPLE_MUSIC_DEVELOPER_TOKEN', '').strip()
    user = os.environ.get('APPLE_MUSIC_USER_TOKEN', '').strip()
    if developer or user:
        if not developer or not user:
            raise RuntimeError('Set both APPLE_MUSIC_DEVELOPER_TOKEN and APPLE_MUSIC_USER_TOKEN')
        return {'dev_token': developer, 'music_user_token': user}
    if no_wrapper:
        raise RuntimeError('Cloud credentials missing; set both Apple Music token environment variables')
    local = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with local.open('http://127.0.0.1:18080/me', timeout=30) as response:
            auth = json.load(response).get('auth', {})
    except (OSError, ValueError):
        raise RuntimeError('Local wrapper unavailable; configure Apple Music token environment variables for cloud use') from None
    if auth.get('state') != 'authenticated' or not auth.get('dev_token') or not auth.get('music_user_token'):
        raise RuntimeError('Local Apple Music wrapper is not authenticated')
    return auth


@contextmanager
def state_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path) + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another Apple Music sync is running') from None
        yield
    finally:
        os.close(fd)


def save(report):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=STATE.parent, delete=False) as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write('\n')
        name = fh.name
    os.chmod(name, 0o600)
    os.replace(name, STATE)


class ApiError(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__(f'Apple Music HTTP {status}')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, host, auth):
        if host not in ALLOWED_HOSTS:
            raise RuntimeError('Unsupported Apple Music API host')
        self.host = host
        self.auth = auth
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, path, method='GET', body=None):
        url = urllib.parse.urljoin('https://' + self.host, path)
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname != self.host or self.host not in ALLOWED_HOSTS:
            raise RuntimeError('Refusing unexpected pagination host')
        headers = {'Authorization': 'Bearer ' + self.auth['dev_token'],
                   'Music-User-Token': self.auth['music_user_token'],
                   'Origin': 'https://music.apple.com', 'Accept': 'application/json',
                   'Content-Type': 'application/json'}
        if self.host == 'amp-api.music.apple.com':
            headers['Cookie'] = 'media-user-token=' + self.auth['music_user_token']
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, headers=headers, data=data, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            raise ApiError(error.code) from None

    def collection(self, path):
        items = []
        visited = set()
        while path:
            if path in visited:
                raise RuntimeError('Pagination repeated')
            visited.add(path)
            _, data = self.request(path)
            if not isinstance(data.get('data'), list) or data.get('errors'):
                raise RuntimeError('Invalid Apple Music collection response')
            items.extend(data['data'])
            path = data.get('next')
        return items

    def playlist_tracks(self, playlist_id):
        return self.collection(f'/v1/me/library/playlists/{playlist_id}/tracks?limit=100&include=catalog')


def catalog_ids(tracks):
    ids = set()
    for track in tracks:
        if track.get('type') == 'songs':
            ids.add(str(track['id']))
        params = track.get('attributes', {}).get('playParams', {})
        if params.get('catalogId'):
            ids.add(str(params['catalogId']))
        for catalog in track.get('relationships', {}).get('catalog', {}).get('data', []):
            if catalog.get('type') == 'songs':
                ids.add(str(catalog['id']))
    return ids


def ordered_catalog_ids(tracks):
    result = []
    for track in tracks:
        ids = catalog_ids([track])
        if len(ids) != 1:
            raise RuntimeError('Ambiguous catalog identity in playlist readback')
        result.append(next(iter(ids)))
    return result


def verify(client, playlist_id, expected):
    for attempt in range(12):
        try:
            tracks = client.playlist_tracks(playlist_id)
            ids = catalog_ids(tracks)
            if expected <= ids:
                return tracks, ids
        except ApiError as error:
            if error.status not in (404, 429, 500, 502, 503, 504):
                raise
        if attempt < 11:
            time.sleep(3)
    raise RuntimeError('Write not confirmed by readback; stopped without resending')


def sync(args):
    from local_sync_gate import authorize_args, recheck
    authorization = authorize_args(args) if args.execute else None
    manifests = sorted((args.repo / 'recommendations').glob('*/*/*.json'), reverse=True)
    if authorization:
        manifests = [args.repo / authorization['manifest_path']]
        if args.replace:
            raise RuntimeError('Frozen publication does not allow catalog replacements')
    targets = []
    seen = set()
    for manifest in manifests:
        daily = json.loads(manifest.read_text())
        for track in sorted(daily['tracks'], key=lambda t: t['position']):
            song_id = track['apple_music']['song_id']
            if not isinstance(song_id, str) or not song_id.isdigit() or track['apple_music']['storefront'] != 'cn':
                raise RuntimeError('Invalid catalog song ID or storefront')
            if song_id not in seen:
                targets.append(song_id)
                seen.add(song_id)
    if not targets:
        raise RuntimeError('No recommendation manifests')
    if authorization and targets != authorization['song_ids']:
        raise RuntimeError('Authorized manifest changed')
    original_targets = targets[:]
    replacements = {}
    for specification in args.replace:
        source, replacement = specification.split('=', 1)
        if source not in targets or not replacement.isdigit():
            raise RuntimeError('Invalid catalog replacement')
        replacements[source] = replacement
    targets = [replacements.get(sid, sid) for sid in targets]
    if len(set(targets)) != len(targets):
        raise RuntimeError('Replacement would duplicate another recommendation')
    auth = load_auth(args.no_wrapper)
    previous = json.loads(STATE.read_text()) if STATE.exists() else {}
    host = args.api_host or previous.get('host', 'api.music.apple.com')
    client = Client(host, auth)
    catalog = set()
    for index in range(0, len(targets), 100):
        _, response = client.request('/v1/catalog/cn/songs?' +
                                     urllib.parse.urlencode({'ids': ','.join(targets[index:index+100])}))
        if not isinstance(response.get('data'), list):
            raise RuntimeError('Invalid catalog preflight response')
        catalog.update(item['id'] for item in response['data'])
    unavailable = [sid for sid in targets if sid not in catalog]
    if authorization and unavailable:
        raise RuntimeError('Authorized selection unavailable; no partial publication')
    importable = [sid for sid in targets if sid in catalog]
    playlists = client.collection('/v1/me/library/playlists?limit=100')
    matches = [p for p in playlists if p.get('attributes', {}).get('name') == NAME]
    pinned_id = args.playlist_id or previous.get('playlist_id')
    if pinned_id and not re.fullmatch(r'[A-Za-z0-9._-]+', pinned_id):
        raise RuntimeError('Invalid Apple Music playlist ID')
    playlist = next((p for p in playlists if p['id'] == pinned_id), None)
    if args.playlist_id and playlist is None:
        raise RuntimeError('Configured Apple Music playlist was not found; refusing to create another')
    if playlist is None:
        if len(matches) > 1:
            raise RuntimeError('Multiple matching playlists; refusing ambiguous selection')
        playlist = matches[0] if matches else None
    if playlist is None and not args.create:
        raise RuntimeError('Playlist not found; use --create once to explicitly create it')
    report = {'name': NAME, 'host': client.host, 'target_count': len(targets),
              'target_song_ids': targets, 'latest_date': manifests[0].stem,
              'oldest_date': manifests[-1].stem, 'manifest_count': len(manifests),
              'original_song_ids': original_targets, 'replacements': replacements,
              'unavailable_song_ids': unavailable}
    if playlist:
        if playlist.get('attributes', {}).get('canEdit') is False:
            raise RuntimeError('Existing playlist is not editable')
        report['playlist_id'] = playlist['id']
        tracks = client.playlist_tracks(playlist['id'])
        existing = catalog_ids(tracks)
        missing = [sid for sid in importable if sid not in existing]
    else:
        tracks, existing, missing = [], set(), importable[:]
    print(f'{NAME}: {len(manifests)} days, {len(targets)} songs; {len(missing)} to add', flush=True)
    if unavailable:
        print('Unavailable in China storefront: ' + ', '.join(unavailable), flush=True)
    if not args.execute:
        print('Dry run; no cloud changes', flush=True)
        return
    if previous.get('creation_pending') and playlist is None:
        raise RuntimeError('Previous creation is unconfirmed; inspect Apple Music before creating again')
    if previous.get('pending_song_ids'):
        pending = set(previous['pending_song_ids'])
        if not playlist:
            raise RuntimeError('Previous append is unconfirmed and its playlist cannot be found')
        tracks, existing = verify(client, playlist['id'], pending)
        missing = [sid for sid in importable if sid not in existing]
    save(report)
    if playlist is None:
        report['creation_pending'] = True
        save(report)
        first = missing[:5]
        payload = {'attributes': {'name': NAME, 'description': 'Daily 5 历史推荐；新推荐追加到歌单末尾。'},
                   'relationships': {'tracks': {'data': [{'id': sid, 'type': 'songs'} for sid in first]}}}
        try:
            recheck(authorization)
            status, response = client.request('/v1/me/library/playlists', 'POST', payload)
        except ApiError as error:
            if error.status not in (401, 403) or client.host != 'api.music.apple.com':
                raise
            # Explicit auth rejection proves the first request did not create a playlist.
            client = Client('amp-api.music.apple.com', auth)
            recheck(authorization)
            status, response = client.request('/v1/me/library/playlists', 'POST', payload)
        playlist = response['data'][0]
        report.update(host=client.host, playlist_id=playlist['id'], creation_status=status, creation_pending=False)
        save(report)
        print(f'Created playlist {playlist["id"]}; checking first {len(first)} songs', flush=True)
        tracks, existing = verify(client, playlist['id'], set(first))
        missing = [sid for sid in importable if sid not in existing]
    report['verified_target_song_ids'] = [sid for sid in targets if sid in existing]
    save(report)
    while missing:
        batch = missing[:25]
        report['pending_song_ids'] = batch
        save(report)
        recheck(authorization)
        status, _ = client.request(f'/v1/me/library/playlists/{playlist["id"]}/tracks', 'POST',
                                   {'data': [{'id': sid, 'type': 'songs'} for sid in batch]})
        report['last_append_status'] = status
        save(report)
        print(f'Append accepted ({status}): {len(batch)} songs; verifying', flush=True)
        tracks, existing = verify(client, playlist['id'], set(batch))
        missing = [sid for sid in importable if sid not in existing]
        report['verified_target_song_ids'] = [sid for sid in targets if sid in existing]
        report['remote_track_count'] = len(tracks)
        report['pending_song_ids'] = []
        save(report)
        print(f'Verified {len(report["verified_target_song_ids"])}/{len(targets)}', flush=True)
        if missing:
            time.sleep(3)
    tracks, existing = verify(client, playlist['id'], set(importable))
    report.update(complete=not unavailable, verified_count=len(set(targets) & existing), remote_track_count=len(tracks))
    if playlist.get('attributes', {}).get('url'):
        report['url'] = playlist['attributes']['url']
    save(report)
    label = 'Complete' if report['complete'] else 'Partial (unavailable catalog songs)'
    print(f'{label}: {report["verified_count"]}/{len(targets)}, playlist {playlist["id"]}', flush=True)
    if unavailable:
        print('Unavailable in China storefront: ' + ', '.join(unavailable), flush=True)


def main(argv=None):
    global STATE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='Append missing songs; default is read-only')
    parser.add_argument('--create', action='store_true', help='Allow first-time creation if no matching playlist exists')
    parser.add_argument('--repo', type=Path, default=DEFAULT_REPO, help='Metadata repository root')
    parser.add_argument('--state-file', type=Path, default=STATE, help='Private runtime checkpoint; never commit it')
    parser.add_argument('--no-wrapper', action='store_true', help='Require environment credentials; never contact localhost')
    parser.add_argument('--api-host', choices=sorted(ALLOWED_HOSTS), default=os.environ.get('APPLE_MUSIC_API_HOST'))
    parser.add_argument('--playlist-id', default=os.environ.get('APPLE_MUSIC_PLAYLIST_ID'), help='Pin an existing library playlist')
    parser.add_argument('--replace', action='append', default=[], metavar='ORIGINAL=REPLACEMENT',
                        help='Use an explicitly chosen catalog edition for an unavailable song')
    from local_sync_gate import add_arguments
    add_arguments(parser)
    args = parser.parse_args(argv)
    STATE = args.state_file.expanduser().resolve()
    args.repo = args.repo.expanduser().resolve()
    if STATE.is_relative_to(args.repo):
        raise RuntimeError('Runtime checkpoint must be outside the metadata repository')
    if args.execute:
        with state_lock(STATE):
            sync(args)
    else:
        sync(args)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Never log tokens, request/response bodies, or raw network exceptions.
        if isinstance(error, (RuntimeError, ApiError)):
            message = str(error)
        else:
            message = 'Check configuration, manifests, connectivity, and checkpoint JSON'
        print(f'Stopped: {type(error).__name__}: {message}', file=sys.stderr, flush=True)
        sys.exit(1)
