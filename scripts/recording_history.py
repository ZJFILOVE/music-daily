#!/usr/bin/env python3
"""Read-only recording reservations; structural audit never authorizes publication."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import subprocess

from check_consistency import AuditError, git, iso_day, loads, require

INCIDENT_PATH = '.audit/2026-11-06-recording-collision.json'
# Pin the reviewed evidence, not a song-wide or date-wide exemption.
INCIDENT_SHA256 = '7e848dee4d14221d02ae8d3b6ba929590457aaa64779fd9e938e8340eb3e8f8a'


def blob_sha(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def digest(doc):
    return hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def historical_blobs(root, prefixes, suffix='.json'):
    """Walk every tree in HEAD ancestry, including deleted paths and renames.

    rev-list --objects alone gives only one name per object and can lose a path
    when identical bytes have been stored at more than one path.
    """
    require(git(root, 'rev-parse', '--is-shallow-repository').strip() == b'false',
            'Full Git history required; fetch-depth: 0')
    require(not git(root, 'replace', '-l').strip(), 'Git replacement refs are not history evidence')
    grafts = Path(git(root, 'rev-parse', '--git-path', 'info/grafts').decode().strip())
    if not grafts.is_absolute():
        grafts = root / grafts
    require(not grafts.exists(), 'Git grafts are not complete history evidence')
    seen = set()
    for commit in git(root, 'rev-list', 'HEAD').decode().splitlines():
        for entry in git(root, 'ls-tree', '-r', '-z', commit, '--', *prefixes).split(b'\0'):
            if not entry:
                continue
            meta, path = entry.split(b'\t', 1)
            mode, kind, sha = meta.decode().split()
            path = path.decode()
            if not path.endswith(suffix):
                continue
            require(kind == 'blob' and mode == '100644', 'Non-regular history file: ' + path)
            key = (path, sha)
            if key not in seen:
                seen.add(key)
                raw = git(root, 'cat-file', 'blob', sha)
                require(blob_sha(raw) == sha, 'Historical blob mismatch')
                yield path, sha, raw


def occurrences(raw, source):
    doc = loads(raw)
    day = iso_day(doc['date'])
    require(doc['provider'] == 'apple_music', 'Wrong history provider')
    result = []
    for t in doc['tracks']:
        am = dict(t['apple_music'])
        url = re.fullmatch(r'https://music\.apple\.com/([a-z]{2})/(?:song/(?:[^/]+/)?([0-9]+)|search\?[^#]+)', am.get('url', ''))
        require(url is not None, 'Unparseable historical Apple Music URL')
        if 'storefront' not in am:
            am['storefront'] = url.group(1)  # Derivation only; original bytes remain untouched.
        require(am['storefront'] == url.group(1), 'Historical storefront/URL mismatch')
        require(am.get('song_id') == url.group(2), 'Historical song ID/URL mismatch')
        require(t['canonical_id'] == t['artist'] + '::' + t['title'], 'Historical canonical mismatch')
        require(type(t['position']) is int and 1 <= t['position'] <= 5, 'Invalid historical position')
        result.append(dict(provider=doc['provider'], storefront=am['storefront'], song_id=am['song_id'],
                           date=day, position=t['position'], canonical_id=t['canonical_id'],
                           manifest_blob=blob_sha(raw), source=source))
    return result


def incident(root):
    path = root / INCIDENT_PATH
    if not path.exists():
        return None
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == INCIDENT_SHA256, 'Incident evidence changed or expanded')
    doc = loads(raw)
    for row in doc['occurrences']:
        original = git(root, 'cat-file', 'blob', row['manifest_blob'])
        require(blob_sha(original) == row['manifest_blob'], 'Original incident evidence unavailable')
        if row['date'] == '2026-11-06':
            from recording_withdrawal import verify
            verify(root)
        else:
            require((root / row['manifest_path']).read_bytes() == original, 'First incident occurrence changed')
    return doc


def verify_incident_vault(vault, evidence):
    prepared = evidence['immutable_prepared']
    receipt = evidence['failure_receipt_evidence']
    for row in (prepared, receipt):
        historical = git(vault, 'show', prepared['commit'] + ':' + row['path'])
        require(blob_sha(historical) == row['blob'], 'Incident Vault evidence blob mismatch')
        require((vault / row['path']).read_bytes() == historical, 'Original Vault incident evidence changed')
    require(loads((vault / prepared['path']).read_bytes())['bundle_sha256'] == prepared['bundle_sha256'],
            'Incident bundle digest mismatch')
    raw = (vault / receipt['path']).read_bytes()
    require(hashlib.sha256(raw).hexdigest() == receipt['sha256'], 'Failure receipt digest mismatch')
    require(loads(raw)['status'] == 'blocked_current_publication', 'Incident failure state changed')


def occurrence_key(row):
    return tuple(row[k] for k in ('date', 'position'))


def check_rows(rows, evidence):
    by_identity = defaultdict(list)
    by_canonical = defaultdict(set)
    for row in rows:
        identity = tuple(row[k] for k in ('provider', 'storefront', 'song_id'))
        if row['song_id'] is not None:
            by_identity[identity].append(row)
        if row['canonical_id']:
            by_canonical[row['canonical_id']].add((row['date'], row['position']))
    require(all(len(v) == 1 for v in by_canonical.values()), 'Canonical ID reused in another operation')
    known = []
    for identity, values in by_identity.items():
        if len({occurrence_key(r) for r in values}) <= 1:
            continue  # Same operation readback/retry; no additional selection.
        allowed = evidence and tuple(evidence[k] for k in ('provider', 'storefront', 'song_id')) == identity
        expected = {tuple(r[k] for k in ('date', 'position', 'canonical_id', 'manifest_blob'))
                    for r in evidence['occurrences']} if allowed else set()
        actual = {tuple(r[k] for k in ('date', 'position', 'canonical_id', 'manifest_blob')) for r in values}
        require(allowed and actual == expected, 'Recording reused across operations: ' + repr(identity))
        known.append(evidence['incident_id'])
    return known


def prepared_rows(raw, path):
    b = loads(raw)
    require(set(b) == {'schema_version', 'plan', 'payloads', 'plan_sha256', 'bundle_sha256'}
            and b['schema_version'] == 1, 'Invalid PREPARED envelope')
    require(digest({k: v for k, v in b.items() if k != 'bundle_sha256'}) == b['bundle_sha256'],
            'PREPARED bundle digest mismatch')
    plan = b['plan']
    require(digest(plan) == b['plan_sha256'], 'PREPARED plan digest mismatch')
    day = iso_day(plan['date'])
    require(plan['project'] == 'music' and plan['operation_id'] == 'music:' + day
            and path == '.recommendation/transactions/music/' + day + '.json', 'PREPARED operation mismatch')
    targets = plan['targets']
    keys = [t['repository'] + ':' + t['path'] for t in targets]
    require(len(keys) == len(set(keys)) and set(keys) == set(b['payloads']), 'PREPARED targets mismatch')
    for t, key in zip(targets, keys):
        require(blob_sha(b['payloads'][key].encode()) == t['target_sha'], 'PREPARED payload hash mismatch')
    key = 'ZJFILOVE/music-daily:recommendations/' + day[:4] + '/' + day[5:7] + '/' + day + '.json'
    raw_manifest = b['payloads'][key].encode()
    require(b['payloads']['ZJFILOVE/obsidian-vault:Music/Daily/manifests/' + day + '.json'].encode()
            == raw_manifest, 'PREPARED manifest mirrors mismatch')
    rows = occurrences(raw_manifest, path + '@' + blob_sha(raw))
    require(all(r['date'] == day for r in rows), 'PREPARED manifest date mismatch')
    require([r['canonical_id'] for r in rows] == plan['exposed_resource_ids'], 'PREPARED exposures mismatch')
    return rows


def retired_note_rows(vault, existing_rows, state):
    """Recover legacy state-only identities from exact numbered note headings.

    No fuzzy artist matching. Each row keeps its original note Git blob evidence.
    """
    rows = []
    known = {(r['date'], r['storefront'], r['song_id']) for r in existing_rows}
    for path, sha, raw in historical_blobs(vault, ['Music/Daily'], suffix='.md'):
        match = re.fullmatch(r'Music/Daily/(\d{4}-\d{2}-\d{2})\.md', path)
        if not match:
            continue
        day = iso_day(match.group(1))
        for section in re.split(r'(?m)(?=^## )', raw.decode()):
            heading = re.match(r'## ([1-5])\. (.+?) — (.+)\n', section)
            cid = heading.group(2) + '::' + heading.group(3) if heading else ''
            urls = re.findall(r'https://music\.apple\.com/([a-z]{2})/song/(?:[^/\s)]+/)?([0-9]+)', section)
            for storefront, sid in set(urls):
                if (day, storefront, sid) in known:
                    continue  # Already reserved by exact recording/date evidence.
                rows.append(dict(provider='apple_music', storefront=storefront, song_id=sid, date=day,
                                 position=int(heading.group(1)) if heading else 0,
                                 canonical_id=cid if cid in state else '', manifest_blob=sha,
                                 source='vault-note:' + path + '@' + sha))
    return rows


def associate_missing_rows(rows, root, vault):
    """Only committed descendant evidence may associate an original unknown row."""
    revisions, ancestry = {}, {}

    def source(row):
        value = row['source']
        if value.startswith('working:'):
            return root, value[len('working:'):], row['manifest_blob']
        checkout = root if value.startswith('music:') else vault
        if checkout is None:
            return None, '', ''
        value = value.split(':', 1)[1] if ':' in value else value
        path, oid = value.rsplit('@', 1)
        return checkout, path, oid

    def commits(checkout, path, oid):
        key = (str(checkout), path)
        if key not in revisions:
            versions = {}
            for commit in git(checkout, 'log', '--format=%H', 'HEAD', '--', path).decode().splitlines():
                for entry in git(checkout, 'ls-tree', commit, '--', path).decode().splitlines():
                    meta, name = entry.split('\t', 1)
                    if name == path:
                        versions.setdefault(meta.split()[2], []).append(commit)
            revisions[key] = versions
        return revisions[key].get(oid, [])

    def later(checkout, old, new):
        if old == new:
            return False
        key = (str(checkout), old, new)
        if key not in ancestry:
            try:
                git(checkout, 'merge-base', '--is-ancestor', old, new)
                ancestry[key] = True
            except subprocess.CalledProcessError as exc:
                if exc.returncode != 1:
                    raise
                ancestry[key] = False
        return ancestry[key]

    mappings, unknown, unlinked = [], set(), set()
    for row in rows:
        if row['song_id'] is not None:
            continue
        key = row['date'] + ':' + row['canonical_id']
        unknown.add(key)
        checkout, path, oid = source(row)
        origins = commits(checkout, path, oid) if checkout else []
        evidence = []
        for candidate in rows:
            if (candidate['song_id'] is None or occurrence_key(candidate) != occurrence_key(row)
                    or candidate['canonical_id'] != row['canonical_id']):
                continue
            other, candidate_path, candidate_oid = source(candidate)
            if other != checkout or checkout is None:
                continue
            versions = commits(other, candidate_path, candidate_oid)
            proof = next(((old, new) for old in origins for new in versions if later(checkout, old, new)), None)
            if proof:
                item = dict(identity=candidate['provider'] + ':' + candidate['storefront'] + ':' + candidate['song_id'],
                            source=candidate['source'], original_source=row['source'],
                            original_commit=proof[0], later_commit=proof[1])
                if item not in evidence:
                    evidence.append(item)
        if evidence:
            mappings.append(dict(occurrence=key, original_recording_id=None,
                                 historically_linked_identities=sorted({x['identity'] for x in evidence}),
                                 evidence=evidence))
        else:
            unlinked.add(key)
    return mappings, sorted(unknown), sorted(unlinked)


def audit_history(root, vault=None):
    rows = []
    for path, sha, raw in historical_blobs(root, ['recommendations']):
        rows.extend(occurrences(raw, 'music:' + path + '@' + sha))
    for path in sorted((root / 'recommendations').rglob('*.json')):
        rows.extend(occurrences(path.read_bytes(), 'working:' + path.relative_to(root).as_posix()))
    require(bool(rows), 'No historical recordings found')
    prepared = 0
    frozen = {}
    if vault is not None:
        for path, sha, raw in historical_blobs(vault, ['Music/Daily/manifests', '.recommendation/transactions/music']):
            if path.startswith('.recommendation/transactions/'):
                bundle = loads(raw)
                require(path not in frozen or frozen[path] == (bundle['bundle_sha256'], sha), 'Immutable PREPARED changed: ' + path)
                frozen[path] = (bundle['bundle_sha256'], sha)
                rows.extend(prepared_rows(raw, path)); prepared += 1
            else:
                rows.extend(occurrences(raw, 'vault:' + path + '@' + sha))
        require(prepared > 0, 'No PREPARED history; completeness not established')
        # Include uncommitted reservations too; never rely on a cached export.
        for path in sorted((vault / '.recommendation/transactions/music').glob('*.json')):
            raw = path.read_bytes(); name = path.relative_to(vault).as_posix()
            require(name not in frozen or frozen[name] == (loads(raw)['bundle_sha256'], blob_sha(raw)), 'Immutable PREPARED changed: ' + name)
            rows.extend(prepared_rows(raw, name))
    state = loads((root / 'state/tracks.json').read_bytes())['recommended']
    retired = []
    if vault is not None:
        retired = retired_note_rows(vault, rows, state)
        rows.extend(retired)
    evidence = incident(root)
    if vault is not None and evidence is not None:
        verify_incident_vault(vault, evidence)
    known = check_rows(rows, evidence)
    state = loads((root / 'state/tracks.json').read_bytes())['recommended']
    require({r['canonical_id'] for r in rows if r['canonical_id']} <= state.keys(), 'Historical/reserved canonical keys missing from state')
    mappings, unresolved, unlinked = associate_missing_rows(rows, root, vault)
    return {'status': 'structural_history_checked', 'recording_identities': len({(r['provider'], r['storefront'], r['song_id']) for r in rows if r['song_id'] is not None}),
            'unresolved_historical_occurrences': sorted(unresolved),
            'unlinked_historical_occurrences': unlinked,
            'search_only_history_mappings': sorted(mappings, key=lambda r: r['occurrence']),
            'retired_note_evidence': sorted({(r['canonical_id'], r['song_id'], r['source']) for r in retired}),
            'unmapped_canonical_keys': sorted(set(state) - {r['canonical_id'] for r in rows}),
            'identity_format': 'apple_music:<storefront>:<song_id>',
            'known_incidents': known, 'publication_authorized': False,
            'prepared_history': 'checked' if vault else 'NOT_CHECKED: supply authorized full --vault-root checkout',
            'prepared_blob_versions': prepared, 'music_head': git(root, 'rev-parse', 'HEAD').decode().strip(),
            'vault_head': git(vault, 'rev-parse', 'HEAD').decode().strip() if vault else None}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--music-root', type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument('--vault-root', type=Path)
    args = p.parse_args()
    try:
        print(json.dumps(audit_history(args.music_root, args.vault_root), ensure_ascii=False, indent=2))
    except (AuditError, OSError, ValueError, KeyError, TypeError) as exc:
        p.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
