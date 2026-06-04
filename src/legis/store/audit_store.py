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

    def append(self, payload: dict[str, Any]) -> int:
        c_hash = content_hash(payload)
        with self._engine.begin() as conn:
            if conn.dialect.name == "sqlite":
                conn.execute(text("BEGIN IMMEDIATE"))
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

    def read_all(self) -> list[AuditRecord]:
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
        prev_hash = GENESIS
        try:
            records = self.read_all()
        except (json.JSONDecodeError, TypeError, ValueError):
            return False
        for rec in records:
            if content_hash(rec.payload) != rec.content_hash:
                return False
            if rec.prev_hash != prev_hash:
                return False
            if rec.chain_hash != _chain(rec.prev_hash, rec.content_hash):
                return False
            prev_hash = rec.chain_hash
        return True

    def get_latest_sequence_and_hash(self) -> tuple[int, str]:
        with self._engine.begin() as conn:
            row = conn.execute(
                select(self._log.c.seq, self._log.c.chain_hash)
                .order_by(self._log.c.seq.desc())
                .limit(1)
            ).first()
        if row is None:
            return 0, GENESIS
        return row.seq, row.chain_hash
