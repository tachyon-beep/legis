from legis.clock import FixedClock
from legis.enforcement.protected import ProtectedGate, TrailVerifier, signing_fields
from legis.enforcement.signing import verify
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.identity.entity_key import EntityKey
from legis.store.audit_store import AuditStore

KEY = b"protected-key-1"
CLARION = {"clarion": {"alive": True, "content_hash": "blake3h",
                       "lineage_snapshot": {"length": 1, "hash": "lh"}}}


class ScriptedJudge:
    def __init__(self, opinion):
        self.opinion = opinion

    def evaluate(self, record):
        return self.opinion


def _gate(tmp_path):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    g = ProtectedGate(store, FixedClock("2026-06-02T12:00:00+00:00"),
                      judge=ScriptedJudge(JudgeOpinion(Verdict.ACCEPTED, "judge@1", "ok")),
                      key=KEY)
    return g, store


def test_submit_carries_clarion_block(tmp_path):
    g, store = _gate(tmp_path)
    g.submit(policy="no-eval", entity_key=EntityKey.from_sei("clarion:eid:abc"),
             rationale="r", agent_id="a", file_fingerprint="fp", ast_path="ap",
             extensions=CLARION)
    ext = store.read_all()[0].payload["extensions"]
    assert ext["clarion"] == CLARION["clarion"]
    # Fixed signed fields are untouched by the caller's extensions.
    assert ext["judge_verdict"] == "ACCEPTED"
    assert ext["file_fingerprint"] == "fp"


def test_clarion_block_does_not_break_the_signature(tmp_path):
    g, store = _gate(tmp_path)
    g.submit(policy="no-eval", entity_key=EntityKey.from_sei("clarion:eid:abc"),
             rationale="r", agent_id="a", file_fingerprint="fp", ast_path="ap",
             extensions=CLARION)
    payload = store.read_all()[0].payload
    sig = payload["extensions"]["judge_metadata_signature"]
    assert verify(signing_fields(payload), sig, KEY) is True


def test_mutating_clarion_block_does_not_invalidate_the_signature(tmp_path):
    # Discriminating regression lock for WP-A1: the clarion block lives OUTSIDE
    # the signed field set. Mutating it after signing must NOT break the
    # signature — if a refactor pulled clarion into signing_fields, this fails.
    g, store = _gate(tmp_path)
    g.submit(policy="no-eval", entity_key=EntityKey.from_sei("clarion:eid:abc"),
             rationale="r", agent_id="a", file_fingerprint="fp", ast_path="ap",
             extensions=CLARION)
    record = store.read_all()[0]
    payload = record.payload
    payload["extensions"]["clarion"]["content_hash"] = "TAMPERED"
    payload["extensions"]["clarion"]["lineage_snapshot"] = {"length": 99, "hash": "x"}
    sig = payload["extensions"]["judge_metadata_signature"]
    assert verify(signing_fields(payload), sig, KEY) is True
    # The protected-tier load-time verifier likewise accepts the mutated record.
    TrailVerifier(KEY, frozenset({"no-eval"})).verify([record])


def test_operator_override_carries_clarion_block(tmp_path):
    g, store = _gate(tmp_path)
    g.operator_override(policy="no-eval", entity_key=EntityKey.from_sei("clarion:eid:abc"),
                        rationale="r", operator_id="op", file_fingerprint="fp", ast_path="ap",
                        extensions=CLARION)
    ext = store.read_all()[0].payload["extensions"]
    assert ext["clarion"] == CLARION["clarion"]
    assert ext["judge_verdict"] == "OVERRIDDEN_BY_OPERATOR"


def test_caller_extensions_cannot_override_fixed_fields(tmp_path):
    g, store = _gate(tmp_path)
    g.submit(policy="no-eval", entity_key=EntityKey.from_sei("clarion:eid:abc"),
             rationale="r", agent_id="a", file_fingerprint="fp", ast_path="ap",
             extensions={"judge_verdict": "TAMPERED", "file_fingerprint": "evil"})
    ext = store.read_all()[0].payload["extensions"]
    assert ext["judge_verdict"] == "ACCEPTED"   # gate wins
    assert ext["file_fingerprint"] == "fp"
