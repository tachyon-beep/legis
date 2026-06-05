"""CheckSurface — records and serves CI check runs.

Backed by an **indexed** relational table (queryable by commit / branch / PR) —
deliberately not Sprint 0's append-only hash-chained audit log. That log is the
governance trail (tamper-evidence); check runs are operational facts queried by
dimension. NullPool keeps a clean connection lifecycle (matching the audit
store).
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    insert,
    select,
)
from sqlalchemy.pool import NullPool

from legis.checks.models import CheckOutcome, CheckRun


class CheckSurface:
    def __init__(self, db_url: str) -> None:
        self._engine = create_engine(db_url, future=True, poolclass=NullPool)
        self._md = MetaData()
        self._runs = Table(
            "check_runs",
            self._md,
            Column("seq", Integer, primary_key=True, autoincrement=True),
            Column("check_name", String(256), nullable=False, index=True),
            Column("run_id", String(256), nullable=False),
            Column("commit_sha", String(64), nullable=False, index=True),
            Column("outcome", String(32), nullable=False),
            Column("branch", String(256), nullable=True, index=True),
            Column("pr", Integer, nullable=True, index=True),
            Column("ran_against", Text, nullable=True),
            Column("rule_set", Text, nullable=True),
            Column("policy_version", Text, nullable=True),
            Column("started_at", Text, nullable=True),
            Column("finished_at", Text, nullable=True),
            Column("recorded_by", Text, nullable=True),
            Column("provenance", Text, nullable=True),
        )
        self._md.create_all(self._engine)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._engine.begin() as conn:
            cols = {
                row[1]
                for row in conn.exec_driver_sql("PRAGMA table_info(check_runs)").all()
            }
            if "recorded_by" not in cols:
                conn.exec_driver_sql("ALTER TABLE check_runs ADD COLUMN recorded_by TEXT")
            if "provenance" not in cols:
                conn.exec_driver_sql("ALTER TABLE check_runs ADD COLUMN provenance TEXT")

    def record(self, run: CheckRun) -> int:
        with self._engine.begin() as conn:
            result = conn.execute(
                insert(self._runs).values(
                    check_name=run.check_name,
                    run_id=run.run_id,
                    commit_sha=run.commit_sha,
                    outcome=run.outcome.value,
                    branch=run.branch,
                    pr=run.pr,
                    ran_against=run.ran_against,
                    rule_set=run.rule_set,
                    policy_version=run.policy_version,
                    started_at=run.started_at,
                    finished_at=run.finished_at,
                    recorded_by=run.recorded_by,
                    provenance=run.provenance,
                )
            )
            primary_key = result.inserted_primary_key
            if primary_key is None:
                raise RuntimeError("check run insert did not return a primary key")
            return int(primary_key[0])

    def _select(self, whereclause) -> list[tuple[int, CheckRun]]:
        with self._engine.begin() as conn:
            rows = conn.execute(
                select(self._runs).where(whereclause).order_by(self._runs.c.seq.asc())
            ).all()
        return [(r.seq, self._to_run(r)) for r in rows]

    @staticmethod
    def _to_run(r) -> CheckRun:
        return CheckRun(
            check_name=r.check_name,
            run_id=r.run_id,
            commit_sha=r.commit_sha,
            outcome=CheckOutcome(r.outcome),
            branch=r.branch,
            pr=r.pr,
            ran_against=r.ran_against,
            rule_set=r.rule_set,
            policy_version=r.policy_version,
            started_at=r.started_at,
            finished_at=r.finished_at,
            recorded_by=r.recorded_by,
            # Rows written before this column existed are still writer-asserted.
            provenance=r.provenance or "unauthenticated",
        )

    def for_commit(self, sha: str) -> list[CheckRun]:
        return [run for _, run in self._select(self._runs.c.commit_sha == sha)]

    def for_branch(self, name: str) -> list[CheckRun]:
        return [run for _, run in self._select(self._runs.c.branch == name)]

    def for_pr(self, pr: int) -> list[CheckRun]:
        return [run for _, run in self._select(self._runs.c.pr == pr)]

    def latest_state(self, commit_sha: str) -> dict[str, CheckRun]:
        # Last write per check_name wins (rows are ordered by insert seq).
        state: dict[str, CheckRun] = {}
        for _, run in self._select(self._runs.c.commit_sha == commit_sha):
            state[run.check_name] = run
        return state
