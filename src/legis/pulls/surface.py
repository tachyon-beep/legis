"""PullSurface — records and serves pull-request metadata."""

from __future__ import annotations

from sqlalchemy import Column, Integer, MetaData, String, Table, Text, create_engine, delete, insert, select
from sqlalchemy.pool import NullPool

from legis.pulls.models import PullRequest, PullRequestState


class PullSurface:
    def __init__(self, db_url: str) -> None:
        self._engine = create_engine(db_url, future=True, poolclass=NullPool)
        self._md = MetaData()
        self._pulls = Table(
            "pull_requests",
            self._md,
            Column("number", Integer, primary_key=True),
            Column("title", Text, nullable=False),
            Column("base", String(256), nullable=False, index=True),
            Column("head", String(256), nullable=False, index=True),
            Column("state", String(32), nullable=False, index=True),
            Column("url", Text, nullable=True),
            Column("recorded_by", Text, nullable=True),
        )
        self._md.create_all(self._engine)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._engine.begin() as conn:
            cols = {
                row[1]
                for row in conn.exec_driver_sql("PRAGMA table_info(pull_requests)").all()
            }
            if "recorded_by" not in cols:
                conn.exec_driver_sql("ALTER TABLE pull_requests ADD COLUMN recorded_by TEXT")

    def record(self, pr: PullRequest) -> None:
        with self._engine.begin() as conn:
            conn.execute(delete(self._pulls).where(self._pulls.c.number == pr.number))
            conn.execute(
                insert(self._pulls).values(
                    number=pr.number,
                    title=pr.title,
                    base=pr.base,
                    head=pr.head,
                    state=pr.state.value,
                    url=pr.url,
                    recorded_by=pr.recorded_by,
                )
            )

    def get(self, number: int) -> PullRequest | None:
        with self._engine.begin() as conn:
            row = conn.execute(
                select(self._pulls).where(self._pulls.c.number == number)
            ).first()
        if row is None:
            return None
        return PullRequest(
            number=row.number,
            title=row.title,
            base=row.base,
            head=row.head,
            state=PullRequestState(row.state),
            url=row.url,
            recorded_by=row.recorded_by,
        )
