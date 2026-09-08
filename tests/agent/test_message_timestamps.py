"""Cross-channel timestamp rendering follows the messaging gateway contract."""

from datetime import datetime
from zoneinfo import ZoneInfo


def test_non_gateway_config_falls_back_to_legacy_without_overriding_gateway_route():
    from agent.message_timestamps import message_timestamps_enabled

    legacy_enabled = {"gateway": {"message_timestamps": {"enabled": True}}}
    conflicting_enabled = {
        "message_timestamps": {"enabled": False},
        "gateway": {"message_timestamps": {"enabled": True}},
    }
    conflicting_disabled = {
        "message_timestamps": {"enabled": True},
        "gateway": {"message_timestamps": {"enabled": False}},
    }

    assert message_timestamps_enabled(legacy_enabled) is True
    assert message_timestamps_enabled(conflicting_enabled) is False
    assert message_timestamps_enabled(conflicting_disabled) is True
    assert message_timestamps_enabled(conflicting_enabled, messaging_gateway=True) is True
    assert message_timestamps_enabled(conflicting_disabled, messaging_gateway=True) is False


def test_non_gateway_rendering_keeps_structured_content_and_clean_history_immutable():
    from agent.message_timestamps import render_turn_with_message_timestamps

    tz = ZoneInfo("Europe/Berlin")
    timestamp = datetime(2026, 4, 28, 13, 42, 10, tzinfo=tz).timestamp()
    history = [{"role": "user", "content": "earlier", "timestamp": timestamp}]
    structured = [
        {"type": "text", "text": "describe this"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
    ]

    rendered_history, rendered_user, persisted_timestamp = render_turn_with_message_timestamps(
        history,
        structured,
        config={"message_timestamps": {"enabled": True}},
        current_timestamp=timestamp,
        tz=tz,
    )

    assert history == [{"role": "user", "content": "earlier", "timestamp": timestamp}]
    assert rendered_history[0]["content"] == "[Tue 2026-04-28 13:42:10 CEST] earlier"
    assert rendered_user is structured
    assert persisted_timestamp == timestamp


def test_history_cleanup_invalidates_sidecars_and_keeps_compatible_rendered_sidecars_exact():
    from agent.message_timestamps import render_turn_with_message_timestamps

    tz = ZoneInfo("UTC")
    timestamp = datetime(2026, 8, 20, 12, 0, tzinfo=tz).timestamp()
    rendered = "[Thu 2026-08-20 12:00:00 UTC] retain this"
    history = [
        {
            "role": "user",
            "content": "retain this",
            "timestamp": timestamp,
            "api_content": rendered + "\n\nretrieval context",
        },
        {
            "role": "user",
            "content": "[2026-08-20T12:00:00+00:00] [System note: Your previous turn was interrupted. Continue.] actual question",
            "timestamp": timestamp + 60,
            "api_content": "stale sidecar",
        },
    ]

    replay, _, _ = render_turn_with_message_timestamps(
        history,
        "next",
        config={"message_timestamps": {"enabled": True}},
        current_timestamp=timestamp,
        tz=tz,
    )

    assert replay[0]["api_content"] == rendered + "\n\nretrieval context"
    assert replay[1]["content"] == "[Thu 2026-08-20 12:00:00 UTC] actual question"
    assert "api_content" not in replay[1]
    assert history[1]["content"].endswith("actual question")


def test_history_cleanup_runs_when_timestamp_rendering_is_disabled():
    from agent.message_timestamps import render_turn_with_message_timestamps

    history = [{
        "role": "user",
        "content": "[System note: Your previous turn was interrupted. Continue.] actual question",
        "api_content": "stale sidecar",
    }]

    replay, _, rendered_timestamp = render_turn_with_message_timestamps(
        history,
        "next",
        config={"message_timestamps": {"enabled": False}},
    )

    assert replay == [{"role": "user", "content": "actual question"}]
    assert rendered_timestamp is None


def test_merged_defaults_leave_global_setting_unset_for_legacy_fallback(tmp_path, monkeypatch):
    from agent.message_timestamps import message_timestamps_enabled
    from hermes_cli.config import load_config_readonly

    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "gateway:\n  message_timestamps:\n    enabled: true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    config = load_config_readonly()

    assert config["message_timestamps"]["enabled"] is None
    assert message_timestamps_enabled(config) is True
