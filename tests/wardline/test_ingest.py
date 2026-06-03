from legis.wardline.ingest import (
    TRUST_TIERS,
    WardlineFinding,
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
