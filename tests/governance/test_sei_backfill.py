from legis.clock import FixedClock
from legis.identity.entity_key import EntityKey
from legis.store.audit_store import AuditStore


class FakeBatchClarion:
    def __init__(self):
        self.calls = []

    def capability(self):
        return True

    def resolve_locator(self, locator):
        raise AssertionError("backfill must use resolve_batch")

    def resolve_sei(self, sei):
        raise AssertionError

    def lineage(self, sei):
        return [{"event": "born", "new_locator": "python:function:m.f"}]

    def resolve_batch(self, locators):
        self.calls.append(list(locators))
        return {
            "resolved": {
                "python:function:m.f": {
                    "sei": "clarion:eid:abc",
                    "current_locator": "python:function:m.f",
                    "content_hash": "hash-abc",
                    "alive": True,
                }
            },
            "invalid": ["malformed"],
            "not_found": ["python:function:gone"],
        }


def _store(tmp_path):
    return AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")


def _legacy_payload(locator):
    return {
        "policy": "p",
        "entity_key": EntityKey.from_locator(locator).to_dict(),
        "rationale": "r",
        "agent_id": "agent-1",
        "recorded_at": "2026-06-01T00:00:00+00:00",
        "identity_stable": False,
        "extensions": {},
    }


def test_pre_sei_backfill_dry_run_uses_batch_and_does_not_append(tmp_path):
    from legis.governance.sei_backfill import run_pre_sei_backfill

    store = _store(tmp_path)
    store.append(_legacy_payload("python:function:m.f"))
    store.append(_legacy_payload("python:function:gone"))
    client = FakeBatchClarion()

    report = run_pre_sei_backfill(
        store,
        client,
        FixedClock("2026-06-04T12:00:00+00:00"),
        dry_run=True,
    )

    assert client.calls == [["python:function:gone", "python:function:m.f"]]
    assert report.to_dict() == {
        "dry_run": True,
        "scanned": 2,
        "eligible": 2,
        "resolved": 1,
        "unresolved": 1,
        "invalid": 0,
        "already_stable": 0,
        "already_backfilled": 0,
        "appended": 0,
    }
    assert len(store.read_all()) == 2


def test_pre_sei_backfill_execute_appends_resolved_and_honest_degrade_events(tmp_path):
    from legis.governance.sei_backfill import run_pre_sei_backfill

    store = _store(tmp_path)
    alive_seq = store.append(_legacy_payload("python:function:m.f"))
    dead_seq = store.append(_legacy_payload("python:function:gone"))
    store.append(
        {
            "policy": "already",
            "entity_key": EntityKey.from_sei("clarion:eid:stable").to_dict(),
            "identity_stable": True,
            "extensions": {},
        }
    )

    report = run_pre_sei_backfill(
        store,
        FakeBatchClarion(),
        FixedClock("2026-06-04T12:00:00+00:00"),
        dry_run=False,
        actor="operator-1",
    )

    assert report.appended == 2
    records = store.read_all()
    resolved = records[3].payload
    unresolved = records[4].payload

    assert resolved["event"] == "SEI_BACKFILL"
    assert resolved["original_seq"] == alive_seq
    assert resolved["agent_id"] == "operator-1"
    assert resolved["recorded_at"] == "2026-06-04T12:00:00+00:00"
    assert resolved["entity_key"] == {
        "value": "clarion:eid:abc",
        "identity_stable": True,
    }
    assert resolved["identity_stable"] is True
    assert resolved["extensions"]["clarion"]["alive"] is True
    assert resolved["extensions"]["clarion"]["content_hash"] == "hash-abc"
    assert resolved["extensions"]["clarion"]["lineage_snapshot_status"] == "verified"
    assert resolved["extensions"]["backfill"]["original_entity_key"] == {
        "value": "python:function:m.f",
        "identity_stable": False,
    }

    assert unresolved["event"] == "SEI_BACKFILL_UNRESOLVED"
    assert unresolved["original_seq"] == dead_seq
    assert unresolved["entity_key"] == {
        "value": "python:function:gone",
        "identity_stable": False,
    }
    assert unresolved["identity_stable"] is False
    assert unresolved["extensions"]["clarion"] == {
        "alive": False,
        "identity_resolution_status": "not_alive",
        "lineage_snapshot_status": "not_applicable",
    }


def test_pre_sei_backfill_rerun_skips_originals_already_backfilled(tmp_path):
    from legis.governance.sei_backfill import run_pre_sei_backfill

    store = _store(tmp_path)
    store.append(_legacy_payload("python:function:m.f"))
    clock = FixedClock("2026-06-04T12:00:00+00:00")

    first = run_pre_sei_backfill(store, FakeBatchClarion(), clock, dry_run=False)
    second_client = FakeBatchClarion()
    second = run_pre_sei_backfill(store, second_client, clock, dry_run=False)

    assert first.appended == 1
    assert second.to_dict() == {
        "dry_run": False,
        "scanned": 2,
        "eligible": 0,
        "resolved": 0,
        "unresolved": 0,
        "invalid": 0,
        "already_stable": 0,
        "already_backfilled": 1,
        "appended": 0,
    }
    assert second_client.calls == []
    assert len(store.read_all()) == 2
