"""Native TUI timestamp intake reaches the common agent request boundary."""

from copy import deepcopy
from datetime import datetime
import threading
from types import SimpleNamespace
from zoneinfo import ZoneInfo


STAMP = datetime(2026, 8, 20, 12, 0, tzinfo=ZoneInfo("UTC")).timestamp()


def test_tui_prompt_turn_stages_clean_persistence_and_timestamped_provider_request(tmp_path, monkeypatch):
    """Desktop/Relay's native TUI path passes its staged text through the real agent loop."""
    from hermes_state import SessionDB
    from run_agent import AIAgent
    from tui_gateway import server

    captured = []
    db = SessionDB(db_path=tmp_path / "state.db")

    def respond(kwargs):
        captured.append(deepcopy(kwargs))
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content="done", tool_calls=None), finish_reason="stop",
            )],
            model="test-model", usage=None,
        )

    try:
        agent = AIAgent(
            api_key="test-key", base_url="http://127.0.0.1:1/v1", provider="openai-compat",
            model="test-model", api_mode="chat_completions", max_iterations=1,
            enabled_toolsets=[], quiet_mode=True, skip_context_files=True,
            skip_memory=True, save_trajectories=False, session_db=db, session_id="timestamp-tui",
        )
        agent._cached_system_prompt = "Stable synthetic system prompt."
        agent._disable_streaming = True
        monkeypatch.setattr("hermes_cli.config.load_config_readonly", lambda: {
            "message_timestamps": {"enabled": True},
        })
        monkeypatch.setattr("hermes_time.get_timezone", lambda: ZoneInfo("UTC"))
        monkeypatch.setattr("agent.turn_context.time.time", lambda: STAMP)
        monkeypatch.setattr(agent, "_interruptible_api_call", respond)
        ticker_stop = threading.Event()
        monkeypatch.setattr(
            server, "_start_usage_ticker",
            lambda _sid, _agent: (ticker_stop, SimpleNamespace(join=lambda: None)),
        )
        history = [
            {"role": "user", "content": "earlier question", "timestamp": STAMP},
            {"role": "assistant", "content": "earlier answer"},
        ]
        session = {"session_key": "timestamp-tui", "history_lock": threading.RLock()}
        st = server._TurnRun(agent, None, None, False, history=history)

        server._invoke_agent(
            "ui-session", session, st, "current question", "current question", None, [], None, None,
        )

        assert st.result["completed"] is True
        assert st.run_kwargs["persist_user_message"] == "current question"
        wire_users = [message["content"] for message in captured[0]["messages"] if message["role"] == "user"]
        assert wire_users == [
            "[Thu 2026-08-20 12:00:00 UTC] earlier question",
            "[Thu 2026-08-20 12:00:00 UTC] current question",
        ]
        persisted = db.get_messages_as_conversation("timestamp-tui")
        persisted_user = next(message for message in persisted if message["role"] == "user")
        assert persisted_user["content"] == "current question"
        assert persisted_user["timestamp"] == STAMP
    finally:
        db.close()
