"""Extracted governance decision logic — transport-agnostic.

Functions added here take their dependencies explicitly (no closures, no
globals) and, when they signal failure, raise ``ServiceError`` subclasses —
never a transport error. (``resolve_for_record`` itself propagates no errors.)
"""

from __future__ import annotations

from collections.abc import Callable

from legis.enforcement.lifecycle import evaluate_override_rate
from legis.enforcement.protected import TamperError
from legis.governance import params
from legis.identity.entity_key import EntityKey
from legis.identity.resolver import IdentityResolver
from legis.service.errors import AuditIntegrityError


def resolve_for_record(
    identity: IdentityResolver | None, locator: str
) -> tuple[EntityKey, dict]:
    """The one resolve-then-key boundary.

    Keys on the SEI when Clarion proves a stable identity, on the locator
    otherwise. When no resolver is wired legis runs standalone (locator-keyed).
    The ``clarion`` extension carries the two distinct axes (identity: ``alive``,
    content: ``content_hash``) plus the REQ-L-01 lineage snapshot, never
    collapsed — present only when a resolution decision was actually made.
    """
    if identity is None:
        return EntityKey.from_locator(locator), {}
    res = identity.resolve(locator)
    ext: dict = {}
    if res.alive is not None:
        ext["clarion"] = {
            "alive": res.alive,
            "content_hash": res.content_hash,
            "lineage_snapshot": res.lineage_snapshot,
        }
    return res.entity_key, ext


def verified_records(
    protected_gate,
    trail_verifier,
    engine_records: Callable[[], list],
):
    """The verified governance trail.

    The protected gate (when wired) owns the governance trail; otherwise the
    simple-tier engine does (read lazily via ``engine_records`` so a protected
    deployment never initialises the engine store). Never mix the two stores.
    Verification is fail-closed and applies to EVERY consumer of the protected
    trail, so a tampered record is an honest integrity error
    (``AuditIntegrityError``), never silently read or scored.
    """
    if protected_gate is not None:
        records = protected_gate.records()
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
