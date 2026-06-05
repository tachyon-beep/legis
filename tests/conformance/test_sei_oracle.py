"""Weft SEI §8 conformance oracle — legis as consumer.

Six shared scenarios (identity round-trip + opacity, rename, move, ambiguous,
delete, capability-absent). A subsystem is SEI-conformant only when all six pass.
The ``FakeLoomweave`` returns Loomweave's documented response shapes — transcribed
from the spec's ``sei-conformance-oracle.json`` scenario definitions (whose
``expect`` blocks are symbolic, e.g. ``"<opaque>"``, not replayable bodies), not
loaded from the sibling repo. The assertions are legis's required *consumer*
responses. This suite proves consumer behaviour against shapes; a live-Loomweave
integration run is a separate, environment-gated check.
"""
from legis.governance.gaps import find_orphan_gaps
from legis.identity.resolver import IdentityResolver
from legis.store.audit_store import AuditStore


class FakeLoomweave:
    def __init__(self, *, capable=True, resolve=None, sei=None, lineage=None):
        self._capable = capable
        self._resolve = resolve or {}      # {locator: response}
        self._sei = sei or {}              # {sei: response}
        self._lineage = lineage or {}      # {sei: [events]}

    def capability(self):
        return self._capable

    def resolve_locator(self, locator):
        return self._resolve.get(locator, {"alive": False})

    def resolve_sei(self, sei):
        return self._sei.get(sei, {"sei": sei, "alive": False, "lineage": []})

    def lineage(self, sei):
        return self._lineage.get(sei, [])


def test_identity_round_trip_and_opacity():
    loc = "python:function:m.f"
    client = FakeLoomweave(resolve={loc: {"sei": "loomweave:eid:rt", "current_locator": loc,
                                        "content_hash": "h", "alive": True}})
    res = IdentityResolver(client).resolve(loc)
    assert res.entity_key.identity_stable is True
    assert res.entity_key.value.startswith("loomweave:eid:")   # opaque, carries prefix
    assert res.entity_key.value != loc                       # not the locator
    assert res.alive is True and res.content_hash == "h"


def _attest(tmp_path, sei):
    store = AuditStore(f"sqlite:///{tmp_path / 'g.db'}")
    store.append({"entity_key": {"value": sei, "identity_stable": True},
                  "identity_stable": True, "extensions": {}})
    return store


def test_rename_carries_sei_record_survives(tmp_path):
    # The record was keyed on the SEI; after rename the SEI still resolves alive
    # at the NEW locator. legis's consumer behaviour: NOT orphaned — carried.
    sei = "loomweave:eid:ren"
    store = _attest(tmp_path, sei)
    client = FakeLoomweave(sei={sei: {"sei": sei, "current_locator": "python:function:new.f",
                                    "content_hash": "h", "alive": True}})
    assert find_orphan_gaps(store.read_all(), client) == []   # carried, not orphaned


def test_move_carries_sei(tmp_path):
    sei = "loomweave:eid:mov"
    store = _attest(tmp_path, sei)
    client = FakeLoomweave(sei={sei: {"sei": sei, "current_locator": "python:function:b.f",
                                    "content_hash": "h", "alive": True}})
    assert find_orphan_gaps(store.read_all(), client) == []   # carried, not orphaned


def test_ambiguous_old_sei_orphaned_surfaces_gap(tmp_path):
    sei = "loomweave:eid:amb"
    store = AuditStore(f"sqlite:///{tmp_path / 'g.db'}")
    store.append({"entity_key": {"value": sei, "identity_stable": True},
                  "identity_stable": True, "extensions": {}})
    client = FakeLoomweave(sei={sei: {"sei": sei, "alive": False,
                                    "lineage": [{"event": "orphaned"}]}})
    gaps = find_orphan_gaps(store.read_all(), client)
    assert [g.sei for g in gaps] == [sei]   # fail-closed: surfaced, never carried


def test_delete_old_sei_orphaned_surfaces_gap(tmp_path):
    sei = "loomweave:eid:del"
    store = AuditStore(f"sqlite:///{tmp_path / 'g.db'}")
    store.append({"entity_key": {"value": sei, "identity_stable": True},
                  "identity_stable": True, "extensions": {}})
    client = FakeLoomweave(sei={sei: {"sei": sei, "alive": False,
                                    "lineage": [{"event": "orphaned"}]}})
    assert [g.sei for g in find_orphan_gaps(store.read_all(), client)] == [sei]


def test_capability_absent_degrades_gracefully():
    client = FakeLoomweave(capable=False)
    res = IdentityResolver(client).resolve("python:function:any")
    assert res.entity_key.identity_stable is False   # honest 'identity unavailable'
    assert res.entity_key.value == "python:function:any"   # keeps working on locators
