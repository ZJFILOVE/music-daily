import argparse
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import apple_music_sync
import daily_playlists_sync
import recording_history as h

ROOT = Path(__file__).resolve().parents[1]


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.git('init', '-q'); self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], stderr=subprocess.PIPE)

    def manifest(self, day='2020-01-01', sid='123', artist='Alias A'):
        return {'schema_version': 1, 'provider': 'apple_music', 'date': day, 'tracks': [
            {'position': 1, 'artist': artist, 'title': 'Track', 'canonical_id': artist + '::Track',
             'apple_music': {'storefront': 'cn', 'song_id': sid, 'url': 'https://music.apple.com/cn/song/' + sid}}]}

    def write(self, path, doc):
        p = self.root / path; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc)); return p

    def commit(self):
        self.git('add', '-A'); self.git('commit', '-qm', 'fixture')

    def rows(self, doc):
        return h.occurrences(json.dumps(doc).encode(), 'fixture')

    def test_alias_and_old_history_reuse_blocked(self):
        rows = self.rows(self.manifest()) + self.rows(self.manifest('2026-11-07', artist='Alias B'))
        with self.assertRaisesRegex(h.AuditError, 'Recording reused'):
            h.check_rows(rows, None)

    def test_same_occurrence_multiple_snapshots_and_alias_correction(self):
        rows = self.rows(self.manifest())
        self.assertEqual(h.check_rows(rows * 3 + self.rows(self.manifest(artist='Alias B')), None), [])

    def test_deleted_and_replaced_tracks_remain_reserved(self):
        p = self.write('recommendations/2020/01/2020-01-01.json', self.manifest()); self.commit()
        self.write(str(p.relative_to(self.root)), self.manifest(sid='456')); self.commit()
        p.unlink(); self.commit()
        rows = [r for path, sha, raw in h.historical_blobs(self.root, ['recommendations'])
                for r in h.occurrences(raw, path)]
        self.assertEqual({r['song_id'] for r in rows}, {'123', '456'})
        for sid in ('123', '456'):
            with self.assertRaises(h.AuditError):
                h.check_rows(rows + self.rows(self.manifest('2026-11-07', sid, 'Other')), None)

    def test_shallow_history_fails(self):
        self.write('recommendations/2020/01/2020-01-01.json', self.manifest()); self.commit()
        (self.root / '.git/shallow').write_bytes(self.git('rev-parse', 'HEAD'))
        with self.assertRaisesRegex(h.AuditError, 'Full Git history'):
            list(h.historical_blobs(self.root, ['recommendations']))

    def test_search_only_history_is_explicitly_unresolved(self):
        doc = self.manifest(); doc['tracks'][0]['apple_music'] = {
            'song_id': None, 'url': 'https://music.apple.com/cn/search?term=Track'}
        row = self.rows(doc)[0]
        self.assertIsNone(row['song_id']); self.assertEqual(row['canonical_id'], 'Alias A::Track')

    def test_search_lineage_preserves_original_unknown_and_all_later_ids(self):
        doc = self.manifest()
        doc['tracks'][0]['apple_music'] = {'song_id': None, 'url': 'https://music.apple.com/cn/search?term=Track'}
        path = 'recommendations/2020/01/2020-01-01.json'
        self.write(path, doc)
        self.write('state/tracks.json', {'recommended': {'Alias A::Track': 1}})
        self.commit()
        first = h.audit_history(self.root)
        self.assertEqual(len(first['unlinked_historical_occurrences']), 1)
        self.write(path, self.manifest(sid='123')); self.commit()
        self.write(path, self.manifest(sid='456')); self.commit()
        result = h.audit_history(self.root)
        self.assertEqual(len(result['unresolved_historical_occurrences']), 1)
        self.assertEqual(result['unlinked_historical_occurrences'], [])
        self.assertEqual(result['search_only_history_mappings'][0]['historically_linked_identities'],
                         ['apple_music:cn:123', 'apple_music:cn:456'])

    def test_uncommitted_direct_link_cannot_resolve_search_history(self):
        doc = self.manifest()
        doc['tracks'][0]['apple_music'] = {'song_id': None, 'url': 'https://music.apple.com/cn/search?term=Track'}
        path = 'recommendations/2020/01/2020-01-01.json'
        self.write(path, doc); self.write('state/tracks.json', {'recommended': {'Alias A::Track': 1}}); self.commit()
        self.write(path, self.manifest())
        self.assertEqual(len(h.audit_history(self.root)['unlinked_historical_occurrences']), 1)

    def test_earlier_direct_id_cannot_resolve_later_unknown_exposure(self):
        path = 'recommendations/2020/01/2020-01-01.json'
        self.write(path, self.manifest()); self.write('state/tracks.json', {'recommended': {'Alias A::Track': 1}}); self.commit()
        doc = self.manifest()
        doc['tracks'][0]['apple_music'] = {'song_id': None, 'url': 'https://music.apple.com/cn/search?term=Track'}
        self.write(path, doc); self.commit()
        self.assertEqual(len(h.audit_history(self.root)['unlinked_historical_occurrences']), 1)

    def incident_rows(self):
        evidence = h.incident(ROOT)
        rows = []
        for r in evidence['occurrences']:
            rows += h.occurrences((ROOT / r['manifest_path']).read_bytes(), r['manifest_path'])
        return evidence, rows

    def test_exact_incident_allows_unrelated_future_date(self):
        evidence, rows = self.incident_rows()
        rows += self.rows(self.manifest('2026-11-07'))
        self.assertEqual(h.check_rows(rows, evidence), [evidence['incident_id']])

    def test_incident_third_occurrence_fails(self):
        evidence, rows = self.incident_rows()
        with self.assertRaises(h.AuditError):
            h.check_rows(rows + self.rows(self.manifest('2026-11-07', '714861225', 'Another Alias')), evidence)

    def test_incident_original_manifest_bytes_changed_fails(self):
        evidence, rows = self.incident_rows()
        for row in rows:
            if row['song_id'] == '714861225':
                row['manifest_blob'] = '0' * 40; break
        with self.assertRaises(h.AuditError): h.check_rows(rows, evidence)

    def test_incident_evidence_tamper_or_expansion_fails(self):
        evidence = h.incident(ROOT)
        for field, value in [('song_id', '*'), ('occurrences', evidence['occurrences'] * 2)]:
            changed = copy.deepcopy(evidence); changed[field] = value
            self.write(h.INCIDENT_PATH, changed)
            with self.assertRaisesRegex(h.AuditError, 'evidence changed'):
                h.incident(self.root)

    def bundle(self):
        day = '2020-01-01'; doc = self.manifest(day)
        payload = json.dumps(doc)
        payloads = {'ZJFILOVE/music-daily:recommendations/2020/01/' + day + '.json': payload,
                    'ZJFILOVE/obsidian-vault:Music/Daily/manifests/' + day + '.json': payload}
        plan = dict(project='music', date=day, operation_id='music:' + day,
                    exposed_resource_ids=['Alias A::Track'],
                    targets=[dict(repository=k.split(':')[0], path=k.split(':')[1], target_sha=h.blob_sha(v.encode()))
                             for k, v in payloads.items()])
        body = dict(schema_version=1, plan=plan, payloads=payloads, plan_sha256=h.digest(plan))
        return dict(body, bundle_sha256=h.digest(body))

    def prepared(self, bundle):
        return h.prepared_rows(json.dumps(bundle).encode(), '.recommendation/transactions/music/2020-01-01.json')

    def test_prepared_reservation_blocks_other_operation(self):
        rows = self.prepared(self.bundle())
        with self.assertRaises(h.AuditError):
            h.check_rows(rows + self.rows(self.manifest('2026-11-07', artist='Alias B')), None)

    def test_same_prepared_readback_idempotent(self):
        rows = self.prepared(self.bundle())
        self.assertEqual(h.check_rows(rows + rows, None), [])

    def test_prepared_hashes_and_operation_not_just_id_text(self):
        for field in ('bundle_sha256', 'plan_sha256'):
            b = self.bundle(); b[field] = '0' * 64
            with self.assertRaises(h.AuditError): self.prepared(b)
        b = self.bundle(); b['plan']['date'] = '2026-11-07'
        b['plan_sha256'] = h.digest(b['plan']); b['bundle_sha256'] = h.digest({k:v for k,v in b.items() if k != 'bundle_sha256'})
        with self.assertRaises(h.AuditError): self.prepared(b)

    def test_deleted_prepared_is_still_enumerated(self):
        p = self.write('.recommendation/transactions/music/2020-01-01.json', self.bundle()); self.commit()
        p.unlink(); self.commit()
        rows = [r for path, sha, raw in h.historical_blobs(self.root, ['.recommendation/transactions/music'])
                for r in h.prepared_rows(raw, path)]
        self.assertEqual(rows[0]['song_id'], '123')


class PublicationGateTests(unittest.TestCase):
    def test_no_or_failed_or_forged_success_qa_never_touches_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for status in (None, 'failed', 'passed'):
                if status:
                    (root / 'qa.json').write_text(json.dumps({'status': status}))
                for module in (apple_music_sync, daily_playlists_sync):
                    args = argparse.Namespace(repo=root, execute=True)
                    with patch.object(apple_music_sync, 'load_auth') as auth, patch.object(apple_music_sync, 'Client') as client:
                        with self.assertRaisesRegex(RuntimeError, 'publication blocked'):
                            module.sync(args)
                        auth.assert_not_called(); client.assert_not_called()

    def test_failed_incident_always_blocked(self):
        import publication_gate
        with self.assertRaisesRegex(RuntimeError, '2026-11-06 remains failed'):
            publication_gate.require_publication_authorization(ROOT, day='2026-11-06')


if __name__ == '__main__':
    unittest.main()
