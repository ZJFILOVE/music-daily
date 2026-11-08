"""Read existing QA formats without rewriting receipts or relabelling old CI."""
import hashlib
import re
from urllib.parse import urlsplit, unquote
from check_consistency import preserve_state
from check_consistency import git, loads, require
from recording_history import blob_sha


def manifest_map(root, head, prefix):
    result = {}
    for entry in git(root, 'ls-tree', '-r', head, '--', prefix).decode().splitlines():
        meta, path = entry.split('\t', 1)
        if path.endswith('.json'):
            require(meta.split()[1] == 'blob', 'Invalid manifest tree')
            name = path.rsplit('/', 1)[-1]
            require(name not in result, 'Repeated manifest name')
            result[name] = meta.split()[2]
    return result


def normalize(qa, bundle, music, vault, live_verified=False):
    if qa.get('status') == 'passed_current_publication_gates':
        return normalize_gates(qa, bundle, music, vault, live_verified)
    if qa.get('status') != 'success_current_publication':
        return qa
    # This historical format explicitly records failed structural CI. Only a
    # fresh independently verified CI run may supply current structural evidence.
    require(live_verified, 'Alternate QA format requires independent live CI')
    require(qa.get('schema_version') == 1 and qa.get('project', 'music') == 'music', 'Unsupported QA envelope')
    plan, payloads = bundle['plan'], bundle['payloads']; day = plan['date']
    mp = f'recommendations/{day[:4]}/{day[5:7]}/{day}.json'
    manifest_raw = payloads['ZJFILOVE/music-daily:' + mp].encode()
    manifest = loads(manifest_raw); tracks = manifest['tracks']
    require(qa['date'] == day and qa['operation_id'] == 'music:' + day, 'QA operation mismatch')
    require(qa['bundle_sha256'] == bundle['bundle_sha256'] and qa['plan_sha256'] == bundle['plan_sha256'], 'QA frozen digest mismatch')
    require(qa['selected_tracks'] == tracks, 'QA selected payload differs')
    sem = qa['checks']['semantic_qa']; scope = qa['qa_scope']
    require(sem['status'] == 'passed_current_publication_semantic_qa' and sem['date'] == day
            and sem['track_count'] == 5 and sem['song_ids'] == [t['apple_music']['song_id'] for t in tracks]
            and sem['canonical_ids'] == [t['canonical_id'] for t in tracks], 'Semantic QA mismatch')
    require(all(scope[k] == 'passed' for k in ('current_source_identity', 'current_semantic_editorial_review',
                                             'five_artifact_mirrors', 'history_preservation')), 'Source/semantic QA failed')
    require(qa['checks']['bundle'] == 'bundle_valid' and qa['checks']['planner'] == 'artifacts_match_next_stage_null', 'Incomplete publication')
    for label, key in [('manifest','ZJFILOVE/music-daily:' + mp), ('state','ZJFILOVE/music-daily:state/tracks.json'),
                       ('note','ZJFILOVE/obsidian-vault:Music/Daily/' + day + '.md')]:
        require(sem['sha256'][label] == hashlib.sha256(payloads[key].encode()).hexdigest(), 'Semantic payload digest mismatch')
    selected = []
    require(len(qa['source_verification']) == 5, 'Incomplete source evidence')
    for source, t in zip(qa['source_verification'], tracks):
        require(source['position'] == t['position'] and source['verification_method'] == 'direct_apple_music_cn_song_resource'
                and set(source['verified_fields']) >= {'title','artist','album','numeric_song_id','storefront','url_id_match'}
                and source['observed_at'] and source['web_observation_ref'], 'Missing actual source observations')
        selected.append({**source, 'id': source['song_id'], 'verification': source['verification_method']})
    heads = {'music': qa['stage_commits']['music_daily'], 'vault': qa['stage_commits']['obsidian_vault']}
    mm = manifest_map(music, heads['music'], 'recommendations')
    require(mm == manifest_map(vault, heads['vault'], 'Music/Daily/manifests'), 'Historical QA mirrors differ')
    require(qa['checks']['remote_snapshot'] == f'passed_current_music_mirrors_{len(mm)}_manifests', 'QA snapshot count mismatch')
    readbacks = {}
    for target in plan['targets']:
        repo, path = target['repository'], target['path']; key = repo + ':' + path
        legacy = 'music-daily:state/tracks.json' if key == 'ZJFILOVE/music-daily:state/tracks.json' else path
        require(qa['readbacks'][legacy] == target['target_sha'], 'QA readback differs from frozen target')
        readbacks[key] = dict(sha=qa['readbacks'][legacy], bytes=len(payloads[key].encode()))
    # Normalization is in memory only. No CI success is manufactured here.
    return dict(schema_version=1, date=day, project='music', operation_id='music:' + day,
                status='passed_current_publication', original_status=qa['status'],
                bundle=dict(path='.recommendation/transactions/music/' + day + '.json',
                            bundle_sha256=qa['bundle_sha256'], plan_sha256=qa['plan_sha256']),
                readbacks=readbacks, source_and_candidates=dict(selected=selected),
                checks=dict(freeze_and_baselines='passed', state_values_preserved=True, order_fields_and_sections='passed',
                            mirror_check=dict(status='passed_current_music_mirrors', heads=heads, manifest_blobs=mm,
                                              manifests=len(mm), state_blob_sha=blob_sha(payloads['ZJFILOVE/music-daily:state/tracks.json'].encode()))))


def normalize_gates(qa, bundle, music, vault, live_verified):
    """Verify committed evidence; retain the original receipt and its limited scope."""
    require(live_verified, 'Publication-gates QA requires independent live CI')
    plan, payloads = bundle['plan'], bundle['payloads']
    day = plan['date']; operation = 'music:' + day
    require(qa['schema_version'] == 1 and qa['project'] == 'music'
            and qa['date'] == day and qa['operation_id'] == operation, 'QA envelope mismatch')
    bp = '.recommendation/transactions/music/' + day + '.json'
    bound = qa['bundle']; creation = bound['creation_commit']
    require(bound['path'] == bp and bound['bundle_sha256'] == bundle['bundle_sha256']
            and bound['plan_sha256'] == bundle['plan_sha256'], 'QA frozen digest mismatch')
    def read(root, head, path):
        return git(root, 'show', head + ':' + path)
    git(vault, 'merge-base', '--is-ancestor', creation, 'HEAD')
    raw = read(vault, creation, bp)
    require(blob_sha(raw) == bound['blob_sha'] and loads(raw) == bundle
            and read(vault, 'HEAD', bp) == raw, 'Original PREPARED changed')
    ev = qa['source_evidence']; source_path = ev['path']
    require(source_path.startswith('.recommendation/transaction-runs/music/' + day + '/source-')
            and source_path.endswith('.json') and '..' not in source_path, 'Unexpected source evidence path')
    source_raw = read(vault, creation, source_path)
    require(read(vault, 'HEAD', source_path) == source_raw and blob_sha(source_raw) == ev['blob_sha']
            and hashlib.sha256(source_raw).hexdigest() == ev['sha256'], 'Source evidence hash mismatch')
    source = loads(source_raw)
    require(source['schema_version'] == 1 and source['project'] == 'music' and source['date'] == day
            and source['operation_id'] == operation
            and source['status'] == 'prepared_content_inputs_not_publication_receipt', 'Source scope mismatch')
    tracks = loads(payloads['ZJFILOVE/music-daily:recommendations/' + day[:4] + '/' + day[5:7] + '/' + day + '.json'])['tracks']
    ids = [t['apple_music']['song_id'] for t in tracks]
    require(len(tracks) == 5 and len(set(ids)) == 5 and qa['selection']['ordered_song_ids'] == ids
            and qa['selection']['canonical_ids'] == [t['canonical_id'] for t in tracks], 'QA selection mismatch')
    require(source['source_checks'] == ev['source_checks'] and len(ev['source_checks']) == 5, 'Source observations differ')
    pool = source['candidate_pool']
    require(source['candidate_pool_count'] == ev['candidate_pool_count'] == len(pool), 'Candidate count mismatch')
    from collections import Counter
    require(dict(Counter(c['decision'] for c in pool)) == ev['decision_counts'], 'Candidate decisions mismatch')
    candidates = [c for c in pool if c['decision'] in ('selected', 'selected_wildcard')]
    require([c['song_id'] for c in candidates] == ids, 'Selected candidate order differs')
    selected = []
    for t, observation, candidate in zip(tracks, ev['source_checks'], candidates):
        sid = t['apple_music']['song_id']; url = 'https://music.apple.com/cn/song/' + sid
        observed = urlsplit(observation['observed_url'])
        decoded_path = unquote(observed.path)
        require(not re.search(r'%2f|%5c', observed.path, re.IGNORECASE)
                and chr(92) not in decoded_path
                and not any(segment in ('.', '..') for segment in decoded_path.split('/')),
                'Ambiguous source resource path')
        require(observation['song_id'] == sid and observation['storefront'] == 'cn'
                and observation['requested_url'] == t['apple_music']['url'] == url
                and observed.scheme == 'https' and observed.netloc == 'music.apple.com'
                and re.fullmatch(r'/cn/song/(?:[^/]+/)?' + re.escape(sid), decoded_path)
                and not observed.query and not observed.fragment
                and all(observation[k] == t[k] for k in ('title', 'artist', 'album'))
                and observation['verified_at_utc'] == ev['verification_utc']
                and observation['observations'] and observation['scope'], 'Recording/source version mismatch')
        require(candidate['cn_metadata_verified'] is True and candidate['cn_url_tested'] == url
                and candidate['title'] == t['title'] and candidate['artist'] == t['artist'], 'Candidate source mismatch')
        selected.append(dict(id=sid, storefront='cn', url=url, title=t['title'], artist=t['artist'],
                             album=t['album'], verification=observation['observations'], scope=observation['scope']))
    checks = qa['qa']; fresh = qa['freshness']; pub = qa['publication']
    source_checks = source['checks']
    require(all(source_checks[k] == 'passed' for k in (
                'current_state_canonical_exclusion', 'normalized_artist_title_both_orientations',
                'current_manifest_numeric_id_exclusion', 'parent_reported_retired_numeric_exclusions',
                'full_ancestry_engineer_gate'))
            and all(source_checks[k] is True for k in (
                'preserve_prior_state_values', 'manifest_mirrors_byte_identical', 'state_mirrors_byte_identical',
                'exact_five_order_unique_ids', 'all_five_cn_song_metadata_verified',
                'note_required_sections_and_auto_markers')), 'Source checks explicitly failed or incomplete')
    require(checks['local_checks'] == {
                'integrity': 'passed_local_integrity',
                'immutable_bundles': 'passed_immutable_publication_bundles',
                'permanent_music_identity': 'complete_available_git_history'}, 'Local QA checks failed or incomplete')
    require(source_checks['original_state_count'] == fresh['state_original_count']
            and source_checks['target_state_count'] == fresh['state_target_count'], 'Source state counts differ')
    require(checks['status'] == 'passed_current_publication_qa'
            and checks['cross_repository_mirror_status'] == 'passed_current_music_mirrors'
            and all(checks[k] is True for k in ('manifest_byte_equal', 'state_byte_equal', 'cn_ids_order_exact_five'))
            and checks['detailed_chinese_sections_and_auto_markers'] == 'passed' and checks['editorial_basis']
            and fresh['all_original_values_preserved'] is True
            and fresh['full_ancestry_and_all_prepared'] == 'passed'
            and fresh['numeric_ids_all_storefronts_and_canonical_aliases'] == 'passed'
            and pub['final_replay_status'] == 'artifacts_match', 'Publication QA incomplete')
    heads = {'music': pub['stage1_commit'], 'vault': pub['stage2_commit']}
    require(len(pub['receipts']) == 2 and [r['stage'] for r in pub['receipts']] == [1, 2], 'Missing stage receipts')
    readbacks = {}
    for ref, short, root, repo in zip(pub['receipts'], ('music', 'vault'), (music, vault),
                                    ('ZJFILOVE/music-daily', 'ZJFILOVE/obsidian-vault')):
        require(ref['path'].startswith('.recommendation/transaction-runs/music/' + day + '/stage')
                and '..' not in ref['path'], 'Unexpected stage receipt path')
        rr = read(vault, 'HEAD', ref['path'])
        require(blob_sha(rr) == ref['blob_sha'], 'Stage receipt hash mismatch')
        receipt = loads(rr); head = heads[short]
        git(root, 'merge-base', '--is-ancestor', head, 'HEAD')
        require(receipt['status'] == 'stage_published_and_readback_verified' and receipt['stage'] == ref['stage']
                and receipt['operation_id'] == operation and receipt['repository'] == repo
                and receipt['commit'] == head and receipt['parent'] == git(root, 'rev-parse', head + '^').decode().strip()
                and receipt['prepared_creation_commit'] == creation and receipt['prepared_blob_sha'] == bound['blob_sha']
                and receipt['bundle_sha256'] == bundle['bundle_sha256'] and receipt['plan_sha256'] == bundle['plan_sha256']
                and receipt['final_replay_status'] == 'artifacts_match', 'Stage binding mismatch')
        targets = [t for t in plan['targets'] if t['repository'] == repo]
        require(set(receipt['readbacks']) == {repo + ':' + t['path'] for t in targets}, 'Stage target scope mismatch')
        for t in targets:
            key = repo + ':' + t['path']; frozen = payloads[key].encode()
            require(t['stage'] == ref['stage'] and read(root, head, t['path']) == frozen, 'Stage payload mismatch')
            rb = receipt['readbacks'][key]
            require(rb == pub['readbacks'][key] and rb == {'status': 'ok', 'sha': blob_sha(frozen)}
                    and t['target_sha'] == rb['sha'], 'Stage readback mismatch')
            base = git(root, 'ls-tree', receipt['parent'], '--', t['path']).decode().split()
            require((base[2] if base else None) == t['base_sha'], 'Frozen baseline mismatch')
            if t['path'].endswith('state/tracks.json'):
                before = loads(read(root, receipt['parent'], t['path']))['recommended']
                after = loads(frozen)['recommended']; preserve_state(before, after)
                require(len(before) == fresh['state_original_count'] and len(after) == fresh['state_target_count'], 'State count mismatch')
            readbacks[key] = dict(sha=rb['sha'], bytes=len(frozen))
    require(set(pub['readbacks']) == set(readbacks), 'Unexpected readback scope')
    mm = manifest_map(music, heads['music'], 'recommendations')
    require(mm == manifest_map(vault, heads['vault'], 'Music/Daily/manifests')
            and len(mm) == checks['manifest_dates_compared'], 'Historical QA mirror mismatch')
    return dict(schema_version=1, project='music', date=day, operation_id=operation,
                status='passed_current_publication', original_status=qa['status'], original_scope=qa['scope'],
                bundle=bound, readbacks=readbacks, source_and_candidates=dict(selected=selected),
                checks=dict(freeze_and_baselines='passed', state_values_preserved=True, order_fields_and_sections='passed',
                            mirror_check=dict(status='passed_current_music_mirrors', heads=heads, manifest_blobs=mm,
                                              manifests=len(mm), state_blob_sha=blob_sha(payloads['ZJFILOVE/music-daily:state/tracks.json'].encode()))))
