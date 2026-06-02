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
* **surface+only** records a non-gating ``wardline_surfaced`` governance event
  via ``EnforcementEngine.record_event``; no judge, no sign-off gate. Carries
  ``entity_key`` + ``clarion`` and ``wardline`` extensions so it is
  orphan-detectable consistently with a ``surface_override`` record.
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
    SURFACE_ONLY = "surface_only"


def route_findings(
    findings: list[WardlineFinding],
    *,
    policy: WardlineCellPolicy,
    agent_id: str,
    resolve: Callable[[str | None], tuple[EntityKey, dict[str, Any]]],
    engine: EnforcementEngine | None = None,
    signoff: SignoffGate | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for f in findings:
        entity_key, clarion_ext = resolve(f.qualname)
        rationale = f"[wardline {f.rule_id}] {f.message}"
        wardline_ext = {"fingerprint": f.fingerprint, "tiers": dict(f.properties),
                        "severity": f.severity.value}
        if policy is WardlineCellPolicy.BLOCK_ESCALATE:
            if signoff is None:
                raise ValueError("block_escalate cell requires a signoff gate")
            res = signoff.request(policy=f.rule_id, entity_key=entity_key,
                                  rationale=rationale, agent_id=agent_id)
            results.append({"mode": policy.value, "fingerprint": f.fingerprint,
                            "seq": res.seq, "cleared": res.cleared})
        elif policy is WardlineCellPolicy.SURFACE_OVERRIDE:
            if engine is None:
                raise ValueError("surface_override cell requires an engine")
            # Merge the clarion lineage ext (REQ-L-01) alongside the wardline ext
            # so a wardline-routed override carries the same lineage snapshot a
            # /overrides override would.
            ext = {**clarion_ext, "wardline": wardline_ext}
            res = engine.submit_override(policy=f.rule_id, entity_key=entity_key,
                                         rationale=rationale, agent_id=agent_id,
                                         extensions=ext)
            results.append({"mode": policy.value, "fingerprint": f.fingerprint,
                            "seq": res.seq, "accepted": res.accepted})
        elif policy is WardlineCellPolicy.SURFACE_ONLY:
            # recorded, non-gating
            if engine is None:
                raise ValueError("surface_only cell requires an engine")
            ext = {**clarion_ext, "wardline": wardline_ext}
            seq = engine.record_event({"kind": "wardline_surfaced", "policy": f.rule_id,
                                       "entity_key": entity_key.to_dict(),
                                       "rationale": rationale, "agent_id": agent_id,
                                       "extensions": ext})
            results.append({"mode": policy.value, "fingerprint": f.fingerprint,
                            "seq": seq, "surfaced": True})
        else:
            raise NotImplementedError(f"unhandled WardlineCellPolicy: {policy!r}")
    return results
