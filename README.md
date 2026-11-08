# music-daily

Cloud metadata repository for the Apple Music Daily 5 workflow.

This repository stores recommendations, deduplication state, schemas, and validation logic only. It must never contain audio files, Apple Music cookies, Apple IDs, passwords, access tokens, DRM material, or other credentials.

## Layout

- `recommendations/YYYY/MM/YYYY-MM-DD.json` — machine-readable Daily 5 manifest.
- `state/tracks.json` — recommendation history used for deduplication.
- `schemas/daily5.schema.json` — JSON Schema for manifests.
- `scripts/validate.py` — validation and duplicate checks.

Human-readable Chinese appreciation notes live separately in `ZJFILOVE/obsidian-vault` under `Music/Daily/`.

## Cloud playlist delivery

The single-day entry is a manual workflow dispatch with an explicit `date`.
Both writers validate the clean music checkout, complete local Git recording
history, withdrawal evidence and exact five-song selection before loading Apple
credentials. They recheck the pinned snapshot immediately before writes.
Vault checkout, live CI queries and new write-intent records are not required.
This branch must be reviewed and deployed before the updated entry is available.

- `ChatGPT Daily 5` receives only missing tracks from the selected day; existing
  tracks are not reordered or replaced.
- `ChatGPT Daily 5 · YYYY-MM-DD` must match the exact five-song listening order.
- Missing catalog tracks, extra/out-of-order daily tracks, ambiguous playlists,
  and unresolved writes stop synchronization. November 6 is always rejected.
- Existing private checkpoints, cache and locks are retained. Cache loss does not
  provide an absolute exactly-once guarantee; uncertain operations require
  readback or review, not blind replay.

This is local published-selection validation. It does not claim to inspect
Vault-only PREPARED reservations or retired note aliases. Original unknown
history/mappings remain recorded. No new Vault token or permissions are needed.
See [local synchronization](docs/local-single-day-sync.md).
