"""Core override record (the chill-cell recordable override, Sprint 2 / WP-2.1).

Designed so that the judge fields (Sprint 2 / coached cell) and the
HMAC/binding fields (Sprint 3 / protected cell) attach via ``extensions`` —
keeping the core schema stable across the whole 2x2. The record serializes to a
flat-ish dict and hands it to the record-agnostic :class:`AuditStore`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from legis.identity.entity_key import EntityKey


@dataclass(frozen=True)
class OverrideRecord:
    policy: str
    entity_key: EntityKey
    rationale: str
    agent_id: str
    recorded_at: str
    extensions: dict[str, Any] = field(default_factory=dict)

    @property
    def identity_stable(self) -> bool:
        return self.entity_key.identity_stable

    def to_payload(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "entity_key": self.entity_key.to_dict(),
            "rationale": self.rationale,
            "agent_id": self.agent_id,
            "recorded_at": self.recorded_at,
            "identity_stable": self.identity_stable,
            "extensions": dict(self.extensions),
        }
