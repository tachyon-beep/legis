"""Extracted governance decision logic — transport-agnostic.

Functions added here take their dependencies explicitly (no closures, no
globals) and, when they signal failure, raise ``ServiceError`` subclasses —
never a transport error. (``resolve_for_record`` itself propagates no errors.)
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from legis.enforcement.engine import EnforcementEngine, EnforcementResult
from legis.enforcement.lifecycle import evaluate_override_rate
from legis.enforcement.protected import (
    ProtectedGate,
    ProtectedResult,
    TamperError,
    TrailVerifier,
)
from legis.enforcement.signoff import SignoffGate, SignoffResult
from legis.governance import params
from legis.identity.entity_key import EntityKey
from legis.identity.resolver import IdentityResolver
from legis.policy.grammar import PolicyEvaluation, PolicyGrammar, PolicyResult
from legis.service.errors import (
    AuditIntegrityError,
    NotEnabledError,
    ProtectedKeyRequiredError,
)
from legis.service.source_binding import (
    require_verified_source_binding,
    verify_current_source_binding,
)


def resolve_for_record(
    identity: IdentityResolver | None, locator: str
) -> tuple[EntityKey, dict]:
    """The one resolve-then-key boundary.

    Keys on the SEI when Loomweave proves a stable identity, on the locator
    otherwise. When no resolver is wired legis runs standalone (locator-keyed).
    The ``loomweave`` extension carries the two distinct axes (identity: ``alive``,
    content: ``content_hash``) plus the REQ-L-01 lineage snapshot, never
    collapsed — present only when a resolution decision was actually made.
    """
    if identity is None:
        return EntityKey.from_locator(locator), {}
    res = identity.resolve(locator)
    ext: dict = {}
    if res.alive is not None:
        # Both status axes are mandatory str,Enum fields on IdentityResolution now,
        # so read them directly — the old getattr fallbacks guarded a shape the
        # type no longer permits. The members serialize as their bare strings.
        ext["loomweave"] = {
            "alive": res.alive,
            "content_hash": res.content_hash,
            "lineage_snapshot": res.lineage_snapshot,
            "identity_resolution_status": res.identity_resolution_status,
            "lineage_snapshot_status": res.lineage_snapshot_status,
        }
    return res.entity_key, ext


def verified_records(
    trail_owner,
    trail_verifier,
    engine_records: Callable[[], list],
):
    """The verified governance trail.

    ``trail_owner`` is whichever gate owns the trail being read: the protected
    gate for the governance trail, or the sign-off gate for the sign-off trail
    (the API ``bind-issue`` path passes the latter). When no owner is wired the
    simple-tier engine owns it instead (read lazily via ``engine_records`` so a
    protected deployment never initialises the engine store). Never mix the two
    stores. Verification is fail-closed and applies to EVERY consumer of the
    trail, so a tampered record is an honest integrity error
    (``AuditIntegrityError``), never silently read or scored.

    ``trail_owner`` and ``trail_verifier`` are intentionally left duck-typed (an
    owner exposing ``records()`` / ``verify_integrity()`` and a verifier
    exposing ``verify()``) so the service layer is not coupled to the
    enforcement concrete types.
    """
    if trail_owner is not None:
        records = trail_owner.records()
        verify_integrity = getattr(trail_owner, "verify_integrity", None)
        if verify_integrity is not None and not verify_integrity():
            raise AuditIntegrityError("audit integrity failure: database hash chain verification failed")
        if trail_verifier is not None:
            try:
                trail_verifier.verify(records)
            except TamperError as exc:
                raise AuditIntegrityError(f"audit integrity failure: {exc}") from exc
        return records
    return engine_records()


def compute_override_rate(records: list):
    """Evaluate the override-rate gate against the policy constants.

    Threshold/window/floor come from ADR-0002 constants — NOT caller input — so
    the gate an agent is measured against cannot be tuned by it.
    """
    return evaluate_override_rate(
        records,
        threshold=params.OVERRIDE_RATE_THRESHOLD,
        window=params.OVERRIDE_RATE_WINDOW,
        min_sample=params.OVERRIDE_RATE_MIN_SAMPLE,
    )


def _requires_protected_verification(payload: dict[str, Any], protected_policies) -> bool:
    ext = payload.get("extensions", {}) or {}
    return (
        payload.get("policy") in protected_policies
        or ext.get("protected_cell") is True
        or "judge_metadata_signature" in ext
        or "signoff_signature" in ext
        or "file_fingerprint" in ext
        or "ast_path" in ext
    )


def evaluate_override_rate_gate(
    records: list,
    *,
    hmac_key: str | None,
    protected_policies,
):
    """Content-driven override-rate gate: the single decision path for the cli.

    Detect protected records, require an HMAC key for them (fail closed — a
    protected trail cannot be scored unverified, 07cf54e), verify the protected
    trail, then score the override rate. This is the canonical implementation;
    the cli gate calls it rather than re-deriving the same decision (Q-H2).
    """
    protected_present = any(
        _requires_protected_verification(rec.payload, protected_policies) for rec in records
    )
    if protected_present and not hmac_key:
        raise ProtectedKeyRequiredError(
            "Protected audit records require LEGIS_HMAC_KEY for verification"
        )
    if hmac_key:
        verifier = TrailVerifier(hmac_key.encode("utf-8"), protected_policies)
        try:
            verifier.verify(records)
        except TamperError as exc:
            raise AuditIntegrityError(
                f"Protected audit trail verification failed: {exc}"
            ) from exc
    return compute_override_rate(records)


def submit_override(
    engine: EnforcementEngine,
    *,
    identity: IdentityResolver | None,
    policy: str,
    entity: str,
    rationale: str,
    agent_id: str,
    extra_extensions: dict[str, Any] | None = None,
) -> EnforcementResult:
    """Resolve-then-key, then submit the override to the simple-tier engine.

    Cell semantics live in the engine: judge absent → chill (always accepted);
    judge present → coached (ACCEPTED records, BLOCKED records the attempt). The
    adapter maps ``EnforcementResult.accepted`` to its transport's success/blocked
    signal (HTTP 201/409; MCP ACCEPTED_*/BLOCKED).

    Keyword-only after ``engine`` so the five same-typed fields cannot be
    transposed at the call site; this is the seam the MCP adapter (WP-M3) calls
    directly, alongside the existing ``POST /overrides`` handler.
    """
    entity_key, ext = resolve_for_record(identity, entity)
    return engine.submit_override(
        policy=policy,
        entity_key=entity_key,
        rationale=rationale,
        agent_id=agent_id,
        extensions={**ext, **(extra_extensions or {})},
    )


def submit_protected_override(
    protected_gate: ProtectedGate | None,
    *,
    identity: IdentityResolver | None,
    policy: str,
    entity: str,
    rationale: str,
    agent_id: str,
    file_fingerprint: str,
    ast_path: str,
    source_root: str | Path | None = None,
    extra_extensions: dict[str, Any] | None = None,
) -> ProtectedResult:
    """Submit a protected-cell override using transport-bound agent identity."""
    if protected_gate is None:
        raise NotEnabledError("protected cell not enabled")
    entity_key, ext = resolve_for_record(identity, entity)
    source_binding = verify_current_source_binding(
        entity=entity,
        file_fingerprint=file_fingerprint,
        source_root=source_root,
    )
    require_verified_source_binding(entity, source_binding)
    return protected_gate.submit(
        policy=policy,
        entity_key=entity_key,
        rationale=rationale,
        agent_id=agent_id,
        file_fingerprint=file_fingerprint,
        ast_path=ast_path,
        extensions={**ext, "source_binding": source_binding, **(extra_extensions or {})},
    )


def submit_operator_override(
    protected_gate: ProtectedGate | None,
    *,
    identity: IdentityResolver | None,
    policy: str,
    entity: str,
    rationale: str,
    operator_id: str,
    file_fingerprint: str,
    ast_path: str,
    source_root: str | Path | None = None,
) -> ProtectedResult:
    """Submit a protected-cell operator override with current-source binding."""
    if protected_gate is None:
        raise NotEnabledError("protected cell not enabled")
    entity_key, ext = resolve_for_record(identity, entity)
    source_binding = verify_current_source_binding(
        entity=entity,
        file_fingerprint=file_fingerprint,
        source_root=source_root,
    )
    require_verified_source_binding(entity, source_binding)
    return protected_gate.operator_override(
        policy=policy,
        entity_key=entity_key,
        rationale=rationale,
        operator_id=operator_id,
        file_fingerprint=file_fingerprint,
        ast_path=ast_path,
        extensions={**ext, "source_binding": source_binding},
    )


def request_signoff(
    signoff_gate: SignoffGate | None,
    *,
    identity: IdentityResolver | None,
    policy: str,
    entity: str,
    rationale: str,
    agent_id: str,
    extra_extensions: dict[str, Any] | None = None,
) -> SignoffResult:
    """Open a structured sign-off request for a launch-bound agent."""
    if signoff_gate is None:
        raise NotEnabledError("structured cell not enabled")
    entity_key, ext = resolve_for_record(identity, entity)
    return signoff_gate.request(
        policy=policy,
        entity_key=entity_key,
        rationale=rationale,
        agent_id=agent_id,
        extensions={**ext, **(extra_extensions or {})},
    )


def sign_off(
    signoff_gate: SignoffGate | None,
    *,
    request_seq: int,
    operator_id: str,
    rationale: str = "",
) -> SignoffResult:
    """Operator sign-off on a pending structured request.

    The single service path for clearing a sign-off, so the HTTP route no longer
    reaches past the service layer to the gate (Q-H2).
    """
    if signoff_gate is None:
        raise NotEnabledError("structured cell not enabled")
    return signoff_gate.sign_off(
        request_seq=request_seq,
        operator_id=operator_id,
        rationale=rationale,
    )


def evaluate_policy(
    grammar: PolicyGrammar,
    *,
    engine: EnforcementEngine | None,
    policy: str,
    target: dict[str, Any],
) -> PolicyEvaluation:
    """Evaluate policy grammar and optionally record UNKNOWN provenance gaps."""
    ev = grammar.evaluate(policy, target)
    if ev.result is PolicyResult.UNKNOWN and engine is not None:
        engine.record_event(
            {
                "event": "UNKNOWN_POLICY",
                "policy": ev.policy,
                "detail": ev.detail,
                "provenance_gap": True,
            }
        )
    return ev
