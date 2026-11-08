"""Synthetic committed-evidence transport; no production data or network access."""
import copy
import hashlib
import json
import unittest
from unittest.mock import patch
from pathlib import Path
from recording_history import blob_sha, digest
from qa_receipt import normalize


class GatesReceiptTests(unittest.TestCase):
    def setUp(self):
        self.music, self.vault = Path('/fixture/music'), Path('/fixture/vault')
        self.day = '2030-01-02'; self.creation = 'a'*40
        self.heads = {'music': 'b'*40, 'vault': 'c'*40}; self.parent = 'd'*40
        self.bp = '.recommendation/transactions/music/' + self.day + '.json'
        self.sp = '.recommendation/transaction-runs/music/' + self.day + '/source-fixture.json'
        self.files = {}; self.baselines = {}
        tracks = [dict(position=i, title='Song'+str(i), artist='Artist'+str(i), album='Original version',
                       canonical_id='Artist'+str(i)+'::Song'+str(i),
                       apple_music=dict(song_id=str(i), storefront='cn', url='https://music.apple.com/cn/song/'+str(i)))
                  for i in range(1, 6)]
        self.tracks = tracks
        state = dict(schema_version=1, provider='apple_music', recommended={t['canonical_id']: 1 for t in tracks})
        self.before = dict(schema_version=1, provider='apple_music', recommended={})
        paths = [('music', f'recommendations/2030/01/{self.day}.json', {'tracks': tracks}),
                 ('music', 'state/tracks.json', state), ('vault', f'Music/Daily/{self.day}.md', 'fixture note'),
                 ('vault', f'Music/Daily/manifests/{self.day}.json', {'tracks': tracks}),
                 ('vault', 'Music/Daily/state/tracks.json', state)]
        payloads = {}; targets = []
        for short, path, data in paths:
            repo = 'ZJFILOVE/' + ('music-daily' if short == 'music' else 'obsidian-vault')
            raw = json.dumps(data); key = repo + ':' + path; payloads[key] = raw
            self.files[(short, self.heads[short], path)] = raw.encode()
            base = None
            if path.endswith('state/tracks.json'):
                before = json.dumps(self.before).encode(); base = blob_sha(before)
                self.files[(short, self.parent, path)] = before
            self.baselines[(short, path)] = base
            targets.append(dict(repository=repo, path=path, base_sha=base, target_sha=blob_sha(raw.encode()), stage=1 if short=='music' else 2))
        plan = dict(date=self.day, targets=targets)
        body = dict(plan=plan, payloads=payloads, plan_sha256=digest(plan))
        self.bundle = dict(body, bundle_sha256=digest(body))
        raw = json.dumps(self.bundle).encode()
        self.files[('vault', self.creation, self.bp)] = self.files[('vault', 'HEAD', self.bp)] = raw
        bound = dict(path=self.bp, creation_commit=self.creation, blob_sha=blob_sha(raw),
                     bundle_sha256=self.bundle['bundle_sha256'], plan_sha256=self.bundle['plan_sha256'])
        observations = [dict(song_id=t['apple_music']['song_id'], storefront='cn', requested_url=t['apple_music']['url'],
                             observed_url=t['apple_music']['url'], title=t['title'], artist=t['artist'], album=t['album'],
                             verified_at_utc='2030-01-02T01:00:00Z', observations='Metadata checked', scope='No playback claim') for t in tracks]
        pool = [dict(song_id=t['apple_music']['song_id'], title=t['title'], artist=t['artist'], decision='selected',
                     cn_metadata_verified=True, cn_url_tested=t['apple_music']['url']) for t in tracks]
        source = dict(schema_version=1, project='music', date=self.day, operation_id='music:'+self.day,
                      status='prepared_content_inputs_not_publication_receipt', candidate_pool_count=5,
                      candidate_pool=pool, source_checks=observations)
        sr = json.dumps(source).encode()
        self.files[('vault', self.creation, self.sp)] = self.files[('vault', 'HEAD', self.sp)] = sr
        ev = dict(path=self.sp, blob_sha=blob_sha(sr), sha256=hashlib.sha256(sr).hexdigest(), candidate_pool_count=5,
                  decision_counts={'selected':5}, source_checks=observations, verification_utc='2030-01-02T01:00:00Z')
        pub = dict(stage1_commit=self.heads['music'], stage2_commit=self.heads['vault'], receipts=[], readbacks={}, final_replay_status='artifacts_match')
        for stage, short in [(1,'music'), (2,'vault')]:
            repo = 'ZJFILOVE/' + ('music-daily' if short=='music' else 'obsidian-vault')
            rb = {t['repository']+':'+t['path']:dict(status='ok',sha=t['target_sha']) for t in targets if t['stage']==stage}
            receipt = dict(status='stage_published_and_readback_verified', stage=stage, operation_id='music:'+self.day,
                           repository=repo, commit=self.heads[short], parent=self.parent, prepared_creation_commit=self.creation,
                           prepared_blob_sha=bound['blob_sha'], bundle_sha256=bound['bundle_sha256'], plan_sha256=bound['plan_sha256'],
                           final_replay_status='artifacts_match', readbacks=rb)
            path = f'.recommendation/transaction-runs/music/{self.day}/stage{stage}-fixture.json'
            rr = json.dumps(receipt).encode(); self.files[('vault','HEAD',path)] = rr
            pub['receipts'].append(dict(path=path,blob_sha=blob_sha(rr),stage=stage));pub['readbacks'].update(rb)
        self.qa = dict(schema_version=1, project='music', date=self.day, operation_id='music:'+self.day,
                       status='passed_current_publication_gates', bundle=bound, source_evidence=ev,
                       selection=dict(ordered_song_ids=[str(i) for i in range(1,6)], canonical_ids=[t['canonical_id'] for t in tracks]),
                       freshness=dict(all_original_values_preserved=True,full_ancestry_and_all_prepared='passed',
                                      numeric_ids_all_storefronts_and_canonical_aliases='passed',state_original_count=0,state_target_count=5),
                       publication=pub,scope={'apple_playlist_writes':'none'},
                       qa=dict(status='passed_current_publication_qa',cross_repository_mirror_status='passed_current_music_mirrors',
                               manifest_byte_equal=True,state_byte_equal=True,cn_ids_order_exact_five=True,
                               detailed_chinese_sections_and_auto_markers='passed',editorial_basis='Original editorial work',manifest_dates_compared=1))
        self.mm = {self.day+'.json': targets[0]['target_sha']}

    def git(self, root, *args):
        short = 'music' if root == self.music else 'vault'
        if args[0]=='show':
            head,path = args[1].split(':',1);return self.files[(short,head,path)]
        if args[0]=='merge-base':
            self.assertIn(args[2], [self.creation,*self.heads.values()]);return b''
        if args[0]=='rev-parse':return self.parent.encode()
        if args[0]=='ls-tree':
            base=self.baselines[(short,args[-1])]
            return ('100644 blob '+base+'\t'+args[-1]).encode() if base else b''
        raise AssertionError(args)

    def run_normalize(self, live=True):
        with patch('qa_receipt.git',side_effect=self.git),patch('qa_receipt.manifest_map',return_value=self.mm):
            return normalize(self.qa,self.bundle,self.music,self.vault,live)

    def test_valid_contract_preserves_original_scope_and_bytes(self):
        original=json.dumps(self.qa);r=self.run_normalize()
        self.assertEqual(json.dumps(self.qa),original)
        self.assertEqual(r['original_scope'],{'apple_playlist_writes':'none'})
        self.assertEqual(r['original_status'],'passed_current_publication_gates')
        self.assertNotIn('ci',r)

    def test_requires_independent_live_ci(self):
        with self.assertRaises(ValueError):self.run_normalize(False)

    def test_modified_required_evidence_rejected(self):
        edits = [('bundle','bundle_sha256'),('bundle','plan_sha256'),('bundle','blob_sha'),
                 ('source_evidence','blob_sha'),('source_evidence','sha256'),('source_evidence','verification_utc'),
                 ('publication','stage1_commit'),('publication','stage2_commit'),('publication','final_replay_status'),
                 ('qa','status'),('qa','manifest_byte_equal'),('freshness','all_original_values_preserved')]
        old=copy.deepcopy(self.qa)
        for group,key in edits:
            with self.subTest(group=group,key=key):
                self.qa=copy.deepcopy(old);self.qa[group][key]='tampered'
                with self.assertRaises((ValueError,KeyError,AssertionError)):self.run_normalize()
        self.qa=old

    def test_missing_required_fields_rejected(self):
        old=copy.deepcopy(self.qa)
        for group in ['bundle','source_evidence','selection','freshness','publication','qa']:
            for key in old[group]:
                with self.subTest(group=group,key=key):
                    self.qa=copy.deepcopy(old);del self.qa[group][key]
                    with self.assertRaises((ValueError,KeyError)):self.run_normalize()
        self.qa=old

    def test_source_bytes_or_metadata_version_or_order_rejected(self):
        old=copy.deepcopy(self.qa)
        for field in ['song_id','storefront','album','title','artist','observed_url']:
            self.qa=copy.deepcopy(old);self.qa['source_evidence']['source_checks'][0][field]='tampered'
            with self.assertRaises(ValueError):self.run_normalize()
        self.qa=copy.deepcopy(old);self.qa['selection']['ordered_song_ids'].reverse()
        with self.assertRaises(ValueError):self.run_normalize()
        self.qa=old;self.files[('vault','HEAD',self.sp)]+=b' '
        with self.assertRaises(ValueError):self.run_normalize()

    def test_readback_and_stage_receipt_tamper_rejected(self):
        key=next(iter(self.qa['publication']['readbacks']))
        self.qa['publication']['readbacks'][key]['sha']='bad'
        with self.assertRaises(ValueError):self.run_normalize()
        self.setUp();path=self.qa['publication']['receipts'][0]['path']
        self.files[('vault','HEAD',path)]+=b' '
        with self.assertRaises(ValueError):self.run_normalize()

    def test_frozen_payload_and_original_state_tamper_rejected(self):
        key=next(iter(self.bundle['payloads']));self.bundle['payloads'][key]+=' '
        with self.assertRaises(ValueError):self.run_normalize()
        self.setUp();self.files[('music',self.parent,'state/tracks.json')]=json.dumps({'recommended':{'old':1}}).encode()
        with self.assertRaises(ValueError):self.run_normalize()

    def rewrite_source(self, change):
        source = json.loads(self.files[('vault',self.creation,self.sp)])
        change(source)
        raw = json.dumps(source).encode()
        self.files[('vault',self.creation,self.sp)] = self.files[('vault','HEAD',self.sp)] = raw
        ev = self.qa['source_evidence']
        ev['source_checks'] = copy.deepcopy(source['source_checks'])
        ev['blob_sha'] = blob_sha(raw);ev['sha256'] = hashlib.sha256(raw).hexdigest()

    def test_source_cross_date_and_traversal_paths_rejected(self):
        for path in ['.recommendation/transaction-runs/music/2030-01-03/source-fixture.json',
                     '.recommendation/transaction-runs/music/2030-01-02/source-../other.json']:
            with self.subTest(path=path):
                self.qa['source_evidence']['path'] = path
                with self.assertRaises(ValueError):self.run_normalize()

    def test_rehashed_source_url_region_id_host_query_fragment_rejected(self):
        for field,url in [('observed_url','https://music.apple.com/us/song/1'),
                          ('observed_url','https://music.apple.com/cn/song/99'),
                          ('observed_url','https://example.invalid/cn/song/1'),
                          ('observed_url','https://music.apple.com/cn/song/1?extra=1'),
                          ('observed_url','https://music.apple.com/cn/song/1#fragment'),
                          ('requested_url','https://music.apple.com/us/song/1')]:
            with self.subTest(field=field,url=url):
                self.setUp()
                self.rewrite_source(lambda s:s['source_checks'][0].update({field:url}))
                with self.assertRaises(ValueError):self.run_normalize()

    def test_legitimate_observed_song_slug_is_accepted(self):
        self.rewrite_source(lambda s:s['source_checks'][0].update(observed_url='https://music.apple.com/cn/song/song-one/1'))
        self.assertEqual(self.run_normalize()['source_and_candidates']['selected'][0]['id'],'1')

    def test_rehashed_source_version_and_order_changes_rejected(self):
        for change in [lambda s:s['source_checks'][0].update(album='Different remaster'),
                       lambda s:s['source_checks'].reverse(),
                       lambda s:s['candidate_pool'].reverse(),
                       lambda s:s['candidate_pool'][0].update(decision='not_selected_balance')]:
            self.setUp();self.rewrite_source(change)
            with self.assertRaises(ValueError):self.run_normalize()

    def test_verified_unselected_candidate_not_treated_as_selected(self):
        def change(s):
            s['candidate_pool'].append(dict(song_id='99',title='Other',artist='Other',
                decision='not_selected_balance',cn_metadata_verified=True,cn_url_tested='https://music.apple.com/cn/song/99'))
            s['candidate_pool_count']=6
        self.rewrite_source(change)
        self.qa['source_evidence']['candidate_pool_count']=6
        self.qa['source_evidence']['decision_counts']['not_selected_balance']=1
        self.assertEqual(len(self.run_normalize()['source_and_candidates']['selected']),5)
        self.qa['source_evidence']['decision_counts']['not_selected_balance']=2
        with self.assertRaises(ValueError):self.run_normalize()

    def test_rehashed_stage_receipt_wrong_operation_stage_or_parent_rejected(self):
        for key,value in [('operation_id','music:2030-01-03'),('stage',2),('parent','f'*40),
                          ('commit','f'*40),('prepared_blob_sha','f'*40),('plan_sha256','f'*64)]:
            with self.subTest(key=key):
                self.setUp();ref=self.qa['publication']['receipts'][0];path=ref['path']
                receipt=json.loads(self.files[('vault','HEAD',path)]);receipt[key]=value
                raw=json.dumps(receipt).encode();self.files[('vault','HEAD',path)]=raw;ref['blob_sha']=blob_sha(raw)
                with self.assertRaises(ValueError):self.run_normalize()

    def test_missing_readback_rejected(self):
        del self.qa['publication']['readbacks'][next(iter(self.qa['publication']['readbacks']))]
        with self.assertRaises(KeyError):self.run_normalize()

    def test_matching_baseline_hash_does_not_allow_old_state_value_loss(self):
        raw=json.dumps({'recommended':{'old-key':'original-value'}}).encode()
        self.files[('music',self.parent,'state/tracks.json')]=raw
        self.baselines[('music','state/tracks.json')]=blob_sha(raw)
        self.bundle['plan']['targets'][1]['base_sha']=blob_sha(raw)
        self.bundle['plan_sha256']=digest(self.bundle['plan'])
        body={k:v for k,v in self.bundle.items() if k!='bundle_sha256'}
        self.bundle['bundle_sha256']=digest(body)
        frozen=json.dumps(self.bundle).encode()
        self.files[('vault',self.creation,self.bp)]=self.files[('vault','HEAD',self.bp)]=frozen
        self.qa['bundle'].update(blob_sha=blob_sha(frozen),bundle_sha256=self.bundle['bundle_sha256'],plan_sha256=self.bundle['plan_sha256'])
        for ref in self.qa['publication']['receipts']:
            path=ref['path'];receipt=json.loads(self.files[('vault','HEAD',path)])
            receipt.update(prepared_blob_sha=blob_sha(frozen),bundle_sha256=self.bundle['bundle_sha256'],plan_sha256=self.bundle['plan_sha256'])
            rr=json.dumps(receipt).encode();self.files[('vault','HEAD',path)]=rr;ref['blob_sha']=blob_sha(rr)
        with self.assertRaisesRegex(ValueError,'state|State|old-key|preserv|disappear'):
            self.run_normalize()
