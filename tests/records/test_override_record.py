from legis.identity.entity_key import EntityKey
from legis.records.override_record import OverrideRecord
from legis.store.audit_store import AuditStore


def make_record(**over):
    base = dict(
        policy="no-secret-in-log",
        entity_key=EntityKey.from_locator("clarion:func:mod.foo"),
        rationale="boundary validated by test_x",
        agent_id="agent-1",
        recorded_at="2026-06-01T00:00:00+00:00",
    )
    base.update(over)
    return OverrideRecord(**base)


def test_record_mirrors_identity_stable_from_key():
    assert make_record().identity_stable is False
    r2 = make_record(entity_key=EntityKey.from_sei("clarion:eid:x"))
    assert r2.identity_stable is True


def test_record_persists_through_store_and_round_trips(tmp_path):
    s = AuditStore(f"sqlite:///{tmp_path / 'audit.db'}")
    s.append(make_record().to_payload())
    stored = s.read_all()[0].payload
    assert stored["policy"] == "no-secret-in-log"
    assert stored["entity_key"]["value"] == "clarion:func:mod.foo"
    assert stored["identity_stable"] is False
    assert s.verify_integrity() is True


def test_judge_and_hmac_fields_are_additive_not_a_reshape():
    # Sprint 2/3 fields land in `extensions` with no change to the core schema.
    r = make_record(extensions={"judge_verdict": "ACCEPTED", "judge_model": "m"})
    payload = r.to_payload()
    assert payload["extensions"]["judge_verdict"] == "ACCEPTED"
    assert set(payload) >= {
        "policy",
        "entity_key",
        "rationale",
        "agent_id",
        "recorded_at",
        "identity_stable",
        "extensions",
    }
