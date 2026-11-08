import argparse
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import daily_playlists_sync as daily

class FakeClient:
    def __init__(self):
        self.playlists=[];self.ids=set();self.creates=0;self.fail=False
    def collection(self,p): return list(self.playlists)
    def playlist_tracks(self,p): return [{'id':x,'type':'songs'} for x in sorted(self.ids)]
    def request(self,path,method='GET',body=None):
        if path.endswith('/playlists') and method=='POST':
            self.creates+=1
            if self.fail: raise TimeoutError('unknown result')
            p={'id':'p.day','attributes':body['attributes']};self.playlists.append(p)
            self.ids.update(x['id'] for x in body['relationships']['tracks']['data'])
            return 201,{'data':[p]}
        self.ids.update(x['id'] for x in body['data']);return 204,{}

class DailySyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.args=argparse.Namespace(repo=root/'repo',state_file=root/'private/state.json',report=root/'report.json',api_host='amp-api.music.apple.com',no_wrapper=True,execute=True)
        self.client=FakeClient()
        self.tracks=[{'title':str(i),'artist':'Artist','apple_music':{'song_id':str(i)}} for i in range(1,6)]
        patch('local_sync_gate.authorize_args', return_value=None).start()
        self.addCleanup(patch.stopall)
        patch.object(daily.common,'load_auth',return_value={}).start()
        patch.object(daily.common,'Client',return_value=self.client).start()
        self.plan=patch.object(daily,'plans',return_value=[('2026-11-01',self.tracks,['1','2','3','4','5'])]).start()
    def run_sync(self):
        with contextlib.redirect_stdout(io.StringIO()):daily.sync(self.args)
    def test_creation_preserves_order_and_rerun_does_not_duplicate(self):
        self.run_sync();self.run_sync()
        self.assertEqual(self.client.creates,1)
        self.assertEqual(self.client.playlists[0]['attributes']['name'],'ChatGPT Daily 5 · 2026-11-01')
        self.assertEqual(json.loads(self.args.report.read_text())['playlists'][0]['verified_count'],5)
    def test_unknown_creation_result_blocks_recreation(self):
        self.client.fail=True
        with self.assertRaises(TimeoutError):self.run_sync()
        self.client.fail=False
        with self.assertRaisesRegex(RuntimeError,'unresolved'):self.run_sync()
        self.assertEqual(self.client.creates,1)
    def test_unknown_creation_recovers_when_playlist_becomes_visible(self):
        self.args.state_file.parent.mkdir(parents=True)
        self.args.state_file.write_text(json.dumps({'days':{'2026-11-01':{'creation_pending':True}}}))
        self.client.playlists=[{'id':'p.day','attributes':{'name':'ChatGPT Daily 5 · 2026-11-01'}}]
        self.client.ids={'1','2','3','4','5'}
        self.run_sync();self.assertEqual(self.client.creates,0)
    def test_duplicate_names_stop_without_writes(self):
        self.client.playlists=[{'id':str(i),'attributes':{'name':'ChatGPT Daily 5 · 2026-11-01'}} for i in range(2)]
        with self.assertRaisesRegex(RuntimeError,'duplicate'):self.run_sync()
        self.assertEqual(self.client.creates,0)
    def test_unavailable_track_is_explained_not_replaced(self):
        self.plan.return_value=[('2026-11-01',self.tracks,['1','2','3','4'])]
        self.run_sync();r=json.loads(self.args.report.read_text())['playlists'][0]
        self.assertEqual(r['verified_count'],4);self.assertEqual(r['unavailable'][0]['id'],'5')
        self.assertIn('国区暂不可用',self.client.playlists[0]['attributes']['description'])
    def test_authorized_existing_playlist_extra_track_blocks_append(self):
        authorization = dict(date='2026-11-01', song_ids=['1','2','3','4','5'])
        self.client.playlists=[{'id':'p.day','attributes':{'name':'ChatGPT Daily 5 · 2026-11-01'}}]
        self.client.ids={'1','99'}
        with patch('local_sync_gate.authorize_args', return_value=authorization):
            with self.assertRaisesRegex(RuntimeError, 'exact prefix'): self.run_sync()
        self.assertEqual(self.client.ids, {'1','99'})

    def test_authorized_creation_checks_scope_and_exact_order(self):
        authorization = dict(date='2026-11-01', song_ids=['1','2','3','4','5'])
        self.client.playlist_tracks=lambda p: [{'id':x, 'type':'songs'} for x in sorted(self.client.ids)]
        with patch('local_sync_gate.authorize_args', return_value=authorization), patch('local_sync_gate.recheck') as recheck:
            self.run_sync()
        self.plan.assert_called_once_with(self.args.repo, self.client, '2026-11-01')
        self.assertTrue(recheck.called)
        self.assertEqual(json.loads(self.args.report.read_text())['playlists'][0]['verified_count'], 5)

    def test_authorized_catalog_missing_song_blocks_all_writes(self):
        self.plan.return_value=[('2026-11-01',self.tracks,['1','2','3','4'])]
        with patch('local_sync_gate.authorize_args', return_value=dict(date='2026-11-01',song_ids=['1','2','3','4','5'])):
            with self.assertRaisesRegex(RuntimeError,'unavailable'):self.run_sync()
        self.assertEqual(self.client.creates,0)

    def test_authorized_out_of_order_playlist_blocks_all_writes(self):
        self.client.playlists=[{'id':'p.day','attributes':{'name':'ChatGPT Daily 5 · 2026-11-01'}}]
        self.client.playlist_tracks=lambda p:[{'id':x,'type':'songs'} for x in ['2','1']]
        with patch('local_sync_gate.authorize_args',return_value=dict(date='2026-11-01',song_ids=['1','2','3','4','5'])):
            with self.assertRaisesRegex(RuntimeError,'exact prefix'):self.run_sync()
        self.assertEqual(self.client.creates,0)

    def test_pending_append_requires_readback_not_resend(self):
        self.client.playlists=[{'id':'p.day','attributes':{'name':'ChatGPT Daily 5 · 2026-11-01'}}]
        self.args.state_file.parent.mkdir(parents=True)
        self.args.state_file.write_text(json.dumps({'days':{'2026-11-01':{'playlist_id':'p.day','pending_append':['4','5']}}}))
        with patch.object(self.client,'request') as request,patch.object(daily.common,'verify',side_effect=RuntimeError('unconfirmed append')):
            with self.assertRaisesRegex(RuntimeError,'unconfirmed'):self.run_sync()
            request.assert_not_called()

    def test_playlist_pagination_failure_never_writes(self):
        with patch.object(self.client,'collection',side_effect=RuntimeError('page two failed')),patch.object(self.client,'request') as request:
            with self.assertRaisesRegex(RuntimeError,'page two'):self.run_sync()
            request.assert_not_called()

    def creation_visibility_lifecycle(self, timeout):
        ids=['1','2','3','4','5']
        visible=['1','2']
        original=self.client.request
        def request(path,method='GET',body=None):
            result=original(path,method,body)
            if timeout:raise TimeoutError('created but response lost')
            return result
        self.client.playlist_tracks=lambda p:[{'id':x,'type':'songs'} for x in visible]
        authorization=dict(date='2026-11-01',song_ids=ids)
        with patch('local_sync_gate.authorize_args',return_value=authorization),patch('local_sync_gate.recheck'),patch.object(daily.common.time,'sleep'),patch.object(self.client,'request',side_effect=request) as posts:
            with self.assertRaises((TimeoutError,RuntimeError)):self.run_sync()
            checkpoint=json.loads(self.args.state_file.read_text())['days']['2026-11-01']
            self.assertTrue(checkpoint['creation_pending'])
            self.assertEqual(checkpoint['pending_creation_song_ids'],ids)
            self.assertEqual(posts.call_count,1)
            with self.assertRaises(RuntimeError):self.run_sync()
            self.assertEqual(posts.call_count,1)
            self.assertTrue(json.loads(self.args.state_file.read_text())['days']['2026-11-01']['creation_pending'])
            visible[:]=ids
            self.run_sync()
            self.assertEqual(posts.call_count,1)
            checkpoint=json.loads(self.args.state_file.read_text())['days']['2026-11-01']
            self.assertFalse(checkpoint['creation_pending'])
            self.assertNotIn('pending_creation_song_ids',checkpoint)
            self.run_sync();self.assertEqual(posts.call_count,1)

    def test_created_playlist_partial_visibility_never_appends_on_retry(self):
        self.creation_visibility_lifecycle(False)

    def test_creation_timeout_partial_visibility_never_appends_on_retry(self):
        self.creation_visibility_lifecycle(True)

    def test_dry_run_does_not_create(self):
        self.args.execute=False;self.run_sync();self.assertEqual(self.client.creates,0)
        self.assertFalse(self.args.state_file.exists())
if __name__=='__main__':unittest.main()
