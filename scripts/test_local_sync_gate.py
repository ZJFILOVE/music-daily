import argparse
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import local_sync_gate as gate


class LocalGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.git('init','-q');self.git('config','user.name','Fixture');self.git('config','user.email','fixture@example.invalid')
        self.day='2020-01-02';self.path='recommendations/2020/01/'+self.day+'.json'
        self.doc=dict(schema_version=1,provider='apple_music',date=self.day,tracks=[
            dict(position=i,title='Song'+str(i),artist='Artist'+str(i),canonical_id='Artist'+str(i)+'::Song'+str(i),
                 apple_music=dict(song_id=str(i),storefront='cn',url='https://music.apple.com/cn/song/'+str(i))) for i in range(1,6)])
        self.write(self.path,self.doc)
        self.write('state/tracks.json',dict(schema_version=1,provider='apple_music',recommended={t['canonical_id']:1 for t in self.doc['tracks']}))
        self.commit();self.args=argparse.Namespace(repo=self.root,date=self.day,replace=[])

    def git(self,*args):return subprocess.check_output(['git','-C',str(self.root),*args],stderr=subprocess.PIPE).decode().strip()
    def write(self,path,doc):
        p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(doc))
    def commit(self):self.git('add','-A');self.git('commit','-qm','fixture')

    def test_valid_local_day_without_vault_or_ci(self):
        auth=gate.authorize_args(self.args)
        self.assertEqual(auth['song_ids'],['1','2','3','4','5'])
        self.assertEqual(auth['manifest_path'],self.path)
        gate.recheck(auth)

    def test_explicit_date_withdrawn_date_and_future_date_rejected(self):
        for day in (None,'2026-11-06','2999-01-01'):
            self.args.date=day
            with self.assertRaises(ValueError):gate.authorize_args(self.args)

    def test_replacement_and_wrong_pinned_head_rejected(self):
        self.args.replace=['1=99']
        with self.assertRaises(ValueError):gate.authorize_args(self.args)
        self.args.replace=[];self.args.music_head='a'*40
        with self.assertRaises(ValueError):gate.authorize_args(self.args)

    def test_dirty_selection_and_new_commit_between_writes_rejected(self):
        auth=gate.authorize_args(self.args)
        self.doc['tracks'].reverse();self.write(self.path,self.doc)
        with self.assertRaises(ValueError):gate.recheck(auth)
        self.commit()
        with self.assertRaises(ValueError):gate.recheck(auth)

    def test_alias_repeat_from_deleted_history_is_rejected(self):
        old=copy.deepcopy(self.doc);old['date']='2019-01-01'
        path='recommendations/2019/01/2019-01-01.json';self.write(path,old);self.commit()
        (self.root/path).unlink();self.commit()
        with self.assertRaisesRegex(ValueError,'reused'):gate.authorize_args(self.args)

    def test_order_and_count_rejected(self):
        self.doc['tracks'].reverse();self.write(self.path,self.doc);self.commit()
        with self.assertRaises(ValueError):gate.authorize_args(self.args)

    def test_failed_gate_never_loads_credentials_or_contacts_apple(self):
        import apple_music_sync as total
        import daily_playlists_sync as daily
        self.args.execute=True;self.args.date=None
        for module in (total,daily):
            with patch.object(total,'load_auth') as auth,patch.object(total,'Client') as client:
                with self.assertRaises(ValueError):module.sync(self.args)
                auth.assert_not_called();client.assert_not_called()


    def test_permanent_state_key_loss_rejected(self):
        state=json.loads((self.root/'state/tracks.json').read_text())
        del state['recommended'][self.doc['tracks'][0]['canonical_id']]
        self.write('state/tracks.json',state);self.commit()
        with self.assertRaises(ValueError):gate.authorize_args(self.args)
