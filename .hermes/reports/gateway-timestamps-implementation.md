# Gateway timestamp implementation evidence

Date: 2026-09-08
Approved baseline: `866332bfb52c46e543143b2620a9aeee8bce9c77`
Initial implementation commit: `68b523a34c25364a2c700d58281300326cbddc78`
Prior request-projection correction: `64372ce74e28271829799cd52a3862f59fd8ce4c`
Implementation candidate commit: `1b4577f7d8ed05d283b7e4237ae5d73f42ac2d61`

## Implemented behaviour

- The global `message_timestamps.enabled` controls non-messaging-gateway agent turns only when it is explicitly Boolean; otherwise those turns fall back to `gateway.message_timestamps.enabled`. Messaging gateway turns retain their legacy gateway-only decision.
- `DEFAULT_CONFIG` leaves the global setting as `null`, preserving the legacy fallback after default merging and in isolated profile homes.
- The common agent route stores and returns canonical content. It applies timestamp rendering, recovery cleanup, and `api_content` compatibility validation only to model-facing request copies.
- Historical replay follows the upstream ordering: recognize a prefix, clean recognized recovery noise, discard cleanup-invalidated sidecars, then render and retain only sidecars equal to the full rendered text or beginning with that text plus `\n\n` context.
- The fresh current user input is deliberately distinct from historical replay. It receives normal timestamp rendering but is not treated as a recovery-history row merely because its text starts with a recognized recovery-note form. This correction is covered by an actual Responses request capture.
- Standard messaging-gateway `TurnRunner` calls explicitly select `gateway_prepared`; `/bg` and Feishu document-comment calls explicitly select `disabled`, preserving their existing no-timestamp intake behavior. No storage schema, canonical-prefix persistence, historical repair, or image-array traversal was added.

## Changed files against approved baseline

- `agent/conversation_loop.py`
- `agent/message_timestamps.py`
- `agent/turn_context.py`
- `agent/turn_facade.py`
- `agent/turn_request_assembly.py`
- `gateway/message_timestamps.py`
- `gateway/run_turn.py`
- `gateway/run_turn_runner.py`
- `hermes_cli/config_defaults.py`
- `plugins/platforms/feishu/feishu_comment.py`
- `tests/agent/test_global_timestamp_lifecycle.py`
- `tests/agent/test_message_timestamps.py`
- `tests/agent/test_turn_context.py`
- `tests/gateway/test_background_command.py`
- `tests/gateway/test_feishu_comment.py`
- `tests/gateway/test_message_timestamps.py`
- `website/docs/user-guide/messaging/index.md`

## Executed evidence

- RED: `scripts/run_tests.sh tests/agent/test_global_timestamp_lifecycle.py -k fresh_recovery_shaped_input -v` failed on the uncorrected candidate. The captured Responses input was `[Thu 2026-08-20 12:00:00 UTC] actual question`, proving current input had incorrectly undergone historical recovery cleanup.
- GREEN: the same targeted command passed after the correction: 1 passed.
- Configuration coverage: `scripts/run_tests.sh tests/agent/test_message_timestamps.py -v` passed: 12 passed. This includes both route-specific precedence directions, absent/null fallback, gateway Boolean shorthand, default merging, and two isolated profile-home configurations.
- Focused regression: `scripts/run_tests.sh tests/agent/test_message_timestamps.py tests/agent/test_global_timestamp_lifecycle.py tests/agent/test_api_content_sidecar.py tests/agent/test_turn_context.py tests/agent/test_proactive_prune_restart_safety.py tests/run_agent/test_compression_persistence.py tests/gateway/test_message_timestamps.py tests/gateway/test_timestamp_sidecar_replay.py tests/gateway/test_background_command.py tests/gateway/test_feishu_comment.py` passed: 122 passed across 10 files.
- Static/syntax checks: `python -m compileall -q agent/message_timestamps.py tests/agent/test_message_timestamps.py tests/agent/test_global_timestamp_lifecycle.py` and `git diff --check` both exited 0 before committing the implementation candidate.
- Preserved prior evidence records `scripts/run_tests.sh tests/run_agent/test_codex_app_server_integration.py` as 29 passed. The current run inspected the alternate-runtime test source and the early `api_mode == "codex_app_server"` return path; it did not repeat that unchanged suite. An attempted runner `--collect-only` invocation produced the runner's no-tests-ran status and is not counted as passing evidence.

## Criterion-to-evidence matrix

| Acceptance criterion | Evidence |
| --- | --- |
| Gateway behavior remains gateway-config-only despite either global value | Route-scoped resolver assertions plus `gateway_prepared` common-loop wiring; gateway message-timestamp and sidecar regression tests passed. |
| Non-gateway explicit/global fallback semantics, defaults, shorthand, profiles | `test_timestamp_configuration_precedence_is_route_scoped`, `test_timestamp_config_isolated_between_profile_homes`, and merged-default test; 12-test configuration file passed. |
| Canonical caller/return/persistence history is not contaminated | Real `AIAgent` + isolated `SessionDB` Responses captures in lifecycle tests; focused suite passed. |
| Fresh current input is not cleanup-treated as historical replay | New `test_direct_agent_keeps_fresh_recovery_shaped_input_at_provider_boundary` was red then green against captured Responses input. |
| Upstream-compatible recovery cleanup, compatible sidecars, mismatch invalidation | Agent helper tests plus retained `tests/gateway/test_timestamp_sidecar_replay.py`; focused suite passed. |
| Real tool continuation and cached replay | Lifecycle test executes a real `read_file` tool continuation, compares request prefix stability, then runs cached next turn; focused suite passed. |
| DB close/reopen and fresh agent | Lifecycle tests close/reopen isolated SQLite state, construct fresh agents, and capture resumed Responses inputs; focused suite passed. |
| Actual tool pruning and full durable compaction retain timestamp fields/sidecars | Lifecycle tests invoke production `prune_tool_results_only` and `compress_context`, prove shrink/summary respectively, reopen state, and capture replay; focused suite passed. |
| Provider failure/resume preserves original timestamp and clean history | Lifecycle failure/resume test uses actual loop/persistence with only provider boundary stubbed; focused suite passed. |
| `/bg` and Feishu comments retain upstream no-timestamp intake | Explicit `disabled` call wiring and gateway route tests; focused suite passed. |
| Native TUI/Desktop/Relay and normal direct agent routes | Static call reachability through `AIAgent.run_conversation`; direct-agent controlled transport execution is covered by Responses captures. No client-label or process-environment route detection was introduced. |
| Codex app-server alternate runtime boundary | Source inspection confirms its early return before common request assembly. Preserved 29-test regression evidence covers the isolated path; no claim is made that provider-owned Codex thread input gets timestamp projection. |
| No out-of-scope schema, canonical persistence, image-array, or live-client work | Baseline diff review and focused tests; no migration/persistence schema changes. |

## Residual status

- Clean for the implementation-owned controlled test scope: all currently executed focused timestamp/lifecycle regressions passed (122 tests).
- No live provider, credentials, live configuration, GUI/Desktop/Relay client session, Docker/container rebuild, deployment, or UI-stacking assertion was performed. Native-route support is static reachability plus the controlled direct-agent transport path, not live-client validation.
- A closed/reopened SQLite database and fresh `AIAgent` demonstrate durable process-restart behavior, not a container rebuild.
- The full repository suite and independent council review/lifecycle QA/readiness are intentionally separate dependency-gated phases. This implementation completion releases review; it is not review approval or publication readiness.
