#!/usr/bin/env bash
# Run with cloud network-secret placeholders, injected variables, or Legacy setup secrets.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

: "${APPLE_MUSIC_DEVELOPER_TOKEN:?Configure this secret in the cloud environment}"
: "${APPLE_MUSIC_USER_TOKEN:?Configure this secret in the cloud environment}"
: "${APPLE_MUSIC_PLAYLIST_ID:?Pin the existing playlist in the cloud environment}"

args=(--no-wrapper)
case "${APPLE_MUSIC_SYNC_EXECUTE:-0}" in
  0) ;;
  1) args+=(--execute) ;;
  *) echo 'APPLE_MUSIC_SYNC_EXECUTE must be 0 or 1' >&2; exit 2 ;;
esac
# Runtime checkpoint contains song IDs only, never authentication credentials.
if [[ -n "${APPLE_MUSIC_STATE_FILE:-}" ]]; then
  args+=(--state-file "$APPLE_MUSIC_STATE_FILE")
fi
exec python3 scripts/apple_music_sync.py "${args[@]}"
