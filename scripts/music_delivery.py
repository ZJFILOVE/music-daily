#!/usr/bin/env python3
"""Trusted main reconciliation. No caller-supplied dates, refs, QA or API hosts."""
import argparse
import contextlib
import io
import base64
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from check_consistency import git, iso_day, loads, require
from publication_gate import require_publication_authorization

MUSIC = 'ZJFILOVE/music-daily'
VAULT = 'ZJFILOVE/obsidian-vault'
WORKFLOWS = {MUSIC: '.github/workflows/validate.yml', VAULT: '.github/workflows/recommendation-health.yml'}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError('GitHub redirect rejected; credentials stay on api.github.com')


class GitHub:
    def __init__(self):
        self.tokens = {MUSIC: os.environ.get('GITHUB_TOKEN'), VAULT: os.environ.get('VAULT_READ_TOKEN')}
        require(all(self.tokens.values()), 'Missing read-only Vault credential or repository GitHub token; no Apple access')
        self.opener = urllib.request.build_opener(NoRedirect())

    def get(self, repo, endpoint):
        require(repo in WORKFLOWS and endpoint.startswith('/') and '://' not in endpoint, 'Untrusted GitHub route')
        request = urllib.request.Request('https://api.github.com/repos/' + repo + endpoint,
            headers={'Authorization': 'Bearer ' + self.tokens[repo], 'Accept': 'application/vnd.github+json',
                     'X-GitHub-Api-Version': '2022-11-28'})
        try:
            with self.opener.open(request, timeout=30) as response:
                return loads(response.read())
        except urllib.error.HTTPError as exc:
            raise RuntimeError('GitHub read failed: HTTP ' + str(exc.code)) from None
        except urllib.error.URLError:
            raise RuntimeError('GitHub read unavailable; no publication') from None

    def head(self, repo):
        branch = self.get(repo, '/branches/main')
        require(branch['name'] == 'main' and re.fullmatch('[0-9a-f]{40}', branch['commit']['sha']), 'Invalid trusted main head')
        return branch['commit']['sha']

    def ci(self, repo, sha):
        workflow = WORKFLOWS[repo]
        query = urllib.parse.urlencode({'branch': 'main', 'head_sha': sha, 'event': 'push', 'per_page': 100})
        runs = self.get(repo, '/actions/workflows/' + workflow.rsplit('/', 1)[1] + '/runs?' + query)['workflow_runs']
        valid = [r for r in runs if r.get('head_sha') == sha and r.get('head_branch') == 'main'
                 and r.get('event') == 'push' and r.get('path') == workflow
                 and r.get('head_repository', {}).get('full_name') == repo]
        require(bool(valid), 'No trusted main validation run for ' + repo + '@' + sha)
        run = max(valid, key=lambda r: (r['run_number'], r.get('run_attempt', 1)))
        require(run['status'] == 'completed' and run['conclusion'] == 'success', 'Newest validation is failed/pending')
        jobs = []
        for page in range(1, 101):
            batch = self.get(repo, f"/actions/runs/{int(run['id'])}/jobs?per_page=100&page={page}")['jobs']
            jobs.extend(batch)
            if len(batch) < 100:
                break
        else:
            raise RuntimeError('Incomplete CI job pagination')
        require(any(j['name'] == 'validate' and j['status'] == 'completed' and j['conclusion'] == 'success' for j in jobs),
                'Validation job missing or failed')
        return run['id']

    def verify(self, music_head, vault_head):
        heads = {MUSIC: music_head, VAULT: vault_head}
        require(all(self.head(repo) == sha for repo, sha in heads.items()), 'Remote HEAD moved; retry from fresh snapshots')
        for repo, sha in heads.items():
            self.ci(repo, sha)
        require(all(self.head(repo) == sha for repo, sha in heads.items()), 'Remote HEAD moved during CI verification')

    def confirm_cache(self, key):
        require(re.fullmatch(r'apple-music-state-[0-9]+-[0-9]+-intent', key or ''), 'Invalid intent cache key')
        data = self.get(MUSIC, '/actions/caches?' + urllib.parse.urlencode({'key': key, 'ref': 'refs/heads/main', 'per_page': 100}))
        require(any(c['key'] == key and c['ref'] == 'refs/heads/main' and c['size_in_bytes'] > 0
                    for c in data['actions_caches']), 'Durable intent cache not confirmed; no writes')


def checkout_vault(destination, sha, token):
    """Ephemeral input outside music checkout; no tokens persisted in Git config."""
    require(not destination.exists(), 'Vault input directory must be new')
    destination.mkdir(parents=True)
    env = os.environ.copy()
    index = int(env.get('GIT_CONFIG_COUNT', '0'))
    env['GIT_CONFIG_COUNT'] = str(index + 1)
    env[f'GIT_CONFIG_KEY_{index}'] = 'http.https://github.com/ZJFILOVE/obsidian-vault.git.extraheader'
    env[f'GIT_CONFIG_VALUE_{index}'] = 'AUTHORIZATION: basic ' + base64.b64encode(('x-access-token:' + token).encode()).decode()
    def command(*args, input=None):
        try:
            subprocess.run(['git', '-C', str(destination), *args], input=input, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        except subprocess.CalledProcessError:
            raise RuntimeError('Authorized Vault fetch failed; no publication') from None
    command('init', '-q'); command('remote', 'add', 'origin', 'https://github.com/' + VAULT + '.git')
    command('fetch', '--no-tags', 'origin', 'refs/heads/main')
    require(git(destination, 'rev-parse', 'FETCH_HEAD').decode().strip() == sha, 'Vault moved during checkout')
    # Sparse working files, full history required by permanent identity audit.
    command('sparse-checkout', 'set', '--no-cone', '--stdin', input=(
        '/Music/Daily/\n/.recommendation/transactions/music/\n/.recommendation/transaction-runs/music/\n'
        '/.recommendation/transaction-corrections/music/\n/.recommendation/incidents/music-*\n'
        '/.recommendation/runs/*-music.json\n').encode())
    command('checkout', '--detach', sha)
    # Credential never written to config; environment dies with this function.


def dates(root, business_day=None):
    policy = loads((root / 'state/music-delivery-policy.json').read_bytes())
    require(policy == {'schema_version': 1, 'first_managed_date': '2026-11-07'}, 'Unreviewed delivery policy')
    floor = iso_day(policy['first_managed_date'])
    today = iso_day(business_day) if business_day else datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    return sorted(iso_day(p.stem) for p in (root / 'recommendations').rglob('*.json') if floor <= p.stem <= today)


def plan(root, vault, api):
    music_head = git(root, 'rev-parse', 'HEAD').decode().strip()
    vault_head = git(vault, 'rev-parse', 'HEAD').decode().strip()
    api.verify(music_head, vault_head)
    business_date = datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    entries = []
    for day in dates(root, business_date):
        bundle = loads((vault / '.recommendation/transactions/music' / (day + '.json')).read_bytes())
        auth = require_publication_authorization(root, vault=vault, day=day, music_head=music_head,
            vault_head=vault_head, bundle_digest=bundle['bundle_sha256'], live_verifier=api.verify)
        entries.append({'date': day, 'bundle_digest': auth['bundle_sha256'], 'song_ids': auth['song_ids']})
    api.verify(music_head, vault_head)
    return dict(schema_version=1, music_head=music_head, vault_head=vault_head, business_date=business_date, entries=entries)


def attempt_id():
    run, attempt = os.environ.get('GITHUB_RUN_ID', ''), os.environ.get('GITHUB_RUN_ATTEMPT', '')
    require(run.isdigit() and attempt.isdigit(), 'Trusted Actions attempt identity required')
    return run + '-' + attempt


def reserve(checkpoint, current):
    from daily_playlists_sync import save
    path = checkpoint / 'write-intents.json'
    require(path.exists(), 'Missing checkpoint baseline; perform read-only bootstrap')
    ledger = loads(path.read_bytes())
    require(ledger['schema_version'] == 1 and ledger['initialized'] is True, 'Invalid checkpoint baseline')
    for entry in current['entries']:
        prior = ledger['days'].get(entry['date'])
        if prior is not None:
            require(prior['entry'] == entry, 'Frozen operation changed since reservation')
        else:
            ledger['days'][entry['date']] = dict(entry=entry, attempt=attempt_id(), status='reserved')
    save(path, ledger)


def write_guard(checkpoint, entry):
    ledger = loads((checkpoint / 'write-intents.json').read_bytes())
    marker = ledger['days'][entry['date']]
    require(marker['entry'] == entry and marker['attempt'] == attempt_id() and marker['status'] == 'reserved',
            'Prior/verified operation cannot be blindly replayed; require exact remote readback or review')


def bootstrap(root, vault, checkpoint, expected, api):
    current = plan(root, vault, api)
    require(current == expected, 'Bootstrap plan changed')
    path = checkpoint / 'write-intents.json'
    if path.exists():
        return
    # Cache loss is never treated as permission to create missing playlists.
    # Bootstrap can certify only an already complete remote baseline, using GETs.
    import apple_music_sync as common
    from daily_playlists_sync import PREFIX, save
    playlist_id = os.environ.get('APPLE_MUSIC_PLAYLIST_ID')
    require(bool(playlist_id), 'Pinned total playlist required')
    client = common.Client('amp-api.music.apple.com', common.load_auth(True))
    playlists = client.collection('/v1/me/library/playlists?limit=100')
    require(any(p['id'] == playlist_id for p in playlists), 'Total playlist missing')
    total_ids = common.catalog_ids(client.playlist_tracks(playlist_id))
    states = loads((checkpoint / 'days.json').read_bytes()) if (checkpoint / 'days.json').exists() else {'days': {}}
    ledger = dict(schema_version=1, initialized=True, days={})
    for entry in current['entries']:
        matches = [p for p in playlists if p.get('attributes', {}).get('name') == PREFIX + entry['date']]
        require(len(matches) == 1, 'Baseline dated playlist missing/ambiguous; restore checkpoints or review')
        require(common.ordered_catalog_ids(client.playlist_tracks(matches[0]['id'])) == entry['song_ids']
                and set(entry['song_ids']) <= total_ids, 'Incomplete baseline; restore checkpoints or review')
        ledger['days'][entry['date']] = dict(entry=entry, attempt='bootstrap-readback', status='verified')
        states['days'][entry['date']] = dict(playlist_id=matches[0]['id'], creation_pending=False,
                                           expected_count=5, verified_count=5)
    api.verify(current['music_head'], current['vault_head'])
    save(checkpoint / 'days.json', states)
    save(path, ledger)


def deliver(root, vault, checkpoint, expected_plan, api, *, execute=False):
    current = plan(root, vault, api)
    require(current == expected_plan, 'Plan changed; no Apple access')
    if not execute or not current['entries']:
        return current
    api.confirm_cache('apple-music-state-' + attempt_id() + '-intent')
    import apple_music_sync as total
    import daily_playlists_sync as daily
    require(not checkpoint.resolve().is_relative_to(root.resolve()) and not checkpoint.resolve().is_relative_to(vault.resolve()),
            'Private checkpoints must remain outside input repositories')
    for entry in current['entries']:
        # A failed/unknown operation stops the loop; later dates never overtake it.
        args = argparse.Namespace(repo=root, vault_root=vault, date=entry['date'], music_head=current['music_head'],
            vault_head=current['vault_head'], bundle_digest=entry['bundle_digest'], live_verifier=api.verify,
            write_guard=lambda e=entry: write_guard(checkpoint, e),
            execute=True, no_wrapper=True, api_host='amp-api.music.apple.com', create=False, replace=[],
            playlist_id=os.environ.get('APPLE_MUSIC_PLAYLIST_ID'),
            state_file=checkpoint / 'days.json', report=checkpoint / 'report.json')
        require(bool(args.playlist_id), 'Existing total playlist ID required')
        # Legacy unresolved total operations are not silently discarded.
        legacy = checkpoint / 'total.json'
        if legacy.exists():
            old = loads(legacy.read_bytes())
            require(not old.get('creation_pending') and not old.get('pending_song_ids'), 'Legacy total write unresolved; inspect before migration')
        # Daily writer verifies exact sequence. Both writers recheck live HEAD/CI
        # immediately before POST and recover uncertain operations from readback.
        with total.state_lock(checkpoint / 'delivery.lock'):
            with contextlib.redirect_stdout(io.StringIO()):
                daily.sync(args)
                total.STATE = checkpoint / 'total' / (entry['date'] + '.json')
                total.sync(args)
            ledger = loads((checkpoint / 'write-intents.json').read_bytes())
            ledger['days'][entry['date']]['status'] = 'verified'
            daily.save(checkpoint / 'write-intents.json', ledger)
    daily.save(checkpoint / 'delivery-success.json', {'schema_version': 1, 'status': 'verified',
               'music_head': current['music_head'], 'vault_head': current['vault_head'], 'business_date': current['business_date']})
    return current


def unchanged(api, checkpoint, business_date=None):
    if checkpoint is None or not (checkpoint / 'delivery-success.json').exists():
        return False
    receipt = loads((checkpoint / 'delivery-success.json').read_bytes())
    today = business_date or datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    return (receipt.get('schema_version') == 1 and receipt.get('status') == 'verified'
            and receipt.get('business_date') == today
            and api.head(MUSIC) == receipt.get('music_head') and api.head(VAULT) == receipt.get('vault_head'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['prepare', 'bootstrap', 'reserve', 'confirm', 'run'])
    parser.add_argument('--repo', type=Path, default=Path('.'))
    parser.add_argument('--vault-root', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--checkpoint', type=Path)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(); root = args.repo.resolve(); vault = args.vault_root.resolve()
    require(not vault.is_relative_to(root), 'Vault input must be outside music checkout')
    api = GitHub()
    if args.phase == 'prepare':
        require(not args.execute, 'Prepare is read-only')
        if unchanged(api, args.checkpoint):
            if os.environ.get('GITHUB_OUTPUT'):
                with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
                    output.write('ready=false\n')
            print('No new trusted main heads; no checkout or Apple access')
            return
        head = api.head(VAULT)
        checkout_vault(vault, head, api.tokens[VAULT])
        result = plan(root, vault, api)
        args.plan.write_text(json.dumps(result, indent=2) + '\n')
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
                output.write('ready=' + str(bool(result['entries'])).lower() + '\n')
    elif args.phase in ('bootstrap', 'reserve', 'confirm'):
        require(not args.execute and args.checkpoint is not None, 'Read-only coordination requires checkpoint directory')
        result = loads(args.plan.read_bytes())
        if args.phase == 'bootstrap':
            bootstrap(root, vault, args.checkpoint, result, api)
        elif args.phase == 'reserve':
            require(plan(root, vault, api) == result, 'Reservation plan changed')
            reserve(args.checkpoint, result)
        else:
            api.confirm_cache('apple-music-state-' + attempt_id() + '-intent')
    else:
        require(args.checkpoint is not None, 'Private checkpoint directory required')
        result = deliver(root, vault, args.checkpoint, loads(args.plan.read_bytes()), api, execute=args.execute)
    print(json.dumps({'dates': [e['date'] for e in result['entries']], 'music_head': result['music_head'],
                      'vault_head': result['vault_head'], 'executed': args.execute}))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Never print HTTP response bodies, environment or subprocess diagnostics.
        reason = 'missing_readonly_vault_credential' if not os.environ.get('VAULT_READ_TOKEN') else type(exc).__name__
        print('Delivery stopped: ' + reason + '; evidence incomplete or changed, no later dates attempted')
        raise SystemExit(1)
