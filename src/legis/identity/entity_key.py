"""Opaque, SEI-ready cross-tool entity key.

Holds a **locator** today and a **SEI** later. Consumers MUST NOT parse
``value`` (the same opacity discipline the SEI standard mandates, §1/§2).
Switching a key from a locator to an SEI is a *value change with no schema
change* — that is the SEI-shape-independence guarantee (SEI spec §0.3) that
keeps Sprint 5's SEI adoption a swap rather than a migration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EntityKey:
    value: str
    identity_stable: bool

    @classmethod
    def from_locator(cls, locator: str) -> "EntityKey":
        return cls(value=locator, identity_stable=False)

    @classmethod
    def from_sei(cls, sei: str) -> "EntityKey":
        return cls(value=sei, identity_stable=True)

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "identity_stable": self.identity_stable}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EntityKey":
        value = d.get("value")
        identity_stable = d.get("identity_stable")
        if not isinstance(value, str) or not value:
            raise ValueError("entity key value must be a non-empty string")
        if not isinstance(identity_stable, bool):
            raise ValueError("entity key identity_stable must be a boolean")
        return cls(value=value, identity_stable=identity_stable)
