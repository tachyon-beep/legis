"""Resolve a locator to an SEI-keyed (or honestly-degraded) EntityKey.

This is the WP-5.1 swap point: governance records key on SEI when Loomweave proves
a stable, alive identity, and on the locator (``identity_stable=False``) in every
other case — capability absent, no client, locator not alive, or transport error.
The resolver never parses an SEI and never guesses. It also captures the REQ-L-01
append-only lineage snapshot at the moment of the governance decision.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from legis.canonical import content_hash
from legis.identity.loomweave_client import LoomweaveIdentity
from legis.identity.entity_key import EntityKey

# A long-lived resolver re-probes the Loomweave sei capability at most once per
# this window. Without it a positive latch is permanent: a Loomweave that loses
# the capability mid-life would be trusted forever (Q-L6).
_DEFAULT_CAPABILITY_TTL_SECONDS = 300.0


@dataclass(frozen=True)
class IdentityResolution:
    entity_key: EntityKey
    alive: bool | None          # identity axis; None when no capability/decision
    content_hash: str | None    # content axis; None when unavailable
    lineage_snapshot: dict[str, Any] | None  # {"length": N, "hash": ...} or None
    identity_resolution_status: str
    lineage_snapshot_status: str


class IdentityResolver:
    def __init__(
        self,
        client: LoomweaveIdentity | None,
        *,
        capability_ttl: float = _DEFAULT_CAPABILITY_TTL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._capable: bool | None = None  # cached probe result; None = unknown
        self._capable_checked_at: float | None = None
        self._capability_ttl = capability_ttl
        self._monotonic = monotonic

    @property
    def client(self) -> LoomweaveIdentity | None:
        """The underlying read client (None when legis runs standalone)."""
        return self._client

    def _capability(self) -> bool:
        if self._client is None:
            return False
        now = self._monotonic()
        checked_at = self._capable_checked_at
        # The latch (positive OR negative) is fresh only while within the TTL.
        # The original code latched the first result for the resolver's whole
        # life, so a capability lost (or gained) upstream was never noticed by a
        # long-lived resolver (Q-L6).
        fresh = (
            self._capable is not None
            and checked_at is not None
            and now - checked_at < self._capability_ttl
        )
        if not fresh:
            try:
                self._capable = bool(self._client.capability())
            except Exception:
                # Honest transient degrade — clear the latch so the next resolve
                # retries rather than trusting a stale value.
                self._capable = None
                self._capable_checked_at = None
                return False
            self._capable_checked_at = now
        return self._capable if self._capable is not None else False

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
        # content_hash is carried verbatim into the governance record; trust only
        # a string. A non-string from a buggy/hostile Loomweave degrades to None
        # rather than polluting the typed content axis (Q-L6).
        raw_content_hash = res.get("content_hash")
        content_hash_value = raw_content_hash if isinstance(raw_content_hash, str) else None
        return IdentityResolution(
            EntityKey.from_sei(sei),
            True,
            content_hash_value,
            snapshot,
            "resolved",
            snapshot_status,
        )
