from legis.canonical import content_hash
from legis.governance.gaps import (
    LineageDivergence,
    LineageUnavailable,
    find_lineage_integrity,
    find_lineage_divergence,
    find_orphan_gaps,
)
from legis.store.audit_store import AuditStore


def _store(tmp_path, *payloads):
    s = AuditStore(f"sqlite:///{tmp_path / 'g.db'}")
    for p in payloads:
        s.append(p)
    return s


def _rec(sei, *, identity_stable=True, snapshot=None):
    ext = {"clarion": {"lineage_snapshot": snapshot}} if snapshot else {}
    return {"policy": "p", "entity_key": {"value": sei, "identity_stable": identity_stable},
            "rationale": "r", "agent_id": "a", "recorded_at": "t",
            "identity_stable": identity_stable, "extensions": ext}


class FakeClient:
    def __init__(self, sei_status, lineages=None):
        self._status = sei_status          # {sei: {"alive": bool, "lineage": [...]}}
        self._lineages = lineages or {}    # {sei: [events]}

    def resolve_sei(self, sei):
        return {"sei": sei, **self._status.get(sei, {"alive": True})}

    def lineage(self, sei):
        return self._lineages.get(sei, [])


class BrokenLineageClient(FakeClient):
    def lineage(self, sei):
        raise RuntimeError("clarion down")


def test_orphaned_sei_surfaces_a_gap(tmp_path):
    store = _store(tmp_path, _rec("clarion:eid:alive"), _rec("clarion:eid:dead"))
    client = FakeClient({
        "clarion:eid:alive": {"alive": True},
        "clarion:eid:dead": {"alive": False, "lineage": [{"event": "orphaned"}]},
    })
    gaps = find_orphan_gaps(store.read_all(), client)
    assert [g.sei for g in gaps] == ["clarion:eid:dead"]
    assert gaps[0].lineage == [{"event": "orphaned"}]


def test_locator_keyed_records_are_not_probed(tmp_path):
    store = _store(tmp_path, _rec("python:function:x", identity_stable=False))
    gaps = find_orphan_gaps(store.read_all(), FakeClient({}))
    assert gaps == []   # nothing stable to probe → no gap, no crash


def test_appended_lineage_is_not_divergence(tmp_path):
    born = [{"event": "born"}]
    snap = {"length": 1, "hash": content_hash(born)}
    store = _store(tmp_path, _rec("clarion:eid:s", snapshot=snap))
    grown = born + [{"event": "locator_changed"}]   # legitimate append
    div = find_lineage_divergence(store.read_all(), FakeClient({}, {"clarion:eid:s": grown}))
    assert div == []


def test_truncated_or_mutated_prefix_is_divergence(tmp_path):
    born = [{"event": "born"}, {"event": "moved"}]
    snap = {"length": 2, "hash": content_hash(born)}
    store = _store(tmp_path, _rec("clarion:eid:s", snapshot=snap))
    tampered = [{"event": "born"}]   # the 'moved' event vanished — prefix broken
    div = find_lineage_divergence(store.read_all(), FakeClient({}, {"clarion:eid:s": tampered}))
    assert div == [LineageDivergence(sei="clarion:eid:s", recorded_length=2, current_length=1)]


def test_lineage_integrity_reports_unavailable_fetches(tmp_path):
    born = [{"event": "born"}]
    snap = {"length": 1, "hash": content_hash(born)}
    store = _store(tmp_path, _rec("clarion:eid:s", snapshot=snap))
    integrity = find_lineage_integrity(store.read_all(), BrokenLineageClient({}))
    assert integrity.divergences == []
    assert integrity.unavailable == [
        LineageUnavailable(sei="clarion:eid:s", reason="lineage_fetch_failed")
    ]


def test_lineage_integrity_reports_missing_snapshot_as_unverified(tmp_path):
    store = _store(tmp_path, _rec("clarion:eid:s"))
    integrity = find_lineage_integrity(store.read_all(), FakeClient({}))
    assert integrity.divergences == []
    assert integrity.unavailable == [
        LineageUnavailable(sei="clarion:eid:s", reason="missing_snapshot")
    ]
