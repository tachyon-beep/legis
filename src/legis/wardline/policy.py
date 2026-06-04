"""Map a Wardline finding's severity to a configured 2x2 cell."""

from __future__ import annotations

from legis.wardline.governor import WardlineCellPolicy
from legis.wardline.ingest import WardlineFinding, WardlineSeverity


def resolve_cell(
    finding: WardlineFinding,
    *,
    fail_on: WardlineSeverity,
    gate_cell: WardlineCellPolicy,
) -> WardlineCellPolicy:
    if finding.severity.rank >= fail_on.rank:
        return gate_cell
    return WardlineCellPolicy.SURFACE_ONLY
