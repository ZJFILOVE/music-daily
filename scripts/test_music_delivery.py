import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import music_delivery as d


class LiveEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.api = object.__new__(d.GitHub)
        self.sha = 'a'*40
        self.run = dict(id=1, run_number=1, run_attempt=1, head_sha=self.sha, head_branch='main',
                        event='push', path=d.WORKFLOWS[d.MUSIC], head_repository=dict(full_name=d.MUSIC),
                        status='completed', conclusion='success')
        self.job=dict(name='validate', status='completed', conclusion='success')

    def response(self, runs, jobs=None):
        self.api.get=Mock(side_effect=lambda repo,path: {'workflow_runs':runs} if '/workflows/' in path else {'jobs':jobs or [self.job]})

    def test_exact_main_workflow_ci(self):
        self.response([self.run]); self.assertEqual(self.api.ci(d.MUSIC,self.sha),1)

    def test_same_sha_schedule_or_manual_without_push_is_authority(self):
        for event in ('schedule', 'workflow_dispatch'):
            run = {**self.run, 'event': event, 'path': d.WORKFLOWS[d.VAULT],
                   'head_repository': {'full_name': d.VAULT}}
            self.response([run])
            self.assertEqual(self.api.ci(d.VAULT, self.sha), 1)
            query = self.api.get.call_args_list[0].args[1]
            self.assertNotIn('event=', query)
            self.assertIn('head_sha=' + self.sha, query)

    def test_latest_scheduled_failure_or_running_blocks_old_push_green(self):
        for status, conclusion in [('completed', 'failure'), ('in_progress', None)]:
            run = {**self.run, 'id': 2, 'run_number': 2, 'event': 'schedule',
                   'status': status, 'conclusion': conclusion}
            self.response([self.run, run])
            with self.assertRaises(ValueError):
                self.api.ci(d.MUSIC, self.sha)

    def test_scheduled_green_on_previous_sha_cannot_cover_current_head(self):
        self.response([{**self.run, 'event': 'schedule', 'head_sha': 'b' * 40}])
        with self.assertRaises(ValueError):
            self.api.ci(d.MUSIC, self.sha)

    def test_wrong_head_repo_workflow_or_pr_is_not_authority(self):
        for field,value in [('head_sha','b'*40),('head_branch','feature'),('event','pull_request'),
                            ('path','.github/workflows/untrusted.yml'),('head_repository',{'full_name':'other/repo'})]:
            run=copy.deepcopy(self.run);run[field]=value;self.response([run])
            with self.assertRaises(ValueError):self.api.ci(d.MUSIC,self.sha)

    def test_latest_failed_or_pending_run_cannot_fallback_to_old_green(self):
        for state in ('failure','pending'):
            run={**self.run,'run_number':2,'conclusion':state};self.response([self.run,run])
            with self.assertRaises(ValueError):self.api.ci(d.MUSIC,self.sha)

    def test_failed_job_and_incomplete_jobs_rejected(self):
        self.response([self.run],[{**self.job,'conclusion':'failure'}])
        with self.assertRaises(ValueError):self.api.ci(d.MUSIC,self.sha)

    def test_head_move_during_ci_verification_stops(self):
        self.api.head=Mock(side_effect=[self.sha,self.sha,self.sha,'b'*40]);self.api.ci=Mock()
        with self.assertRaisesRegex(ValueError,'moved'):self.api.verify(self.sha,self.sha)

    def test_missing_credentials_no_network(self):
        with patch.dict(os.environ,{},clear=True),patch.object(d.urllib.request,'build_opener') as opener:
            with self.assertRaisesRegex(ValueError,'Missing read-only'):d.GitHub()
            opener.assert_not_called()

    def test_intent_cache_confirmation_requires_exact_main_key(self):
        key='apple-music-state-123-1-intent'
        for ref,size in [('refs/pull/1/merge',10),('refs/heads/main',0)]:
            self.api.get=Mock(return_value={'actions_caches':[dict(key=key,ref=ref,size_in_bytes=size)]})
            with self.assertRaises(ValueError):self.api.confirm_cache(key)
        self.api.get=Mock(return_value={'actions_caches':[dict(key=key,ref='refs/heads/main',size_in_bytes=10)]})
        self.api.confirm_cache(key)

    def test_redirect_does_not_forward_credentials(self):
        with self.assertRaises(RuntimeError):d.NoRedirect().redirect_request(None,None,None,None,None,None)


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'music';self.root.mkdir()
        self.vault=Path(self.tmp.name)/'vault';self.vault.mkdir()
        self.checkpoint=Path(self.tmp.name)/'private';self.checkpoint.mkdir()
        self.api=Mock()
        env=patch.dict(os.environ,{'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'})
        env.start();self.addCleanup(env.stop)
        self.plan=dict(schema_version=1,business_date='2026-11-07',music_head='a'*40,vault_head='b'*40,entries=[
            dict(date='2026-11-07',bundle_digest='c'*64,song_ids=['1','2','3','4','5']),
            dict(date='2026-11-08',bundle_digest='d'*64,song_ids=['6','7','8','9','10'])])
        (self.checkpoint/'write-intents.json').write_text(json.dumps(dict(schema_version=1,initialized=True,days={})))
        d.reserve(self.checkpoint,self.plan)

    def test_reserved_same_attempt_allowed_but_later_attempt_denied(self):
        entry=self.plan['entries'][0]
        d.write_guard(self.checkpoint,entry)
        with patch.dict(os.environ,{'GITHUB_RUN_ATTEMPT':'2'}):
            with self.assertRaisesRegex(ValueError,'cannot be blindly replayed'):d.write_guard(self.checkpoint,entry)

    def test_verified_operation_cannot_be_readded_after_external_removal(self):
        path=self.checkpoint/'write-intents.json';ledger=json.loads(path.read_text())
        ledger['days']['2026-11-07']['status']='verified';path.write_text(json.dumps(ledger))
        with self.assertRaises(ValueError):d.write_guard(self.checkpoint,self.plan['entries'][0])

    def test_missing_intent_cache_stops_before_writers(self):
        self.api.confirm_cache.side_effect=ValueError('intent not durable')
        with patch.object(d,'plan',return_value=self.plan),patch('daily_playlists_sync.sync') as daily:
            with self.assertRaises(ValueError):d.deliver(self.root,self.vault,self.checkpoint,self.plan,self.api,execute=True)
            daily.assert_not_called()

    def test_missing_baseline_does_not_create_empty_intent_ledger(self):
        (self.checkpoint/'write-intents.json').unlink()
        with self.assertRaisesRegex(ValueError,'read-only bootstrap'):d.reserve(self.checkpoint,self.plan)

    def test_bootstrap_requires_complete_existing_playlists_and_only_gets(self):
        (self.checkpoint/'write-intents.json').unlink()
        client=Mock();client.collection.return_value=[{'id':'p.total'}]
        client.playlist_tracks.return_value=[]
        with patch.object(d,'plan',return_value=self.plan),patch('apple_music_sync.load_auth',return_value={}),patch('apple_music_sync.Client',return_value=client),patch.dict(os.environ,{'APPLE_MUSIC_PLAYLIST_ID':'p.total'}):
            with self.assertRaisesRegex(ValueError,'missing/ambiguous'):d.bootstrap(self.root,self.vault,self.checkpoint,self.plan,self.api)
        client.request.assert_not_called()
        self.assertFalse((self.checkpoint/'write-intents.json').exists())

    def test_date_order_floor_and_future_dates(self):
        policy=self.root/'state/music-delivery-policy.json';policy.parent.mkdir()
        policy.write_text(json.dumps({'schema_version':1,'first_managed_date':'2026-11-07'}))
        folder=self.root/'recommendations/2026/11';folder.mkdir(parents=True)
        for day in ('2026-11-06','2026-11-09','2026-11-08','2026-11-07'):(folder/(day+'.json')).write_text('{}')
        self.assertEqual(d.dates(self.root,'2026-11-08'),['2026-11-07','2026-11-08'])

    def test_same_success_heads_is_cheap_noop(self):
        (self.checkpoint/'delivery-success.json').write_text(json.dumps(dict(schema_version=1,status='verified',business_date='2026-11-07',music_head='a'*40,vault_head='b'*40)))
        self.api.head.side_effect=['a'*40,'b'*40]
        self.assertTrue(d.unchanged(self.api,self.checkpoint,'2026-11-07'));self.assertEqual(self.api.head.call_count,2)
        self.api.verify.assert_not_called()

    def test_day_rollover_is_not_noop_even_with_same_heads(self):
        (self.checkpoint/'delivery-success.json').write_text(json.dumps(dict(schema_version=1,status='verified',business_date='2026-11-07',music_head='a'*40,vault_head='b'*40)))
        self.assertFalse(d.unchanged(self.api,self.checkpoint,'2026-11-08'))

    def test_head_advance_is_not_noop(self):
        (self.checkpoint/'delivery-success.json').write_text(json.dumps(dict(schema_version=1,status='verified',business_date='2026-11-07',music_head='a'*40,vault_head='b'*40)))
        self.api.head.return_value='c'*40
        self.assertFalse(d.unchanged(self.api,self.checkpoint,'2026-11-07'))

    def test_changed_or_missing_qa_never_calls_writers(self):
        with patch.object(d,'plan',side_effect=ValueError('QA missing')),patch('daily_playlists_sync.sync') as daily,patch('apple_music_sync.sync') as total:
            with self.assertRaises(ValueError):d.deliver(self.root,self.vault,self.checkpoint,self.plan,self.api,execute=True)
            daily.assert_not_called();total.assert_not_called()

    def test_plan_tamper_never_calls_writers(self):
        altered={**self.plan,'entries':[]}
        with patch.object(d,'plan',return_value=self.plan),patch('daily_playlists_sync.sync') as daily:
            with self.assertRaises(ValueError):d.deliver(self.root,self.vault,self.checkpoint,altered,self.api,execute=True)
            daily.assert_not_called()

    def test_dates_order_partial_failure_and_retry(self):
        calls=[]
        def daily(args):calls.append(('daily',args.date))
        def total(args):
            calls.append(('total',args.date))
            if args.date=='2026-11-07':raise RuntimeError('unknown write')
        with patch.object(d,'plan',return_value=self.plan),patch('daily_playlists_sync.sync',side_effect=daily),patch('apple_music_sync.sync',side_effect=total),patch.dict(os.environ,{'APPLE_MUSIC_PLAYLIST_ID':'p.fixture'}):
            with self.assertRaises(RuntimeError):d.deliver(self.root,self.vault,self.checkpoint,self.plan,self.api,execute=True)
        self.assertEqual(calls,[('daily','2026-11-07'),('total','2026-11-07')])
        self.assertFalse((self.checkpoint/'delivery-success.json').exists())
        calls=[]
        with patch.object(d,'plan',return_value=self.plan),patch('daily_playlists_sync.sync',side_effect=daily),patch('apple_music_sync.sync',side_effect=lambda args:calls.append(('total',args.date))),patch.dict(os.environ,{'APPLE_MUSIC_PLAYLIST_ID':'p.fixture'}):
            d.deliver(self.root,self.vault,self.checkpoint,self.plan,self.api,execute=True)
        self.assertEqual(calls,[('daily','2026-11-07'),('total','2026-11-07'),('daily','2026-11-08'),('total','2026-11-08')])
        self.assertEqual(json.loads((self.checkpoint/'delivery-success.json').read_text())['status'],'verified')

    def test_default_mode_never_writes(self):
        with patch.object(d,'plan',return_value=self.plan),patch('daily_playlists_sync.sync') as daily:
            d.deliver(self.root,self.vault,self.checkpoint,self.plan,self.api)
            daily.assert_not_called()

    def test_legacy_unknown_creation_is_not_discarded(self):
        (self.checkpoint/'total.json').write_text(json.dumps({'creation_pending':True}))
        with patch.object(d,'plan',return_value=self.plan),patch('daily_playlists_sync.sync') as daily,patch.dict(os.environ,{'APPLE_MUSIC_PLAYLIST_ID':'p.fixture'}):
            with self.assertRaisesRegex(ValueError,'Legacy total write unresolved'):
                d.deliver(self.root,self.vault,self.checkpoint,self.plan,self.api,execute=True)
            daily.assert_not_called()


if __name__=='__main__':unittest.main()
