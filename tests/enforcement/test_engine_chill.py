from legis.clock import FixedClock
from legis.enforcement.engine import EnforcementEngine
from legis.identity.entity_key import EntityKey
from legis.store.audit_store import AuditStore


def engine(tmp_path, judge=None):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    eng = EnforcementEngine(
        store, FixedClock("2026-06-02T12:00:00+00:00"), judge=judge
    )
    return eng, store


def test_chill_override_is_accepted_and_recorded(tmp_path):
    eng, store = engine(tmp_path)
    result = eng.submit_override(
        policy="no-broad-except",
        entity_key=EntityKey.from_locator("src/app.py:handler"),
        rationale="re-raised after logging",
        agent_id="agent-7",
    )
    assert result.accepted is True
    assert result.verdict is None          # no judge in the chill cell
    assert result.judge_model is None
    assert result.seq >= 1

    trail = store.read_all()
    assert len(trail) == 1
    payload = trail[0].payload
    assert payload["policy"] == "no-broad-except"
    assert payload["rationale"] == "re-raised after logging"
    assert payload["agent_id"] == "agent-7"
    assert payload["recorded_at"] == "2026-06-02T12:00:00+00:00"  # clock-stamped
    assert payload["identity_stable"] is False                    # locator, pre-SEI
    assert payload["extensions"] == {}                            # no judge fields


def test_chill_trail_is_append_only_and_integrity_holds(tmp_path):
    eng, store = engine(tmp_path)
    for i in range(3):
        eng.submit_override(
            policy="p",
            entity_key=EntityKey.from_locator(f"e{i}"),
            rationale="r",
            agent_id="a",
        )
    assert len(store.read_all()) == 3
    assert store.verify_integrity() is True
