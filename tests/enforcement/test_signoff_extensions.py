from legis.clock import FixedClock
from legis.enforcement.signoff import SignoffGate
from legis.identity.entity_key import EntityKey
from legis.store.audit_store import AuditStore

LOOMWEAVE = {"loomweave": {"alive": True, "content_hash": "blake3h",
                       "lineage_snapshot": {"length": 1, "hash": "lh"}}}


def _gate(tmp_path):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    return SignoffGate(store, FixedClock("2026-06-02T12:00:00+00:00")), store


def test_request_carries_loomweave_block(tmp_path):
    g, store = _gate(tmp_path)
    g.request(policy="no-eval", entity_key=EntityKey.from_sei("loomweave:eid:abc"),
              rationale="r", agent_id="a", extensions=LOOMWEAVE)
    ext = store.read_all()[0].payload["extensions"]
    assert ext["loomweave"] == LOOMWEAVE["loomweave"]
    assert ext["signoff_state"] == "PENDING_SIGNOFF"


def test_caller_extensions_cannot_override_signoff_state(tmp_path):
    g, store = _gate(tmp_path)
    g.request(policy="no-eval", entity_key=EntityKey.from_sei("loomweave:eid:abc"),
              rationale="r", agent_id="a", extensions={"signoff_state": "SIGNED_OFF"})
    ext = store.read_all()[0].payload["extensions"]
    assert ext["signoff_state"] == "PENDING_SIGNOFF"   # gate wins


def test_loomweave_block_does_not_break_the_signature(tmp_path):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    g = SignoffGate(store, FixedClock("2026-06-02T12:00:00+00:00"),
                    signer=True, key=b"k")
    g.request(policy="no-eval", entity_key=EntityKey.from_sei("loomweave:eid:abc"),
              rationale="r", agent_id="a", extensions=LOOMWEAVE)
    ext = store.read_all()[0].payload["extensions"]
    assert ext["loomweave"] == LOOMWEAVE["loomweave"]
    assert ext["signoff_state"] == "PENDING_SIGNOFF"
    assert ext.get("signoff_signature")   # present and non-empty
