"""Ingest a Wardline scan result — select the gate population, carry the tiers.

legis does not call Wardline (Wardline has no HTTP); the agent hands legis the
MCP scan response. legis never re-analyzes — it reads findings and governs. The
trust tiers are Wardline's, carried verbatim as the one suite vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

# The shared trust vocabulary (Wardline taints.py) — carried, never re-derived.
TRUST_TIERS: frozenset[str] = frozenset({
    "INTEGRAL", "ASSURED", "GUARDED", "EXTERNAL_RAW",
    "UNKNOWN_RAW", "UNKNOWN_GUARDED", "UNKNOWN_ASSURED", "MIXED_RAW",
})


class WardlineSeverity(Enum):
    CRITICAL = ("CRITICAL", 4)
    ERROR = ("ERROR", 3)
    WARN = ("WARN", 2)
    INFO = ("INFO", 1)
    NONE = ("NONE", 0)

    def __init__(self, value: str, rank: int) -> None:
        self._value_ = value
        self.rank = rank


@dataclass(frozen=True)
class WardlineFinding:
    rule_id: str
    message: str
    severity: WardlineSeverity
    kind: str
    fingerprint: str
    qualname: str | None
    properties: Mapping[str, Any]
    suppressed: str

    @classmethod
    def from_wire(cls, d: Mapping[str, Any]) -> "WardlineFinding":
        return cls(
            rule_id=d["rule_id"],
            message=d["message"],
            severity=WardlineSeverity[d["severity"]],
            kind=d["kind"],
            fingerprint=d["fingerprint"],
            qualname=d.get("qualname"),
            properties=dict(d.get("properties", {})),
            suppressed=d.get("suppressed", "active"),
        )


def active_defects(scan: Mapping[str, Any]) -> list[WardlineFinding]:
    """The gate population: active (non-suppressed) DEFECT findings."""
    out: list[WardlineFinding] = []
    for raw in scan.get("findings", []):
        f = WardlineFinding.from_wire(raw)
        if f.kind == "defect" and f.suppressed == "active":
            out.append(f)
    return out
