"""Route Wardline findings into the configured 2x2 cell — legis governs.

One judge, not two: Wardline produced the finding; legis decides who answers.
The cell is configured for the whole scan (surface+override or block+escalate).
The finding's ``rule_id`` is the policy; its ``qualname`` is the entity to key
on (resolved to an SEI via the same Sprint-5 resolver when available); its
``message`` seeds the rationale.

* **surface+override** records an override carrying the finding's
  ``fingerprint``, trust ``tiers`` (the one shared vocabulary, verbatim from
  ``properties``), and ``severity`` in the record's ``extensions``.
* **block+escalate** opens a sign-off request carrying ``rule_id`` (policy),
  the resolved entity, and the seeded rationale. Carrying the Wardline tiers
  onto the sign-off record is deferred: ``SignoffGate.request`` has no
  ``extensions`` field yet.
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
    if policy is WardlineCellPolicy.SURFACE_OVERRIDE and engine is None:
        raise ValueError("surface_override cell requires an engine")
    if policy is WardlineCellPolicy.BLOCK_ESCALATE and signoff is None:
        raise ValueError("block_escalate cell requires a signoff gate")

    results: list[dict[str, Any]] = []
    for f in findings:
        entity_key = resolve(f.qualname)
        rationale = f"[wardline {f.rule_id}] {f.message}"
        if policy is WardlineCellPolicy.SURFACE_OVERRIDE:
            assert engine is not None  # guarded above
            ext = {"wardline": {"fingerprint": f.fingerprint,
                                "tiers": dict(f.properties),
                                "severity": f.severity.value}}
            override_res = engine.submit_override(
                policy=f.rule_id, entity_key=entity_key,
                rationale=rationale, agent_id=agent_id, extensions=ext,
            )
            results.append({"mode": policy.value, "fingerprint": f.fingerprint,
                            "seq": override_res.seq,
                            "accepted": override_res.accepted})
        else:
            assert signoff is not None  # guarded above
            escalate_res = signoff.request(
                policy=f.rule_id, entity_key=entity_key,
                rationale=rationale, agent_id=agent_id,
            )
            results.append({"mode": policy.value, "fingerprint": f.fingerprint,
                            "seq": escalate_res.seq,
                            "cleared": escalate_res.cleared})
    return results
