"""One authorized, reversible withdrawal; never a general short-playlist exception."""
import hashlib
import json
from pathlib import Path
import subprocess

DAY = '2026-11-06'
MANIFEST = 'recommendations/2026/11/2026-11-06.json'
RECORD = '.audit/2026-11-06-recording-withdrawal.json'
ORIGINAL = 'b0b271bf1c9a1e083e8756b841c43e18e02ab11a'
TARGET = 'de69236647a44e52ece517afb18fc9091bd9e46e'
RECORD_SHA256 = '1ba09166e7754cb247fbcc5f1c0080a0a5321b1ffb9bfbe5f82759f47a3f55a2'


def blob(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify(root, raw=None):
    root = Path(root)
    receipt = (root / RECORD).read_bytes()
    require(hashlib.sha256(receipt).hexdigest() == RECORD_SHA256, 'Withdrawal record changed or expanded')
    original = subprocess.check_output(['git', '-C', str(root), 'cat-file', 'blob', ORIGINAL], stderr=subprocess.PIPE)
    require(blob(original) == ORIGINAL, 'Withdrawal original evidence unavailable')
    old = json.loads(original)
    removed = old['tracks'][2]
    require((old['date'], removed['position'], removed['canonical_id'], removed['apple_music']['song_id']) ==
            (DAY, 3, 'Brian Eno::An Ending (Ascent)', '714861225'), 'Wrong withdrawal original occurrence')
    expected = {**old, 'tracks': [t for t in old['tracks'] if t['position'] != 3]}
    expected_raw = (json.dumps(expected, ensure_ascii=False, indent=2) + '\n').encode()
    actual = (root / MANIFEST).read_bytes() if raw is None else raw
    require(blob(expected_raw) == TARGET and actual == expected_raw,
            'Withdrawal must remove only the exact duplicate and retain all other bytes/values')
    return json.loads(receipt)


def positions(root, day, raw):
    if day != DAY:
        return [1, 2, 3, 4, 5]
    verify(root, raw)
    return [1, 2, 4, 5]
