from datetime import datetime
from zoneinfo import ZoneInfo

from gateway.message_timestamps import (
    coerce_message_timestamp,
    render_user_content_with_timestamp,
    strip_leading_message_timestamps,
)
from run_agent import AIAgent


BERLIN = ZoneInfo("Europe/Berlin")


def _epoch(year, month, day, hour, minute, second):
    return datetime(year, month, day, hour, minute, second, tzinfo=BERLIN).timestamp()


def test_render_user_content_deduplicates_existing_timestamp_and_preserves_embedded_time():
    db_processing_ts = _epoch(2026, 4, 27, 15, 55, 36)
    stored_content = (
        "[Mon 2026-04-27 15:54:44 CEST] "
        "[Example User] This should go on our todo list"
    )

    rendered = render_user_content_with_timestamp(
        stored_content,
        db_processing_ts,
        tz=BERLIN,
    )

    assert rendered == stored_content
    assert rendered.count("2026-04-27") == 1


# ---------------------------------------------------------------------------
# Opt-in gate: gateway.message_timestamps.enabled (default OFF)
# ---------------------------------------------------------------------------


def test_message_timestamps_enabled_defaults_off():
    from gateway.run import _message_timestamps_enabled

    assert _message_timestamps_enabled(None) is False
    assert _message_timestamps_enabled({}) is False
    assert _message_timestamps_enabled({"gateway": {}}) is False
    assert (
        _message_timestamps_enabled({"gateway": {"message_timestamps": {}}}) is False
    )


def test_gateway_timestamp_setting_uses_explicit_gateway_then_global_fallback():
    from gateway.run import _message_timestamps_enabled

    assert _message_timestamps_enabled({
        "message_timestamps": {"enabled": False},
        "gateway": {"message_timestamps": {"enabled": True}},
    }) is True
    assert _message_timestamps_enabled({
        "message_timestamps": {"enabled": True},
        "gateway": {"message_timestamps": {"enabled": False}},
    }) is False
    assert _message_timestamps_enabled({"message_timestamps": {"enabled": True}}) is True
    assert _message_timestamps_enabled({
        "message_timestamps": {"enabled": True},
        "gateway": {"message_timestamps": None},
    }) is True


def test_build_history_injects_only_when_enabled():
    from gateway.run import _build_gateway_agent_history

    history = [
        {"role": "user", "content": "hello", "timestamp": _epoch(2026, 4, 28, 13, 40, 53)},
        {"role": "assistant", "content": "hi"},
    ]

    # Default (off): user content stays clean, no timestamp prefix.
    agent_history, _ = _build_gateway_agent_history(history)
    assert agent_history[0]["content"] == "hello"

    # Enabled: user content gets exactly one timestamp prefix.
    agent_history, _ = _build_gateway_agent_history(history, inject_timestamps=True)
    assert agent_history[0]["content"].startswith("[")
    assert agent_history[0]["content"].endswith("hello")
    # Assistant message is never timestamped.
    assert agent_history[1]["content"] == "hi"


def test_gateway_fresh_intake_and_runner_replay_share_global_fallback(monkeypatch):
    """Raw gateway config reaches fresh intake and TurnRunner replay consistently."""
    from gateway.run_turn import GatewayTurnMixin
    from gateway.run_turn_runner import TurnRunner
    from gateway.turn_context import TurnContext

    timestamp = _epoch(2026, 4, 28, 13, 40, 53)
    config = {"message_timestamps": {"enabled": True}, "gateway": {"message_timestamps": None}}
    monkeypatch.setattr("gateway.run._load_gateway_config", lambda: config)
    monkeypatch.setattr("hermes_time.get_timezone", lambda: BERLIN)

    fresh, persist_message, persist_timestamp = GatewayTurnMixin()._hmwa_apply_message_timestamp(
        type("Event", (), {"timestamp": timestamp})(), "hello",
    )

    assert fresh == "[Tue 2026-04-28 13:40:53 CEST] hello"
    assert persist_message == "hello"
    assert persist_timestamp == timestamp

    runner = TurnRunner(object(), TurnContext(
        history=[{"role": "user", "content": "earlier", "timestamp": timestamp}],
        user_config=config,
    ))
    replay, observed, _media_paths = runner._load_turn_history(object(), reused_cached_agent=False)

    assert observed is None
    assert replay[0]["content"] == "[Tue 2026-04-28 13:40:53 CEST] earlier"
