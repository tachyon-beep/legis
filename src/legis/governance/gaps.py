"""Lineage-spine consumers: orphan governance gaps + append-only custody.

An attestation keyed on an SEI that Clarion now reports ``alive: false`` is a
*governance gap* (fail-closed: surfaced, never silently dropped — locked
decision 4). REQ-L-01 Option 3 custody: legis stored a lineage snapshot at the
decision; on re-read it verifies the snapshot is still a PREFIX of the current
lineage. Appends (rename/move) are legitimate; a removed or mutated prior event
is divergence. A bare whole-list mismatch is NOT tamper — lineage legitimately
grows; only a broken prefix is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from legis.canonical import content_hash
from legis.identity.clarion_client import ClarionIdentity
from legis.store.audit_store import AuditRecord


@dataclass(frozen=True)
class GovernanceGap:
    sei: str
    reason: str
    lineage: list[dict[str, Any]]


@dataclass(frozen=True)
class LineageDivergence:
    sei: str
    recorded_length: int
    current_length: int


def _stable_seis(records: list[AuditRecord]) -> list[str]:
    seen: dict[str, None] = {}  # ordered, de-duplicated
    for rec in records:
        ek = rec.payload.get("entity_key", {})
        if ek.get("identity_stable") and ek.get("value"):
            seen.setdefault(ek["value"], None)
    return list(seen)


def find_orphan_gaps(
    records: list[AuditRecord], client: ClarionIdentity
) -> list[GovernanceGap]:
    gaps: list[GovernanceGap] = []
    for sei in _stable_seis(records):
        res = client.resolve_sei(sei)
        if not res.get("alive"):
            gaps.append(GovernanceGap(sei, "orphaned", list(res.get("lineage", []))))
    return gaps


def find_lineage_divergence(
    records: list[AuditRecord], client: ClarionIdentity
) -> list[LineageDivergence]:
    divergences: list[LineageDivergence] = []
    lineages: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        ek = rec.payload.get("entity_key", {})
        sei = ek.get("value")
        if not (ek.get("identity_stable") and sei):
            continue
        snap = (rec.payload.get("extensions", {}).get("clarion", {}) or {}).get(
            "lineage_snapshot"
        )
        if not snap:
            continue
        if sei not in lineages:
            try:
                lineages[sei] = client.lineage(sei)
            except Exception:
                continue
        current = lineages[sei]
        n = snap["length"]
        if len(current) < n or content_hash(current[:n]) != snap["hash"]:
            divergences.append(
                LineageDivergence(sei=sei, recorded_length=n, current_length=len(current))
            )
    return divergences
