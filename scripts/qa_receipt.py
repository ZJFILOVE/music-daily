"""Read existing QA formats without rewriting receipts or relabelling old CI."""
import hashlib
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
