"""Protected cell — tamper-bound, judge-gated verdicts + load-time verification.

Layered over the coached cell: every verdict is bound to the inspected source
(``file_fingerprint`` + ``ast_path``) and HMAC-signed. ``signing_fields`` is the
single source of the signed dict — both the gate (write) and ``TrailVerifier``
(read) call it, so they cannot drift. The signed dict binds entity + policy in
addition to the roadmap's six fields, so a valid signed verdict cannot be
transplanted onto a different entity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from legis.clock import Clock
from legis.enforcement.judge import Judge
from legis.enforcement.signing import sign, verify
from legis.enforcement.verdict import Verdict
from legis.identity.entity_key import EntityKey
from legis.records.override_record import OverrideRecord
from legis.store.audit_store import AuditStore


class TamperError(RuntimeError):
    """A protected record failed load-time signature verification."""


@dataclass(frozen=True)
class ProtectedResult:
    accepted: bool
    seq: int
    verdict: Verdict
    judge_model: str | None
    judge_rationale: str | None
    signature: str


def signing_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """The exact dict that is HMAC-signed — reconstructable from a stored payload.

    Binds entity + policy in addition to the roadmap's six fields, so a signed
    verdict cannot be transplanted to another entity.
    """
    ext = payload["extensions"]
    return {
        "policy": payload["policy"],
        "entity": payload["entity_key"],
        "verdict": ext["judge_verdict"],
        "model": ext.get("judge_model"),
        "recorded_at": payload["recorded_at"],
        "rationale": payload["rationale"],
        "file_fingerprint": ext.get("file_fingerprint"),
        "ast_path": ext.get("ast_path"),
    }


class ProtectedGate:
    def __init__(
        self, store: AuditStore, clock: Clock, judge: Judge, key: bytes
    ) -> None:
        self._store = store
        self._clock = clock
        self._judge = judge
        self._key = key

    def _record_signed(
        self,
        *,
        policy: str,
        entity_key: EntityKey,
        rationale: str,
        actor_id: str,
        verdict: Verdict,
        model: str | None,
        judge_rationale: str | None,
        file_fingerprint: str,
        ast_path: str,
    ) -> ProtectedResult:
        ext: dict[str, Any] = {
            "judge_verdict": verdict.value,
            "judge_model": model,
            "judge_rationale": judge_rationale,
            "file_fingerprint": file_fingerprint,
            "ast_path": ast_path,
        }
        base = OverrideRecord(
            policy=policy,
            entity_key=entity_key,
            rationale=rationale,
            agent_id=actor_id,
            recorded_at=self._clock.now_iso(),
            extensions=ext,
        )
        payload = base.to_payload()
        signature = sign(signing_fields(payload), self._key)
        payload["extensions"]["judge_metadata_signature"] = signature
        seq = self._store.append(payload)
        return ProtectedResult(
            accepted=verdict in (Verdict.ACCEPTED, Verdict.OVERRIDDEN_BY_OPERATOR),
            seq=seq,
            verdict=verdict,
            judge_model=model,
            judge_rationale=judge_rationale,
            signature=signature,
        )

    def submit(
        self,
        *,
        policy: str,
        entity_key: EntityKey,
        rationale: str,
        agent_id: str,
        file_fingerprint: str,
        ast_path: str,
    ) -> ProtectedResult:
        proposed = OverrideRecord(
            policy=policy,
            entity_key=entity_key,
            rationale=rationale,
            agent_id=agent_id,
            recorded_at=self._clock.now_iso(),
        )
        opinion = self._judge.evaluate(proposed)
        return self._record_signed(
            policy=policy,
            entity_key=entity_key,
            rationale=rationale,
            actor_id=agent_id,
            verdict=opinion.verdict,
            model=opinion.model,
            judge_rationale=opinion.rationale,
            file_fingerprint=file_fingerprint,
            ast_path=ast_path,
        )

    def operator_override(
        self,
        *,
        policy: str,
        entity_key: EntityKey,
        rationale: str,
        operator_id: str,
        file_fingerprint: str,
        ast_path: str,
    ) -> ProtectedResult:
        # A human uses authority to bypass the judge. No model is consulted; the
        # verdict is the distinct OVERRIDDEN_BY_OPERATOR signal, still tamper-bound.
        return self._record_signed(
            policy=policy,
            entity_key=entity_key,
            rationale=rationale,
            actor_id=operator_id,
            verdict=Verdict.OVERRIDDEN_BY_OPERATOR,
            model=None,
            judge_rationale=None,
            file_fingerprint=file_fingerprint,
            ast_path=ast_path,
        )

    def records(self):
        """The governance trail this gate writes to — for verified reads."""
        return self._store.read_all()
