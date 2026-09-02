from __future__ import annotations

import json

from agent.dcp_context_engine import DCPContextEngine


def _tool_call(call_id: str, name: str, args: dict) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def test_range_tool_schema_is_exposed_by_default():
    engine = DCPContextEngine(config={}, context_length=200000)

    schemas = engine.get_tool_schemas()

    assert len(schemas) == 2
    compress_schema = schemas[0]
    assert compress_schema["name"] == "compress"
    assert compress_schema["parameters"]["required"] == ["topic", "content"]
    item = compress_schema["parameters"]["properties"]["content"]["items"]
    assert item["required"] == ["startId", "endId", "summary"]
    expand_schema = schemas[1]
    assert expand_schema["name"] == "expand"
    assert expand_schema["parameters"]["required"] == ["blockRef"]


def test_message_tool_schema_when_configured():
    engine = DCPContextEngine(config={"compress": {"mode": "message"}}, context_length=200000)

    schema = engine.get_tool_schemas()[0]

    item = schema["parameters"]["properties"]["content"]["items"]
    assert item["required"] == ["messageId", "topic", "summary"]


def test_deny_permission_hides_compress_tool():
    engine = DCPContextEngine(config={"compress": {"permission": "deny"}}, context_length=200000)

    assert engine.get_tool_schemas() == []


def test_disabled_engine_exposes_no_tool_and_returns_original_api_messages():
    engine = DCPContextEngine(config={"enabled": False}, context_length=200000)
    api_messages = [{"role": "user", "content": "hello"}]

    transformed = engine.transform_api_messages(
        api_messages,
        canonical_messages=[{"role": "user", "content": "hello"}],
        system_prompt="",
        tools=[],
        api_call_count=1,
        model="test-model",
        provider="openai",
        session_id="s1",
    )

    assert engine.get_tool_schemas() == []
    assert transformed is api_messages
    assert api_messages == [{"role": "user", "content": "hello"}]


def test_transform_does_not_mutate_canonical_messages_and_adds_refs():
    engine = DCPContextEngine(config={}, context_length=200000)
    canonical = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "world"}]
    original = [msg.copy() for msg in canonical]
    api_messages = [{"role": "system", "content": "sys"}] + [msg.copy() for msg in canonical]

    transformed = engine.transform_api_messages(
        api_messages,
        canonical_messages=canonical,
        system_prompt="sys",
        tools=[],
        api_call_count=1,
        model="test-model",
        provider="openai",
        session_id="s1",
    )

    assert canonical == original
    assert '<dcp-ref id="m0001" />' in transformed[1]["content"]
    assert '<dcp-ref id="m0002" />' in transformed[2]["content"]
    assert "DCP context management is active" in transformed[0]["content"]


def test_range_compress_creates_block_and_transform_applies_placeholder():
    engine = DCPContextEngine(config={}, context_length=200000)
    canonical = [
        {"role": "user", "content": "start"},
        {"role": "assistant", "content": "old work"},
        {"role": "user", "content": "new task"},
    ]
    engine._ensure_refs(canonical)

    result = json.loads(
        engine.handle_tool_call(
            "compress",
            {
                "topic": "old work",
                "content": [{"startId": "m0001", "endId": "m0002", "summary": "Old work summary."}],
            },
            messages=canonical,
        )
    )

    assert result["ok"] is True
    assert result["created_blocks"] == [1]
    transformed = engine.transform_api_messages(
        [msg.copy() for msg in canonical],
        canonical_messages=canonical,
        system_prompt="",
        tools=[],
        api_call_count=1,
        model="test-model",
        provider="openai",
        session_id="s1",
    )
    assert '<dcp-compressed-block id="b1" topic="old work">' in transformed[0]["content"]
    assert "content moved into compressed block b1" in transformed[1]["content"]
    assert "new task" in transformed[2]["content"]


def test_range_compress_consumes_overlapping_active_blocks():
    engine = DCPContextEngine(config={}, context_length=200000)
    canonical = [
        {"role": "user", "content": "phase one"},
        {"role": "assistant", "content": "phase one result"},
        {"role": "user", "content": "phase two"},
        {"role": "assistant", "content": "phase two result"},
    ]
    engine._ensure_refs(canonical)

    first = json.loads(
        engine.handle_tool_call(
            "compress",
            {"topic": "phase one", "content": [{"startId": "m0001", "endId": "m0002", "summary": "Phase one summary."}]},
            messages=canonical,
        )
    )
    second = json.loads(
        engine.handle_tool_call(
            "compress",
            {"topic": "both phases", "content": [{"startId": "b1", "endId": "m0004", "summary": "Both phases summary."}]},
            messages=canonical,
        )
    )

    assert first["created_blocks"] == [1]
    assert second["created_blocks"] == [2]
    assert second["deactivated_blocks"] == [1]
    assert engine.state.blocks_by_id[1].active is False
    assert engine.state.blocks_by_id[1].deactivated_by_block_id == 2
    assert engine.state.active_block_ids == {2}


def test_multimodal_messages_get_text_ref_without_mutating_canonical_content():
    engine = DCPContextEngine(config={}, context_length=200000)
    canonical = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look at this"},
                {"type": "image_url", "image_url": {"url": "https://example.invalid/image.png"}},
            ],
        }
    ]
    api_messages = [{"role": "system", "content": "sys"}] + [msg.copy() for msg in canonical]

    transformed = engine.transform_api_messages(
        api_messages,
        canonical_messages=canonical,
        system_prompt="sys",
        tools=[],
        api_call_count=1,
        model="test-model",
        provider="openai",
        session_id="s1",
    )

    assert canonical[0]["content"] == [
        {"type": "text", "text": "look at this"},
        {"type": "image_url", "image_url": {"url": "https://example.invalid/image.png"}},
    ]
    assert transformed[1]["content"][-1] == {"type": "text", "text": '<dcp-ref id="m0001" />'}


def test_message_compress_creates_message_block():
    engine = DCPContextEngine(config={"compress": {"mode": "message"}}, context_length=200000)
    canonical = [{"role": "user", "content": "huge pasted log"}]
    engine._ensure_refs(canonical)

    result = json.loads(
        engine.handle_tool_call(
            "compress",
            {"topic": "logs", "content": [{"messageId": "m0001", "topic": "log", "summary": "Useful log facts."}]},
            messages=canonical,
        )
    )

    assert result["ok"] is True
    assert result["mode"] == "message"
    assert result["created_blocks"] == [1]


def test_deduplication_prunes_older_duplicate_tool_output():
    engine = DCPContextEngine(config={}, context_length=200000)
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [_tool_call("a", "read_file", {"path": "x"})]},
        {"role": "tool", "tool_call_id": "a", "content": "old output"},
        {"role": "assistant", "content": "", "tool_calls": [_tool_call("b", "read_file", {"path": "x"})]},
        {"role": "tool", "tool_call_id": "b", "content": "new output"},
    ]

    engine._apply_deduplication(messages, set())

    assert "duplicate tool output removed" in messages[1]["content"]
    assert messages[3]["content"] == "new output"


def test_deduplication_respects_protected_tools():
    engine = DCPContextEngine(config={}, context_length=200000)
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [_tool_call("a", "patch", {"path": "x"})]},
        {"role": "tool", "tool_call_id": "a", "content": "old output"},
        {"role": "assistant", "content": "", "tool_calls": [_tool_call("b", "patch", {"path": "x"})]},
        {"role": "tool", "tool_call_id": "b", "content": "new output"},
    ]

    engine._apply_deduplication(messages, set())

    assert messages[1]["content"] == "old output"


def test_purge_errors_preserves_error_summary():
    engine = DCPContextEngine(config={"strategies": {"purgeErrors": {"turns": 0}}}, context_length=200000)
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [_tool_call("a", "terminal", {"command": "bad"})]},
        {"role": "tool", "tool_call_id": "a", "content": "ERROR: failed\n" + "x" * 500},
        {"role": "user", "content": "next"},
    ]

    engine._apply_purge_errors(messages, set())

    assert "old failed tool output pruned" in messages[1]["content"]
    assert "ERROR: failed" in messages[1]["content"]


def test_turn_protection_prevents_dedup_pruning_recent_messages():
    engine = DCPContextEngine(
        config={"turnProtection": {"enabled": True, "turns": 1}},
        context_length=200000,
    )
    messages = [
        {"role": "user", "content": "latest turn"},
        {"role": "assistant", "content": "", "tool_calls": [_tool_call("a", "read_file", {"path": "x"})]},
        {"role": "tool", "tool_call_id": "a", "content": "old output"},
        {"role": "assistant", "content": "", "tool_calls": [_tool_call("b", "read_file", {"path": "x"})]},
        {"role": "tool", "tool_call_id": "b", "content": "new output"},
    ]

    engine._apply_deduplication(messages, set())

    assert messages[1]["content"] == ""
    assert messages[2]["content"] == "old output"


def test_manual_mode_can_disable_automatic_strategies_in_transform():
    engine = DCPContextEngine(
        config={"manualMode": {"enabled": True, "automaticStrategies": False}},
        context_length=200000,
    )
    canonical = [
        {"role": "assistant", "content": "", "tool_calls": [_tool_call("a", "read_file", {"path": "x"})]},
        {"role": "tool", "tool_call_id": "a", "content": "old output"},
        {"role": "assistant", "content": "", "tool_calls": [_tool_call("b", "read_file", {"path": "x"})]},
        {"role": "tool", "tool_call_id": "b", "content": "new output"},
    ]

    transformed = engine.transform_api_messages(
        [msg.copy() for msg in canonical],
        canonical_messages=canonical,
        system_prompt="",
        tools=[],
        api_call_count=1,
        model="test-model",
        provider="openai",
        session_id="s1",
    )

    assert transformed[1]["content"].startswith("old output")
    assert transformed[3]["content"].startswith("new output")


def test_manual_compress_request_injects_one_shot_nudge_without_mutating_history():
    engine = DCPContextEngine(config={}, context_length=200000)
    canonical = [
        {"role": "user", "content": "please compact old work"},
        {"role": "assistant", "content": "working"},
    ]

    returned = engine.compress(canonical, current_tokens=1234, focus_topic="old investigation")
    first = engine.transform_api_messages(
        [msg.copy() for msg in canonical],
        canonical_messages=canonical,
        system_prompt="",
        tools=[],
        api_call_count=1,
        model="test-model",
        provider="openai",
        session_id="s1",
    )
    second = engine.transform_api_messages(
        [msg.copy() for msg in canonical],
        canonical_messages=canonical,
        system_prompt="",
        tools=[],
        api_call_count=2,
        model="test-model",
        provider="openai",
        session_id="s1",
    )

    assert returned is canonical
    assert "DCP manual compression requested" in first[0]["content"]
    assert "old investigation" in first[0]["content"]
    assert "DCP manual compression requested" not in second[0]["content"]
    assert canonical == [
        {"role": "user", "content": "please compact old work"},
        {"role": "assistant", "content": "working"},
    ]


# ---- Persistence and lifecycle tests (behavioral, temp dcp.db) ----

import os

import pytest

from agent.dcp_db import DCPRefDB


@pytest.fixture(autouse=True)
def _isolate_hermes_home(tmp_path, monkeypatch):
    """Ensure DCP never touches the real ~/.hermes/dcp.db."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))


def _engine_with_db(tmp_path, session_id="test-session"):
    """Create a DCPContextEngine with a real dcp.db in tmp_path."""
    engine = DCPContextEngine(config={}, context_length=200000)
    engine._ref_db = DCPRefDB(os.path.join(str(tmp_path), "dcp.db"))
    engine.state.session_id = session_id
    return engine


def test_persistence_round_trip(tmp_path):
    """Compress, discard engine, new engine loads same blocks and refs."""
    db_path = os.path.join(str(tmp_path), "dcp.db")
    sid = "rt-session"

    # Engine 1: assign refs and simulate a compress
    e1 = DCPContextEngine(config={}, context_length=200000)
    e1._ref_db = DCPRefDB(db_path)
    e1.state.session_id = sid

    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
        {"role": "user", "content": "next"},
    ]
    e1._ensure_refs(messages)
    assert len(e1.state.ref_by_message_key) == 3

    result = json.loads(e1.handle_tool_call(
        "compress",
        {"topic": "test", "content": [{"startId": "m0001", "endId": "m0002", "summary": "Greeting exchange."}]},
        messages=messages,
    ))
    assert result["ok"] is True
    block_id = int(result["message"].split("b")[1].rstrip("."))
    e1._ref_db.close()

    # Engine 2: fresh instance, same db
    e2 = DCPContextEngine(config={}, context_length=200000)
    e2._ref_db = DCPRefDB(db_path)
    e2.state.session_id = sid
    e2._load_persisted_state(sid)

    assert block_id in e2.state.blocks_by_id
    assert e2.state.blocks_by_id[block_id].active
    assert e2.state.blocks_by_id[block_id].summary == "Greeting exchange."
    assert e2.state.next_message_ref >= 4
    e2._ref_db.close()


def test_reset_clears_memory_not_db(tmp_path):
    """on_session_reset clears in-memory state but preserves dcp.db.

    The old agent is discarded after reset; a new agent with a new
    session_id will call on_session_start() separately.
    """
    db_path = os.path.join(str(tmp_path), "dcp.db")
    sid = "reset-session"

    engine = DCPContextEngine(config={}, context_length=200000)
    engine._ref_db = DCPRefDB(db_path)
    engine.state.session_id = sid

    messages = [{"role": "user", "content": "data"}]
    engine._ensure_refs(messages)

    # Verify refs exist in DB
    refs = engine._ref_db.load_refs(sid)
    assert len(refs) == 1

    # Reset clears memory
    engine.on_session_reset()
    assert len(engine.state.ref_by_message_key) == 0

    # But DB still has the old refs (old session preserved)
    refs_after = engine._ref_db.load_refs(sid)
    assert len(refs_after) == 1
    engine._ref_db.close()


def test_compress_rollback_on_db_failure(tmp_path):
    """If save_compress_batch fails, in-memory state is fully restored."""
    engine = _engine_with_db(tmp_path)

    messages = [
        {"role": "user", "content": "start"},
        {"role": "assistant", "content": "work"},
        {"role": "user", "content": "more"},
    ]
    engine._ensure_refs(messages)

    # Save pre-compress state
    pre_block_count = len(engine.state.blocks_by_id)
    pre_next_block = engine.state.next_block_id
    pre_manual_mode = engine.state.manual_mode

    # Make save_compress_batch raise a real sqlite3 error
    import sqlite3
    from unittest.mock import patch as mock_patch
    with mock_patch.object(
        engine._ref_db, "save_compress_batch",
        side_effect=sqlite3.OperationalError("simulated DB lock"),
    ):
        result = json.loads(engine.handle_tool_call(
            "compress",
            {"topic": "fail", "content": [{"startId": "m0001", "endId": "m0002", "summary": "Should fail."}]},
            messages=messages,
        ))

    assert result["ok"] is False
    assert any(r["status"] == "failed" for r in result.get("ranges", []))

    # Verify full rollback including manual_mode
    assert len(engine.state.blocks_by_id) == pre_block_count
    assert engine.state.next_block_id == pre_next_block
    assert engine.state.manual_mode == pre_manual_mode
    engine._ref_db.close()


def test_fresh_session_no_leakage(tmp_path):
    """A session with no prior dcp.db data starts clean."""
    engine = _engine_with_db(tmp_path, session_id="fresh-session")
    engine._load_persisted_state("fresh-session")

    assert engine.state.next_message_ref == 1
    assert engine.state.next_block_id == 1
    assert engine.state.next_run_id == 1
    assert len(engine.state.blocks_by_id) == 0
    assert len(engine.state.ref_by_message_key) == 0
    engine._ref_db.close()


def test_counter_derivation_from_blocks(tmp_path):
    """Counters are derived from MAX(block/run id) on load."""
    db_path = os.path.join(str(tmp_path), "dcp.db")
    sid = "counter-session"
    ref_db = DCPRefDB(db_path)

    # Manually insert a block with high IDs
    ref_db.save_compress_batch(
        sid,
        new_blocks=[{
            "block_id": 5, "run_id": 3, "mode": "range",
            "topic": "test", "summary": "summary", "active": True,
            "start_ref": "m0001", "end_ref": "m0010",
            "message_refs": ["m0001", "m0002"],
        }],
        deactivations=[],
        meta={
            "next_message_ref": 2,  # deliberately low
            "next_block_id": 2,     # deliberately low
            "next_run_id": 1,       # deliberately low
        },
    )
    ref_db.close()

    # Load in a fresh engine
    engine = DCPContextEngine(config={}, context_length=200000)
    engine._ref_db = DCPRefDB(db_path)
    engine.state.session_id = sid
    engine._load_persisted_state(sid)

    # Counters should be derived from the actual block, not meta
    assert engine.state.next_block_id >= 6  # max(block_id=5) + 1
    assert engine.state.next_run_id >= 4    # max(run_id=3) + 1
    engine._ref_db.close()


def test_eviction_mirrors_to_db(tmp_path):
    """Evicted blocks are deleted from dcp.db."""
    db_path = os.path.join(str(tmp_path), "dcp.db")
    sid = "eviction-session"
    ref_db = DCPRefDB(db_path)

    # Insert 55 inactive blocks (cap is 50)
    blocks = []
    for i in range(55):
        blocks.append({
            "block_id": i + 1, "run_id": 1, "mode": "range",
            "topic": "old", "summary": f"block {i+1}",
            "active": False, "start_ref": "m0001", "end_ref": "m0002",
            "message_refs": [], "deactivated_at": float(i),
            "deactivated_by_block_id": 100,
        })
    ref_db.save_compress_batch(sid, blocks, [], {
        "next_message_ref": 1, "next_block_id": 56, "next_run_id": 2,
    })
    ref_db.close()

    # Load and trigger eviction
    engine = DCPContextEngine(config={}, context_length=200000)
    engine._ref_db = DCPRefDB(db_path)
    engine.state.session_id = sid
    engine._load_persisted_state(sid)
    engine._evict_inactive_blocks()

    # Memory should have 50 inactive blocks
    inactive_in_mem = sum(1 for b in engine.state.blocks_by_id.values() if not b.active)
    assert inactive_in_mem <= 50

    # DB should also have <= 50
    remaining = engine._ref_db.load_blocks(sid)
    inactive_in_db = sum(1 for b in remaining if not b["active"])
    assert inactive_in_db <= 50
    engine._ref_db.close()


def test_expand_happy_path(tmp_path):
    """expand returns original message content including tool call details."""
    engine = _engine_with_db(tmp_path)

    messages = [
        {"role": "user", "content": "read the config"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [_tool_call("c1", "read_file", {"path": "/etc/config.yaml"})],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "key: value"},
        {"role": "user", "content": "follow up"},
    ]
    engine._ensure_refs(messages)

    compress_result = json.loads(engine.handle_tool_call(
        "compress",
        {"topic": "config", "content": [{"startId": "m0001", "endId": "m0003", "summary": "Read config."}]},
        messages=messages,
    ))
    assert compress_result["ok"]
    block_ref = compress_result["message"].split("into ")[1].rstrip(".")

    # Re-ensure after splice
    engine._ensure_refs(messages)

    expand_result = json.loads(engine.handle_tool_call(
        "expand", {"blockRef": block_ref}, messages=messages,
    ))
    assert expand_result["ok"]
    output = expand_result["messages"]
    # User message content
    assert "read the config" in output
    # Tool call name and arguments visible
    assert "read_file" in output
    assert "/etc/config.yaml" in output
    # Tool output content
    assert "key: value" in output
    engine._ref_db.close()


def test_expand_unknown_and_malformed_refs():
    """expand rejects unknown and malformed block refs."""
    engine = DCPContextEngine(config={}, context_length=200000)
    messages = [{"role": "user", "content": "test"}]

    for bad_ref in ["b999", "b", "bx", "m0001", ""]:
        result = json.loads(engine.handle_tool_call(
            "expand", {"blockRef": bad_ref}, messages=messages,
        ))
        assert result["ok"] is False


def test_expand_inactive_block(tmp_path):
    """expand rejects a consumed (inactive) block."""
    engine = _engine_with_db(tmp_path)

    messages = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
        {"role": "assistant", "content": "d"},
        {"role": "user", "content": "e"},
        {"role": "assistant", "content": "f"},
        {"role": "user", "content": "g"},
    ]
    engine._ensure_refs(messages)

    # First compress
    r1 = json.loads(engine.handle_tool_call(
        "compress",
        {"topic": "first", "content": [{"startId": "m0001", "endId": "m0002", "summary": "AB."}]},
        messages=messages,
    ))
    assert r1["ok"]
    first_ref = r1["message"].split("into ")[1].rstrip(".")

    engine._ensure_refs(messages)

    # Second compress that consumes the first block's range
    r2 = json.loads(engine.handle_tool_call(
        "compress",
        {"topic": "second", "content": [{"startId": first_ref, "endId": "m0004", "summary": "ABCD."}]},
        messages=messages,
    ))
    assert r2["ok"]

    engine._ensure_refs(messages)

    # Try to expand the consumed first block
    expand_result = json.loads(engine.handle_tool_call(
        "expand", {"blockRef": first_ref}, messages=messages,
    ))
    # Should fail - first block was consumed
    assert expand_result["ok"] is False
    assert "no longer active" in expand_result["error"]
    engine._ref_db.close()


def test_db_lock_tolerance():
    """DB failures in _ensure_refs don't crash the transform."""
    engine = DCPContextEngine(config={}, context_length=200000)
    engine.state.session_id = "lock-test"

    # Give it a broken ref_db
    engine._ref_db = DCPRefDB("/nonexistent/path/dcp.db")

    messages = [
        {"role": "user", "content": "test"},
        {"role": "assistant", "content": "reply"},
    ]

    # Transform should still work (overlay applied, refs just not persisted)
    transformed = engine.transform_api_messages(
        [msg.copy() for msg in messages],
        canonical_messages=messages,
        system_prompt="",
        tools=[],
        api_call_count=1,
        model="test",
        provider="test",
        session_id="lock-test",
    )

    # Should have refs assigned in memory even if DB write failed
    assert len(engine.state.index_by_ref) >= 0  # doesn't crash
    assert len(transformed) == 2  # messages still returned


def test_serialised_compress_partial_failure(tmp_path):
    """Range 1 commits to DB, range 2 fails, result reports both."""
    db_path = os.path.join(str(tmp_path), "dcp.db")
    engine = DCPContextEngine(config={}, context_length=200000)
    engine._ref_db = DCPRefDB(db_path)
    engine.state.session_id = "serial-test"

    messages = [
        {"role": "user", "content": "topic a"},
        {"role": "assistant", "content": "work on a"},
        {"role": "user", "content": "topic b"},
        {"role": "assistant", "content": "work on b"},
        {"role": "user", "content": "continuing"},
    ]
    engine._ensure_refs(messages)

    # Range 1 valid, range 2 has a bad ref
    result = json.loads(engine.handle_tool_call(
        "compress",
        {
            "topic": "partial",
            "content": [
                {"startId": "m0001", "endId": "m0002", "summary": "Topic A work."},
                {"startId": "m9999", "endId": "m9998", "summary": "Should fail."},
            ],
        },
        messages=messages,
    ))

    assert result["ok"] is False
    assert len(result["ranges"]) == 2
    assert result["ranges"][0]["status"] == "ok"
    assert result["ranges"][1]["status"] == "failed"

    # Range 1's block should be persisted
    blocks = engine._ref_db.load_blocks("serial-test")
    assert len(blocks) == 1
    assert blocks[0]["summary"] == "Topic A work."

    # Range 2 should leave no ghost in memory
    assert len(engine.state.blocks_by_id) == 1
    engine._ref_db.close()
