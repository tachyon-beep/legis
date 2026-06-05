"""Structured / protected sign-off — block + escalate, no LLM in the path.

``request`` records a PENDING_SIGNOFF and does NOT clear the gate; a designated
operator's ``sign_off`` records SIGNED_OFF (referencing the request) and clears
it. An optional ``signer`` makes protected-cell sign-offs tamper-bound;
structured sign-offs are procedural (unsigned). Human-in-the-loop by exception.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from legis.canonical import content_hash
from legis.clock import Clock
from legis.enforcement.signing import sign
from legis.enforcement.verdict import SignoffState
from legis.identity.entity_key import EntityKey
from legis.records.override_record import OverrideRecord
from legis.store.protocol import AppendOnlyStore


@dataclass(frozen=True)
class SignoffResult:
    seq: int
    cleared: bool


def signoff_signing_fields(payload: dict[str, Any]) -> dict[str, Any]:
    ext = payload.get("extensions") or {}
    clar = ext.get("loomweave") or {}
    snap = clar.get("lineage_snapshot") or {}
    return {
        "policy": payload.get("policy"),
        "entity": payload.get("entity_key"),
        "recorded_at": payload.get("recorded_at"),
        "rationale": payload.get("rationale"),
        "actor": payload.get("agent_id"),
        "signoff_state": ext.get("signoff_state"),
        "request_seq": ext.get("request_seq"),
        "request_payload_hash": ext.get("request_payload_hash"),
        "loomweave_content_hash": clar.get("content_hash"),
        "loomweave_lineage_hash": snap.get("hash"),
        "loomweave_lineage_len": snap.get("length"),
    }


class SignoffGate:
    def __init__(
        self,
        store: AppendOnlyStore,
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
                signoff_signing_fields(payload), self._key
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
        req = self.request_record(request_seq)
        if req is None:
            raise ValueError(f"No pending sign-off request found at sequence {request_seq}")
        if self.is_cleared(request_seq):
            raise ValueError(f"Request at sequence {request_seq} has already been signed off")
        seq = self._append(
            policy=req["policy"],
            entity_key=EntityKey.from_dict(req["entity_key"]),
            rationale=rationale,
            actor_id=operator_id,
            ext={
                "signoff_state": SignoffState.SIGNED_OFF.value,
                "request_seq": request_seq,
                "request_payload_hash": content_hash(req),
            },
        )
        return SignoffResult(seq=seq, cleared=True)

    def request_record(self, request_seq: int) -> dict | None:
        """The recorded PENDING_SIGNOFF request payload at this seq, or None."""
        rec = self._store.read_by_seq(request_seq)
        if rec is None:
            return None
        payload = rec.payload
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

    def records(self):
        """The sign-off trail this gate writes to — for verified consumers."""
        return self._store.read_all()

    def verify_integrity(self) -> bool:
        """Verify the underlying append-only hash chain before HMAC checks."""
        return self._store.verify_integrity()
