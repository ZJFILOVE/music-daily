#!/usr/bin/env python3
"""Verify one frozen date against caller-pinned, authorized complete Git checkouts."""
import argparse
import json
from pathlib import Path
import re
import subprocess

from check_consistency import audit, history_ids, git, iso_day, loads, require, preserve_state
from recording_history import audit_history, blob_sha, incident, prepared_rows


def committed(root, head, path):
    raw = git(root, 'show', head + ':' + path)
    require((root / path).read_bytes() == raw, 'Working bytes differ from pinned snapshot: ' + path)
    return raw


def check_snapshot(root, head):
    require(isinstance(head, str) and re.fullmatch(r'[0-9a-f]{40}', head), 'Full pinned commit required')
    require(git(root, 'rev-parse', 'HEAD').decode().strip() == head, 'Checkout HEAD changed')
    require(git(root, 'rev-parse', '--is-shallow-repository').strip() == b'false', 'Complete Git history required')
    require(not git(root, 'status', '--porcelain', '--untracked-files=all').strip(), 'Clean authorized checkout required')


def recheck(authorization):
    """Repeat immediately before each write; never reuse an audit after inputs change."""
    if authorization is not None:
        check_snapshot(Path(authorization['music_root']), authorization['music_head'])
        check_snapshot(Path(authorization['vault_root']), authorization['vault_head'])
        if authorization.get('_live_verifier'):
            authorization['_live_verifier'](authorization['music_head'], authorization['vault_head'])
        if authorization.get('_write_guard'):
            authorization['_write_guard']()


def require_publication_authorization(repo, *, vault=None, day=None, music_head=None,
                                      vault_head=None, bundle_digest=None, live_verifier=None):
    try:
        require(day != '2026-11-06', '2026-11-06 remains failed; incident acknowledgment cannot authorize publication')
        require(vault is not None and day is not None and bundle_digest is not None,
                'Full Vault checkout, date, both pinned HEADs and original bundle digest required')
        root, vault = Path(repo).resolve(), Path(vault).resolve()
        iso_day(day)
        check_snapshot(root, music_head); check_snapshot(vault, vault_head)
        evidence = incident(root)
        require(not evidence or day not in {r['date'] for r in evidence['occurrences'][1:]},
                '2026-11-06 remains failed; incident acknowledgment cannot authorize publication')
        history = audit_history(root, vault)
        require(not history['unlinked_historical_occurrences'] and not history['unmapped_canonical_keys'], 'Unresolved legacy recording identities')
        audit(root, vault, history_ids(root), music_only=True)
        bundle_path = '.recommendation/transactions/music/' + day + '.json'
        bundle_raw = committed(vault, vault_head, bundle_path)
        b = loads(bundle_raw)
        rows = prepared_rows(bundle_raw, bundle_path)
        require(b['bundle_sha256'] == bundle_digest, 'Original frozen bundle digest changed')
        require(len(rows) == 5 and [r['position'] for r in rows] == [1, 2, 3, 4, 5], 'Incomplete frozen selection')
        manifest_path = f'recommendations/{day[:4]}/{day[5:7]}/{day}.json'
        expected_paths = {
            'ZJFILOVE/music-daily:' + manifest_path,
            'ZJFILOVE/music-daily:state/tracks.json',
            'ZJFILOVE/obsidian-vault:Music/Daily/' + day + '.md',
            'ZJFILOVE/obsidian-vault:Music/Daily/manifests/' + day + '.json',
            'ZJFILOVE/obsidian-vault:Music/Daily/state/tracks.json',
        }
        require(set(b['payloads']) == expected_paths, 'Unexpected frozen write scope')
        qa = loads(committed(vault, vault_head, '.recommendation/runs/' + day + '-music.json'))
        if live_verifier:
            live_verifier(music_head, vault_head)
        from qa_receipt import normalize
        qa = normalize(qa, b, root, vault, live_verified=live_verifier is not None)
        require(qa['status'] == 'passed_current_publication' and qa['project'] == 'music'
                and qa['date'] == day and qa['operation_id'] == 'music:' + day, 'Missing or failed corresponding QA')
        require(qa['bundle']['path'] == bundle_path and qa['bundle']['bundle_sha256'] == bundle_digest
                and qa['bundle']['plan_sha256'] == b['plan_sha256'], 'QA bundle binding mismatch')
        checks = qa['checks']; mirror = checks['mirror_check']
        require(checks['freeze_and_baselines'] == 'passed' and checks['state_values_preserved'] is True
                and checks['order_fields_and_sections'] == 'passed'
                and mirror['status'] == 'passed_current_music_mirrors', 'QA checks not passed')
        require(set(qa['readbacks']) == expected_paths, 'Incomplete QA target readbacks')
        heads = mirror['heads']
        for short, checkout, head in [('music', root, music_head), ('vault', vault, vault_head)]:
            prior = heads[short]
            require(isinstance(prior, str) and re.fullmatch(r'[0-9a-f]{40}', prior), 'Invalid QA snapshot')
            git(checkout, 'merge-base', '--is-ancestor', prior, head)
            # QA itself is committed after the checked artifact snapshot. Its
            # snapshot must be an ancestor and every checked input must still match.
            if live_verifier is None:
                runs = qa['ci'][prior]
                require(any(run.get('status') == 'completed' and run.get('conclusion') == 'success'
                            and any(job.get('name') == 'validate' and job.get('conclusion') == 'success'
                                    for job in run.get('jobs', [])) for run in runs), 'No successful snapshot-bound validation CI')
        for target in b['plan']['targets']:
            key = target['repository'] + ':' + target['path']
            short = 'music' if target['repository'] == 'ZJFILOVE/music-daily' else 'vault'
            checkout, head = (root, music_head) if short == 'music' else (vault, vault_head)
            raw = committed(checkout, head, target['path'])
            frozen_raw = b['payloads'][key].encode()
            if target['path'].endswith('state/tracks.json'):
                frozen_state, current_state = loads(frozen_raw), loads(raw)
                require(frozen_state['provider'] == current_state['provider'] and frozen_state['schema_version'] == current_state['schema_version'], 'State schema changed')
                preserve_state(frozen_state['recommended'], current_state['recommended'])
            else:
                require(raw == frozen_raw, 'Current payload differs from original frozen bundle')
            require(git(checkout, 'show', heads[short] + ':' + target['path']) == frozen_raw, 'QA snapshot payload mismatch')
            raw = frozen_raw
            require(qa['readbacks'][key]['sha'] == blob_sha(raw)
                    and qa['readbacks'][key]['bytes'] == len(raw), 'QA target hash mismatch')
        actual = {p.name: blob_sha(p.read_bytes()) for p in (root / 'recommendations').rglob('*.json')}
        require(mirror['manifests'] == len(mirror['manifest_blobs']), 'QA manifest count mismatch')
        for name, sha in mirror['manifest_blobs'].items():
            if name == '2026-11-06.json' and sha != actual.get(name):
                from recording_withdrawal import ORIGINAL, TARGET, verify
                verify(root)
                require(sha == ORIGINAL and actual.get(name) == TARGET, 'Unknown historical correction')
            else:
                require(actual.get(name) == sha, 'Checked manifest changed or disappeared')
        require(mirror['state_blob_sha'] == blob_sha(b['payloads']['ZJFILOVE/music-daily:state/tracks.json'].encode()), 'QA frozen state mismatch')
        manifest = loads((root / manifest_path).read_bytes())
        selected = qa['source_and_candidates']['selected']
        require(len(selected) == 5, 'Missing source QA')
        for t, source in zip(manifest['tracks'], selected):
            require(source['id'] == t['apple_music']['song_id'] and source['storefront'] == 'cn'
                    and source['url'] == t['apple_music']['url']
                    and all(source[k] == t[k] for k in ('title', 'artist', 'album'))
                    and bool(source['verification']), 'Source QA does not match selected recording')
        result = dict(date=day, song_ids=[r['song_id'] for r in rows], manifest_path=manifest_path,
                      music_root=str(root), vault_root=str(vault), music_head=music_head, vault_head=vault_head,
                      bundle_sha256=bundle_digest, publication_authorized=True)
        if live_verifier is not None:
            result['_live_verifier'] = live_verifier
        recheck(result)
        return result
    except (ValueError, OSError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        raise RuntimeError('Apple Music publication blocked: ' + str(exc)) from exc


def add_arguments(parser):
    parser.add_argument('--vault-root', type=Path)
    parser.add_argument('--date')
    parser.add_argument('--music-head')
    parser.add_argument('--vault-head')
    parser.add_argument('--bundle-digest')


def authorize_args(args):
    if getattr(args, 'execute', False) and not callable(getattr(args, 'live_verifier', None)):
        raise RuntimeError('Apple Music publication blocked: execution requires the trusted live delivery driver')
    result = require_publication_authorization(args.repo, vault=getattr(args, 'vault_root', None),
        day=getattr(args, 'date', None), music_head=getattr(args, 'music_head', None),
        vault_head=getattr(args, 'vault_head', None), bundle_digest=getattr(args, 'bundle_digest', None), live_verifier=getattr(args, 'live_verifier', None))
    if getattr(args, 'execute', False):
        require(callable(getattr(args, 'write_guard', None)), 'Durable write intent guard required')
        result['_write_guard'] = args.write_guard
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path('.')); add_arguments(parser)
    args = parser.parse_args()
    try:
        print(json.dumps(authorize_args(args), indent=2))
    except RuntimeError as exc:
        parser.exit(1, str(exc) + '\n')
