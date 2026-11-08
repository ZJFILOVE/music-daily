import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import apple_music_sync as sync


class FakeClient:
    def __init__(self, host, auth):
        self.host = host
        self.posts = []
        self.ids = {'1'}
        self.hidden = False
        self.fail_post = False

    def request(self, path, method='GET', body=None):
        if method == 'GET':
            return 200, {'data': [{'id': str(i)} for i in range(1, 6)]}
        self.posts.append(body)
        self.ids.update(t['id'] for t in body['data'])
        if self.fail_post:
            raise TimeoutError('uncertain write')
        return 204, {}

    def collection(self, path):
        return [{'id': 'p.existing', 'attributes': {'name': sync.NAME, 'canEdit': True}}]

    def playlist_tracks(self, playlist_id):
        ids = {'1'} if self.hidden else self.ids
        return [{'id': sid, 'type': 'songs'} for sid in ids]


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        folder = self.repo / 'recommendations/2026/10'
        folder.mkdir(parents=True)
        (folder / '2026-10-18.json').write_text(json.dumps({'tracks': [
            {'position': i, 'apple_music': {'song_id': str(i), 'storefront': 'cn'}}
            for i in range(1, 6)
        ]}))
        self.state = self.root / 'private/checkpoint.json'
        self.client = FakeClient('api.music.apple.com', {})
        patch('local_sync_gate.authorize_args', return_value=None).start()
        self.addCleanup(patch.stopall)
        patch.object(sync, 'Client', return_value=self.client).start()
        patch.object(sync.time, 'sleep').start()
        patch.dict(os.environ, {
            'APPLE_MUSIC_DEVELOPER_TOKEN': 'test-developer',
            'APPLE_MUSIC_USER_TOKEN': 'test-user',
            'APPLE_MUSIC_PLAYLIST_ID': '',
            'APPLE_MUSIC_API_HOST': '',
        }).start()

    def run_sync(self, *flags):
        with contextlib.redirect_stdout(io.StringIO()):
            sync.main(['--repo', str(self.repo), '--state-file', str(self.state),
                       '--no-wrapper', *flags])

    def test_env_auth_never_contacts_localhost(self):
        with patch.object(sync.urllib.request, 'build_opener', side_effect=AssertionError('network')):
            self.assertEqual(sync.load_auth(True)['music_user_token'], 'test-user')

    def test_incomplete_cloud_credentials_fail_without_wrapper(self):
        with patch.dict(os.environ, {'APPLE_MUSIC_USER_TOKEN': ''}):
            with self.assertRaisesRegex(RuntimeError, 'Set both'):
                sync.load_auth()

    def test_no_credentials_cloud_never_falls_back(self):
        with patch.dict(os.environ, {'APPLE_MUSIC_DEVELOPER_TOKEN': '', 'APPLE_MUSIC_USER_TOKEN': ''}):
            with patch.object(sync.urllib.request, 'build_opener', side_effect=AssertionError('network')):
                with self.assertRaisesRegex(RuntimeError, 'Cloud credentials missing'):
                    sync.load_auth(True)

    def test_read_only_does_not_write_checkpoint_or_playlist(self):
        self.run_sync()
        self.assertEqual(self.client.posts, [])
        self.assertFalse(self.state.exists())

    def test_append_is_idempotent_with_fresh_cloud_state(self):
        self.run_sync('--execute', '--playlist-id', 'p.existing')
        self.assertEqual(len(self.client.posts), 1)
        self.assertEqual([t['id'] for t in self.client.posts[0]['data']], ['2', '3', '4', '5'])
        self.state.unlink()  # A new ephemeral container must read Apple Music, not rely on a cache.
        self.run_sync('--execute', '--playlist-id', 'p.existing')
        self.assertEqual(len(self.client.posts), 1)
        report = json.loads(self.state.read_text())
        self.assertEqual(report['verified_count'], 5)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('test-user', self.state.read_text())

    def test_missing_pinned_playlist_never_creates(self):
        with self.assertRaisesRegex(RuntimeError, 'Configured Apple Music playlist was not found'):
            self.run_sync('--execute', '--playlist-id', 'p.deleted')
        self.assertEqual(self.client.posts, [])

    def test_uncertain_post_preserves_checkpoint_and_is_not_resent(self):
        self.client.fail_post = True
        with self.assertRaises(TimeoutError):
            self.run_sync('--execute')
        pending = json.loads(self.state.read_text())['pending_song_ids']
        self.assertEqual(pending, ['2', '3', '4', '5'])
        self.client.hidden = True
        with self.assertRaisesRegex(RuntimeError, 'Write not confirmed'):
            self.run_sync('--execute')
        self.assertEqual(len(self.client.posts), 1)
        self.assertEqual(json.loads(self.state.read_text())['pending_song_ids'], pending)

    def test_checkpoint_cannot_be_saved_inside_repo(self):
        with self.assertRaisesRegex(RuntimeError, 'outside the metadata repository'):
            sync.main(['--repo', str(self.repo), '--state-file', str(self.repo / 'token-state.json')])

    def test_authorized_write_is_scoped_to_one_manifest(self):
        authorization = dict(date='2026-10-18', manifest_path='recommendations/2026/10/2026-10-18.json',
                             song_ids=['1', '2', '3', '4', '5'])
        extra = self.repo / 'recommendations/2026/10/2026-10-19.json'
        extra.write_text(json.dumps({'tracks': [{'position': 1, 'apple_music': {'song_id': '99', 'storefront': 'cn'}}]}))
        with patch('local_sync_gate.authorize_args', return_value=authorization), patch('local_sync_gate.recheck') as recheck:
            self.run_sync('--execute')
        self.assertTrue(recheck.called)
        self.assertEqual(json.loads(self.state.read_text())['target_song_ids'], authorization['song_ids'])
        self.assertNotIn('99', str(self.client.posts))

    def test_authorized_write_rejects_replacement(self):
        authorization = dict(date='2026-10-18', manifest_path='recommendations/2026/10/2026-10-18.json',
                             song_ids=['1', '2', '3', '4', '5'])
        with patch('local_sync_gate.authorize_args', return_value=authorization):
            with self.assertRaisesRegex(RuntimeError, 'does not allow'):
                self.run_sync('--execute', '--replace', '1=99')
        self.assertEqual(self.client.posts, [])

    def test_lock_prevents_concurrent_writes(self):
        with sync.state_lock(self.state):
            with self.assertRaisesRegex(RuntimeError, 'Another Apple Music sync'):
                self.run_sync('--execute')


class HostTests(unittest.TestCase):
    def test_pagination_never_sends_tokens_to_another_host(self):
        client = sync.Client('api.music.apple.com', {'dev_token': 'x', 'music_user_token': 'y'})
        with patch.object(client.opener, 'open', side_effect=AssertionError('network')):
            with self.assertRaisesRegex(RuntimeError, 'unexpected pagination host'):
                client.request('https://example.com/steal')
            with self.assertRaisesRegex(RuntimeError, 'unexpected pagination host'):
                client.request('http://api.music.apple.com/v1/me')

    def test_unsupported_host_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'Unsupported'):
            sync.Client('example.com', {})


if __name__ == '__main__':
    unittest.main()
