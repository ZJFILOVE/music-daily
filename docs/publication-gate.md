# Recording history and publication gate

The 2026-11-06 selection was already synchronized: the combined playlist added
four recordings and the dated playlist read back five. Post-write uniqueness QA
failed. `.audit/2026-11-06-recording-collision.json` records precisely the two
occurrences of `apple_music:cn:714861225`, their original manifest Git blobs,
immutable PREPARED digest and failure receipt. Original manifests, canonical
state values, payloads, QA receipts, and playlists are unchanged by this repair.
The evidence file's SHA-256 is pinned in code. It is an incident acknowledgment,
not a song allowlist or successful QA. A third occurrence, changed original
manifest, changed evidence, or changed failure receipt is rejected.

## Read-only audit interface

```sh
python scripts/validate.py
python scripts/check_consistency.py --base-ref <full-original-music-commit>
python scripts/recording_history.py --vault-root /authorized/full/vault-checkout
python scripts/check_consistency.py --base-ref <full-original-music-commit> --vault-root /authorized/full/vault-checkout
python -m unittest discover -s scripts -p 'test_*.py' -v
```

Each recording is `apple_music:<storefront>:<song_id>`. The derived in-memory
index traverses every tree in the supplied HEAD's complete Git ancestry, including
deleted paths, overwritten manifests and retired selections. Shallow clones fail.
Historical alias corrections at the same date and position are one exposure;
different recordings formerly occupying that position all remain reserved. This
index grouping does not authorize changing or resuming a frozen publication.

With `--vault-root`, the index also walks all Vault manifest mirrors and historical
`.recommendation/transactions/music/*.json`. It verifies bundle/plan SHA-256,
every payload's Git blob hash, exact target sets, operation/date, exposed canonical
IDs, and manifest mirror equality. Deleted reservations remain occupied. Different
bundle digests for the same operation fail, including uncommitted modifications.
Repeated identical bundles are idempotent. Current working files are also indexed.
No code from the other checkout is executed.

Legacy search-only entries retain their original names and unresolved source
bytes. They are linked only to same-date, same-position, same-canonical historical
entries with direct recording IDs; all such identities remain reserved. State
keys without manifest identities are recovered from exact numbered artist/title
headings and unambiguous song links in historical Vault notes. These results keep
the original note blob evidence. Later direct URLs are candidates, not proof of
the original search result: the five September 4 originals remain unresolved even
when later candidates exist. Every linked identity remains permanently reserved.
A legacy row blocks publication only when it lacks this exact lineage evidence;
unmapped canonical keys also block publication. No single candidate is selected
as a fabricated original ID. No historical bytes or canonical values are normalized.

Audit output includes `recording_identities`, `known_incidents`,
`search_only_history_mappings`, `unresolved_historical_occurrences`,
`unlinked_historical_occurrences`,
`retired_note_evidence`, `unmapped_canonical_keys`, `prepared_history`,
`prepared_blob_versions`, and both repository HEADs. It always reports
`publication_authorized: false`. Without Vault, PREPARED/retired-note coverage is
explicitly incomplete. Empty/partial historical mirror snapshots contribute
actual exposures; current manifests still require five tracks.

## Usable positive publication gate

```sh
python scripts/publication_gate.py --repo /authorized/music \
  --vault-root /authorized/vault --date YYYY-MM-DD \
  --music-head <full-music-commit> --vault-head <full-vault-commit> \
  --bundle-digest <original-PREPARED-bundle-sha256>
```

The same five arguments are required with either sync script's `--execute`.
The gate has no network or Apple Music writes. It returns an authorization for
exactly one date and five ordered song IDs only after checking:

- Both complete checkouts are clean and at the caller-pinned HEADs. The caller
  must obtain those pins from trusted fresh repository observations. Local input
  alone cannot prove GitHub freshness or authenticate a downloaded checkout.
- Full historical identity/state preservation and byte-exact current mirrors.
- The original frozen bundle's digest, exact five target paths and byte-identical
  payloads in both pinned snapshots; retries cannot change a payload or select
  again using the same operation ID.
- The existing committed `.recommendation/runs/YYYY-MM-DD-music.json` format:
  `passed_current_publication`, matching operation/date, bundle/plan digests,
  five readback hashes/lengths, successful freeze/baseline/order checks, complete
  mirror hash map, state hash, and source QA matching all five recordings.
- `checks.mirror_check.heads` are ancestors of the pinned snapshots, every target
  remains byte-identical, and the receipt records successful `validate` CI for
  both checked heads. QA is itself committed after its checked Vault snapshot;
  requiring QA and the pre-QA snapshot to have the same commit would be impossible.
  `planner.content_qa=not_evaluated` is not used as a success/failure signal.
- The known failed November 6 operation is always denied. Missing/failed/stale
  QA, changed snapshots, altered bundles, unknown reservations and new duplicates
  fail before credentials or an API client are loaded.

A returned authorization is used only in-process. Both writers limit selection
to the authorized date, refuse replacements/partial catalog availability and
recheck local snapshots immediately before each write. Dated playlists must be
an exact prefix of the authorized sequence before append, and exact count, order
and IDs afterward. An extra, reordered, or unrelated track stops for explicit
repair; the code never blindly adds a sixth song. Existing private checkpoints
remain in use. Unit tests exercise success and refusal paths with fake clients.

## Remaining Actions integration

Stage-1 push delivery is removed. Manual dispatch specifies date, Vault HEAD and
bundle digest, with music pinned to `GITHUB_SHA`. The workflow invokes the real
gate before restoring checkpoints or injecting existing Apple Music secrets.
The two gated sync commands are present after that gate.

**Automatic end-to-end publication is not restored yet.** This repository's
Actions token cannot fetch the other private repository. A separately reviewed
trusted runner/integration must supply a fresh full authorized Vault checkout at
`$RUNNER_TEMP/authorized-vault`, with authenticated provenance and expected HEAD.
Today that input is absent in hosted Actions, so the real gate fails closed.
No persistent credential, token forwarding, permission expansion, override flag,
or self-asserted local success JSON was added. An authorized operator with both
fresh checkouts can run the gate locally; this repair did not run either writer
against Apple Music. Receipt provenance/remote freshness remains the trusted
caller's responsibility, not a claim made by the gate's local verification.
