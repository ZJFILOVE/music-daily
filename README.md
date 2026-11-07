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

The proposed trusted-delivery workflow polls hourly for completed music QA on
trusted `main` snapshots. It requires a separately approved Vault read-only
credential and deployment; missing evidence fails closed. No stage-1 push or
caller-provided date/ref triggers playlist writes. See
[delivery setup and recovery](docs/trusted-music-delivery.md) and
[the publication gate](docs/publication-gate.md).

Historical behavior and playlist names:

- `ChatGPT Daily 5` keeps the combined listening collection.
- `ChatGPT Daily 5 · YYYY-MM-DD` keeps each day's five recommendations in listening order.
- All historical manifests are included. Repeated runs reuse existing playlists.
- Tracks unavailable in the China catalog are recorded in the dated playlist's
  description and the `dated-playlists` run artifact; no substitute is chosen automatically.

Existing repository secrets and private operation checkpoints are unchanged.
Do not rerun older workflows to bypass the gate. Structural audit results and
known-incident acknowledgments do not authorize a playlist write.
