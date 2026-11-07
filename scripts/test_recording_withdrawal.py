import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import recording_history as h
import recording_withdrawal as w
import validate

ROOT = Path(__file__).resolve().parents[1]


class WithdrawalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.original = h.git(ROOT, 'cat-file', 'blob', w.ORIGINAL)
        subprocess.run(['git', '-C', str(self.root), 'hash-object', '-w', '--stdin'],
                       input=self.original, stdout=subprocess.PIPE, check=True)
        for path in (w.MANIFEST, w.RECORD):
            target = self.root / path; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / path).read_bytes())

    def test_exact_four_remaining_objects_and_document_fields(self):
        w.verify(self.root)
        old = json.loads(self.original); new = json.loads((self.root / w.MANIFEST).read_bytes())
        self.assertEqual(new, {**old, 'tracks': [t for t in old['tracks'] if t['position'] != 3]})
        self.assertEqual([t['position'] for t in new['tracks']], [1, 2, 4, 5])

    def test_old_frozen_payload_replay_is_rejected(self):
        (self.root / w.MANIFEST).write_bytes(self.original)
        with self.assertRaisesRegex(ValueError, 'only the exact duplicate'): w.verify(self.root)

    def test_missing_or_forged_withdrawal_is_rejected(self):
        path = self.root / w.RECORD
        doc = json.loads(path.read_bytes()); doc['position'] = 4
        path.write_text(json.dumps(doc))
        with self.assertRaisesRegex(ValueError, 'record changed'): w.verify(self.root)
        path.unlink()
        with self.assertRaises(OSError): w.verify(self.root)

    def test_other_track_change_or_second_deletion_is_rejected(self):
        path = self.root / w.MANIFEST; original = json.loads(path.read_bytes())
        for change in ('title', 'delete', 'renumber'):
            doc = copy.deepcopy(original)
            if change == 'title': doc['tracks'][0]['title'] += ' changed'
            if change == 'delete': doc['tracks'].pop()
            if change == 'renumber': doc['tracks'][2]['position'] = 3
            path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n')
            with self.assertRaises(ValueError): w.verify(self.root)

    def test_four_track_exception_does_not_apply_to_other_dates(self):
        path = self.root / 'other.json'; doc = json.loads((self.root / w.MANIFEST).read_bytes())
        doc['date'] = '2026-11-08'; path.write_text(json.dumps(doc))
        with patch.object(validate, 'ROOT', self.root):
            with self.assertRaises(SystemExit): validate.validate_manifest(path)

    def test_withdrawn_recording_still_refuses_third_exposure(self):
        evidence = h.incident(ROOT)
        rows = [r for occurrence in evidence['occurrences']
                for r in h.occurrences(h.git(ROOT, 'cat-file', 'blob', occurrence['manifest_blob']), 'history')]
        repeat = copy.deepcopy(next(r for r in rows if r['song_id'] == '714861225'))
        repeat.update(date='2026-11-08', canonical_id='New alias::An Ending (Ascent)')
        with self.assertRaises(h.AuditError): h.check_rows(rows + [repeat], evidence)

    def test_specific_schema_is_exact_document_not_general_four_track_rule(self):
        schema = json.loads((ROOT / 'schemas/withdrawn-2026-11-06.schema.json').read_bytes())
        self.assertEqual(schema['const'], json.loads((self.root / w.MANIFEST).read_bytes()))
        self.assertEqual(json.loads((ROOT / 'schemas/daily5.schema.json').read_bytes())['properties']['tracks']['minItems'], 5)


if __name__ == '__main__':
    unittest.main()
