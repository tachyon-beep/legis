import pytest

from legis.clock import FixedClock
from legis.enforcement.signing import sign
from legis.governance.binding_ledger import (
    BindingError,
    BindingLedger,
    binding_signing_fields,
)
from legis.identity.entity_key import EntityKey
from legis.store.audit_store import AuditStore

KEY = b"binding-key-1"


def _ledger(tmp_path):
    store = AuditStore(f"sqlite:///{tmp_path / 'bind.db'}")
    return BindingLedger(store, FixedClock("2026-06-02T12:00:00+00:00"), key=KEY), store


def test_record_then_get_round_trips_the_binding(tmp_path):
    ledger, _ = _ledger(tmp_path)
    seq = ledger.record(signoff_seq=7, issue_id="ISSUE-1",
                        entity_key=EntityKey.from_sei("clarion:eid:abc"), content_hash="h")
    assert seq == 1
    got = ledger.get(7)
    assert got["signoff_seq"] == 7
    assert got["issue_id"] == "ISSUE-1"
    assert got["entity_key"] == {"value": "clarion:eid:abc", "identity_stable": True}
    assert got["content_hash"] == "h"
    assert got["binding_signature"].startswith("hmac-sha256:v1:")


def test_verify_passes_for_a_legit_record(tmp_path):
    ledger, _ = _ledger(tmp_path)
    ledger.record(signoff_seq=1, issue_id="I", entity_key=EntityKey.from_sei("clarion:eid:x"),
                  content_hash="h")
    ledger.verify()  # does not raise


def test_unknown_signoff_seq_returns_none(tmp_path):
    ledger, _ = _ledger(tmp_path)
    ledger.record(signoff_seq=1, issue_id="I", entity_key=EntityKey.from_sei("clarion:eid:x"),
                  content_hash="h")
    assert ledger.get(99) is None


def test_forged_signature_is_rejected(tmp_path):
    ledger, store = _ledger(tmp_path)
    store.append({"kind": "issue_binding", "signoff_seq": 1, "issue_id": "I",
                  "entity_key": {"value": "clarion:eid:x", "identity_stable": True},
                  "content_hash": "h", "recorded_at": "t",
                  "binding_signature": "hmac-sha256:v1:deadbeef"})
    with pytest.raises(BindingError):
        ledger.verify()
    with pytest.raises(BindingError):
        ledger.get(1)


def _signed_payload(**overrides):
    payload = {"kind": "issue_binding", "signoff_seq": 1, "issue_id": "I",
               "entity_key": {"value": "clarion:eid:x", "identity_stable": True},
               "content_hash": "h", "recorded_at": "t"}
    payload["binding_signature"] = sign(binding_signing_fields(payload), KEY)
    payload.update(overrides)
    return payload


def test_tampering_a_signed_field_is_rejected(tmp_path):
    # Copy a legit signature onto a record whose now-signed fields were mutated:
    # flip entity_key.identity_stable and backdate recorded_at. Proves both are
    # in the signed set.
    ledger, store = _ledger(tmp_path)
    store.append(_signed_payload(
        entity_key={"value": "clarion:eid:x", "identity_stable": False},
        recorded_at="2020-01-01T00:00:00+00:00",
    ))
    with pytest.raises(BindingError):
        ledger.verify()


def test_tampering_content_hash_is_rejected(tmp_path):
    ledger, store = _ledger(tmp_path)
    store.append(_signed_payload(content_hash="TAMPERED"))
    with pytest.raises(BindingError):
        ledger.verify()


def test_missing_signature_is_rejected(tmp_path):
    ledger, store = _ledger(tmp_path)
    store.append({"kind": "issue_binding", "signoff_seq": 1, "issue_id": "I",
                  "entity_key": {"value": "clarion:eid:x", "identity_stable": True},
                  "content_hash": "h", "recorded_at": "t"})
    with pytest.raises(BindingError):
        ledger.verify()


def test_malformed_binding_record_is_rejected(tmp_path):
    ledger, store = _ledger(tmp_path)
    store.append({"kind": "issue_binding", "signoff_seq": 1,
                  "binding_signature": "hmac-sha256:v1:whatever"})
    with pytest.raises(BindingError):
        ledger.verify()
