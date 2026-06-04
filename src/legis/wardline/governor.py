"""Route Wardline findings into the configured 2x2 cell — legis governs.

One judge, not two: Wardline produced the finding; legis decides who answers.
The cell is configured either for the whole scan via ``policy`` (single cell,
the proven Wardline handshake path) or per-severity via ``cell_map``
(``dict[WardlineSeverity, WardlineCellPolicy]``).  Exactly one must be given.
With ``cell_map``, every severity present in the scan must be mapped explicitly;
an omission is a configuration error, not an implicit downgrade.

The finding's ``rule_id`` is the policy; its ``qualname`` is the entity to key
on (resolved to an SEI via the same Sprint-5 resolver when available); its
``message`` seeds the rationale.

* **surface+override** records an override carrying the finding's
  ``fingerprint``, trust ``tiers`` (the one shared vocabulary, verbatim from
  ``properties``), and ``severity`` in the record's ``extensions``.
* **block+escalate** opens a sign-off request carrying ``rule_id`` (policy),
  the resolved entity, the seeded rationale, and the same Clarion/Wardline
  evidence extensions the surface paths preserve.
* **surface+only** records a non-gating ``wardline_surfaced`` governance event
  via ``EnforcementEngine.record_event``; no judge, no sign-off gate. Carries
  ``entity_key`` + ``clarion`` and ``wardline`` extensions so it is
  orphan-detectable consistently with a ``surface_override`` record.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Any, Mapping

from legis.enforcement.engine import EnforcementEngine
from legis.enforcement.signoff import SignoffGate
from legis.identity.entity_key import EntityKey
from legis.wardline.ingest import WardlineFinding, WardlineSeverity


class WardlineCellPolicy(str, Enum):
    SURFACE_OVERRIDE = "surface_override"
    BLOCK_ESCALATE = "block_escalate"
    SURFACE_ONLY = "surface_only"


def route_findings(
    findings: list[WardlineFinding],
    *,
    policy: WardlineCellPolicy | None = None,
    cell_map: dict[WardlineSeverity, WardlineCellPolicy] | None = None,
    agent_id: str,
    resolve: Callable[[str | None], tuple[EntityKey, dict[str, Any]]],
    engine: EnforcementEngine | None = None,
    signoff: SignoffGate | None = None,
    batch_provenance: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if (policy is None) == (cell_map is None):
        raise ValueError("exactly one of policy or cell_map must be given")

    # Validate every dependency the run could need BEFORE writing anything.
    # This guard ensures no write begins until all required engine/signoff
    # dependencies are confirmed present — preventing a mid-loop ValueError
    # after some findings have already been persisted. It is NOT full
    # transactional atomicity: a successful mixed batch spans two append-only
    # stores (engine and signoff), and a mid-loop runtime failure leaves any
    # prior writes in those stores permanently persisted.
    if cell_map is not None:
        missing = {f.severity for f in findings} - set(cell_map)
        if missing:
            names = ", ".join(sorted(sev.value for sev in missing))
            raise ValueError(f"unmapped severity in cell_map: {names}")

    if cell_map is not None:
        cells_needed = set(cell_map.values())
    else:
        assert policy is not None
        cells_needed = {policy}
    if engine is None and (WardlineCellPolicy.SURFACE_OVERRIDE in cells_needed
                           or WardlineCellPolicy.SURFACE_ONLY in cells_needed):
        raise ValueError("surface cell(s) require an engine")
    if signoff is None and WardlineCellPolicy.BLOCK_ESCALATE in cells_needed:
        raise ValueError("block_escalate cell requires a signoff gate")
    surface_cells = {
        WardlineCellPolicy.SURFACE_OVERRIDE,
        WardlineCellPolicy.SURFACE_ONLY,
    }
    if WardlineCellPolicy.BLOCK_ESCALATE in cells_needed and cells_needed & surface_cells:
        raise ValueError(
            "split cross-store Wardline batches by cell before routing"
        )

    def cell_for(f: WardlineFinding) -> WardlineCellPolicy:
        if cell_map is not None:
            return cell_map[f.severity]
        assert policy is not None
        return policy

    results: list[dict[str, Any]] = []
    for f in findings:
        cell = cell_for(f)
        entity_key, clarion_ext = resolve(f.qualname)
        rationale = f"[wardline {f.rule_id}] {f.message}"
        wardline_ext = {
            "fingerprint": f.fingerprint,
            "tiers": dict(f.properties),
            "severity": f.severity.value,
            **dict(batch_provenance or {}),
        }
        if cell is WardlineCellPolicy.BLOCK_ESCALATE:
            if signoff is None:
                raise ValueError("block_escalate cell requires a signoff gate")
            ext = {**clarion_ext, "wardline": wardline_ext}
            signoff_result = signoff.request(policy=f.rule_id, entity_key=entity_key,
                                             rationale=rationale, agent_id=agent_id,
                                             extensions=ext)
            results.append({"mode": cell.value, "fingerprint": f.fingerprint,
                            "seq": signoff_result.seq, "cleared": signoff_result.cleared})
        elif cell is WardlineCellPolicy.SURFACE_OVERRIDE:
            if engine is None:
                raise ValueError("surface_override cell requires an engine")
            # Merge the clarion lineage ext (REQ-L-01) alongside the wardline ext
            # so a wardline-routed override carries the same lineage snapshot a
            # /overrides override would.
            ext = {**clarion_ext, "wardline": wardline_ext}
            override_result = engine.submit_override(policy=f.rule_id, entity_key=entity_key,
                                                     rationale=rationale, agent_id=agent_id,
                                                     extensions=ext)
            results.append({"mode": cell.value, "fingerprint": f.fingerprint,
                            "seq": override_result.seq, "accepted": override_result.accepted})
        elif cell is WardlineCellPolicy.SURFACE_ONLY:
            # recorded, non-gating
            if engine is None:
                raise ValueError("surface_only cell requires an engine")
            ext = {**clarion_ext, "wardline": wardline_ext}
            seq = engine.record_event({"kind": "wardline_surfaced", "policy": f.rule_id,
                                       "entity_key": entity_key.to_dict(),
                                       "rationale": rationale, "agent_id": agent_id,
                                       "extensions": ext})
            results.append({"mode": cell.value, "fingerprint": f.fingerprint,
                            "seq": seq, "surfaced": True})
        else:
            raise NotImplementedError(f"unhandled WardlineCellPolicy: {cell!r}")
    return results
