"""Resolve a locator to an SEI-keyed (or honestly-degraded) EntityKey.

This is the WP-5.1 swap point: governance records key on SEI when Clarion proves
a stable, alive identity, and on the locator (``identity_stable=False``) in every
other case — capability absent, no client, locator not alive, or transport error.
The resolver never parses an SEI and never guesses. It also captures the REQ-L-01
append-only lineage snapshot at the moment of the governance decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from legis.canonical import content_hash
from legis.identity.clarion_client import ClarionIdentity
from legis.identity.entity_key import EntityKey


@dataclass(frozen=True)
class IdentityResolution:
    entity_key: EntityKey
    alive: bool | None          # identity axis; None when no capability/decision
    content_hash: str | None    # content axis; None when unavailable
    lineage_snapshot: dict[str, Any] | None  # {"length": N, "hash": ...} or None
    identity_resolution_status: str
    lineage_snapshot_status: str


class IdentityResolver:
    def __init__(self, client: ClarionIdentity | None) -> None:
        self._client = client
        self._capable: bool | None = None  # probe once per instance

    @property
    def client(self) -> ClarionIdentity | None:
        """The underlying read client (None when legis runs standalone)."""
        return self._client

    def _capability(self) -> bool:
        if self._client is None:
            return False
        if self._capable is None:
            try:
                self._capable = bool(self._client.capability())
            except Exception:
                return False  # honest transient degrade — retry on next resolve
        return self._capable

    def _snapshot(self, sei: str) -> tuple[dict[str, Any] | None, str]:
        try:
            lineage = self._client.lineage(sei)  # type: ignore[union-attr]
        except Exception:
            return None, "unavailable"
        return {"length": len(lineage), "hash": content_hash(lineage)}, "verified"

    def resolve(self, locator: str) -> IdentityResolution:
        degraded = IdentityResolution(
            EntityKey.from_locator(locator),
            None,
            None,
            None,
            "unavailable",
            "not_applicable",
        )
        if not self._capability():
            return degraded
        try:
            res = self._client.resolve_locator(locator)  # type: ignore[union-attr]
        except Exception:
            return degraded
        if not isinstance(res, dict):
            return degraded
        if not res.get("alive"):
            # Capability present but this locator has no alive SEI — honest: no
            # stable identity, and we know it (alive recorded False, not None).
            return IdentityResolution(
                EntityKey.from_locator(locator),
                False,
                None,
                None,
                "not_alive",
                "not_applicable",
            )
        sei = res.get("sei")
        if not isinstance(sei, str) or not sei:
            return degraded
        snapshot, snapshot_status = self._snapshot(sei)
        return IdentityResolution(
            EntityKey.from_sei(sei),
            True,
            res.get("content_hash"),
            snapshot,
            "resolved",
            snapshot_status,
        )
