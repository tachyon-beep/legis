"""Append-only audit store.

Record-agnostic: persists opaque dict payloads as canonical JSON in an ordered,
hash-chained, append-only table. It knows nothing about override records or any
other schema layered on top (those serialize to dict and hand bytes here).

Two integrity layers, complementary:
  * **Triggers** reject UPDATE/DELETE at the DB level — mutation is *rejected,
    not discouraged*, and there is no mutation method on this class.
  * **Hash chain** lets ``verify_integrity`` detect any out-of-band edit or
    reordering that bypasses the triggers (e.g. direct file tampering). This is
    the seed of the protected cell's tamper-evidence story (Sprint 3).
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    Table,
    Text,
    create_engine,
    insert,
    select,
    text,
)
from sqlalchemy.pool import NullPool

from legis.canonical import canonical_json, content_hash

GENESIS = "0" * 64


@dataclass(frozen=True)
class AuditRecord:
    seq: int
    payload: dict[str, Any]
    content_hash: str
    prev_hash: str
    chain_hash: str


def _chain(prev_hash: str, c_hash: str) -> str:
    return hashlib.sha256((prev_hash + c_hash).encode("utf-8")).hexdigest()


class AuditStore:
    def __init__(self, url: str) -> None:
        # NullPool: hold no connection between operations — an append-only
        # audit store wants no lingering locks and clean resource lifecycle.
        self._engine = create_engine(url, future=True, poolclass=NullPool)
        # Ambient connection for an in-progress multi-append transaction. Stored
        # thread-locally so a batch on one thread never leaks its open
        # connection into another thread's append (Q-M5). When unset, append()
        # opens its own per-call transaction as before.
        self._txn = threading.local()

        from sqlalchemy import event
        @event.listens_for(self._engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            if "sqlite" in url:
                cursor = dbapi_connection.cursor()
                try:
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA synchronous=NORMAL")
                    cursor.execute("PRAGMA busy_timeout=5000")
                except Exception:
                    pass
                finally:
                    cursor.close()

        # Remove the global force_immediate_transaction event listener to prevent locking on read-only queries.

        self._md = MetaData()
        self._log = Table(
            "audit_log",
            self._md,
            Column("seq", Integer, primary_key=True, autoincrement=True),
            Column("payload", Text, nullable=False),
            Column("content_hash", Text, nullable=False),
            Column("prev_hash", Text, nullable=False),
            Column("chain_hash", Text, nullable=False),
        )
        self._md.create_all(self._engine)
        self._install_append_only_triggers()

    def _install_append_only_triggers(self) -> None:
        if self._engine.dialect.name == "sqlite":
            with self._engine.begin() as conn:
                conn.execute(
                    text(
                        "CREATE TRIGGER IF NOT EXISTS audit_log_no_update "
                        "BEFORE UPDATE ON audit_log BEGIN "
                        "SELECT RAISE(ABORT, 'audit_log is append-only'); END;"
                    )
                )
                conn.execute(
                    text(
                        "CREATE TRIGGER IF NOT EXISTS audit_log_no_delete "
                        "BEFORE DELETE ON audit_log BEGIN "
                        "SELECT RAISE(ABORT, 'audit_log is append-only'); END;"
                    )
                )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Group appends into one all-or-nothing transaction (Q-M5).

        Every ``append`` issued inside this context shares a single connection
        and commits together on clean exit; any exception rolls back the whole
        batch, so a mid-loop failure cannot leave earlier appends persisted.
        Re-entrancy and cross-thread bleed are avoided by stashing the ambient
        connection thread-locally; nested ``transaction()`` calls reuse the
        outer one.

        Appends only, and now *enforced*: ``read_all`` / ``read_by_seq`` /
        ``verify_integrity`` / ``get_latest_sequence_and_hash`` open their own
        connection via ``self._engine.begin()``, so a read issued inside this
        context would not see the batch's uncommitted appends and on SQLite would
        hit ``SQLITE_BUSY`` against the held ``BEGIN IMMEDIATE`` write lock. Each
        guards on the thread-local and raises ``RuntimeError`` rather than
        contending silently (``_assert_no_batch_in_progress``). Do all reads
        before entering the context (as ``wardline.governor`` does: it resolves
        every entity before opening the batch). Only ``append``'s own chain-head
        read is safe here, because it runs on the ambient connection.
        """
        if getattr(self._txn, "conn", None) is not None:
            # Already inside a batch on this thread — reuse it (nested no-op).
            yield
            return
        with self._engine.begin() as conn:
            if conn.dialect.name == "sqlite":
                conn.execute(text("BEGIN IMMEDIATE"))
            self._txn.conn = conn
            try:
                yield
            finally:
                self._txn.conn = None

    def _assert_no_batch_in_progress(self, method: str) -> None:
        """Fail loudly if a fresh-connection read runs inside a held batch (Q-M5).

        ``transaction()`` holds a ``BEGIN IMMEDIATE`` write lock on the ambient
        thread-local connection. Every public read opens its OWN connection, so
        a read issued while the batch is held would (a) contend with that lock
        (``SQLITE_BUSY`` on SQLite, and possibly no error on other backends) and
        (b) miss the batch's uncommitted appends. The original contract relied on
        callers never doing this; this guard *enforces* it, turning a silent,
        backend-dependent contention into an explicit, deterministic error so a
        future in-batch read in a gate append path fails its tests immediately.
        """
        if getattr(self._txn, "conn", None) is not None:
            raise RuntimeError(
                f"AuditStore.{method}() called inside an active transaction() batch "
                "on this thread. Fresh-connection reads contend with the batch's "
                "held BEGIN IMMEDIATE write lock and cannot see its uncommitted "
                "appends — resolve all reads before opening the batch (Q-M5)."
            )

    def _insert(self, conn: Any, payload: dict[str, Any]) -> int:
        c_hash = content_hash(payload)
        prev = conn.execute(
            select(self._log.c.chain_hash)
            .order_by(self._log.c.seq.desc())
            .limit(1)
        ).scalar()
        prev_hash = prev if prev is not None else GENESIS
        result = conn.execute(
            insert(self._log).values(
                payload=canonical_json(payload),
                content_hash=c_hash,
                prev_hash=prev_hash,
                chain_hash=_chain(prev_hash, c_hash),
            )
        )
        primary_key = result.inserted_primary_key
        if primary_key is None:
            raise RuntimeError("audit_log insert did not return a primary key")
        return int(primary_key[0])

    def append(self, payload: dict[str, Any]) -> int:
        ambient = getattr(self._txn, "conn", None)
        if ambient is not None:
            # Inside a transaction(): read-your-writes on the shared connection
            # keeps the hash chain valid mid-batch; the context owns commit.
            return self._insert(ambient, payload)
        with self._engine.begin() as conn:
            if conn.dialect.name == "sqlite":
                conn.execute(text("BEGIN IMMEDIATE"))
            return self._insert(conn, payload)

    def read_all(self) -> list[AuditRecord]:
        self._assert_no_batch_in_progress("read_all")
        with self._engine.begin() as conn:
            rows = conn.execute(
                select(self._log).order_by(self._log.c.seq.asc())
            ).all()
        return [
            AuditRecord(
                seq=r.seq,
                payload=json.loads(r.payload),
                content_hash=r.content_hash,
                prev_hash=r.prev_hash,
                chain_hash=r.chain_hash,
            )
            for r in rows
        ]

    def read_by_seq(self, seq: int) -> AuditRecord | None:
        self._assert_no_batch_in_progress("read_by_seq")
        with self._engine.begin() as conn:
            row = conn.execute(
                select(self._log).where(self._log.c.seq == seq)
            ).first()
        if row is None:
            return None
        return AuditRecord(
            seq=row.seq,
            payload=json.loads(row.payload),
            content_hash=row.content_hash,
            prev_hash=row.prev_hash,
            chain_hash=row.chain_hash,
        )

    def verify_integrity(self) -> bool:
        self._assert_no_batch_in_progress("verify_integrity")
        prev_hash = GENESIS
        try:
            records = self.read_all()
        except (json.JSONDecodeError, TypeError, ValueError):
            return False
        for rec in records:
            # json.loads accepts Infinity/NaN, so a directly-tampered payload
            # survives read_all's decode but makes canonical_json(allow_nan=
            # False) raise out of content_hash. Treat that as tamper, not a
            # crash (Q-M3 / audit M6).
            try:
                computed = content_hash(rec.payload)
            except (ValueError, TypeError):
                return False
            if computed != rec.content_hash:
                return False
            if rec.prev_hash != prev_hash:
                return False
            if rec.chain_hash != _chain(rec.prev_hash, rec.content_hash):
                return False
            prev_hash = rec.chain_hash
        return True

    def get_latest_sequence_and_hash(self) -> tuple[int, str]:
        self._assert_no_batch_in_progress("get_latest_sequence_and_hash")
        with self._engine.begin() as conn:
            row = conn.execute(
                select(self._log.c.seq, self._log.c.chain_hash)
                .order_by(self._log.c.seq.desc())
                .limit(1)
            ).first()
        if row is None:
            return 0, GENESIS
        return row.seq, row.chain_hash
