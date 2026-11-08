# Local single-day synchronization

The two playlist writers use `local_sync_gate.py` rather than the cross-repository
publication gate. Manual dispatch requires a date and runs on main only. Pushes
and PRs do not trigger delivery. The existing `contents: read` permission and
Apple secrets are unchanged. This branch is not deployed by pushing it.

The gate requires full, clean music Git history; validates current manifests,
permanent recording exposure including deleted manifests, canonical state and
exact withdrawal evidence; pins HEAD and the selected manifest blob. It requires
one explicit date, five tracks in positions 1–5, China catalog IDs, no replacements,
and rejects future dates and 2026-11-06. Each POST rechecks the local snapshot.

Total playlist synchronization remains missing-only for the specified day, without
reordering existing tracks. Daily synchronization requires an exact existing prefix
and final ordered five-track readback. Unavailable catalog entries cannot silently
produce a partial authorized publication. Existing creation/append checkpoints,
cache and concurrency locks remain; a pending append must be read back before any
resend. Cache eviction or loss prevents an absolute cross-runner exactly-once claim.
No new persistent intent ledger or cross-repository token is introduced.

Local history cannot prove Vault-only reservations or retired note aliases. Those
are not silently marked resolved, and their absence is not a new prerequisite for
synchronizing an already published local selection. The separate cross-repository
`publication_gate.py` remains available for auditing but is not called by writers.
Original recommendation/state files, withdrawal and incident evidence remain intact.
Vault and Journal files or workflows are not modified.

Read-only preflight (no Apple access):

```sh
python scripts/local_sync_gate.py --repo . --date 2026-11-08 --music-head FULL_MUSIC_SHA
```

Actual synchronization still requires an authorized execution environment with
working Apple access. This repair does not establish that access or claim that
any playlist has been synchronized.
