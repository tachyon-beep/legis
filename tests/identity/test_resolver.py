import pytest

from legis.canonical import content_hash
from legis.identity.entity_key import EntityKey
from legis.identity.resolver import (
    IdentityResolution,
    IdentityResolutionStatus,
    IdentityResolver,
    LineageSnapshotStatus,
)


class FakeClient:
    def __init__(self, *, capable=True, resolve=None, lineage=None, boom=False, lineage_boom=False):
        self._capable = capable
        self._resolve = resolve or {"alive": False}
        self._lineage = lineage or []
        self._boom = boom
        self._lineage_boom = lineage_boom

    def capability(self):
        if self._boom:
            raise RuntimeError("loomweave down")
        return self._capable

    def resolve_locator(self, locator):
        return self._resolve

    def resolve_sei(self, sei):  # not used by the resolver
        raise AssertionError

    def lineage(self, sei):
        if self._lineage_boom:
            raise RuntimeError("lineage down")
        return self._lineage


ALIVE = {"sei": "loomweave:eid:deadbeef", "current_locator": "python:function:m.f",
         "content_hash": "blake3hash", "alive": True}


def test_alive_sei_is_keyed_opaquely_with_two_axes():
    r = IdentityResolver(FakeClient(resolve=ALIVE, lineage=[{"event": "born"}]))
    res = r.resolve("python:function:m.f")
    assert res.entity_key.value == "loomweave:eid:deadbeef"      # the SEI, verbatim
    assert res.entity_key.identity_stable is True
    assert res.entity_key.value.startswith("loomweave:eid:")     # opaque, not parsed
    assert res.entity_key.value != "python:function:m.f"       # not the locator
    assert res.alive is True                                    # identity axis
    assert res.content_hash == "blake3hash"                     # content axis
    assert res.lineage_snapshot == {"length": 1, "hash": content_hash([{"event": "born"}])}
    assert res.identity_resolution_status == "resolved"
    assert res.lineage_snapshot_status == "verified"


# --- the str,Enum axes + the IdentityResolution construction invariant ---


def test_status_axes_are_str_enums_serializing_to_bare_strings():
    # str,Enum members ARE their wire string — comparison and serialization
    # are byte-identical to the old bare strings (the whole compat argument).
    assert IdentityResolutionStatus.RESOLVED == "resolved"
    assert LineageSnapshotStatus.NOT_APPLICABLE == "not_applicable"
    assert content_hash({"s": IdentityResolutionStatus.NOT_ALIVE}) == content_hash(
        {"s": "not_alive"}
    )


def test_identity_resolution_rejects_contradictory_status_alive():
    # The sharpest case: a frozen record claiming "resolved" while alive is False
    # is self-contradictory and must be unrepresentable at construction.
    ek = EntityKey.from_locator("python:function:m.f")
    with pytest.raises(ValueError):
        IdentityResolution(
            ek,
            False,
            None,
            None,
            IdentityResolutionStatus.RESOLVED,
            LineageSnapshotStatus.NOT_APPLICABLE,
        )
    with pytest.raises(ValueError):
        IdentityResolution(
            ek,
            None,
            None,
            None,
            IdentityResolutionStatus.NOT_ALIVE,
            LineageSnapshotStatus.NOT_APPLICABLE,
        )
    with pytest.raises(ValueError):
        IdentityResolution(
            ek,
            True,
            None,
            None,
            IdentityResolutionStatus.UNAVAILABLE,
            LineageSnapshotStatus.NOT_APPLICABLE,
        )


def test_identity_resolution_accepts_the_three_consistent_shapes():
    ek = EntityKey.from_locator("python:function:m.f")
    # alive None ↔ UNAVAILABLE, False ↔ NOT_ALIVE, True ↔ RESOLVED
    IdentityResolution(
        ek, None, None, None,
        IdentityResolutionStatus.UNAVAILABLE, LineageSnapshotStatus.NOT_APPLICABLE,
    )
    IdentityResolution(
        ek, False, None, None,
        IdentityResolutionStatus.NOT_ALIVE, LineageSnapshotStatus.NOT_APPLICABLE,
    )
    IdentityResolution(
        ek, True, "h", {"length": 1, "hash": "x"},
        IdentityResolutionStatus.RESOLVED, LineageSnapshotStatus.VERIFIED,
    )


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


def test_transient_capability_error_is_retried():
    class FlakyCapabilityClient(FakeClient):
        def __init__(self):
            super().__init__(resolve=ALIVE, lineage=[{"event": "born"}])
            self.calls = 0

        def capability(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("loomweave temporarily down")
            return True

    client = FlakyCapabilityClient()
    r = IdentityResolver(client)
    assert r.resolve("python:function:m.f").entity_key.identity_stable is False

    res = r.resolve("python:function:m.f")

    assert res.entity_key.value == "loomweave:eid:deadbeef"
    assert client.calls == 2


def test_alive_response_missing_sei_degrades_instead_of_raw_key_error():
    r = IdentityResolver(FakeClient(resolve={"alive": True, "content_hash": "h"}))
    res = r.resolve("python:function:m.f")
    assert res.entity_key.identity_stable is False
    assert res.alive is None


def test_alive_sei_with_lineage_failure_records_unavailable_status():
    r = IdentityResolver(FakeClient(resolve=ALIVE, lineage_boom=True))
    res = r.resolve("python:function:m.f")
    assert res.entity_key.value == "loomweave:eid:deadbeef"
    assert res.alive is True
    assert res.lineage_snapshot is None
    assert res.identity_resolution_status == "resolved"
    assert res.lineage_snapshot_status == "unavailable"


# --- Q-L6: the capability latch must revalidate (TTL), and content_hash must be
# type-checked, not trusted verbatim from the Loomweave response. ---


class _Probe(FakeClient):
    """A client whose capability can be flipped, counting probes."""

    def __init__(self, *, capable=True, resolve=None, lineage=None):
        super().__init__(capable=capable, resolve=resolve, lineage=lineage)
        self.probes = 0

    def capability(self):
        self.probes += 1
        return self._capable


def test_capability_is_cached_within_ttl():
    # Within the TTL window the positive latch is reused — one probe across many
    # resolves (the caching the original code intended).
    clock = {"t": 1000.0}
    client = _Probe(resolve=ALIVE, lineage=[{"event": "born"}])
    r = IdentityResolver(client, capability_ttl=300.0, monotonic=lambda: clock["t"])
    for _ in range(5):
        assert r.resolve("python:function:m.f").entity_key.identity_stable is True
    assert client.probes == 1


def test_capability_latch_revalidates_after_ttl():
    # A Loomweave that LOSES the sei capability mid-life must not be treated as
    # capable forever by a long-lived resolver. After the TTL elapses the latch
    # is re-probed and the resolver honestly degrades.
    clock = {"t": 1000.0}
    client = _Probe(resolve=ALIVE, lineage=[{"event": "born"}])
    r = IdentityResolver(client, capability_ttl=300.0, monotonic=lambda: clock["t"])

    assert r.resolve("python:function:m.f").entity_key.identity_stable is True
    assert client.probes == 1

    client._capable = False           # capability revoked upstream
    clock["t"] += 299.0               # still within TTL → stale latch reused
    assert r.resolve("python:function:m.f").entity_key.identity_stable is True
    assert client.probes == 1

    clock["t"] += 2.0                 # now past TTL → re-probe, sees the loss
    assert r.resolve("python:function:m.f").entity_key.identity_stable is False
    assert client.probes == 2


def test_capability_regained_after_ttl_is_noticed():
    # Symmetric to revocation: a negative latch must also age out, so a Loomweave
    # that GAINS the capability is eventually picked up.
    clock = {"t": 0.0}
    client = _Probe(capable=False, resolve=ALIVE, lineage=[{"event": "born"}])
    r = IdentityResolver(client, capability_ttl=300.0, monotonic=lambda: clock["t"])

    assert r.resolve("python:function:m.f").entity_key.identity_stable is False
    client._capable = True
    clock["t"] += 301.0
    assert r.resolve("python:function:m.f").entity_key.identity_stable is True


def test_non_string_content_hash_is_dropped():
    # content_hash is carried verbatim into the record; a non-string value from a
    # buggy/hostile Loomweave must not land in the typed str|None field.
    for bad in (12345, {"nested": "obj"}, ["list"], 3.14):
        resolve = {**ALIVE, "content_hash": bad}
        r = IdentityResolver(FakeClient(resolve=resolve, lineage=[{"event": "born"}]))
        res = r.resolve("python:function:m.f")
        assert res.entity_key.value == "loomweave:eid:deadbeef"
        assert res.content_hash is None
