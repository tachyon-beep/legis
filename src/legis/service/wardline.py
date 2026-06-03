"""Transport-agnostic Wardline governance routing."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from legis.canonical import content_hash
from legis.enforcement.engine import EnforcementEngine
from legis.enforcement.signoff import SignoffGate
from legis.identity.entity_key import EntityKey
from legis.identity.resolver import IdentityResolver
from legis.service.governance import resolve_for_record
from legis.wardline.governor import WardlineCellPolicy, route_findings
from legis.wardline.ingest import (
    WardlineSeverity,
    active_defects,
    verify_wardline_artifact,
    wardline_artifact_fields,
)


def route_wardline_scan(
    scan: Mapping[str, Any],
    *,
    agent_id: str,
    identity: IdentityResolver | None,
    engine: EnforcementEngine | None,
    signoff: SignoffGate | None,
    policy: WardlineCellPolicy | None = None,
    cell_map: dict[WardlineSeverity, WardlineCellPolicy] | None = None,
    artifact_key: bytes | None = None,
) -> list[dict[str, Any]]:
    artifact_provenance = verify_wardline_artifact(scan, artifact_key)
    findings = active_defects(scan)

    def resolve(qualname: str | None) -> tuple[EntityKey, dict[str, Any]]:
        if qualname:
            return resolve_for_record(identity, qualname)
        return EntityKey.from_locator("unknown"), {}

    raw_findings = scan.get("findings", [])
    batch_provenance = {
        "scan_digest": f"sha256:{content_hash(wardline_artifact_fields(scan))}",
        "finding_count": len(raw_findings) if isinstance(raw_findings, list) else 0,
        "active_count": len(findings),
        **artifact_provenance,
    }
    return route_findings(
        findings,
        policy=policy,
        cell_map=cell_map,
        agent_id=agent_id,
        resolve=resolve,
        engine=engine,
        signoff=signoff,
        batch_provenance=batch_provenance,
    )
