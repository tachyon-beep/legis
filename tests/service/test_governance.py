from legis.identity.entity_key import EntityKey
from legis.service.governance import resolve_for_record


class _FakeResult:
    def __init__(self, entity_key, alive, content_hash, lineage_snapshot):
        self.entity_key = entity_key
        self.alive = alive
        self.content_hash = content_hash
        self.lineage_snapshot = lineage_snapshot


class _FakeIdentity:
    def __init__(self, result):
        self._result = result

    def resolve(self, locator):
        return self._result


def test_no_identity_keys_on_locator_with_empty_extensions():
    key, ext = resolve_for_record(None, "src/foo.py:bar")
    assert key == EntityKey.from_locator("src/foo.py:bar")
    assert ext == {}


def test_identity_resolution_carries_clarion_extension_when_alive_known():
    resolved_key = EntityKey.from_locator("resolved")
    identity = _FakeIdentity(
        _FakeResult(resolved_key, alive=True, content_hash="abc", lineage_snapshot=["e1"])
    )
    key, ext = resolve_for_record(identity, "src/foo.py:bar")
    assert key == resolved_key
    assert ext["clarion"] == {
        "alive": True,
        "content_hash": "abc",
        "lineage_snapshot": ["e1"],
    }


def test_alive_false_records_clarion_extension_with_alive_false():
    resolved_key = EntityKey.from_locator("src/foo.py:bar")
    identity = _FakeIdentity(
        _FakeResult(resolved_key, alive=False, content_hash=None, lineage_snapshot=None)
    )
    key, ext = resolve_for_record(identity, "src/foo.py:bar")
    assert key == resolved_key
    assert ext["clarion"] == {
        "alive": False,
        "content_hash": None,
        "lineage_snapshot": None,
    }


def test_identity_with_unknown_alive_omits_clarion_extension():
    resolved_key = EntityKey.from_locator("resolved")
    identity = _FakeIdentity(
        _FakeResult(resolved_key, alive=None, content_hash=None, lineage_snapshot=None)
    )
    key, ext = resolve_for_record(identity, "x")
    assert key == resolved_key
    assert ext == {}
