from legis.enforcement.lifecycle import decay_sweep
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.identity.entity_key import EntityKey
from legis.records.override_record import OverrideRecord
from legis.store.audit_store import AuditStore


class PolicyJudge:
    """Blocks any rationale containing 'stale'; accepts the rest."""

    def evaluate(self, record):
        v = Verdict.BLOCKED if "stale" in record.rationale else Verdict.ACCEPTED
        return JudgeOpinion(v, "judge@2", f"re-judged: {record.rationale}")


class CapturingJudge:
    def __init__(self):
        self.seen = []

    def evaluate(self, record):
        self.seen.append(record)
        return JudgeOpinion(Verdict.ACCEPTED, "judge@2", "ok")


def _accepted(policy, entity, rationale):
    rec = OverrideRecord(
        policy=policy,
        entity_key=EntityKey.from_locator(entity),
        rationale=rationale,
        agent_id="a",
        recorded_at="t",
        extensions={"judge_verdict": "ACCEPTED", "judge_model": "judge@1"},
    )
    return rec.to_payload()


def test_decay_flags_kept_suppressions_that_fail_a_fresh_pass(tmp_path):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    store.append(_accepted("p", "e1", "still valid reason"))
    store.append(_accepted("p", "e2", "stale reason no longer holds"))
    # a BLOCKED and an OVERRIDDEN record must be ignored by the sweep
    store.append({**_accepted("p", "e3", "stale"), "extensions": {"judge_verdict": "BLOCKED"}})
    store.append(
        {**_accepted("p", "e4", "stale"), "extensions": {"judge_verdict": "OVERRIDDEN_BY_OPERATOR"}}
    )

    flags = decay_sweep(store.read_all(), PolicyJudge())
    assert {f.entity for f in flags} == {"e2"}  # only the ACCEPTED-but-now-stale one
    assert flags[0].seq == 2
    assert "stale" in flags[0].fresh_rationale


def test_decay_rejudge_preserves_source_and_identity_evidence(tmp_path):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    payload = _accepted("p", "e1", "still valid reason")
    payload["extensions"].update(
        {
            "file_fingerprint": "sha256:abc",
            "ast_path": "Module/FunctionDef[f]",
            "source_binding": {
                "status": "verified",
                "source_path": "src/x.py",
                "current_fingerprint": "sha256:abc",
            },
            "clarion": {
                "alive": True,
                "content_hash": "content-hash",
                "lineage_snapshot": {"length": 1, "hash": "lineage-hash"},
            },
            "judge_rationale": "old rationale",
            "judge_metadata_signature": "hmac-sha256:v2:old",
        }
    )
    store.append(payload)
    judge = CapturingJudge()

    decay_sweep(store.read_all(), judge)

    assert len(judge.seen) == 1
    ext = judge.seen[0].extensions
    assert ext["file_fingerprint"] == "sha256:abc"
    assert ext["ast_path"] == "Module/FunctionDef[f]"
    assert ext["source_binding"]["status"] == "verified"
    assert ext["clarion"]["content_hash"] == "content-hash"
    assert "judge_rationale" not in ext
    assert "judge_metadata_signature" not in ext
