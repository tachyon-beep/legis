"""Route Wardline findings into the configured 2x2 cell — legis governs.

One judge, not two: Wardline produced the finding; legis decides who answers.
The cell is configured for the whole scan (surface+override or block+escalate).
The finding's ``rule_id`` is the policy; its ``qualname`` is the entity to key
on (resolved to an SEI via the same Sprint-5 resolver when available); its
``message`` seeds the rationale. The trust tiers in ``properties`` are carried
verbatim onto the record — the one shared vocabulary.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Any

from legis.enforcement.engine import EnforcementEngine
from legis.enforcement.signoff import SignoffGate
from legis.identity.entity_key import EntityKey
from legis.wardline.ingest import WardlineFinding


class WardlineCellPolicy(str, Enum):
    SURFACE_OVERRIDE = "surface_override"
    BLOCK_ESCALATE = "block_escalate"


def route_findings(
    findings: list[WardlineFinding],
    *,
    policy: WardlineCellPolicy,
    agent_id: str,
    resolve: Callable[[str | None], EntityKey],
    engine: EnforcementEngine | None = None,
    signoff: SignoffGate | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for f in findings:
        entity_key = resolve(f.qualname)
        rationale = f"[wardline {f.rule_id}] {f.message}"
        ext = {"wardline": {"fingerprint": f.fingerprint,
                            "tiers": dict(f.properties), "severity": f.severity.value}}
        if policy is WardlineCellPolicy.SURFACE_OVERRIDE:
            if engine is None:
                raise ValueError("surface_override cell requires an engine")
            res = engine.submit_override(
                policy=f.rule_id, entity_key=entity_key,
                rationale=rationale, agent_id=agent_id, extensions=ext,
            )
            results.append({"mode": "surface_override", "fingerprint": f.fingerprint,
                            "seq": res.seq, "accepted": res.accepted})
        else:
            if signoff is None:
                raise ValueError("block_escalate cell requires a signoff gate")
            res = signoff.request(
                policy=f.rule_id, entity_key=entity_key,
                rationale=rationale, agent_id=agent_id,
            )
            results.append({"mode": "block_escalate", "fingerprint": f.fingerprint,
                            "seq": res.seq, "cleared": res.cleared})
    return results
