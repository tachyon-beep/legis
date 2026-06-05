"""The simple-tier enforcement engine — chill and coached cells.

One method, ``submit_override``. Whether a judge is injected is the *only*
difference between the two cells (the "single config flag"):

* **chill**  (``judge=None``): the proposed override records as-is, accepted.
* **coached** (``judge`` present): the judge evaluates *before* the record is
  written; ACCEPTED records the override as taken, BLOCKED records the attempt
  with ``accepted=False`` and returns the judge's reasoning so the agent can
  revise. There is no operator self-clear in this tier.

Every submission produces exactly one append-only, hash-chained audit record —
no silent path. The engine stamps ``recorded_at`` from the injected clock.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from legis.clock import Clock
from legis.enforcement.judge import Judge
from legis.enforcement.verdict import Verdict
from legis.identity.entity_key import EntityKey
from legis.records.override_record import OverrideRecord
from legis.store.protocol import AppendOnlyStore


@dataclass(frozen=True)
class EnforcementResult:
    accepted: bool
    seq: int
    verdict: Verdict | None
    judge_model: str | None
    judge_rationale: str | None


class EnforcementEngine:
    def __init__(
        self,
        store: AppendOnlyStore,
        clock: Clock,
        judge: Judge | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._judge = judge

    @property
    def has_judge(self) -> bool:
        return self._judge is not None

    def submit_override(
        self,
        *,
        policy: str,
        entity_key: EntityKey,
        rationale: str,
        agent_id: str,
        extensions: dict | None = None,
    ) -> EnforcementResult:
        record = OverrideRecord(
            policy=policy,
            entity_key=entity_key,
            rationale=rationale,
            agent_id=agent_id,
            recorded_at=self._clock.now_iso(),
            extensions=dict(extensions or {}),
        )

        if self._judge is None:
            seq = self._store.append(record.to_payload())
            return EnforcementResult(
                accepted=True,
                seq=seq,
                verdict=None,
                judge_model=None,
                judge_rationale=None,
            )

        opinion = self._judge.evaluate(record)
        judged = replace(
            record,
            extensions={
                **record.extensions,
                "judge_verdict": opinion.verdict.value,
                "judge_model": opinion.model,
                "judge_rationale": opinion.rationale,
            },
        )
        seq = self._store.append(judged.to_payload())
        return EnforcementResult(
            accepted=opinion.verdict is Verdict.ACCEPTED,
            seq=seq,
            verdict=opinion.verdict,
            judge_model=opinion.model,
            judge_rationale=opinion.rationale,
        )

    def trail(self) -> list[dict]:
        """The append-only governance trail, decoded — for async human review."""
        return [rec.payload for rec in self._store.read_all()]

    def records(self):
        """The raw audit records (with seq/hashes) — for lifecycle gates."""
        return self._store.read_all()

    def transaction(self):
        """Group this engine's appends into one all-or-nothing transaction (Q-M5)."""
        return self._store.transaction()

    def record_event(self, payload: dict) -> int:
        """Append a raw governance event (e.g. UNKNOWN_POLICY) to the trail.

        Stamps ``recorded_at`` from the injected clock when the caller omits it,
        so non-override governance events share the one append-only trail.
        """
        body = {**payload}
        body.setdefault("recorded_at", self._clock.now_iso())
        return self._store.append(body)
