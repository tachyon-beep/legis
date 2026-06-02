import pytest

from legis.enforcement.protected import TamperError
from legis.identity.entity_key import EntityKey
from legis.service.errors import AuditIntegrityError
from legis.service.governance import resolve_for_record, verified_records


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


class _FakeProtectedGate:
    def __init__(self, records):
        self._records = records

    def records(self):
        return self._records


class _OkVerifier:
    def verify(self, records):
        return None


class _TamperVerifier:
    def verify(self, records):
        raise TamperError("record 4 hash mismatch")


def _boom():
    raise AssertionError("engine fallback must not be called when a protected gate is wired")


def test_verified_records_uses_engine_store_when_no_protected_gate():
    assert verified_records(None, None, lambda: ["r1", "r2"]) == ["r1", "r2"]


def test_verified_records_uses_protected_store_and_skips_engine_fallback():
    gate = _FakeProtectedGate(["protected"])
    assert verified_records(gate, _OkVerifier(), _boom) == ["protected"]


def test_verified_records_skips_verification_when_no_verifier():
    gate = _FakeProtectedGate(["protected"])
    assert verified_records(gate, None, _boom) == ["protected"]


def test_verified_records_raises_audit_integrity_error_on_tamper():
    gate = _FakeProtectedGate(["bad"])
    with pytest.raises(AuditIntegrityError) as exc_info:
        verified_records(gate, _TamperVerifier(), _boom)
    # the `from exc` chain must be preserved
    assert isinstance(exc_info.value.__cause__, TamperError)
