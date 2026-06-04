"""Append-only pre-SEI governance backfill.

Legis governance history is hash-chained and append-only, so this migration
does not rewrite old locator-keyed rows. It appends explicit backfill events
that reference the original sequence and either carry the resolved SEI key or
record an honest unresolved locator key.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from legis.canonical import content_hash
from legis.clock import Clock
from legis.identity.clarion_client import ClarionIdentity
from legis.identity.entity_key import EntityKey
from legis.store.audit_store import AuditRecord, AuditStore

SEI_PREFIX = "clarion:eid:"
BACKFILL_EVENTS = {"SEI_BACKFILL", "SEI_BACKFILL_UNRESOLVED"}


class SeiBackfillError(RuntimeError):
    """The backfill cannot safely run."""


@dataclass(frozen=True)
class SeiBackfillReport:
    dry_run: bool
    scanned: int
    eligible: int
    resolved: int
    unresolved: int
    invalid: int
    already_stable: int
    already_backfilled: int
    appended: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_pre_sei_backfill(
    store: AuditStore,
    client: ClarionIdentity,
    clock: Clock,
    *,
    dry_run: bool = True,
    actor: str = "legis-sei-backfill",
) -> SeiBackfillReport:
    """Resolve legacy locator-keyed records and append backfill events.

    Eligible records are audit payloads with an ``entity_key`` whose
    ``identity_stable`` flag is false and whose value is not already
    SEI-shaped. Existing backfill events make the run idempotent and resumable.
    """
    if not store.verify_integrity():
        raise SeiBackfillError("audit integrity failure: database hash chain verification failed")

    records = store.read_all()
    backfilled = _backfilled_original_sequences(records)
    eligible: list[AuditRecord] = []
    already_stable = 0
    already_backfilled = 0

    for rec in records:
        if rec.payload.get("event") in BACKFILL_EVENTS:
            continue
        entity_key = _entity_key(rec.payload)
        if entity_key is None:
            continue
        if rec.seq in backfilled:
            already_backfilled += 1
            continue
        if entity_key.identity_stable or entity_key.value.startswith(SEI_PREFIX):
            already_stable += 1
            continue
        eligible.append(rec)

    locators = sorted({EntityKey.from_dict(rec.payload["entity_key"]).value for rec in eligible})
    batch = client.resolve_batch(locators) if locators else {}
    resolved_map = _resolved_map(batch)
    not_found = _string_set(batch.get("not_found", []))
    invalid = _string_set(batch.get("invalid", []))

    resolved_count = 0
    unresolved_count = 0
    invalid_count = 0
    appended = 0

    for rec in eligible:
        locator = EntityKey.from_dict(rec.payload["entity_key"]).value
        if locator in resolved_map and _is_alive_resolution(resolved_map[locator]):
            resolved_count += 1
            if not dry_run:
                store.append(
                    _resolved_event(
                        rec,
                        resolved_map[locator],
                        client=client,
                        clock=clock,
                        actor=actor,
                    )
                )
                appended += 1
        elif locator in invalid:
            invalid_count += 1
            if not dry_run:
                store.append(_unresolved_event(rec, clock=clock, actor=actor, reason="invalid"))
                appended += 1
        elif locator in not_found:
            unresolved_count += 1
            if not dry_run:
                store.append(
                    _unresolved_event(rec, clock=clock, actor=actor, reason="not_alive")
                )
                appended += 1
        else:
            # Missing from every channel is treated as unresolved rather than
            # silently skipped; Clarion's contract should include every input.
            unresolved_count += 1
            if not dry_run:
                store.append(
                    _unresolved_event(rec, clock=clock, actor=actor, reason="not_alive")
                )
                appended += 1

    return SeiBackfillReport(
        dry_run=dry_run,
        scanned=len(records),
        eligible=len(eligible),
        resolved=resolved_count,
        unresolved=unresolved_count,
        invalid=invalid_count,
        already_stable=already_stable,
        already_backfilled=already_backfilled,
        appended=appended,
    )


def _entity_key(payload: dict[str, Any]) -> EntityKey | None:
    raw = payload.get("entity_key")
    if not isinstance(raw, dict):
        return None
    value = raw.get("value")
    if not isinstance(value, str) or not value:
        return None
    return EntityKey.from_dict(raw)


def _backfilled_original_sequences(records: list[AuditRecord]) -> set[int]:
    seqs: set[int] = set()
    for rec in records:
        if rec.payload.get("event") not in BACKFILL_EVENTS:
            continue
        original_seq = rec.payload.get("original_seq")
        if isinstance(original_seq, int):
            seqs.add(original_seq)
    return seqs


def _resolved_map(batch: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = batch.get("resolved", {})
    if not isinstance(raw, dict):
        raise SeiBackfillError("Clarion batch response field 'resolved' must be an object")
    result: dict[str, dict[str, Any]] = {}
    for locator, item in raw.items():
        if isinstance(locator, str) and isinstance(item, dict):
            result[locator] = item
    return result


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str)}


def _is_alive_resolution(item: dict[str, Any]) -> bool:
    return item.get("alive") is True and isinstance(item.get("sei"), str) and bool(item["sei"])


def _resolved_event(
    rec: AuditRecord,
    resolution: dict[str, Any],
    *,
    client: ClarionIdentity,
    clock: Clock,
    actor: str,
) -> dict[str, Any]:
    locator_key = EntityKey.from_dict(rec.payload["entity_key"])
    sei = str(resolution["sei"])
    lineage_snapshot, lineage_status = _lineage_snapshot(client, sei)
    return {
        "event": "SEI_BACKFILL",
        "original_seq": rec.seq,
        "original_content_hash": rec.content_hash,
        "entity_key": EntityKey.from_sei(sei).to_dict(),
        "identity_stable": True,
        "agent_id": actor,
        "recorded_at": clock.now_iso(),
        "extensions": {
            "clarion": {
                "alive": True,
                "content_hash": resolution.get("content_hash"),
                "lineage_snapshot": lineage_snapshot,
                "identity_resolution_status": "resolved",
                "lineage_snapshot_status": lineage_status,
            },
            "backfill": {
                "source": "pre_sei_locator",
                "original_seq": rec.seq,
                "original_entity_key": locator_key.to_dict(),
            },
        },
    }


def _unresolved_event(
    rec: AuditRecord,
    *,
    clock: Clock,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    locator_key = EntityKey.from_dict(rec.payload["entity_key"])
    status = "invalid" if reason == "invalid" else "not_alive"
    return {
        "event": "SEI_BACKFILL_UNRESOLVED",
        "original_seq": rec.seq,
        "original_content_hash": rec.content_hash,
        "entity_key": locator_key.to_dict(),
        "identity_stable": False,
        "agent_id": actor,
        "recorded_at": clock.now_iso(),
        "extensions": {
            "clarion": {
                "alive": False,
                "identity_resolution_status": status,
                "lineage_snapshot_status": "not_applicable",
            },
            "backfill": {
                "source": "pre_sei_locator",
                "original_seq": rec.seq,
                "original_entity_key": locator_key.to_dict(),
            },
        },
    }


def _lineage_snapshot(
    client: ClarionIdentity, sei: str
) -> tuple[dict[str, Any] | None, str]:
    try:
        lineage = client.lineage(sei)
    except Exception:
        return None, "unavailable"
    return {"length": len(lineage), "hash": content_hash(lineage)}, "verified"
