from legis.clock import FixedClock
from legis.enforcement.protected import ProtectedGate, signing_fields
from legis.enforcement.signing import verify
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.identity.entity_key import EntityKey
from legis.store.audit_store import AuditStore


class ScriptedJudge:
    def __init__(self, opinion):
        self.opinion = opinion

    def evaluate(self, record):
        return self.opinion


class CapturingJudge:
    def __init__(self, opinion):
        self.opinion = opinion
        self.seen = None

    def evaluate(self, record):
        self.seen = record
        return self.opinion


KEY = b"protected-key-1"


def gate(tmp_path, opinion):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    g = ProtectedGate(
        store,
        FixedClock("2026-06-02T12:00:00+00:00"),
        judge=ScriptedJudge(opinion),
        key=KEY,
    )
    return g, store


def submit(g):
    return g.submit(
        policy="no-eval",
        entity_key=EntityKey.from_locator("src/x.py:f"),
        rationale="sandboxed eval of trusted template",
        agent_id="agent-9",
        file_fingerprint="sha256:abc",
        ast_path="Module/FunctionDef[f]/Call[eval]",
    )


def test_accepted_record_is_bound_and_signed(tmp_path):
    g, store = gate(tmp_path, JudgeOpinion(Verdict.ACCEPTED, "judge@1", "ok"))
    result = submit(g)
    assert result.accepted is True
    assert result.verdict is Verdict.ACCEPTED

    ext = store.read_all()[0].payload["extensions"]
    assert ext["protected_cell"] is True
    assert ext["judge_verdict"] == "ACCEPTED"
    assert ext["file_fingerprint"] == "sha256:abc"
    assert ext["ast_path"] == "Module/FunctionDef[f]/Call[eval]"
    assert ext["judge_metadata_signature"].startswith("hmac-sha256:v2:")


def test_signature_covers_entity_and_policy(tmp_path):
    g, store = gate(tmp_path, JudgeOpinion(Verdict.ACCEPTED, "judge@1", "ok"))
    submit(g)
    payload = store.read_all()[0].payload
    fields = signing_fields(payload)
    sig = payload["extensions"]["judge_metadata_signature"]
    assert verify(fields, sig, KEY) is True
    # Transplanting the verdict to a different entity must invalidate the sig.
    moved = {**fields, "entity": {"value": "src/other.py:g", "identity_stable": False}}
    assert verify(moved, sig, KEY) is False
    downgraded = {**fields, "protected_cell": False}
    assert verify(downgraded, sig, KEY) is False


def test_key_is_never_written_to_the_payload(tmp_path):
    import json

    g, store = gate(tmp_path, JudgeOpinion(Verdict.ACCEPTED, "judge@1", "ok"))
    submit(g)
    raw = json.dumps(store.read_all()[0].payload)
    assert "protected-key-1" not in raw


def test_judge_receives_source_and_loomweave_context_that_will_be_signed(tmp_path):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    judge = CapturingJudge(JudgeOpinion(Verdict.ACCEPTED, "judge@1", "ok"))
    g = ProtectedGate(store, FixedClock("2026-06-02T12:00:00+00:00"), judge=judge, key=KEY)

    g.submit(
        policy="no-eval",
        entity_key=EntityKey.from_sei("loomweave:eid:abc"),
        rationale="r",
        agent_id="a",
        file_fingerprint="fp",
        ast_path="ap",
        extensions={"loomweave": {"alive": True, "content_hash": "h", "lineage_snapshot": {"length": 1, "hash": "lh"}}},
    )

    assert judge.seen is not None
    assert judge.seen.extensions["file_fingerprint"] == "fp"
    assert judge.seen.extensions["ast_path"] == "ap"
    assert judge.seen.extensions["loomweave"]["content_hash"] == "h"
