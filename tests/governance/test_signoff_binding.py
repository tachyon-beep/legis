import pytest

from legis.governance.signoff_binding import bind_signoff_to_issue
from legis.identity.entity_key import EntityKey


class FakeFiligree:
    def __init__(self):
        self.attached = []

    def attach(self, issue_id, entity_id, content_hash, *, actor):
        self.attached.append((issue_id, entity_id, content_hash, actor))
        return {"issue_id": issue_id, "clarion_entity_id": entity_id,
                "content_hash_at_attach": content_hash, "attached_at": "t",
                "attached_by": actor}

    def associations_for_entity(self, entity_id):
        return []


def test_sei_keyed_signoff_binds_to_issue():
    fil = FakeFiligree()
    out = bind_signoff_to_issue(
        fil, issue_id="ISSUE-1",
        entity_key=EntityKey.from_sei("clarion:eid:abc"),
        content_hash="blake3", signoff_seq=7,
    )
    assert fil.attached == [("ISSUE-1", "clarion:eid:abc", "blake3", "legis")]
    assert out["clarion_entity_id"] == "clarion:eid:abc"   # bound on the SEI → rename-stable
    assert out["signoff_seq"] == 7


def test_locator_keyed_signoff_is_rejected_as_unstable():
    fil = FakeFiligree()
    with pytest.raises(ValueError, match="identity_stable"):
        bind_signoff_to_issue(
            fil, issue_id="ISSUE-1",
            entity_key=EntityKey.from_locator("python:function:m.f"),
            content_hash="blake3", signoff_seq=7,
        )


def test_bind_records_a_signed_binding_when_a_ledger_is_given(tmp_path):
    from legis.clock import FixedClock
    from legis.governance.binding_ledger import BindingLedger
    from legis.store.audit_store import AuditStore

    fil = FakeFiligree()
    ledger = BindingLedger(AuditStore(f"sqlite:///{tmp_path / 'bind.db'}"),
                           FixedClock("2026-06-02T12:00:00+00:00"), key=b"k")
    out = bind_signoff_to_issue(
        fil, issue_id="ISSUE-1", entity_key=EntityKey.from_sei("clarion:eid:abc"),
        content_hash="blake3", signoff_seq=7, ledger=ledger)
    assert fil.attached == [("ISSUE-1", "clarion:eid:abc", "blake3", "legis")]
    assert out["signoff_seq"] == 7
    assert out["binding_seq"] == 1
    recorded = ledger.get(7)
    assert recorded["issue_id"] == "ISSUE-1"
    assert recorded["entity_key"]["value"] == "clarion:eid:abc"
    assert recorded["binding_signature"].startswith("hmac-sha256:v2:")


def test_bind_without_a_ledger_keeps_prior_behaviour():
    fil = FakeFiligree()
    out = bind_signoff_to_issue(
        fil, issue_id="ISSUE-1", entity_key=EntityKey.from_sei("clarion:eid:abc"),
        content_hash="blake3", signoff_seq=7)
    assert out["signoff_seq"] == 7
    assert "binding_seq" not in out
