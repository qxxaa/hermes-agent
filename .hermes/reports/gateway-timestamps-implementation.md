# Gateway timestamp fresh-intake correction evidence

Date: 2026-09-08
Approved baseline: `866332bfb52c46e543143b2620a9aeee8bce9c77`
Prior candidate: `2543550d1d11102687226aaffa4cc73165b27251`

## Corrections

- QA1: common agent intake now identifies the matching CLI-staged user dict by its clean content and reuses its captured timestamp when no explicit timestamp is supplied. The selected admission time drives the request-side timestamp, sidecar, returned row, and persistence override.
- QA2: TUI native-image captions are normalized and timestamp-rendered before `build_native_content_parts`; the clean caption and the same admission timestamp are passed to persistence. Empty captions are not turned into timestamp-only text.
- QA3: non-gateway fresh string intake now strips recognized timestamp prefixes before durable/current-message staging and uses the supplied timestamp ahead of any embedded prefix timestamp. Recovery-note cleanup remains historical-only.
- QA4: replay now applies the pinned gateway eligibility rule before timestamp-sidecar compatibility: only nonempty string sidecars are eligible. Empty/non-string sidecars are removed on request copies; valid string sidecars retain exact bytes.

## RED/GREEN receipts

- QA1/QA3 RED: `scripts/run_tests.sh tests/agent/test_timestamp_fresh_intake.py -v` under a clean child environment: 1 passed, 3 failed, exit 1. The failing cases showed T1 overwriting CLI T0 and fresh prefix persistence/rendering diverging from gateway semantics.
- QA1/QA3 GREEN: the same command: 4 passed, 0 failed, exit 0.
- QA2 RED: native image test initially exposed that an enabled empty caption rendered a timestamp-only text value. The focused native test then passed after the empty-caption guard.
- QA2 GREEN: `scripts/run_tests.sh tests/tui_gateway/test_timestamp_native_intake.py -v`: 4 passed, 0 failed, exit 0. It uses a synthetic local PNG, native routing, real agent persistence, and a replaced provider call; it asserts exact text/image parts, enabled/disabled captions, clean (unprefixed) persistence and timestamp reuse.
- QA4 external RED provenance: `/opt/data/workspace/.scratch/t_7e669c4d/sidecar-probe-result.json` at prior candidate reproduced `AttributeError` for dict/list/int `api_content`; its pinned baseline `_build_replay_entry` discarded those values. The local regression is `test_replay_drops_ineligible_sidecars_before_timestamp_compatibility`.

## Regression suite

Clean-child command (all `HERMES_KANBAN_*` names unset and isolated `HERMES_HOME`):

`scripts/run_tests.sh tests/agent/test_message_timestamps.py tests/agent/test_timestamp_fresh_intake.py tests/agent/test_global_timestamp_lifecycle.py tests/agent/test_api_content_sidecar.py tests/agent/test_turn_context.py tests/gateway/test_message_timestamps.py tests/gateway/test_timestamp_sidecar_replay.py tests/gateway/test_turn_context.py tests/tui_gateway/test_timestamp_native_intake.py tests/gateway/test_timestamp_api_server_intake.py -v`

Observed: 10 files, 107 passed, 0 failed, exit 0.

## Matrix and residuals

Covered through production request assembly: enabled/disabled fresh prefixed text, supplied-versus-embedded precedence, staged CLI time, deterministic sidecar context, closed-DB reload, valid sidecar with appended context, historical cleanup, missing/empty/non-string sidecars, and enabled/disabled native caption/empty-caption preparation.

The required paired detached-baseline versus candidate tool-pruning/full-compaction lifecycle matrix was not executed during this correction run. Existing candidate lifecycle tests exercise pruning, compaction, persistence and reopen, but they are not an independent pinned-baseline comparison. This remains an explicit review/QA residual; no shared-upstream lifecycle behavior is claimed fixed. No real provider, GUI, Docker/container rebuild, live configuration, credentials, or deployment was used.
