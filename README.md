# music-daily

Cloud metadata repository for the Apple Music Daily 5 workflow.

This repository stores recommendations, deduplication state, schemas, and validation logic only. It must never contain audio files, Apple Music cookies, Apple IDs, passwords, access tokens, DRM material, or other credentials.

## Layout

- `recommendations/YYYY/MM/YYYY-MM-DD.json` — machine-readable Daily 5 manifest.
- `state/tracks.json` — recommendation history used for deduplication.
- `schemas/daily5.schema.json` — JSON Schema for manifests.
- `scripts/validate.py` — validation and duplicate checks.
- `scripts/apple_music_sync.py` — local or headless cloud playlist synchronization.

For cloud authentication, setup commands, and verification, see [CLOUD_SYNC.md](CLOUD_SYNC.md). Credentials and runtime checkpoints must remain outside this repository.

Human-readable Chinese appreciation notes live separately in `ZJFILOVE/obsidian-vault` under `Music/Daily/`.
