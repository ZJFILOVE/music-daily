#!/usr/bin/env python3
"""Authorize one explicit date from a clean, complete music repository snapshot.

This is local structural/history validation, not Vault/source QA or live CI.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo
from check_consistency import audit, history_ids, git, iso_day, loads, require
from publication_gate import check_snapshot, committed
from recording_history import audit_history, blob_sha


def recheck(authorization):
    if authorization is None:
        return
    root = Path(authorization['music_root'])
    check_snapshot(root, authorization['music_head'])
    require(blob_sha(committed(root, authorization['music_head'], authorization['manifest_path']))
            == authorization['manifest_blob'], 'Authorized selection changed before write')


def authorize_args(args):
    root = Path(args.repo).resolve()
    day = getattr(args, 'date', None)
    require(isinstance(day, str), 'Explicit --date required for single-day synchronization')
    iso_day(day)
    require(day != '2026-11-06', 'Withdrawn 2026-11-06 cannot be synchronized')
    require(day <= datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat(), 'Future date cannot be synchronized')
    require(not getattr(args, 'replace', []), 'Single-day selection cannot be replaced')
    head = getattr(args, 'music_head', None) or git(root, 'rev-parse', 'HEAD').decode().strip()
    check_snapshot(root, head)
    history = audit_history(root)
    require(not history['unlinked_historical_occurrences'], 'Unlinked local historical recordings')
    audit(root, None, history_ids(root))
    path = f'recommendations/{day[:4]}/{day[5:7]}/{day}.json'
    raw = committed(root, head, path); manifest = loads(raw); tracks = manifest['tracks']
    require(manifest['date'] == day and len(tracks) == 5
            and [t['position'] for t in tracks] == [1,2,3,4,5], 'Exact five-song order required')
    ids = [t['apple_music']['song_id'] for t in tracks]
    require(len(set(ids)) == 5 and all(t['apple_music']['storefront'] == 'cn' for t in tracks), 'Invalid selected recordings')
    result = dict(date=day, music_root=str(root), music_head=head, manifest_path=path,
                  manifest_blob=blob_sha(raw), song_ids=ids, validation_scope='complete_music_git_history_only')
    recheck(result)
    return result


def add_arguments(parser):
    parser.add_argument('--date', help='Explicit single date; required for writes')
    parser.add_argument('--music-head', help='Optional expected full music commit SHA')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path('.')); add_arguments(parser)
    args = parser.parse_args()
    try:
        print(json.dumps(authorize_args(args), indent=2))
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, 'Local synchronization blocked: ' + str(exc) + '\n')
