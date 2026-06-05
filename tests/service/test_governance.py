import pytest

from legis.clock import SystemClock
from legis.enforcement.engine import EnforcementEngine
from legis.enforcement.lifecycle import GateStatus
from legis.enforcement.protected import ProtectedGate, TamperError
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.identity.entity_key import EntityKey
from legis.service.errors import AuditIntegrityError, InvalidArgumentError
from legis.service.governance import (
    compute_override_rate,
    resolve_for_record,
    submit_override,
    submit_protected_override,
    verified_records,
)
from legis.store.audit_store import AuditStore


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


def test_identity_resolution_carries_loomweave_extension_when_alive_known():
    resolved_key = EntityKey.from_locator("resolved")
    identity = _FakeIdentity(
        _FakeResult(resolved_key, alive=True, content_hash="abc", lineage_snapshot=["e1"])
    )
    key, ext = resolve_for_record(identity, "src/foo.py:bar")
    assert key == resolved_key
    assert ext["loomweave"] == {
        "alive": True,
        "content_hash": "abc",
        "lineage_snapshot": ["e1"],
        "identity_resolution_status": "resolved",
        "lineage_snapshot_status": "verified",
    }


def test_alive_false_records_loomweave_extension_with_alive_false():
    resolved_key = EntityKey.from_locator("src/foo.py:bar")
    identity = _FakeIdentity(
        _FakeResult(resolved_key, alive=False, content_hash=None, lineage_snapshot=None)
    )
    key, ext = resolve_for_record(identity, "src/foo.py:bar")
    assert key == resolved_key
    assert ext["loomweave"] == {
        "alive": False,
        "content_hash": None,
        "lineage_snapshot": None,
        "identity_resolution_status": "not_alive",
        "lineage_snapshot_status": "not_applicable",
    }


def test_identity_with_unknown_alive_omits_loomweave_extension():
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


class _IntegrityFailGate(_FakeProtectedGate):
    def verify_integrity(self):
        return False


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


def test_verified_records_uses_public_gate_integrity_hook():
    gate = _IntegrityFailGate(["bad"])
    with pytest.raises(AuditIntegrityError, match="hash chain"):
        verified_records(gate, None, _boom)


def test_compute_override_rate_returns_status_rate_sample_below_min_sample():
    # An empty trail is below min-sample → the gate is not FAIL; rate is 0.
    res = compute_override_rate([])
    assert res.status == GateStatus.PASS_WITH_NOTICE
    assert res.rate == 0.0
    assert res.sample_size == 0


def _sqlite_engine(tmp_path):
    # file-backed sqlite store, no judge → chill cell
    return EnforcementEngine(AuditStore(f"sqlite:///{tmp_path / 'gov.db'}"), SystemClock())


def test_submit_override_chill_records_and_accepts(tmp_path):
    engine = _sqlite_engine(tmp_path)
    result = submit_override(
        engine,
        identity=None,
        policy="no-direct-push",
        entity="src/foo.py:bar",
        rationale="generated file; lint N/A",
        agent_id="agent-7",
    )
    assert result.accepted is True
    # a fresh append-only store assigns seq 1 to its first record
    assert result.seq == 1
    trail = engine.trail()
    assert len(trail) == 1
    assert trail[0]["agent_id"] == "agent-7"
    assert trail[0]["policy"] == "no-direct-push"


class _BlockingJudge:
    model_id = "stub-judge"

    def evaluate(self, record):
        return JudgeOpinion(
            verdict=Verdict.BLOCKED, model="stub-judge", rationale="rationale insufficient"
        )


class _AcceptingJudge:
    def evaluate(self, record):
        return JudgeOpinion(verdict=Verdict.ACCEPTED, model="stub-judge", rationale="ok")


def test_submit_override_coached_blocks_on_negative_verdict(tmp_path):
    engine = EnforcementEngine(
        AuditStore(f"sqlite:///{tmp_path}/gov.db"), SystemClock(), judge=_BlockingJudge()
    )
    result = submit_override(
        engine,
        identity=None,
        policy="no-direct-push",
        entity="src/foo.py:bar",
        rationale="trust me",
        agent_id="agent-7",
    )
    assert result.accepted is False
    assert result.verdict is Verdict.BLOCKED
    assert result.judge_model == "stub-judge"
    # the BLOCKED attempt is still recorded (no silent path)
    assert len(engine.trail()) == 1


def test_submit_protected_override_rejects_unverified_source_binding_before_signing(tmp_path):
    store = AuditStore(f"sqlite:///{tmp_path}/protected.db")
    gate = ProtectedGate(store, SystemClock(), judge=_AcceptingJudge(), key=b"k")

    with pytest.raises(InvalidArgumentError, match="source binding could not be verified"):
        submit_protected_override(
            gate,
            identity=None,
            policy="no-eval",
            entity="src/missing.py:f",
            rationale="sandboxed",
            agent_id="agent-7",
            file_fingerprint="sha256:" + "0" * 64,
            ast_path="Module/FunctionDef[f]",
            source_root=tmp_path,
        )

    assert store.read_all() == []


# --- Q-H2: the override-rate gate decision lives in the service layer ---

def _protected_gate_with_record(tmp_path, db_name="gov.db"):
    from legis.clock import FixedClock

    class _AcceptJudge:
        def evaluate(self, record):
            return JudgeOpinion(Verdict.ACCEPTED, "judge@1", "ok")

    db = f"sqlite:///{tmp_path / db_name}"
    gate = ProtectedGate(AuditStore(db), FixedClock("2026-06-02T12:00:00+00:00"),
                         judge=_AcceptJudge(), key=b"protected-key")
    gate.submit(
        policy="no-eval",
        entity_key=EntityKey.from_locator("src/x.py:f"),
        rationale="approved",
        agent_id="agent-1",
        file_fingerprint="sha256:abc",
        ast_path="Module/Call[eval]",
    )
    return db


def test_evaluate_override_rate_gate_fails_closed_without_key(tmp_path):
    from legis.service.errors import ProtectedKeyRequiredError
    from legis.service.governance import evaluate_override_rate_gate

    db = _protected_gate_with_record(tmp_path)
    records = AuditStore(db).read_all()
    with pytest.raises(ProtectedKeyRequiredError):
        evaluate_override_rate_gate(records, hmac_key=None, protected_policies=frozenset())


def test_evaluate_override_rate_gate_scores_with_key(tmp_path):
    from legis.service.governance import evaluate_override_rate_gate

    db = _protected_gate_with_record(tmp_path)
    records = AuditStore(db).read_all()
    res = evaluate_override_rate_gate(
        records, hmac_key="protected-key", protected_policies=frozenset({"no-eval"})
    )
    assert res.status in {GateStatus.PASS, GateStatus.PASS_WITH_NOTICE, GateStatus.FAIL}


def test_sign_off_raises_not_enabled_when_gate_absent():
    from legis.service.errors import NotEnabledError
    from legis.service.governance import sign_off

    with pytest.raises(NotEnabledError):
        sign_off(None, request_seq=1, operator_id="op-1")
