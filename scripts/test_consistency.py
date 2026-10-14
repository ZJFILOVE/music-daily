"""Negative tests for permanent history, identity, mirrors and idempotent repair."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import check_consistency as c

class ConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'music';self.vault=Path(self.tmp.name)/'vault'
        tracks=[{'position':i+1,'title':f'T{i}','artist':'A','canonical_id':f'A::T{i}','apple_music':{'url':f'https://music.apple.com/cn/song/{100+i}','song_id':str(100+i),'storefront':'cn'}} for i in range(5)]
        self.manifest={'schema_version':1,'provider':'apple_music','date':'2026-10-12','tracks':tracks}
        self.state={'schema_version':1,'provider':'apple_music','recommended':{t['canonical_id']:1 for t in tracks}}
        self.state['recommended']['Retired::Track']=1;self.ever=set(self.state['recommended'])
        self.write(self.root/'state/tracks.json',self.state)
        self.path=self.root/'recommendations/2026/10/2026-10-12.json';self.write(self.path,self.manifest)
        self.write(self.vault/'Music/Daily/manifests/2026-10-12.json',self.manifest)
        self.write(self.vault/'Music/Daily/state/tracks.json',self.state)
        self.note=self.vault/'Music/Daily/2026-10-12.md';self.note.parent.mkdir(parents=True,exist_ok=True)
        self.note.write_text('\n'.join(t['apple_music']['url'] for t in tracks))
        hp=self.vault/'.recommendation/history/english.jsonl';self.write(hp,{'date':'2026-10-12','source_url':'https://example.com/source'})
        for comp in ('Daily','Translation','Vocabulary'):
            p=self.vault/f'50-courses/English/{comp}/2026-10-12.md';p.parent.mkdir(parents=True);p.write_text('Source: https://example.com/source\n')
    def write(self,path,data):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False)+'\n')
    def run_check(self):return c.audit(self.root,self.vault,self.ever)
    def test_valid_cross_repo(self):self.assertEqual(self.run_check()['cross_repository'],'passed_byte_exact')
    def test_read_only_idempotent(self):
        before={str(p):p.read_bytes() for p in Path(self.tmp.name).rglob('*') if p.is_file()};a=self.run_check();b=self.run_check()
        self.assertEqual(a,b);self.assertEqual(before,{str(p):p.read_bytes() for p in Path(self.tmp.name).rglob('*') if p.is_file()})
    def test_retired_exposure_cannot_disappear(self):
        del self.state['recommended']['Retired::Track'];self.write(self.root/'state/tracks.json',self.state)
        with self.assertRaises(c.AuditError):self.run_check()
    def test_empty_history_fails(self):
        self.state['recommended']={};self.write(self.root/'state/tracks.json',self.state)
        with self.assertRaises(c.AuditError):self.run_check()
    def test_state_mirror_partial_write_fails(self):
        self.state['recommended']['Extra::Exposure']=1;self.write(self.root/'state/tracks.json',self.state)
        with self.assertRaises(c.AuditError):self.run_check()
    def test_manifest_mirror_fails(self):
        self.manifest['tracks'][0]['title']='Changed';self.write(self.vault/'Music/Daily/manifests/2026-10-12.json',self.manifest)
        with self.assertRaises(c.AuditError):self.run_check()
    def test_missing_english_history_fails(self):
        (self.vault/'.recommendation/history/english.jsonl').write_text('')
        with self.assertRaises(c.AuditError):self.run_check()
    def test_duplicate_append_fails(self):
        p=self.vault/'.recommendation/history/english.jsonl';p.write_text(p.read_text()*2)
        with self.assertRaises(c.AuditError):self.run_check()
    def test_wrong_storefront_fails(self):
        self.manifest['tracks'][0]['apple_music']['url']='https://music.apple.com/us/song/100';self.write(self.path,self.manifest)
        with self.assertRaises(c.AuditError):self.run_check()
    def test_url_id_mismatch_fails(self):
        self.manifest['tracks'][0]['apple_music']['url']='https://music.apple.com/cn/song/999';self.write(self.path,self.manifest)
        with self.assertRaises(c.AuditError):self.run_check()
    def test_changed_history_value_fails(self):
        with self.assertRaises(c.AuditError):c.preserve_state({'A::T':1},{'A::T':2})
    def test_nonfinite_and_duplicate_json_rejected(self):
        for text in ('{"x":1,"x":2}','{"x":NaN}'):
            with self.assertRaises(c.AuditError):c.loads(text)
    def test_invalid_calendar_date(self):
        with self.assertRaises(ValueError):c.iso_day('2026-02-30')
    def test_git_history_includes_deleted_manifest(self):
        repo=Path(self.tmp.name)/'history';repo.mkdir()
        def git(*args):return subprocess.check_output(['git','-C',str(repo),*args],stderr=subprocess.PIPE)
        git('init','-q');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
        p=repo/'recommendations/2026/09/2026-09-05.json';self.write(p,{'tracks':[{'canonical_id':'Retired::Track'}]})
        git('add','.');git('commit','-qm','original exposure');p.unlink();git('add','-A');git('commit','-qm','removed manifest')
        self.assertIn('Retired::Track',c.history_ids(repo))

if __name__=='__main__':unittest.main()
