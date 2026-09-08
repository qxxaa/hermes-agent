"""Non-gateway timestamps reach the real Responses request builder."""

from copy import deepcopy
from datetime import datetime
import json
from zoneinfo import ZoneInfo

import pytest


STAMP = datetime(2026, 8, 20, 12, 0, tzinfo=ZoneInfo("UTC")).timestamp()


@pytest.mark.parametrize("enabled", [False, True])
def test_direct_agent_global_timestamp_reaches_responses_request(tmp_path, monkeypatch, enabled):
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
            "message_timestamps": {"enabled": enabled},
        })
        monkeypatch.setattr("hermes_time.get_timezone", lambda: ZoneInfo("UTC"))
        monkeypatch.setattr(agent, "_interruptible_streaming_api_call", respond)
        monkeypatch.setattr(agent, "_interruptible_api_call", respond)

        result = agent.run_conversation(
            "current question",
            conversation_history=[
                {"role": "user", "content": "[System note: Your previous turn was interrupted. Continue.] earlier question", "timestamp": STAMP},
                {"role": "assistant", "content": "earlier answer"},
            ],
            task_id="timestamp-direct",
            persist_user_timestamp=STAMP,
        )

        assert result["completed"] is True
        historical_wire = "[Thu 2026-08-20 12:00:00 UTC] earlier question" if enabled else "earlier question"
        current_wire = "[Thu 2026-08-20 12:00:00 UTC] current question" if enabled else "current question"
        assert captured[0]["input"][0]["content"] == historical_wire
        assert captured[0]["input"][-1]["content"] == current_wire
        # Rendering is confined to the request: callers retain clean canonical rows
        # for the next turn and any later persistence/reload lifecycle.
        assert result["messages"][0]["content"] == "[System note: Your previous turn was interrupted. Continue.] earlier question"
        assert result["messages"][-2]["content"] == "current question"
        persisted = db.get_messages_as_conversation("timestamp-direct")
        persisted_user = next(message for message in reversed(persisted) if message["role"] == "user")
        assert persisted_user["content"] == "current question"
        assert persisted_user["timestamp"] == STAMP
    finally:
        db.close()


def test_direct_agent_timestamp_projection_survives_tool_continuation_and_db_reopen(tmp_path, monkeypatch):
    """Only the provider boundary is replaced; cached and durable histories stay canonical."""
    from hermes_state import SessionDB
    from openai.types.responses import Response
    from run_agent import AIAgent

    db_path = tmp_path / "state.db"
    db = SessionDB(db_path=db_path)
    captured, outputs = [], []
    tool_file = tmp_path / "tool.txt"
    tool_file.write_text("tool fixture", encoding="utf-8")

    def respond(kwargs, **unused):
        captured.append(deepcopy(kwargs))
        return Response.model_validate({
            "id": f"resp_{len(captured)}", "object": "response", "created_at": STAMP,
            "model": "test-model", "status": "completed", "output": outputs.pop(0),
            "usage": None, "error": None, "incomplete_details": None,
            "instructions": None, "metadata": {}, "parallel_tool_calls": True,
            "temperature": None, "tool_choice": "auto", "tools": [], "top_p": None,
        })

    def make_agent(session_id):
        agent = AIAgent(
            api_key="test-key", base_url="http://127.0.0.1:1/v1", provider="openai-compat",
            model="test-model", api_mode="codex_responses", max_iterations=4,
            enabled_toolsets=[], quiet_mode=True, skip_context_files=True, skip_memory=True,
            save_trajectories=False, session_db=db, session_id=session_id,
        )
        agent._cached_system_prompt = "Stable synthetic system prompt."
        agent.valid_tool_names = {"read_file"}
        monkeypatch.setattr(agent, "_interruptible_streaming_api_call", respond)
        monkeypatch.setattr(agent, "_interruptible_api_call", respond)
        return agent

    monkeypatch.setattr("hermes_cli.config.load_config_readonly", lambda: {
        "message_timestamps": {"enabled": True},
    })
    monkeypatch.setattr("hermes_time.get_timezone", lambda: ZoneInfo("UTC"))
    try:
        outputs.extend([
            [{
                "type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "read_file",
                "arguments": json.dumps({"path": str(tool_file)}),
            }],
            [{
                "type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                "phase": "final_answer", "content": [{"type": "output_text", "text": "first", "annotations": []}],
            }],
        ])
        first_agent = make_agent("timestamp-lifecycle")
        first = first_agent.run_conversation("first question", task_id="first", persist_user_timestamp=STAMP)

        assert first["completed"] is True
        assert len(captured) == 2
        assert captured[1]["input"][:len(captured[0]["input"])] == captured[0]["input"]
        assert first["messages"][0]["content"] == "first question"

        outputs.append([{
            "type": "message", "id": "msg_2", "role": "assistant", "status": "completed",
            "phase": "final_answer", "content": [{"type": "output_text", "text": "cached", "annotations": []}],
        }])
        cached = first_agent.run_conversation(
            "second question", conversation_history=first["messages"], task_id="cached",
            persist_user_timestamp=STAMP + 30,
        )
        assert cached["completed"] is True
        assert captured[-1]["input"][0]["content"] == "[Thu 2026-08-20 12:00:00 UTC] first question"
        assert cached["messages"][0]["content"] == "first question"

        reopened = SessionDB(db_path=db_path)
        try:
            durable_history = reopened.get_messages_as_conversation("timestamp-lifecycle")
        finally:
            reopened.close()
        outputs.append([{
            "type": "message", "id": "msg_3", "role": "assistant", "status": "completed",
            "phase": "final_answer", "content": [{"type": "output_text", "text": "reopened", "annotations": []}],
        }])
        reopened_agent = make_agent("timestamp-lifecycle")
        resumed = reopened_agent.run_conversation(
            "third question", conversation_history=durable_history, task_id="reopened",
            persist_user_timestamp=STAMP + 60,
        )
        assert resumed["completed"] is True
        assert captured[-1]["input"][0]["content"] == "[Thu 2026-08-20 12:00:00 UTC] first question"
        assert resumed["messages"][0]["content"] == "first question"
    finally:
        db.close()
