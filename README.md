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

The `Update Apple Music playlists` GitHub Actions workflow runs when the daily
recommendation manifests are pushed to `main`, and can also be started manually.
It requires no running personal computer, local wrapper, or audio downloads.
The existing recommendation task continues publishing the same manifests.

- `ChatGPT Daily 5` keeps the combined listening collection.
- `ChatGPT Daily 5 · YYYY-MM-DD` keeps each day's five recommendations in listening order.
- All historical manifests are included. Repeated runs reuse existing playlists.
- Tracks unavailable in the China catalog are recorded in the dated playlist's
  description and the `dated-playlists` run artifact; no substitute is chosen automatically.

Configure `APPLE_MUSIC_DEVELOPER_TOKEN` and `APPLE_MUSIC_USER_TOKEN` as repository
Actions secrets. Credentials are read only from the environment in cloud mode.
If Apple expires or revokes the session, renew these secrets and rerun the failed
workflow. GitHub's normal workflow failure notifications identify failed updates.
Operation checkpoints live in the Actions cache, outside the repository; they
contain playlist IDs and pending operations, never credentials. An uncertain
playlist creation stops rather than blindly creating a duplicate.
