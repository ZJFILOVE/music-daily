"""Positive and negative gate tests use committed fixtures, never Apple Music."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import publication_gate as g
import recording_history as h


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.music = Path(self.tmp.name) / 'music'; self.vault = Path(self.tmp.name) / 'vault'
        for root in (self.music, self.vault):
            root.mkdir(); self.git(root, 'init', '-q')
            self.git(root, 'config', 'user.name', 'Fixture'); self.git(root, 'config', 'user.email', 'fixture@example.invalid')
        self.day = '2026-11-07'
        self.mp = 'recommendations/2026/11/' + self.day + '.json'
        self.bp = '.recommendation/transactions/music/' + self.day + '.json'
        self.qp = '.recommendation/runs/' + self.day + '-music.json'
        self.doc = {'schema_version': 1, 'provider': 'apple_music', 'date': self.day, 'tracks': [
            dict(position=i, title='Track' + str(i), artist='Artist' + str(i), album='Album',
                 canonical_id='Artist' + str(i) + '::Track' + str(i),
                 apple_music=dict(song_id=str(i), storefront='cn', url='https://music.apple.com/cn/song/' + str(i)))
            for i in range(1, 6)]}
        state = dict(schema_version=1, provider='apple_music', recommended={t['canonical_id']: 1 for t in self.doc['tracks']})
        manifest_text, state_text = json.dumps(self.doc), json.dumps(state)
        self.payloads = {
            'ZJFILOVE/music-daily:' + self.mp: manifest_text,
            'ZJFILOVE/music-daily:state/tracks.json': state_text,
            'ZJFILOVE/obsidian-vault:Music/Daily/manifests/' + self.day + '.json': manifest_text,
            'ZJFILOVE/obsidian-vault:Music/Daily/state/tracks.json': state_text,
            'ZJFILOVE/obsidian-vault:Music/Daily/' + self.day + '.md': '\n'.join(t['apple_music']['url'] for t in self.doc['tracks']),
        }
        for key, text in self.payloads.items():
            repo, path = key.split(':', 1)
            self.write(self.music if repo.endswith('music-daily') else self.vault, path, text)
        self.write(self.vault, '.recommendation/history/english.jsonl', '')
        plan = dict(schema_version=1, project='music', date=self.day, operation_id='music:' + self.day,
                    exposed_resource_ids=[t['canonical_id'] for t in self.doc['tracks']],
                    targets=[dict(repository=k.split(':', 1)[0], path=k.split(':', 1)[1],
                                  target_sha=h.blob_sha(v.encode())) for k, v in self.payloads.items()])
        body = dict(schema_version=1, plan=plan, payloads=self.payloads, plan_sha256=h.digest(plan))
        self.bundle = dict(body, bundle_sha256=h.digest(body))
        self.write(self.vault, self.bp, json.dumps(self.bundle))
        self.mhead = self.commit(self.music); self.vhead = self.commit(self.vault)
        self.qa = dict(schema_version=1, operation_id='music:' + self.day, project='music', date=self.day,
            status='passed_current_publication',
            bundle=dict(path=self.bp, bundle_sha256=self.bundle['bundle_sha256'], plan_sha256=self.bundle['plan_sha256']),
            readbacks={k: dict(sha=h.blob_sha(v.encode()), bytes=len(v.encode())) for k,v in self.payloads.items()},
            checks=dict(freeze_and_baselines='passed', state_values_preserved=True, order_fields_and_sections='passed',
                mirror_check=dict(status='passed_current_music_mirrors', manifests=1,
                    manifest_blobs={self.day + '.json': h.blob_sha(manifest_text.encode())},
                    state_blob_sha=h.blob_sha(state_text.encode()), heads=dict(music=self.mhead, vault=self.vhead))),
            ci={head: [dict(status='completed', conclusion='success', jobs=[dict(name='validate', conclusion='success')])]
                for head in (self.mhead, self.vhead)},
            source_and_candidates=dict(selected=[dict(id=t['apple_music']['song_id'], storefront='cn',
                url=t['apple_music']['url'], title=t['title'], artist=t['artist'], album=t['album'],
                verification='verified official source') for t in self.doc['tracks']]))
        self.save_qa()

    def git(self, root, *args):
        return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE).decode().strip()

    def write(self, root, path, text):
        p = root / path; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text)

    def commit(self, root):
        self.git(root, 'add', '-A'); self.git(root, 'commit', '-qm', 'fixture')
        return self.git(root, 'rev-parse', 'HEAD')

    def save_qa(self):
        self.write(self.vault, self.qp, json.dumps(self.qa)); self.vhead = self.commit(self.vault)

    def run_gate(self, **overrides):
        options = dict(vault=self.vault, day=self.day, music_head=self.mhead, vault_head=self.vhead,
                       bundle_digest=self.bundle['bundle_sha256'])
        options.update(overrides)
        return g.require_publication_authorization(self.music, **options)

    def test_valid_later_day_and_identical_retry(self):
        result = self.run_gate()
        self.assertTrue(result['publication_authorized']); self.assertEqual(result['song_ids'], ['1','2','3','4','5'])
        self.assertEqual(self.run_gate(), result)

    def test_missing_qa(self):
        (self.vault / self.qp).unlink(); self.vhead = self.commit(self.vault)
        with self.assertRaises(RuntimeError): self.run_gate()

    def test_failed_qa(self):
        self.qa['status'] = 'blocked_current_publication'; self.save_qa()
        with self.assertRaises(RuntimeError): self.run_gate()

    def test_old_failed_date_even_with_success_string(self):
        with self.assertRaisesRegex(RuntimeError, '2026-11-06 remains failed'):
            self.run_gate(day='2026-11-06')

    def test_digest_mismatch(self):
        with self.assertRaisesRegex(RuntimeError, 'digest changed'): self.run_gate(bundle_digest='0'*64)

    def test_wrong_pinned_head(self):
        with self.assertRaisesRegex(RuntimeError, 'HEAD changed'): self.run_gate(music_head='0'*40)

    def test_forged_source_qa(self):
        self.qa['source_and_candidates']['selected'][0]['id'] = '999'; self.save_qa()
        with self.assertRaises(RuntimeError): self.run_gate()

    def test_failed_ci(self):
        self.qa['ci'][self.mhead][0]['conclusion'] = 'failure'; self.save_qa()
        with self.assertRaises(RuntimeError): self.run_gate()

    def test_wrong_readback_hash(self):
        self.qa['readbacks']['ZJFILOVE/music-daily:' + self.mp]['sha'] = '0'*40; self.save_qa()
        with self.assertRaises(RuntimeError): self.run_gate()

    def test_dirty_checkout_and_prewrite_change(self):
        authorized = self.run_gate()
        self.write(self.music, self.mp, json.dumps(self.doc) + '\n')
        with self.assertRaises(RuntimeError): self.run_gate()
        with self.assertRaises(ValueError): g.recheck(authorized)

    def test_frozen_payload_changed_even_if_new_bundle_rehashed(self):
        changed = copy.deepcopy(self.bundle)
        key = 'ZJFILOVE/music-daily:' + self.mp
        changed['payloads'][key] += '\n'
        changed['plan']['targets'][0]['target_sha'] = h.blob_sha(changed['payloads'][key].encode())
        mirror = 'ZJFILOVE/obsidian-vault:Music/Daily/manifests/' + self.day + '.json'
        changed['payloads'][mirror] += '\n'
        for target in changed['plan']['targets']:
            k = target['repository'] + ':' + target['path']
            target['target_sha'] = h.blob_sha(changed['payloads'][k].encode())
        changed['plan_sha256'] = h.digest(changed['plan'])
        changed['bundle_sha256'] = h.digest({k:v for k,v in changed.items() if k != 'bundle_sha256'})
        self.write(self.vault, self.bp, json.dumps(changed)); self.vhead = self.commit(self.vault)
        with self.assertRaisesRegex(RuntimeError, 'Immutable PREPARED changed'): self.run_gate()

    def test_new_mirror_snapshot_without_new_qa_is_rejected(self):
        state = json.loads(self.payloads['ZJFILOVE/music-daily:state/tracks.json'])
        state['recommended']['Other::Song'] = 1
        self.write(self.music, 'state/tracks.json', json.dumps(state)); self.mhead = self.commit(self.music)
        self.write(self.vault, 'Music/Daily/state/tracks.json', json.dumps(state)); self.vhead = self.commit(self.vault)
        with self.assertRaises(RuntimeError): self.run_gate()


if __name__ == '__main__':
    unittest.main()
