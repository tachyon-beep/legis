"""Structured / protected sign-off — block + escalate, no LLM in the path.

``request`` records a PENDING_SIGNOFF and does NOT clear the gate; a designated
operator's ``sign_off`` records SIGNED_OFF (referencing the request) and clears
it. An optional ``signer`` makes protected-cell sign-offs tamper-bound;
structured sign-offs are procedural (unsigned). Human-in-the-loop by exception.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from legis.clock import Clock
from legis.enforcement.signing import sign
from legis.enforcement.verdict import SignoffState
from legis.identity.entity_key import EntityKey
from legis.records.override_record import OverrideRecord
from legis.store.audit_store import AuditStore


@dataclass(frozen=True)
class SignoffResult:
    seq: int
    cleared: bool


class SignoffGate:
    def __init__(
        self,
        store: AuditStore,
        clock: Clock,
        signer: bool | None = None,
        key: bytes | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        # `signer` truthy → protected sign-off (sign the SIGNED_OFF record).
        self._sign = bool(signer)
        self._key = key

    def _append(
        self,
        *,
        policy: str,
        entity_key: EntityKey,
        rationale: str,
        actor_id: str,
        ext: dict[str, Any],
    ) -> int:
        rec = OverrideRecord(
            policy=policy,
            entity_key=entity_key,
            rationale=rationale,
            agent_id=actor_id,
            recorded_at=self._clock.now_iso(),
            extensions=ext,
        )
        payload = rec.to_payload()
        if self._sign and self._key is not None:
            payload["extensions"]["signoff_signature"] = sign(
                {
                    "policy": payload["policy"],
                    "entity": payload["entity_key"],
                    "recorded_at": payload["recorded_at"],
                    "rationale": payload["rationale"],
                    "operator": actor_id,
                    "signoff_state": ext.get("signoff_state"),
                    "request_seq": ext.get("request_seq"),
                },
                self._key,
            )
        return self._store.append(payload)

    def request(
        self,
        *,
        policy: str,
        entity_key: EntityKey,
        rationale: str,
        agent_id: str,
        extensions: dict[str, Any] | None = None,
    ) -> SignoffResult:
        seq = self._append(
            policy=policy,
            entity_key=entity_key,
            rationale=rationale,
            actor_id=agent_id,
            ext={**(extensions or {}), "signoff_state": SignoffState.PENDING.value},
        )
        return SignoffResult(seq=seq, cleared=False)

    def sign_off(
        self, *, request_seq: int, operator_id: str, rationale: str = ""
    ) -> SignoffResult:
        req = self._store.read_all()[request_seq - 1].payload
        seq = self._append(
            policy=req["policy"],
            entity_key=EntityKey.from_dict(req["entity_key"]),
            rationale=rationale,
            actor_id=operator_id,
            ext={
                "signoff_state": SignoffState.SIGNED_OFF.value,
                "request_seq": request_seq,
            },
        )
        return SignoffResult(seq=seq, cleared=True)

    def request_record(self, request_seq: int) -> dict | None:
        """The recorded PENDING_SIGNOFF request payload at this seq, or None."""
        records = self._store.read_all()
        if not (1 <= request_seq <= len(records)):
            return None
        payload = records[request_seq - 1].payload
        if payload.get("extensions", {}).get("signoff_state") != SignoffState.PENDING.value:
            return None
        return payload

    def is_cleared(self, request_seq: int) -> bool:
        for rec in self._store.read_all():
            ext = rec.payload.get("extensions", {})
            if (
                ext.get("signoff_state") == SignoffState.SIGNED_OFF.value
                and ext.get("request_seq") == request_seq
            ):
                return True
        return False
