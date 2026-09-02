"""Persistent storage for the DCP context engine.

Stores compression blocks, message-key to ref mappings, and session
counters in a separate SQLite database at ``$HERMES_HOME/dcp.db``.
Loaded once per session on ``on_session_start()``, written to during
compression and ref assignment.

Separate from state.db to avoid schema coupling with the Hermes
framework. Safe to delete - refs and blocks are rebuilt on next
session if the canonical message list is intact.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


class DCPRefDB:
    """Persistent storage for the DCP context engine."""

    def __init__(self, db_path: str):
        self._path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()

    def _ensure_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            # R3-4: ensure parent directory exists
            parent = os.path.dirname(self._path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            conn = sqlite3.connect(
                self._path, check_same_thread=False
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            # R3-1: busy timeout so transient locks don't fail immediately
            conn.execute("PRAGMA busy_timeout=1000")
            # R3-3: only set self._conn after schema succeeds
            self._ensure_schema(conn)
            self._conn = conn
        return self._conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        # R3-5: only stamp user_version when current < 1
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS dcp_blocks (
                block_id    INTEGER NOT NULL,
                session_id  TEXT NOT NULL,
                run_id      INTEGER NOT NULL,
                mode        TEXT NOT NULL DEFAULT 'range',
                topic       TEXT NOT NULL,
                summary     TEXT NOT NULL,
                active      INTEGER NOT NULL DEFAULT 1,
                start_ref   TEXT,
                end_ref     TEXT,
                message_refs TEXT NOT NULL DEFAULT '[]',
                included_block_ids TEXT NOT NULL DEFAULT '[]',
                consumed_block_ids TEXT NOT NULL DEFAULT '[]',
                created_at  REAL NOT NULL,
                deactivated_at REAL,
                deactivated_by_block_id INTEGER,
                PRIMARY KEY (session_id, block_id)
            );

            CREATE TABLE IF NOT EXISTS dcp_refs (
                session_id   TEXT NOT NULL,
                message_key  TEXT NOT NULL,
                ref          TEXT NOT NULL,
                created_at   REAL NOT NULL,
                PRIMARY KEY (session_id, message_key),
                UNIQUE (session_id, ref)
            );

            CREATE TABLE IF NOT EXISTS dcp_session_meta (
                session_id       TEXT PRIMARY KEY,
                next_message_ref INTEGER NOT NULL DEFAULT 1,
                next_block_id    INTEGER NOT NULL DEFAULT 1,
                next_run_id      INTEGER NOT NULL DEFAULT 1,
                manual_mode      TEXT NOT NULL DEFAULT 'false',
                pending_manual_focus TEXT,
                stats            TEXT NOT NULL DEFAULT '{}',
                updated_at       REAL NOT NULL
            );
        """)
        if version < 1:
            conn.execute("PRAGMA user_version = 1")

    # -- Block operations -------------------------------------------------

    def save_compress_batch(
        self,
        session_id: str,
        new_blocks: list[dict[str, Any]],
        deactivations: list[tuple[int, float | None, int | None]],
        meta: dict[str, Any],
        ensure_refs: list[tuple[str, str]] | None = None,
    ) -> None:
        """Atomic single-transaction compress: blocks + deactivations + meta.

        ``deactivations`` is a list of
        ``(block_id, deactivated_at, deactivated_by_block_id)``.
        ``meta`` has keys: next_message_ref, next_block_id, next_run_id,
        manual_mode, pending_manual_focus, stats.
        ``ensure_refs`` is an optional list of ``(message_key, ref)`` pairs
        to upsert in the same transaction, guaranteeing the block's refs
        are durable before the block itself.
        """
        with self._lock:
            conn = self._ensure_conn()
            try:
                # Ensure refs are durable before persisting blocks
                if ensure_refs:
                    now_refs = time.time()
                    conn.executemany(
                        "INSERT OR IGNORE INTO dcp_refs "
                        "(session_id, message_key, ref, created_at) "
                        "VALUES (?, ?, ?, ?)",
                        [(session_id, k, r, now_refs) for k, r in ensure_refs],
                    )
                for block in new_blocks:
                    conn.execute(
                        "INSERT INTO dcp_blocks "
                        "(block_id, session_id, run_id, mode, topic, summary, "
                        " active, start_ref, end_ref, message_refs, "
                        " included_block_ids, consumed_block_ids, created_at, "
                        " deactivated_at, deactivated_by_block_id) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            block["block_id"],
                            session_id,
                            block["run_id"],
                            block.get("mode", "range"),
                            block["topic"],
                            block["summary"],
                            1 if block.get("active", True) else 0,
                            block.get("start_ref"),
                            block.get("end_ref"),
                            json.dumps(block.get("message_refs", [])),
                            json.dumps(block.get("included_block_ids", [])),
                            json.dumps(block.get("consumed_block_ids", [])),
                            block.get("created_at", time.time()),
                            block.get("deactivated_at"),
                            block.get("deactivated_by_block_id"),
                        ),
                    )
                for bid, deact_at, deact_by in deactivations:
                    conn.execute(
                        "UPDATE dcp_blocks SET active = 0, deactivated_at = ?, "
                        "deactivated_by_block_id = ? "
                        "WHERE session_id = ? AND block_id = ?",
                        (deact_at, deact_by, session_id, bid),
                    )
                now = time.time()
                conn.execute(
                    "INSERT INTO dcp_session_meta "
                    "(session_id, next_message_ref, next_block_id, next_run_id, "
                    " manual_mode, pending_manual_focus, stats, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(session_id) DO UPDATE SET "
                    "next_message_ref = ?, next_block_id = ?, next_run_id = ?, "
                    "manual_mode = ?, pending_manual_focus = ?, stats = ?, "
                    "updated_at = ?",
                    (
                        session_id,
                        meta["next_message_ref"], meta["next_block_id"],
                        meta["next_run_id"], meta.get("manual_mode", "false"),
                        meta.get("pending_manual_focus"),
                        json.dumps(meta.get("stats", {})), now,
                        meta["next_message_ref"], meta["next_block_id"],
                        meta["next_run_id"], meta.get("manual_mode", "false"),
                        meta.get("pending_manual_focus"),
                        json.dumps(meta.get("stats", {})), now,
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def delete_block(self, session_id: str, block_id: int) -> None:
        """Delete an evicted block from dcp.db."""
        with self._lock:
            conn = self._ensure_conn()
            try:
                conn.execute(
                    "DELETE FROM dcp_blocks "
                    "WHERE session_id = ? AND block_id = ?",
                    (session_id, block_id),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def delete_session(self, session_id: str) -> None:
        """Delete all DCP data for a session (blocks, refs, meta)."""
        with self._lock:
            conn = self._ensure_conn()
            try:
                conn.execute(
                    "DELETE FROM dcp_blocks WHERE session_id = ?",
                    (session_id,),
                )
                conn.execute(
                    "DELETE FROM dcp_refs WHERE session_id = ?",
                    (session_id,),
                )
                conn.execute(
                    "DELETE FROM dcp_session_meta WHERE session_id = ?",
                    (session_id,),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    _BLOCK_COLUMNS = (
        "block_id", "session_id", "run_id", "mode", "topic",
        "summary", "active", "start_ref", "end_ref", "message_refs",
        "included_block_ids", "consumed_block_ids", "created_at",
        "deactivated_at", "deactivated_by_block_id",
    )

    def load_blocks(self, session_id: str) -> list[dict[str, Any]]:
        """Load all blocks for a session."""
        with self._lock:
            conn = self._ensure_conn()
            cols = ", ".join(self._BLOCK_COLUMNS)
            rows = conn.execute(
                f"SELECT {cols} FROM dcp_blocks WHERE session_id = ? "
                "ORDER BY block_id ASC",
                (session_id,),
            ).fetchall()
            result = []
            for row in rows:
                d = dict(zip(self._BLOCK_COLUMNS, row))
                d["active"] = bool(d["active"])
                # R3-10: guard json.loads
                for key in ("message_refs", "included_block_ids",
                            "consumed_block_ids"):
                    try:
                        d[key] = json.loads(d[key])
                    except (json.JSONDecodeError, TypeError):
                        d[key] = []
                result.append(d)
            return result

    # -- Ref operations ---------------------------------------------------

    def save_refs_batch(
        self, session_id: str, refs: list[tuple[str, str]]
    ) -> None:
        """Persist multiple (message_key, ref) mappings in one transaction."""
        if not refs:
            return
        with self._lock:
            conn = self._ensure_conn()
            try:
                now = time.time()
                conn.executemany(
                    "INSERT OR IGNORE INTO dcp_refs "
                    "(session_id, message_key, ref, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    [(session_id, key, ref, now) for key, ref in refs],
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def load_refs(
        self, session_id: str
    ) -> dict[str, str]:
        """Load all ref mappings for a session.

        Returns ``{message_key: ref}``.
        """
        with self._lock:
            conn = self._ensure_conn()
            rows = conn.execute(
                "SELECT message_key, ref FROM dcp_refs "
                "WHERE session_id = ?",
                (session_id,),
            ).fetchall()
            return {row[0]: row[1] for row in rows}

    # -- Session meta operations ------------------------------------------

    def save_counter(
        self, session_id: str, next_message_ref: int,
        next_block_id: int = 1, next_run_id: int = 1,
    ) -> None:
        """Persist the ref counter (lightweight per-turn write).

        Uses live counters instead of hardcoded defaults for the INSERT
        path so a new meta row starts with correct values.
        """
        with self._lock:
            conn = self._ensure_conn()
            try:
                now = time.time()
                conn.execute(
                    "INSERT INTO dcp_session_meta "
                    "(session_id, next_message_ref, next_block_id, "
                    " next_run_id, updated_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(session_id) DO UPDATE SET "
                    "next_message_ref = ?, updated_at = ?",
                    (
                        session_id, next_message_ref,
                        next_block_id, next_run_id, now,
                        next_message_ref, now,
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def load_session_meta(
        self, session_id: str
    ) -> dict[str, Any] | None:
        """Load session counters and state. Returns None if not found."""
        with self._lock:
            conn = self._ensure_conn()
            row = conn.execute(
                "SELECT next_message_ref, next_block_id, next_run_id, "
                "manual_mode, pending_manual_focus, stats "
                "FROM dcp_session_meta WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            # R3-10: guard json.loads for stats
            try:
                stats = json.loads(row[5]) if row[5] else {}
            except (json.JSONDecodeError, TypeError):
                stats = {}
            return {
                "next_message_ref": row[0],
                "next_block_id": row[1],
                "next_run_id": row[2],
                "manual_mode": row[3],
                "pending_manual_focus": row[4],
                "stats": stats,
            }

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
