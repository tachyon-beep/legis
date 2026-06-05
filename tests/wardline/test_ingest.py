import pytest

from legis.wardline.ingest import (
    TRUST_TIERS,
    WardlineFinding,
    WardlinePayloadError,
    WardlineSeverity,
    active_defects,
)


def _finding(**over):
    base = {"rule_id": "PY-WL-101", "message": "m", "severity": "ERROR",
            "kind": "defect", "fingerprint": "fp1", "qualname": "m.f",
            "properties": {"actual_return": "UNKNOWN_RAW", "declared_return": "ASSURED"},
            "suppressed": "active"}
    base.update(over)
    return base


def test_from_wire_carries_trust_properties_verbatim():
    f = WardlineFinding.from_wire(_finding())
    assert f.rule_id == "PY-WL-101"
    assert f.severity is WardlineSeverity.ERROR
    assert f.properties["actual_return"] == "UNKNOWN_RAW"  # tier carried verbatim
    assert f.fingerprint == "fp1"


def test_active_defects_excludes_suppressed_and_non_defects():
    scan = {"findings": [
        _finding(fingerprint="a"),                              # active defect → in
        _finding(
            fingerprint="b",
            suppressed="waived",
            properties={
                "actual_return": "UNKNOWN_RAW",
                "declared_return": "ASSURED",
                "suppression_proof": "ISSUE-1",
            },
        ),                                                      # proved waiver → out
        _finding(fingerprint="c", kind="metric", severity="NONE"),  # not a defect → out
    ]}
    got = [f.fingerprint for f in active_defects(scan)]
    assert got == ["a"]


def test_severity_is_ordered_critical_highest():
    assert WardlineSeverity.CRITICAL.rank > WardlineSeverity.ERROR.rank
    assert WardlineSeverity.ERROR.rank > WardlineSeverity.WARN.rank
    assert WardlineSeverity.WARN.rank > WardlineSeverity.INFO.rank


def test_trust_tiers_is_the_shared_vocabulary():
    # Wardline's tiers, carried as the one suite vocabulary (no tier1/2/3).
    assert {"INTEGRAL", "ASSURED", "GUARDED", "EXTERNAL_RAW"} <= TRUST_TIERS


def test_diagnostic_properties_are_carried_verbatim_not_rejected():
    # Real Wardline findings stash diagnostics (sink, callee, markers) in
    # properties alongside trust tiers. legis carries properties verbatim as
    # evidence and never acts on the values, so a non-tier diagnostic must be
    # accepted, not rejected as an "invalid trust tier".
    scan = {"findings": [_finding(
        fingerprint="a",
        properties={"sink": "os.system", "actual_return": "UNKNOWN_RAW"},
    )]}
    got = active_defects(scan)
    assert [f.fingerprint for f in got] == ["a"]
    assert got[0].properties["sink"] == "os.system"            # carried verbatim
    assert got[0].properties["actual_return"] == "UNKNOWN_RAW"


def test_baselined_and_judged_defects_are_non_active_without_proof():
    # baselined/judged are non-agent-initiated suppression states: not in the
    # active gate population, and (unlike an agent waiver) they carry no proof.
    scan = {"findings": [
        _finding(fingerprint="a"),                              # active → in
        _finding(fingerprint="b", suppressed="baselined"),      # non-active → out
        _finding(fingerprint="c", suppressed="judged"),         # non-active → out
    ]}
    assert [f.fingerprint for f in active_defects(scan)] == ["a"]


def test_waived_defect_accepts_top_level_suppression_proof():
    # Wardline keeps suppression_reason at the finding's top level, not inside
    # properties; legis must accept proof in either location.
    scan = {"findings": [_finding(
        fingerprint="b",
        suppressed="waived",
        suppression_reason="ISSUE-9",
        properties={"actual_return": "UNKNOWN_RAW"},            # no proof key here
    )]}
    assert active_defects(scan) == []                           # accepted + excluded


def test_waived_defect_without_any_proof_is_still_rejected():
    # The proof control is preserved: an agent waiver with no proof anywhere
    # (neither top-level nor in properties) is rejected.
    scan = {"findings": [_finding(
        fingerprint="b",
        suppressed="waived",
        properties={"actual_return": "UNKNOWN_RAW"},
    )]}
    with pytest.raises(WardlinePayloadError, match="suppression proof"):
        active_defects(scan)


def test_unknown_suppression_state_is_still_rejected():
    scan = {"findings": [_finding(fingerprint="x", suppressed="haunted")]}
    with pytest.raises(WardlinePayloadError, match="unsupported suppression state"):
        active_defects(scan)
