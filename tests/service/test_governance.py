import pytest

from legis.clock import SystemClock
from legis.enforcement.engine import EnforcementEngine
from legis.enforcement.lifecycle import GateStatus
from legis.enforcement.protected import TamperError
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.identity.entity_key import EntityKey
from legis.service.errors import AuditIntegrityError
from legis.service.governance import compute_override_rate, resolve_for_record, submit_override, verified_records
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
