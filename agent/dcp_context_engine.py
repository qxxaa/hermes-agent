"""DCP-style model-guided context engine for Hermes Agent."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections import defaultdict, deque
from typing import Any

from agent.context_engine import ContextEngine
from agent.dcp_config import (
    DCP_DEFAULT_PROTECTED_TOOLS,
    DCPConfig,
    parse_dcp_config,
    resolve_model_limit,
)
from agent.dcp_db import DCPRefDB
from agent.dcp_state import CompressionBlock, DCPSessionState

logger = logging.getLogger(__name__)

_ERROR_RE = re.compile(r"\b(error|exception|traceback|failed|failure|timed out|timeout)\b", re.I)

_DCP_SYSTEM_EXTENSION = (
    "DCP context management is active. Message refs look like m0001; "
    "compressed blocks look like b1. Use the compress tool when older work "
    "is complete or stale. Use expand to retrieve original messages from "
    "a compressed block when you need details the summary omitted. "
    "Preserve concrete file paths, commands, errors, "
    "test results, decisions, constraints, and open questions. Do not "
    "compress the active task or very recent user turns."
)

# Maximum deactivated blocks to retain; older ones are evicted to bound memory.
_MAX_INACTIVE_BLOCKS = 50


class DCPContextEngine(ContextEngine):
    """Model-guided context engine inspired by Dynamic Context Pruning.

    The engine keeps canonical history intact. ``compress`` creates DCP state;
    ``transform_api_messages`` applies that state to the provider-bound copy.
    """

    def __init__(
        self,
        *,
        config: dict[str, Any] | DCPConfig | None = None,
        context_length: int = 0,
        model: str = "",
        provider: str = "",
        quiet_mode: bool = False,
    ) -> None:
        self.config = config if isinstance(config, DCPConfig) else parse_dcp_config(config)
        self.context_length = context_length or 0
        self.model = model or ""
        self.provider = provider or ""
        self.quiet_mode = quiet_mode
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self.compression_count = 0
        self.threshold_tokens = self._min_limit()
        self.state = DCPSessionState()
        # Cache for message signatures keyed by id(msg) — invalidated when
        # the canonical message list changes.  Avoids re-hashing the same
        # dicts on every API call.
        self._sig_cache: dict[int, str] = {}
        self._ref_db: DCPRefDB | None = None

    @property
    def name(self) -> str:
        return "dcp"

    def update_from_response(self, usage: dict[str, Any]) -> None:
        self.last_prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        self.last_completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        self.last_total_tokens = int(usage.get("total_tokens") or (self.last_prompt_tokens + self.last_completion_tokens))
        self.state.last_prompt_tokens = self.last_prompt_tokens

    def should_compress(self, prompt_tokens: int = None) -> bool:
        return False

    def should_compress_preflight(self, messages: list[dict[str, Any]]) -> bool:
        return False

    def has_content_to_compress(self, messages: list[dict[str, Any]]) -> bool:
        self._ensure_refs(messages)
        return len(self.state.index_by_ref) > 4

    def compress(
        self,
        messages: list[dict[str, Any]],
        current_tokens: int = None,
        focus_topic: str = None,
    ) -> list[dict[str, Any]]:
        if focus_topic:
            self.state.manual_mode = "compress-pending"
            self.state.pending_manual_focus = focus_topic
        return messages

    def on_session_start(self, session_id: str, **kwargs: Any) -> None:
        self.state.session_id = session_id
        model = kwargs.get("model")
        context_length = kwargs.get("context_length")
        if isinstance(model, str) and model:
            self.model = model
        if isinstance(context_length, int) and context_length > 0:
            self.context_length = context_length
            self.threshold_tokens = self._min_limit()

        # Lazy init dcp.db and load persisted state
        if self._ref_db is None:
            from hermes_constants import get_hermes_home
            import os
            self._ref_db = DCPRefDB(
                os.path.join(get_hermes_home(), "dcp.db")
            )
        try:
            self._load_persisted_state(session_id)
        except Exception:
            logger.warning(
                "DCP: failed to load persisted state, degrading to in-memory",
                exc_info=True,
            )
            self._ref_db = None

    def on_session_reset(self) -> None:
        super().on_session_reset()
        # Clear in-memory state only. Do NOT delete from dcp.db - the old
        # session's data belongs to the old agent which is being discarded.
        # A new agent with a new session_id will call on_session_start().
        self.state = DCPSessionState(session_id=self.state.session_id)
        self._sig_cache.clear()

    def update_model(
        self,
        model: str,
        context_length: int,
        base_url: str = "",
        api_key: str = "",
        provider: str = "",
        api_mode: str = "",
    ) -> None:
        self.model = model or self.model
        self.provider = provider or self.provider
        self.context_length = context_length
        self.threshold_tokens = self._min_limit()

    # -- Persistence helpers ----------------------------------------------

    def _load_persisted_state(self, session_id: str) -> None:
        """Load blocks, refs, and counters from dcp.db into in-memory state."""
        if not self._ref_db:
            return

        # Load session meta (counters, manual_mode, stats)
        meta = self._ref_db.load_session_meta(session_id)
        if meta:
            self.state.next_message_ref = max(
                self.state.next_message_ref, meta["next_message_ref"]
            )
            self.state.next_block_id = max(
                self.state.next_block_id, meta["next_block_id"]
            )
            self.state.next_run_id = max(
                self.state.next_run_id, meta["next_run_id"]
            )
            mm = meta.get("manual_mode", "false")
            if mm == "compress-pending":
                self.state.manual_mode = "compress-pending"
            elif mm == "true":
                self.state.manual_mode = True
            else:
                self.state.manual_mode = False
            self.state.pending_manual_focus = meta.get(
                "pending_manual_focus"
            )
            self.state.stats = meta.get("stats", {})

        # Load ref mappings
        ref_map = self._ref_db.load_refs(session_id)
        self.state.ref_by_message_key = dict(ref_map)
        self.state.message_key_by_ref = {
            v: k for k, v in ref_map.items()
        }

        # Derive next_message_ref from persisted refs (crash safety)
        for ref_str in ref_map.values():
            if ref_str.startswith("m"):
                try:
                    num = int(ref_str[1:])
                    self.state.next_message_ref = max(
                        self.state.next_message_ref, num + 1
                    )
                except ValueError:
                    pass

        # Load blocks
        block_rows = self._ref_db.load_blocks(session_id)
        self.state.blocks_by_id.clear()
        self.state.active_block_ids.clear()
        for row in block_rows:
            block = CompressionBlock(
                block_id=row["block_id"],
                run_id=row["run_id"],
                mode=row.get("mode", "range"),
                topic=row["topic"],
                summary=row["summary"],
                active=row["active"],
                start_ref=row.get("start_ref"),
                end_ref=row.get("end_ref"),
                message_refs=row.get("message_refs", []),
                included_block_ids=row.get("included_block_ids", []),
                consumed_block_ids=row.get("consumed_block_ids", []),
                created_at=row.get("created_at"),
                deactivated_at=row.get("deactivated_at"),
                deactivated_by_block_id=row.get(
                    "deactivated_by_block_id"
                ),
            )
            self.state.blocks_by_id[block.block_id] = block
            if block.active:
                self.state.active_block_ids.add(block.block_id)

        # Derive counters from actual blocks to survive crash between
        # save_block and _persist_session_meta
        if block_rows:
            max_bid = max(r["block_id"] for r in block_rows)
            max_rid = max(r["run_id"] for r in block_rows)
            self.state.next_block_id = max(
                self.state.next_block_id, max_bid + 1
            )
            self.state.next_run_id = max(
                self.state.next_run_id, max_rid + 1
            )

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        if not self.config.enabled or self.config.compress.permission == "deny":
            return []
        schemas = [self._compress_tool_schema()]
        schemas.append(self._expand_tool_schema())
        return schemas

    def handle_tool_call(self, name: str, args: dict[str, Any], **kwargs: Any) -> str:
        messages = kwargs.get("messages")
        if not isinstance(messages, list):
            return json.dumps({"ok": False, "error": f"{name} requires current messages"})
        try:
            if name == "compress":
                result = self._handle_compress(args, messages)
            elif name == "expand":
                result = self._handle_expand(args, messages)
            else:
                return json.dumps({"ok": False, "error": f"Unknown context engine tool: {name}"})
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)})
        return json.dumps(result)

    def transform_api_messages(
        self,
        api_messages: list[dict[str, Any]],
        *,
        canonical_messages: list[dict[str, Any]],
        system_prompt: str,
        tools: list[dict[str, Any]] | None,
        api_call_count: int,
        model: str,
        provider: str | None,
        session_id: str | None,
    ) -> list[dict[str, Any]]:
        if not self.config.enabled:
            return api_messages

        self.model = model or self.model
        self.provider = provider or self.provider

        # Session rebind guard: detect in-process session switches
        # (e.g. CLI /resume) that bypass on_session_start
        incoming_sid = session_id or self.state.session_id
        if (
            incoming_sid
            and self.state.session_id is not None
            and incoming_sid != self.state.session_id
        ):
            logger.warning(
                "DCP: session changed from %s to %s without on_session_start; rebinding",
                self.state.session_id, incoming_sid,
            )
            self.state = DCPSessionState(session_id=incoming_sid)
            self._sig_cache.clear()
            if self._ref_db:
                try:
                    self._load_persisted_state(incoming_sid)
                except Exception:
                    logger.warning(
                        "DCP: failed to load state for %s, degrading to in-memory",
                        incoming_sid, exc_info=True,
                    )
                    self._ref_db = None
        else:
            self.state.session_id = incoming_sid

        # Build / refresh refs from canonical messages.  This also updates
        # turn counters and message-since-last-user tracking.
        self._ensure_refs(canonical_messages)

        # Match API messages to refs by content signature.  We use a
        # role+content-based signature that excludes tool_calls (whose JSON
        # may have been re-serialised by _canonicalize_api_tool_calls between
        # the canonical list and the API copy) so that assistant tool-calling
        # messages still match.
        ref_by_api_index = self._match_api_messages_to_refs(api_messages, canonical_messages)

        # Shallow-copy the list and structurally clone messages we will
        # mutate.  This avoids the O(n) cost of copy.deepcopy on every call
        # while still protecting the caller's message dicts.
        transformed = list(api_messages)
        mutated: set[int] = set()

        self._annotate_refs(transformed, ref_by_api_index, mutated)
        self._apply_blocks(transformed, ref_by_api_index, mutated)

        if self._automatic_strategies_enabled():
            if self.config.deduplication.enabled:
                self._apply_deduplication(transformed, mutated)
            if self.config.purge_errors.enabled:
                self._apply_purge_errors(transformed, mutated)

        self._inject_system_extension(transformed, mutated)
        self._inject_nudge(transformed, api_call_count=api_call_count, mutated=mutated)
        return transformed

    def get_status(self) -> dict[str, Any]:
        active_blocks = self.state.active_blocks()
        return {
            **super().get_status(),
            "engine": "dcp",
            "active_blocks": len(active_blocks),
            "message_refs": len(self.state.ref_by_message_key),
            "min_context_limit": self._min_limit(),
            "max_context_limit": self._max_limit(),
            "compress_mode": self.config.compress.mode,
            "compress_permission": self.config.compress.permission,
        }

    # -- Tool schema ------------------------------------------------------

    def _compress_tool_schema(self) -> dict[str, Any]:
        is_message_mode = self.config.compress.mode == "message"
        if is_message_mode:
            item_properties = {
                "messageId": {"type": "string", "description": "Message ref, e.g. m0042."},
                "topic": {"type": "string", "description": "Short label for this message."},
                "summary": {"type": "string", "description": "Complete technical summary replacing this message."},
            }
            item_required = ["messageId", "topic", "summary"]
            description = (
                "Compress individual high-volume messages by ref. Preserve concrete "
                "technical facts, file paths, commands, decisions, errors, and open questions."
            )
        else:
            item_properties = {
                "startId": {"type": "string", "description": "Starting message or block ref, e.g. m0004 or b2."},
                "endId": {"type": "string", "description": "Ending message or block ref, e.g. m0018 or b3."},
                "summary": {"type": "string", "description": "Complete technical summary replacing the range."},
            }
            item_required = ["startId", "endId", "summary"]
            description = (
                "Compress completed, stale context ranges by message/block ref. "
                "Use this when prior work is closed and a concise technical summary "
                "will preserve the useful state. Do not compress the active task."
            )
        return {
            "name": "compress",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Short 3-5 word label for this compression batch."},
                    "content": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": item_properties,
                            "required": item_required,
                        },
                    },
                },
                "required": ["topic", "content"],
            },
        }

    def _expand_tool_schema(self) -> dict[str, Any]:
        return {
            "name": "expand",
            "description": (
                "Retrieve original messages from a compressed block. "
                "Use when you need details that the summary omitted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "blockRef": {
                        "type": "string",
                        "description": (
                            "The block ref to expand, e.g. b1 or b2."
                        ),
                    },
                },
                "required": ["blockRef"],
            },
        }

    # -- Tool handling ----------------------------------------------------

    def _handle_compress(self, args: dict[str, Any], messages: list[dict[str, Any]]) -> dict[str, Any]:
        self._ensure_refs(messages)
        topic = self._require_str(args, "topic")
        content = args.get("content")
        if not isinstance(content, list) or not content:
            raise ValueError("compress.content must be a non-empty array")

        is_message_mode = self.config.compress.mode == "message"
        all_created: list[int] = []
        all_deactivated: list[int] = []
        range_results: list[dict[str, Any]] = []
        run_id = self.state.new_run_id()

        for i, item in enumerate(content):
            if not isinstance(item, dict):
                range_results.append({
                    "range": i + 1, "status": "failed",
                    "error": "Entry must be an object",
                })
                break

            # Save state for single-range rollback
            saved_next_block = self.state.next_block_id
            saved_turns = self.state.turns_since_last_compress
            saved_manual_mode = self.state.manual_mode
            saved_manual_focus = self.state.pending_manual_focus
            created_this: list[int] = []
            deactivated_this: list[int] = []

            try:
                summary = self._require_str(item, "summary")

                if is_message_mode:
                    ref = self._require_str(item, "messageId")
                    if ref not in self.state.index_by_ref:
                        raise ValueError(f"Unknown message ref: {ref}")
                    item_topic = item.get("topic") if isinstance(item.get("topic"), str) else topic
                    message_refs = [ref]
                    block = CompressionBlock(
                        block_id=self.state.new_block_id(),
                        run_id=run_id,
                        mode="message",
                        topic=item_topic,
                        summary=self._augment_summary(summary, message_refs),
                        message_refs=message_refs,
                        included_block_ids=[],
                        consumed_block_ids=[],
                        created_at=time.time(),
                    )
                else:
                    start_ref = self._require_str(item, "startId")
                    end_ref = self._require_str(item, "endId")
                    message_refs, included_blocks = self._resolve_range(start_ref, end_ref)
                    if not message_refs:
                        raise ValueError(f"Range {start_ref}-{end_ref} does not cover any messages")
                    item_topic = topic
                    consumed_blocks: list[int] = []
                    block_id = self.state.new_block_id()
                    for included in included_blocks:
                        old = self.state.blocks_by_id.get(included)
                        if old and old.active:
                            old.active = False
                            old.deactivated_at = time.time()
                            old.deactivated_by_block_id = block_id
                            self.state.active_block_ids.discard(included)
                            consumed_blocks.append(included)
                            deactivated_this.append(included)
                    block = CompressionBlock(
                        block_id=block_id,
                        run_id=run_id,
                        mode="range",
                        topic=item_topic,
                        summary=self._augment_summary(summary, message_refs),
                        start_ref=start_ref,
                        end_ref=end_ref,
                        message_refs=message_refs,
                        included_block_ids=included_blocks,
                        consumed_block_ids=consumed_blocks,
                        created_at=time.time(),
                    )

                self.state.blocks_by_id[block.block_id] = block
                self.state.active_block_ids.add(block.block_id)
                created_this.append(block.block_id)

                self.compression_count += 1
                self.state.turns_since_last_compress = 0
                self.state.manual_mode = False
                self.state.pending_manual_focus = None

                # Persist this range immediately
                if self._ref_db and self.state.session_id:
                    b = block
                    mm_str = "false"
                    self._ref_db.save_compress_batch(
                        self.state.session_id,
                        new_blocks=[{
                            "block_id": b.block_id,
                            "run_id": b.run_id,
                            "mode": b.mode,
                            "topic": b.topic,
                            "summary": b.summary,
                            "active": b.active,
                            "start_ref": b.start_ref,
                            "end_ref": b.end_ref,
                            "message_refs": b.message_refs,
                            "included_block_ids": b.included_block_ids,
                            "consumed_block_ids": b.consumed_block_ids,
                            "created_at": b.created_at,
                        }],
                        deactivations=[
                            (bid,
                             self.state.blocks_by_id[bid].deactivated_at,
                             self.state.blocks_by_id[bid].deactivated_by_block_id)
                            for bid in deactivated_this
                            if bid in self.state.blocks_by_id
                        ],
                        meta={
                            "next_message_ref": self.state.next_message_ref,
                            "next_block_id": self.state.next_block_id,
                            "next_run_id": self.state.next_run_id,
                            "manual_mode": mm_str,
                            "pending_manual_focus": None,
                            "stats": self.state.stats,
                        },
                        ensure_refs=[
                            (self.state.message_key_by_ref[r], r)
                            for r in b.message_refs
                            if r in self.state.message_key_by_ref
                        ],
                    )

                all_created.extend(created_this)
                all_deactivated.extend(deactivated_this)
                range_results.append({
                    "range": i + 1, "status": "ok",
                    "ref": f"b{block.block_id}",
                })

            except Exception:
                # Roll back just this range
                for bid in created_this:
                    self.state.blocks_by_id.pop(bid, None)
                    self.state.active_block_ids.discard(bid)
                for bid in deactivated_this:
                    b = self.state.blocks_by_id.get(bid)
                    if b:
                        b.active = True
                        b.deactivated_at = None
                        b.deactivated_by_block_id = None
                        self.state.active_block_ids.add(bid)
                self.state.next_block_id = saved_next_block
                self.state.turns_since_last_compress = saved_turns
                self.state.manual_mode = saved_manual_mode
                self.state.pending_manual_focus = saved_manual_focus
                if created_this:
                    self.compression_count -= len(created_this)
                # Build descriptive error with available context
                start = item.get("startId", "?") if isinstance(item, dict) else "?"
                end = item.get("endId", "?") if isinstance(item, dict) else "?"
                range_results.append({
                    "range": i + 1, "status": "failed",
                    "error": f"Failed to compress ({start}-{end}), try compress tool again",
                })
                break  # Stop processing further ranges

        # Evict after all ranges complete
        self._evict_inactive_blocks()

        mode = "message" if is_message_mode else "range"
        all_ok = all(r["status"] == "ok" for r in range_results)
        return {
            "ok": all_ok,
            "mode": mode,
            "created_blocks": all_created,
            "deactivated_blocks": all_deactivated,
            "active_blocks": sorted(self.state.active_block_ids),
            "ranges": range_results,
            "message": f"Compressed {len(all_created)} {mode}(s) into {', '.join(f'b{i}' for i in all_created)}." if all_created else "No ranges compressed.",
        }

    _MAX_EXPAND_BYTES = 50_000

    def _handle_expand(
        self, args: dict[str, Any], messages: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Retrieve original messages from a compressed block."""
        self._ensure_refs(messages)
        ref = args.get("blockRef")
        if not isinstance(ref, str) or not ref.strip():
            raise ValueError("blockRef must be a non-empty string")
        ref = ref.strip()

        # Parse block ID from ref (b1 -> 1)
        if not ref.startswith("b"):
            raise ValueError(f"Invalid block ref: {ref}. Expected bN format.")
        try:
            block_id = int(ref[1:])
        except ValueError:
            raise ValueError(f"Invalid block ref: {ref}")

        block = self.state.blocks_by_id.get(block_id)
        if block is None:
            raise ValueError(f"Unknown block: {ref}")
        if not block.active:
            raise ValueError(
                f"Block {ref} is no longer active (consumed by a later compression)"
            )

        # Resolve covered refs to canonical messages
        formatted = []
        total_bytes = 0
        resolved = 0
        omitted = 0
        for mref in block.message_refs:
            idx = self.state.index_by_ref.get(mref)
            if idx is None or idx >= len(messages):
                continue
            resolved += 1
            msg = messages[idx]
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, (list, dict)):
                content = json.dumps(content, default=str)
            if not isinstance(content, str):
                content = str(content)
            # Include tool call names and arguments for assistant messages
            tool_calls = msg.get("tool_calls")
            if role == "assistant" and tool_calls:
                tc_parts = []
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function", {})
                    if not isinstance(fn, dict):
                        continue
                    name = fn.get("name", "?")
                    args = fn.get("arguments", "")
                    tc_parts.append(f"{name}({args})")
                if tc_parts:
                    tc_str = ", ".join(tc_parts)
                    content = f"{content} [{tc_str}]" if content.strip() else f"[{tc_str}]"
            entry = f"[{role}] {content}"
            entry_bytes = len(entry.encode("utf-8", "ignore"))
            if total_bytes + entry_bytes > self._MAX_EXPAND_BYTES:
                remaining = self._MAX_EXPAND_BYTES - total_bytes
                if remaining > 200:
                    truncated = entry.encode("utf-8", "ignore")[:remaining].decode("utf-8", "ignore")
                    formatted.append(truncated + "\n[... message truncated to fit cap]")
                else:
                    omitted += 1  # Fix 1: count the skipped message
                omitted += len(block.message_refs) - resolved
                break
            total_bytes += entry_bytes
            formatted.append(entry)

        if omitted > 0:
            formatted.append(
                f"[... output capped at ~{self._MAX_EXPAND_BYTES // 1000}KB. "
                f"{omitted} more message(s) not shown.]"
            )

        if not formatted:
            return {
                "ok": True,
                "messages": "",
                "note": "No messages found for this block.",
            }

        return {
            "ok": True,
            "block_ref": ref,
            "covers": (
                f"{block.start_ref}-{block.end_ref}"
                if block.start_ref
                else ""
            ),
            "message_count": len(block.message_refs),
            "returned": len([f for f in formatted if not f.startswith("[... output")]),
            "messages": "\n---\n".join(formatted),
        }

    # -- Transforms -------------------------------------------------------

    def _ensure_refs(self, messages: list[dict[str, Any]]) -> None:
        self._sig_cache.clear()
        self.state.index_by_ref.clear()
        new_refs: list[tuple[str, str]] = []
        for idx, msg in enumerate(messages):
            key = self._message_key(msg, idx)
            ref = self.state.ref_by_message_key.get(key)
            if ref is None:
                ref = self.state.new_message_ref()
                self.state.ref_by_message_key[key] = ref
                self.state.message_key_by_ref[ref] = key
                new_refs.append((key, ref))
            self.state.index_by_ref[ref] = idx
        user_indices = [idx for idx, msg in enumerate(messages) if msg.get("role") == "user"]
        if user_indices:
            last_user = user_indices[-1]
            if last_user != self.state.last_user_turn_index:
                self.state.turns_since_last_compress += 1
            self.state.last_user_turn_index = last_user
            self.state.messages_since_last_user = len(messages) - last_user - 1
        # Persist new refs to dcp.db
        if new_refs and self._ref_db and self.state.session_id:
            try:
                self._ref_db.save_refs_batch(self.state.session_id, new_refs)
                self._ref_db.save_counter(
                    self.state.session_id,
                    self.state.next_message_ref,
                    next_block_id=self.state.next_block_id,
                    next_run_id=self.state.next_run_id,
                )
            except Exception:
                logger.warning("DCP: failed to persist refs to dcp.db", exc_info=True)

    def _match_api_messages_to_refs(
        self,
        api_messages: list[dict[str, Any]],
        canonical_messages: list[dict[str, Any]],
    ) -> dict[int, str]:
        # Build a mapping from content signature (role + content only,
        # NOT tool_calls) to a deque of refs.  We exclude tool_calls from
        # the signature because _canonicalize_api_tool_calls may have
        # re-serialised tool-call argument JSON with sort_keys=True on the
        # API copy, producing a different hash than the canonical message.
        refs_by_sig: dict[str, deque[str]] = defaultdict(deque)
        for idx, msg in enumerate(canonical_messages):
            key = self._message_key(msg, idx)
            ref = self.state.ref_by_message_key.get(key)
            if ref:
                refs_by_sig[self._content_signature(msg)].append(ref)

        out: dict[int, str] = {}
        for api_idx, msg in enumerate(api_messages):
            if msg.get("role") == "system":
                continue
            sig = self._content_signature(msg)
            queue = refs_by_sig.get(sig)
            if queue:
                out[api_idx] = queue.popleft()
        return out

    def _clone_if_needed(self, messages: list[dict[str, Any]], idx: int, mutated: set[int]) -> dict[str, Any]:
        """Clone a message dict before mutating it (copy-on-write)."""
        if idx not in mutated:
            messages[idx] = dict(messages[idx])
            mutated.add(idx)
        return messages[idx]

    def _annotate_refs(self, messages: list[dict[str, Any]], ref_by_api_index: dict[int, str], mutated: set[int]) -> None:
        for idx, ref in ref_by_api_index.items():
            msg = self._clone_if_needed(messages, idx, mutated)
            content = msg.get("content")
            marker = f'<dcp-ref id="{ref}" />'
            if isinstance(content, str):
                if marker not in content:
                    msg["content"] = f"{content}\n\n{marker}" if content else marker
            elif isinstance(content, list):
                msg["content"] = content + [{"type": "text", "text": marker}]

    def _apply_blocks(self, messages: list[dict[str, Any]], ref_by_api_index: dict[int, str], mutated: set[int]) -> None:
        ref_to_api_index = {ref: idx for idx, ref in ref_by_api_index.items()}
        for block in self.state.active_blocks():
            covered = [ref for ref in block.message_refs if ref in ref_to_api_index]
            if not covered:
                continue
            anchor_ref = covered[0]
            anchor_idx = ref_to_api_index[anchor_ref]
            anchor = self._clone_if_needed(messages, anchor_idx, mutated)
            anchor["content"] = self._block_summary_text(block)
            for ref in covered[1:]:
                idx = ref_to_api_index[ref]
                msg = self._clone_if_needed(messages, idx, mutated)
                msg["content"] = f'[DCP: content moved into compressed block {block.ref}.]'

    def _apply_deduplication(self, messages: list[dict[str, Any]], mutated: set[int]) -> None:
        protected = DCP_DEFAULT_PROTECTED_TOOLS | self.config.deduplication.protected_tools
        latest_by_sig: dict[str, int] = {}
        result_by_call_id: dict[str, int] = {}
        calls: list[tuple[int, str, str]] = []
        for idx, msg in enumerate(messages):
            if msg.get("role") == "assistant":
                for tc in msg.get("tool_calls") or []:
                    name = self._tool_name(tc)
                    if not name or name in protected:
                        continue
                    call_id = tc.get("id") if isinstance(tc, dict) else None
                    if not isinstance(call_id, str):
                        continue
                    sig = self._tool_signature(tc)
                    calls.append((idx, call_id, sig))
                    latest_by_sig[sig] = idx
            elif msg.get("role") == "tool":
                call_id = msg.get("tool_call_id")
                if isinstance(call_id, str):
                    result_by_call_id[call_id] = idx
        protected_indices = self._turn_protected_indices(messages)
        for call_idx, call_id, sig in calls:
            if latest_by_sig.get(sig) == call_idx:
                continue
            result_idx = result_by_call_id.get(call_id)
            if result_idx is not None and result_idx not in protected_indices:
                msg = self._clone_if_needed(messages, result_idx, mutated)
                msg["content"] = "[DCP: duplicate tool output removed. Same tool and arguments were called again later.]"

    def _apply_purge_errors(self, messages: list[dict[str, Any]], mutated: set[int]) -> None:
        protected = DCP_DEFAULT_PROTECTED_TOOLS | self.config.purge_errors.protected_tools
        keep_tail = max(0, self.config.purge_errors.turns * 2)
        cutoff = max(0, len(messages) - keep_tail)
        call_name_by_id: dict[str, str] = {}
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            for tc in msg.get("tool_calls") or []:
                call_id = tc.get("id") if isinstance(tc, dict) else None
                name = self._tool_name(tc)
                if isinstance(call_id, str) and name:
                    call_name_by_id[call_id] = name
        protected_indices = self._turn_protected_indices(messages)
        for idx, msg in enumerate(messages[:cutoff]):
            if idx in protected_indices or msg.get("role") != "tool":
                continue
            call_id = msg.get("tool_call_id")
            name = call_name_by_id.get(call_id) if isinstance(call_id, str) else None
            if name in protected:
                continue
            content = msg.get("content")
            if isinstance(content, str) and len(content) > 240 and _ERROR_RE.search(content):
                first_line = content.strip().splitlines()[0][:240]
                cloned = self._clone_if_needed(messages, idx, mutated)
                cloned["content"] = f"[DCP: old failed tool output pruned after {self.config.purge_errors.turns} turns. Error preserved: {first_line}]"

    def _inject_system_extension(self, messages: list[dict[str, Any]], mutated: set[int]) -> None:
        if self.config.compress.permission == "deny":
            return
        if messages and messages[0].get("role") == "system" and isinstance(messages[0].get("content"), str):
            if _DCP_SYSTEM_EXTENSION not in messages[0]["content"]:
                msg = self._clone_if_needed(messages, 0, mutated)
                msg["content"] = f"{msg['content']}\n\n{_DCP_SYSTEM_EXTENSION}"

    def _inject_nudge(self, messages: list[dict[str, Any]], *, api_call_count: int, mutated: set[int]) -> None:
        # Use the provider-reported token count from the last response if
        # available — avoids re-estimating tokens on every API call.
        prompt_tokens = self.last_prompt_tokens
        max_limit = self._max_limit()
        min_limit = self._min_limit()
        nudge: str | None = None
        if max_limit and prompt_tokens >= max_limit:
            force = (
                "Before continuing, call compress on any completed range if safe."
                if self.config.compress.nudge_force == "strong"
                else "Consider calling compress on completed older ranges before continuing."
            )
            nudge = f"DCP context pressure is high (~{prompt_tokens:,} tokens). {force}"
        elif min_limit and prompt_tokens >= min_limit and self.state.turns_since_last_compress >= self.config.compress.nudge_frequency:
            nudge = "DCP: context is growing. If an older topic is complete, use compress with the visible refs."
        elif self.state.messages_since_last_user >= self.config.compress.iteration_nudge_threshold:
            nudge = "DCP: many assistant/tool messages have accumulated since the last user turn. Compress closed context if safe."
        elif self.state.manual_mode == "compress-pending":
            focus = f" Focus: {self.state.pending_manual_focus}." if self.state.pending_manual_focus else ""
            nudge = f"DCP manual compression requested.{focus} Call compress before continuing if there is safe completed context."
            self.state.manual_mode = False
            self.state.pending_manual_focus = None

        if not nudge:
            return
        # Only inject into user messages — never into tool results or
        # assistant messages, which could violate provider message semantics.
        for idx in range(len(messages) - 1, -1, -1):
            msg = messages[idx]
            if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                cloned = self._clone_if_needed(messages, idx, mutated)
                cloned["content"] = f"{cloned['content']}\n\n<dcp-nudge>{nudge}</dcp-nudge>"
                return

    # -- Helpers ----------------------------------------------------------

    def _message_key(self, msg: dict[str, Any], idx: int) -> str:
        return f"{idx}:{self._content_signature(msg)}"

    def _content_signature(self, msg: dict[str, Any]) -> str:
        """Signature based on role + content only.

        Excludes tool_calls and tool_call_id because the API copy may have
        been re-serialised (sorted JSON keys) by _canonicalize_api_tool_calls,
        which would produce a different hash than the canonical message.
        """
        cache_key = id(msg)
        cached = self._sig_cache.get(cache_key)
        if cached is not None:
            return cached
        clean = {
            "role": msg.get("role"),
            "content": msg.get("content"),
        }
        raw = json.dumps(clean, sort_keys=True, default=str, separators=(",", ":"))
        sig = hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()
        self._sig_cache[cache_key] = sig
        return sig

    def _require_str(self, args: dict[str, Any], key: str) -> str:
        value = args.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"compress.{key} must be a non-empty string")
        return value.strip()

    def _resolve_range(self, start_ref: str, end_ref: str) -> tuple[list[str], list[int]]:
        start_idx = self._resolve_ref_to_index(start_ref)
        end_idx = self._resolve_ref_to_index(end_ref)
        if start_idx is None:
            raise ValueError(f"Unknown startId: {start_ref}")
        if end_idx is None:
            raise ValueError(f"Unknown endId: {end_ref}")
        if end_idx < start_idx:
            start_idx, end_idx = end_idx, start_idx
        refs = [ref for ref, idx in self.state.index_by_ref.items() if start_idx <= idx <= end_idx]
        refs.sort(key=lambda ref: self.state.index_by_ref[ref])
        included_blocks = [
            block.block_id
            for block in self.state.active_blocks()
            if any(ref in refs for ref in block.message_refs)
        ]
        return refs, included_blocks

    def _resolve_ref_to_index(self, ref: str) -> int | None:
        if ref.startswith("m"):
            return self.state.index_by_ref.get(ref)
        if ref.startswith("b"):
            try:
                block_id = int(ref[1:])
            except ValueError:
                return None
            block = self.state.blocks_by_id.get(block_id)
            if not block or not block.message_refs:
                return None
            return self.state.index_by_ref.get(block.message_refs[0])
        return None

    def _augment_summary(self, summary: str, message_refs: list[str]) -> str:
        parts = [summary.strip()]
        if self.config.compress.protect_user_messages:
            parts.append(f"Covered refs: {', '.join(message_refs)}")
        return "\n\n".join(part for part in parts if part)

    def _block_summary_text(self, block: CompressionBlock) -> str:
        covers = f"{block.start_ref}-{block.end_ref}" if block.start_ref and block.end_ref else ", ".join(block.message_refs)
        return (
            f'<dcp-compressed-block id="{block.ref}" topic="{block.topic}">\n'
            f"Summary: {block.summary}\n"
            f"Covers: {covers}\n"
            f'[Call expand(blockRef="{block.ref}") to retrieve original messages.]\n'
            f'</dcp-compressed-block>'
        )

    def _tool_name(self, tool_call: Any) -> str | None:
        if not isinstance(tool_call, dict):
            return None
        function = tool_call.get("function")
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            return function["name"]
        return None

    def _tool_signature(self, tool_call: dict[str, Any]) -> str:
        function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
        name = function.get("name", "")
        args = function.get("arguments", "")
        try:
            args_obj = json.loads(args) if isinstance(args, str) else args
            args_norm = json.dumps(args_obj, sort_keys=True, separators=(",", ":"), default=str)
        except Exception:
            args_norm = str(args)
        return f"{name}::{args_norm}"

    def _automatic_strategies_enabled(self) -> bool:
        if self.config.manual_mode.enabled and not self.config.manual_mode.automatic_strategies:
            return False
        return True

    def _turn_protected_indices(self, messages: list[dict[str, Any]]) -> set[int]:
        if not self.config.turn_protection.enabled or self.config.turn_protection.turns <= 0:
            return set()
        user_indices = [idx for idx, msg in enumerate(messages) if msg.get("role") == "user"]
        if not user_indices:
            return set()
        start = user_indices[-self.config.turn_protection.turns] if len(user_indices) >= self.config.turn_protection.turns else user_indices[0]
        return set(range(start, len(messages)))

    def _evict_inactive_blocks(self) -> None:
        """Bound memory by evicting old deactivated blocks."""
        inactive = sorted(
            (bid for bid, b in self.state.blocks_by_id.items() if not b.active),
            key=lambda bid: self.state.blocks_by_id[bid].deactivated_at or 0,
        )
        for bid in inactive[_MAX_INACTIVE_BLOCKS:]:
            del self.state.blocks_by_id[bid]
            if self._ref_db and self.state.session_id:
                try:
                    self._ref_db.delete_block(self.state.session_id, bid)
                except Exception:
                    logger.warning("DCP: failed to evict block %d from dcp.db", bid, exc_info=True)

    def _min_limit(self) -> int:
        return resolve_model_limit(
            self.config.compress.model_min_limits,
            provider=self.provider,
            model=self.model,
            context_length=self.context_length,
            fallback=self.config.compress.min_context_limit,
        )

    def _max_limit(self) -> int:
        return resolve_model_limit(
            self.config.compress.model_max_limits,
            provider=self.provider,
            model=self.model,
            context_length=self.context_length,
            fallback=self.config.compress.max_context_limit,
        )
