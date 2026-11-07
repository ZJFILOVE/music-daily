# Trusted music delivery (proposal; not deployed)

## Approval and exact credential contract

No credential has been created, uploaded, copied from the assistant connector or
configured by this change. The previous secret-name query returned Forbidden;
no verified existing read-only credential is known. Reuse one only after its
owner confirms the exact scope.

Recommended initial mechanism: a fine-grained personal access token limited to
**ZJFILOVE/obsidian-vault only**, with **Contents: read**, **Actions: read**, and
implicit Metadata read; suggested expiry 30 days. The user creates it in GitHub
Settings → Developer settings → Personal access tokens → Fine-grained tokens,
and places it directly in music-daily Settings → Secrets and variables → Actions
as the repository secret **MUSIC_VAULT_READ_TOKEN**. Never paste the value in chat.
The workflow maps it to `VAULT_READ_TOKEN`. The builtin music `GITHUB_TOKEN` needs
`contents: read` and `actions: read` for main, CI and cache metadata. Neither token
needs write permissions. Configuring credentials and deploying this workflow
require separate explicit approval; this branch itself performs neither action.

This is **repository-level** read access, technically including Journal and other
Vault data, not a music-path-scoped token. A Vault-only GitHub App installation
with the same permissions and short-lived tokens is an alternative, but requires
App installation/private-key administration; this change does not provision it.
See GitHub's [fine-grained token guidance](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)
and [workflow-run read permissions](https://docs.github.com/en/rest/actions/workflow-runs#list-workflow-runs-for-a-workflow).

## Trusted evidence flow

1. Only scheduled/manual executions of the default `main` workflow enter the
   `reconcile` job. There are no PR, stage-1 push, repository-dispatch, arbitrary
   date/ref/QA payload inputs, or `continue-on-error` paths into writes.
2. Once per hour (`17 * * * *`), read the two trusted main HEADs. If a complete
   previous readback has the same HEADs and Shanghai business date, exit without
   cloning Vault or loading Apple credentials. Day rollover rechecks future-dated
   publications even if HEADs stayed unchanged.
3. Otherwise fetch a full-history Vault clone into runner temp, outside the music
   checkout. Sparse working files contain only music manifests/notes/QA/records.
   Full Git objects may contain other repository history because Git credentials
   are repo-scoped; all of this stays in ephemeral runner temp and is removed in
   the final cleanup step. No Vault contents, Git objects or tokens enter music's
   commits, cache, logs or artifacts. No Vault code is executed.
4. Verify the exact fixed workflow paths on each pinned main (`validate.yml` in
   music, `recommendation-health.yml` in Vault) using GitHub REST responses:
   correct repository/head/main/push event, newest run completed successfully,
   successful `validate` job, and stable HEADs before and after CI lookup. Pending,
   failed, absent or unreadable evidence stops the workflow. A QA receipt's own
   JSON CI claims cannot substitute for these live checks.
5. Recompute complete history, PREPARED reservations, state preservation and
   current byte-exact mirrors; normalize only supported committed music QA
   formats. Validate frozen payload hashes and actual source/semantic QA, without
   modifying the original receipt or converting old failed CI to success.
6. Process dates from the reviewed floor **2026-11-07** through the current
   Asia/Shanghai day in ascending order. November 6 stays withdrawn/failed.
   Missing/invalid QA anywhere in the prepared queue prevents release of the
   write phase. Already delivered dates are checked idempotently, not blindly
   appended. Full tests run in commit CI, not every hourly invocation.
7. Restore private checkpoints, bootstrap missing baseline with GETs only, reserve
   immutable attempts, save an intent cache, and confirm that exact nonempty
   main-ref cache through GitHub's cache metadata API. Only then enter writes.
   Apple credential steps appear only after source/QA/CI preflight; bootstrap
   uses them for read-only verification, never creation or appends.
8. Recompute the plan in the write process, and recheck local/remote HEADs and
   actual CI immediately before each POST. A changed HEAD aborts; it is never
   silently switched mid-operation. The dated playlist is reconciled before the
   total playlist, preserving exact track order and stopping on extra/reordered
   content. Checkpoints are per-date for total appends and shared for dated IDs.

## Recovery and uncertainty

`write-intents.json` reserves each date, bundle and ordered song IDs for one
Actions run/attempt **before any write**. The cache save action is not assumed
successful merely because its step returned: the driver confirms the exact cache
key and main ref exist. The result cache is a distinct key, so an intent remains
available if a runner crashes before final checkpoint upload.

A later attempt cannot reuse an earlier reservation to issue another POST.
It first reads Apple state: if both playlists are complete it records verified
recovery without writes. An unconfirmed creation or append stays blocked; pending
daily appends now require readback before any resend. A successful dated write
followed by an uncertain total write cannot cause a later date to overtake it.
Previously verified dates also cannot silently re-add externally removed songs.

If **all checkpoints are lost or evicted**, initialization is GET-only and requires
all currently managed dated playlists and the total playlist to already contain
the exact expected results. Missing, ambiguous or partial results do not grant
permission to create anything: restore checkpoints or review the operation.
Initial activation can therefore use the already synchronized November 7 baseline.
If new unsynchronized dates appear before setup, activation needs a deliberate
reviewed baseline/recovery decision; the code does not invent an empty history.
This conservative pause is intentional; no system can infer whether an unrecorded
network write succeeded merely from a temporarily absent readback.

Cache entries contain only private operation metadata (dates, digests, song IDs,
playlist IDs, pending flags); never Vault data or tokens. The Vault clone is not
cached or uploaded. Writer output is suppressed in favor of minimal date/hash/
status summaries. There is no independent backup service, write token or permission
expansion introduced for the journal.

## QA compatibility and scope

The legacy `passed_current_publication` format remains supported. The November 7
`success_current_publication` format is accepted only with live CI verification,
its exact semantic payload SHA-256 values, selected track payloads, source field
verification/observations, stage commits and original readback blobs. Its old
structural CI failures remain untouched. Semantic/source success is not inferred
from schema or unit tests.

State append-only growth and unrelated Journal HEAD advances do not invalidate an
older frozen operation. Frozen state values must all remain equal; the selected
manifest/note stay exact. Historical manifest changes are refused except the one
hash-bound November 6 withdrawal. The driver does not modify Journal, English,
news, resource schedules, QA, bundles, history or publication planners.

## Commands and actual deployment status

`music_delivery.py prepare`, `bootstrap`, `reserve`, `confirm`, and `run` are
invoked by the proposed workflow. `run` defaults to no writes unless `--execute`
is present. Standalone sync `--execute` without the live driver/intent callbacks
is rejected. There is no bypass flag or self-asserted attestation file.

Before deployment: review the branch/CI, explicitly approve token configuration,
configure it in GitHub without sharing its value, and approve the exact workflow
merge separately. Then perform trusted live-read preflight and baseline readback
before enabling actual automatic writes. This implementation has been tested
with fake APIs and real immutable QA as an **offline contract fixture**; no actual
PAT-backed Actions checkout/CI handshake or Apple API write has been executed by
this repair. A successful fixture is not a claim that production is connected.
