from legis.canonical import content_hash
from legis.identity.resolver import IdentityResolver


class FakeClient:
    def __init__(self, *, capable=True, resolve=None, lineage=None, boom=False):
        self._capable = capable
        self._resolve = resolve or {"alive": False}
        self._lineage = lineage or []
        self._boom = boom

    def capability(self):
        if self._boom:
            raise RuntimeError("clarion down")
        return self._capable

    def resolve_locator(self, locator):
        return self._resolve

    def resolve_sei(self, sei):  # not used by the resolver
        raise AssertionError

    def lineage(self, sei):
        return self._lineage


ALIVE = {"sei": "clarion:eid:deadbeef", "current_locator": "python:function:m.f",
         "content_hash": "blake3hash", "alive": True}


def test_alive_sei_is_keyed_opaquely_with_two_axes():
    r = IdentityResolver(FakeClient(resolve=ALIVE, lineage=[{"event": "born"}]))
    res = r.resolve("python:function:m.f")
    assert res.entity_key.value == "clarion:eid:deadbeef"      # the SEI, verbatim
    assert res.entity_key.identity_stable is True
    assert res.entity_key.value.startswith("clarion:eid:")     # opaque, not parsed
    assert res.entity_key.value != "python:function:m.f"       # not the locator
    assert res.alive is True                                    # identity axis
    assert res.content_hash == "blake3hash"                     # content axis
    assert res.lineage_snapshot == {"length": 1, "hash": content_hash([{"event": "born"}])}


def test_capability_absent_degrades_to_locator():
    r = IdentityResolver(FakeClient(capable=False))
    res = r.resolve("python:function:m.f")
    assert res.entity_key.value == "python:function:m.f"
    assert res.entity_key.identity_stable is False
    assert res.alive is None and res.content_hash is None and res.lineage_snapshot is None


def test_no_client_degrades_to_locator():
    res = IdentityResolver(None).resolve("python:function:m.f")
    assert res.entity_key.identity_stable is False


def test_locator_with_no_alive_sei_degrades_but_records_alive_false():
    r = IdentityResolver(FakeClient(resolve={"alive": False}))
    res = r.resolve("python:function:gone")
    assert res.entity_key.identity_stable is False
    assert res.alive is False        # capability present, but no stable identity → honest


def test_transport_error_degrades_never_raises():
    r = IdentityResolver(FakeClient(boom=True))
    res = r.resolve("python:function:m.f")
    assert res.entity_key.identity_stable is False
