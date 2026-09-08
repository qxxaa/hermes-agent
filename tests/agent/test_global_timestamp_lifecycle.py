"""Non-gateway timestamps reach the real Responses request builder."""

from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo


STAMP = datetime(2026, 8, 20, 12, 0, tzinfo=ZoneInfo("UTC")).timestamp()


def test_direct_agent_global_timestamp_reaches_responses_request(tmp_path, monkeypatch):
    """Replace only the provider call; persistence and request conversion are real."""
    from hermes_state import SessionDB
    from openai.types.responses import Response
    from run_agent import AIAgent

    captured = []
    db = SessionDB(db_path=tmp_path / "state.db")

    def respond(kwargs, **unused):
        captured.append(deepcopy(kwargs))
        return Response.model_validate({
            "id": "resp_test", "object": "response", "created_at": STAMP,
            "model": "test-model", "status": "completed",
            "output": [{
                "type": "message", "id": "msg_done", "role": "assistant",
                "status": "completed", "phase": "final_answer",
                "content": [{"type": "output_text", "text": "done", "annotations": []}],
            }],
            "usage": None, "error": None, "incomplete_details": None,
            "instructions": None, "metadata": {}, "parallel_tool_calls": True,
            "temperature": None, "tool_choice": "auto", "tools": [], "top_p": None,
        })

    try:
        agent = AIAgent(
            api_key="test-key", base_url="http://127.0.0.1:1/v1", provider="openai-compat",
            model="test-model", api_mode="codex_responses", max_iterations=1,
            enabled_toolsets=[], quiet_mode=True, skip_context_files=True,
            skip_memory=True, save_trajectories=False, session_db=db, session_id="timestamp-direct",
        )
        agent._cached_system_prompt = "Stable synthetic system prompt."
        monkeypatch.setattr("hermes_cli.config.load_config_readonly", lambda: {
            "message_timestamps": {"enabled": True},
        })
        monkeypatch.setattr("hermes_time.get_timezone", lambda: ZoneInfo("UTC"))
        monkeypatch.setattr(agent, "_interruptible_streaming_api_call", respond)
        monkeypatch.setattr(agent, "_interruptible_api_call", respond)

        result = agent.run_conversation(
            "current question",
            conversation_history=[
                {"role": "user", "content": "earlier question", "timestamp": STAMP},
                {"role": "assistant", "content": "earlier answer"},
            ],
            task_id="timestamp-direct",
            persist_user_timestamp=STAMP,
        )

        assert result["completed"] is True
        assert captured[0]["input"][0]["content"] == "[Thu 2026-08-20 12:00:00 UTC] earlier question"
        assert captured[0]["input"][-1]["content"] == "[Thu 2026-08-20 12:00:00 UTC] current question"
        persisted = db.get_messages_as_conversation("timestamp-direct")
        persisted_user = next(message for message in reversed(persisted) if message["role"] == "user")
        assert persisted_user["content"] == "current question"
        assert persisted_user["timestamp"] == STAMP
    finally:
        db.close()
