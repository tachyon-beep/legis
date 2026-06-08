"""Protected cell — tamper-bound, judge-gated verdicts + load-time verification.

Layered over the coached cell: every verdict is bound to the inspected source
(``file_fingerprint`` + ``ast_path``) and HMAC-signed. ``signing_fields`` is the
single source of the signed dict — both the gate (write) and ``TrailVerifier``
(read) call it, so they cannot drift. The signed dict binds entity + policy in
addition to the roadmap's six fields, so a valid signed verdict cannot be
transplanted onto a different entity.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from legis.clock import Clock
from legis.enforcement.judge import Judge
from legis.enforcement.signing import SIG_PREFIX_V3, sign, verify
from legis.enforcement.signoff import signoff_signing_fields
from legis.enforcement.verdict import Verdict
from legis.identity.entity_key import EntityKey
from legis.records.override_record import OverrideRecord
from legis.store.head_anchor import AnchorError, HeadAnchor
from legis.store.protocol import AppendOnlyStore


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


def signing_fields(
    payload: dict[str, Any], *, seq: int | None = None
) -> dict[str, Any]:
    """The exact dict that is HMAC-signed — reconstructable from a stored payload.

    Binds entity + policy in addition to the roadmap's six fields, so a signed
    verdict cannot be transplanted to another entity.

    When *seq* is given (AUD-1 / v3), the record's chain position is folded in,
    binding the verdict not just to its content but to *where* it sits in the
    trail — closing the delete-and-rechain forgery. At verify time *seq* MUST be
    the seq column of the stored row, never a payload field (which an attacker
    controls identically), or the binding is theatre.
    """
    ext = payload.get("extensions") or {}
    clar = ext.get("loomweave") or {}
    snap = clar.get("lineage_snapshot") or {}
    fields = {
        "policy": payload.get("policy"),
        "entity": payload.get("entity_key"),
        "verdict": ext.get("judge_verdict"),
        "model": ext.get("judge_model"),
        "recorded_at": payload.get("recorded_at"),
        "rationale": payload.get("rationale"),
        "agent_id": payload.get("agent_id"),
        "protected_cell": ext.get("protected_cell") is True,
        "file_fingerprint": ext.get("file_fingerprint"),
        "ast_path": ext.get("ast_path"),
        "judge_rationale": ext.get("judge_rationale"),
        "loomweave_content_hash": clar.get("content_hash"),
        "loomweave_lineage_hash": snap.get("hash"),
        "loomweave_lineage_len": snap.get("length"),
    }
    source_binding = ext.get("source_binding")
    if isinstance(source_binding, dict) and source_binding:
        fields.update(
            {
                "source_binding_status": source_binding.get("status"),
                "source_binding_reason": source_binding.get("reason"),
                "source_binding_source_path": source_binding.get("source_path"),
                "source_binding_current_fingerprint": source_binding.get(
                    "current_fingerprint"
                ),
            }
        )
    if seq is not None:
        fields["chain_seq"] = seq
    return fields


class TrailVerifier:
    """Load-time signature check. A record whose policy is protected MUST carry a
    valid signature; a missing or mismatched signature is tampering.

    The protected-policy set comes from config (ADR-0002), NOT from the record —
    so stripping a signature and flipping an in-record flag cannot downgrade a
    protected record to "unsigned, skip".
    """

    def __init__(
        self,
        key: bytes,
        protected_policies: frozenset[str],
        *,
        anchor: HeadAnchor | None = None,
    ) -> None:
        self._key = key
        self._protected = protected_policies
        # Opt-in (AUD-1): an out-of-band head anchor that catches tail-truncation,
        # which seq-binding + contiguity structurally cannot. None → not anchored.
        self._anchor = anchor

    @property
    def protected_policies(self) -> frozenset[str]:
        return self._protected

    def _requires_verification(self, payload: dict[str, Any]) -> bool:
        ext = payload.get("extensions", {}) or {}
        return (
            payload.get("policy") in self._protected
            or ext.get("protected_cell") is True
            or "judge_metadata_signature" in ext
            or "signoff_signature" in ext
            or "file_fingerprint" in ext
            or "ast_path" in ext
        )

    def verify(self, records) -> None:
        records = list(records)
        # Tail-truncation check first (AUD-1): the per-record signature pass
        # below cannot see records that are simply gone. The anchor can.
        if self._anchor is not None:
            try:
                self._anchor.check(records)
            except AnchorError as exc:
                raise TamperError(str(exc)) from exc
        for rec in records:
            if not self._requires_verification(rec.payload):
                continue
            if "entity_key" not in rec.payload:
                raise TamperError(
                    f"protected record seq={rec.seq} is missing entity_key"
                )
            ext = rec.payload.get("extensions", {})
            if "signoff_state" in ext:
                sig = ext.get("signoff_signature")
                if not sig:
                    raise TamperError(
                        f"protected sign-off record seq={rec.seq} is missing its signature"
                    )
                if sig.startswith(SIG_PREFIX_V3):
                    fields = signoff_signing_fields(rec.payload, seq=rec.seq)
                else:
                    fields = signoff_signing_fields(rec.payload)
                if not verify(fields, sig, self._key):
                    raise TamperError(
                        f"protected sign-off record seq={rec.seq} signature does not verify"
                    )
            else:
                sig = ext.get("judge_metadata_signature")
                if not sig:
                    raise TamperError(
                        f"protected override record seq={rec.seq} is missing its signature"
                    )
                try:
                    # v3 (AUD-1) binds the chain position: reconstruct from the
                    # seq COLUMN (rec.seq), never a payload field, so a renumbered
                    # record fails to verify at its new position. v2 records
                    # (legacy / pre-AUD-1) carry no position binding.
                    if sig.startswith(SIG_PREFIX_V3):
                        fields = signing_fields(rec.payload, seq=rec.seq)
                    else:
                        fields = signing_fields(rec.payload)
                except (KeyError, AttributeError, TypeError) as exc:
                    raise TamperError(
                        f"protected record seq={rec.seq} is structurally malformed: {exc}"
                    ) from exc
                if not verify(fields, sig, self._key):
                    raise TamperError(
                        f"protected record seq={rec.seq} signature does not verify"
                    )


# A deterministic, non-LLM check that an ACCEPTED override on a protected policy
# is actually justified. Returns True to confirm the model's ACCEPTED, False to
# veto it. Receives the proposed record (its rationale is data, never executed).
ProtectedValidator = Callable[[OverrideRecord], bool]


class ProtectedGate:
    def __init__(
        self,
        store: AppendOnlyStore,
        clock: Clock,
        judge: Judge,
        key: bytes,
        *,
        protected_policies: frozenset[str] = frozenset(),
        validator: ProtectedValidator | None = None,
        anchor: HeadAnchor | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._judge = judge
        self._key = key
        # Opt-in (AUD-1): advanced to the committed head after each append so a
        # later tail-truncation is detectable. None → not anchored (default).
        self._anchor = anchor
        # For these policies the LLM judge is ADVISORY ONLY (Q-H3): a model
        # ACCEPTED does not clear the gate on the model's word. A prompt-injected
        # rationale that fools the judge into ACCEPTED would otherwise be
        # HMAC-signed as authoritative evidence. ACCEPTED stands only if a
        # non-LLM deterministic validator confirms it; otherwise it is downgraded
        # to BLOCKED and the agent must obtain operator sign-off
        # (operator_override). Empty set / no validator preserves prior behaviour
        # for non-protected policies.
        self._protected_policies = protected_policies
        self._validator = validator

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
        extensions: dict[str, Any] | None = None,
    ) -> ProtectedResult:
        ext: dict[str, Any] = {
            **(extensions or {}),
            "protected_cell": True,
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
        captured: dict[str, str] = {}

        def build(seq: int, _prev_hash: str) -> dict[str, Any]:
            # AUD-1 / v3: the store hands us our own chain position so the
            # signature binds seq. A renumber-to-hide-a-deletion then fails to
            # verify at the new position.
            payload = base.to_payload()
            signature = sign(
                signing_fields(payload, seq=seq), self._key, version="v3"
            )
            payload["extensions"]["judge_metadata_signature"] = signature
            captured["signature"] = signature
            return payload

        seq = self._store.append_signed(build)
        if self._anchor is not None:
            self._anchor.update(*self._store.get_latest_sequence_and_hash())
        signature = captured["signature"]
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
        extensions: dict[str, Any] | None = None,
    ) -> ProtectedResult:
        proposed_ext = {
            **(extensions or {}),
            "file_fingerprint": file_fingerprint,
            "ast_path": ast_path,
        }
        proposed = OverrideRecord(
            policy=policy,
            entity_key=entity_key,
            rationale=rationale,
            agent_id=agent_id,
            recorded_at=self._clock.now_iso(),
            extensions=proposed_ext,
        )
        opinion = self._judge.evaluate(proposed)
        verdict = opinion.verdict
        record_ext = dict(extensions or {})
        if (
            verdict is Verdict.ACCEPTED
            and policy in self._protected_policies
            and (self._validator is None or not self._validator(proposed))
        ):
            # Model is advisory on a protected policy: its ACCEPTED is recorded
            # for audit but does NOT clear the gate (Q-H3). Downgrade the signed
            # verdict to BLOCKED; the agent must escalate to operator sign-off.
            record_ext["judge_advisory_verdict"] = Verdict.ACCEPTED.value
            verdict = Verdict.BLOCKED
        return self._record_signed(
            policy=policy,
            entity_key=entity_key,
            rationale=rationale,
            actor_id=agent_id,
            verdict=verdict,
            model=opinion.model,
            judge_rationale=opinion.rationale,
            file_fingerprint=file_fingerprint,
            ast_path=ast_path,
            extensions=record_ext,
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
        extensions: dict[str, Any] | None = None,
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
            extensions=extensions,
        )

    def records(self):
        """The governance trail this gate writes to — for verified reads."""
        return self._store.read_all()

    def verify_integrity(self) -> bool:
        """Verify the underlying append-only hash chain before HMAC checks."""
        return self._store.verify_integrity()
