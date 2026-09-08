# Gateway timestamp implementation evidence

Date: 2026-09-08
Approved baseline: `866332bfb52c46e543143b2620a9aeee8bce9c77`
Initial implementation commit: `68b523a34c25364a2c700d58281300326cbddc78`
Prior request-projection correction: `64372ce74e28271829799cd52a3862f59fd8ce4c`
Fresh-current correction: `1b4577f7d8ed05d283b7e4237ae5d73f42ac2d61`
Pre-correction review candidate: `4e1d7f1bb27407549767a9a96038694b74092ec2`

## Implemented behaviour

- The global `message_timestamps.enabled` controls non-messaging-gateway agent turns only when it is explicitly Boolean; otherwise those turns fall back to `gateway.message_timestamps.enabled`. Messaging gateway turns retain their legacy gateway-only decision.
- `DEFAULT_CONFIG` leaves the global setting as `null`, preserving the legacy fallback after default merging and in isolated profile homes.
- The common agent route stores and returns canonical content. It applies timestamp rendering, recovery cleanup, and `api_content` compatibility validation only to model-facing request copies.
- Historical replay follows the upstream ordering: recognize a prefix, clean recognized recovery noise, discard cleanup-invalidated sidecars, then render and retain only sidecars equal to the full rendered text or beginning with that text plus `\n\n` context.
- Fresh current user input receives normal timestamp rendering but is not cleanup-treated as a historical recovery row merely because it begins with a recognized recovery-note form.
- Standard messaging-gateway `TurnRunner` calls explicitly select `gateway_prepared`; `/bg` and Feishu document-comment calls explicitly select `disabled`, preserving their existing no-timestamp intake behavior. No storage schema, canonical-prefix persistence, historical repair, or image-array traversal was added.

## Review corrections F1-F4

### F1 — gateway configuration ownership and wiring

`tests/agent/test_turn_context.py::test_gateway_prepared_turn_defers_to_gateway_configuration` now passes the real `message_timestamp_handling="gateway_prepared"` mode and exercises both conflicting configurations: global true/gateway false and global false/gateway true. In both cases `TurnContext.message_timestamp_replay_enabled is None`, so the direct-agent global gate cannot affect a messaging-gateway request.

`tests/gateway/test_turn_context.py::TestTurnRunner::test_normal_gateway_turn_selects_gateway_prepared_timestamp_handling` invokes the normal `TurnRunner._run_conversation_with_approval` path and asserts the agent receives `message_timestamp_handling="gateway_prepared"`. It fails if the normal gateway call falls back to the direct-agent default. The obsolete test-only `_message_timestamps_prepared_by_gateway` attribute was removed.

### F2 — Chat Completions provider-boundary capture

`tests/agent/test_global_timestamp_lifecycle.py::test_direct_agent_global_timestamp_reaches_chat_completions_request` uses a real `AIAgent`, isolated `SessionDB`, and Chat Completions transport with only the provider call replaced. It captures exact outgoing user bytes:

- historical: `[Thu 2026-08-20 12:00:00 UTC] earlier question`
- current: `[Thu 2026-08-20 12:00:00 UTC] current question`

It also proves returned history and persisted user content remain clean (`current question`) with the original timestamp. Existing Responses lifecycle captures remain in the same file.

### F3 — native intake inventory and controlled wire evidence

Normal entrypoints were inventoried from `.run_conversation(` call sites. Messaging gateway `gateway/run_turn_runner.py` is explicitly `gateway_prepared`; gateway `/bg` and Feishu comments are explicitly `disabled`. Native/direct callers retaining default `agent` handling include:

- `tui_gateway/prompt_turn.py` (TUI, Desktop, Dashboard, Relay)
- `tui_gateway/methods_prompt.py` background prompt paths
- `acp_adapter/server.py` ACP
- `gateway/platforms/api_server.py` and `gateway/platforms/api_server_runs.py` HTTP API and runs
- CLI and one-shot paths (`cli.py`, `hermes_cli/oneshot.py`, `hermes_cli/cli_chat_turn_mixin.py`, `hermes_cli/cli_commands_mixin.py`)
- direct/internal callers (`tools/delegate_tool_child_run.py`, `agent/curator.py`, `agent/side_question.py`, `agent/background_review.py`, `batch_runner.py`).

Controlled native tests cover the required staged families without GUI, Docker, or a live provider:

- `tests/tui_gateway/test_timestamp_native_intake.py` invokes the production `tui_gateway.server._invoke_agent` function used by `prompt_turn`. It asserts the real `run_kwargs["persist_user_message"]` staging, actual Chat Completions historical/current request bytes, and clean persisted content/timestamp.
- `tests/gateway/test_timestamp_api_server_intake.py` invokes production `APIServerAdapter._run_agent`, with a real `AIAgent`, isolated persistence, and only the provider boundary replaced. It asserts historical/current Chat Completions bytes and clean persisted current content/timestamp.

ACP was inventoried but not selected for controlled execution because the canonical test environment lacks the optional `acp` package (`ModuleNotFoundError: acp`); the API-server family supplies the approved “ACP or API” native evidence. Codex app-server remains a preserved alternate-runtime bypass: it returns before common request assembly, and no timestamp projection is claimed for provider-owned Codex thread input.

### F4 — production replay path only

The unused `render_turn_with_message_timestamps` wrapper was removed from `agent/message_timestamps.py`. Its structured-content immutability, recovery cleanup, and compatible/stale-sidecar tests now invoke production `render_message_timestamp_replay`, the helper used by `build_api_messages`. No production caller remains for the deleted wrapper.

## Changed files against approved baseline

- `.hermes/reports/gateway-timestamps-implementation.md`
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
- `tests/gateway/test_timestamp_api_server_intake.py`
- `tests/gateway/test_turn_context.py`
- `tests/tui_gateway/test_timestamp_native_intake.py`
- `website/docs/user-guide/messaging/index.md`

## Executed evidence

- Earlier RED/GREEN evidence for fresh recovery-shaped current input is retained: `scripts/run_tests.sh tests/agent/test_global_timestamp_lifecycle.py -k fresh_recovery_shaped_input -v` failed before the fresh-current correction and then passed after it.
- An ACP-native test was attempted through the canonical runner and exited 1 because the environment cannot import the optional `acp` package (`ModuleNotFoundError: No module named 'acp'`). No dependency or environment change was made; the authorised API-server alternative was implemented and tested instead.
- F1/F2/F3/F4 focused verification: `scripts/run_tests.sh tests/agent/test_message_timestamps.py tests/agent/test_turn_context.py tests/gateway/test_turn_context.py tests/agent/test_global_timestamp_lifecycle.py tests/tui_gateway/test_timestamp_native_intake.py tests/gateway/test_timestamp_api_server_intake.py -v` exited 0: 52 passed across 6 files.
- Final relevant timestamp/gateway/lifecycle suite: `scripts/run_tests.sh tests/agent/test_message_timestamps.py tests/agent/test_global_timestamp_lifecycle.py tests/agent/test_api_content_sidecar.py tests/agent/test_turn_context.py tests/agent/test_proactive_prune_restart_safety.py tests/run_agent/test_compression_persistence.py tests/gateway/test_message_timestamps.py tests/gateway/test_timestamp_sidecar_replay.py tests/gateway/test_background_command.py tests/gateway/test_feishu_comment.py tests/gateway/test_turn_context.py tests/tui_gateway/test_timestamp_native_intake.py tests/gateway/test_timestamp_api_server_intake.py -v` exited 0: 133 passed across 13 files.
- Static checks: `python -m compileall -q agent/message_timestamps.py tests/agent/test_message_timestamps.py tests/agent/test_turn_context.py tests/agent/test_global_timestamp_lifecycle.py tests/tui_gateway/test_timestamp_native_intake.py tests/gateway/test_timestamp_api_server_intake.py` and `git diff --check` exited 0.

## Residual status

- No live provider, credentials, live configuration, GUI/Desktop/Relay client session, Docker/container rebuild, deployment, or UI-stacking assertion was performed.
- TUI/Desktop/Relay and API server have controlled native intake, real common-agent, real isolated persistence, and provider-boundary evidence; this is not a live-client validation.
- ACP is inventoried but has no controlled run in this environment because its optional package is absent. Codex app-server remains an inspected intentional request-assembly bypass, not a timestamp-projection claim.
- A closed/reopened SQLite database and fresh `AIAgent` demonstrate durable process-restart behavior, not a container rebuild.
- The full repository suite, re-review, lifecycle QA, and readiness assessment remain separate dependency-gated phases.
