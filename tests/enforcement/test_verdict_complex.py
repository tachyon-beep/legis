from legis.enforcement.verdict import SignoffState, Verdict


def test_operator_override_is_a_first_class_verdict():
    assert Verdict.OVERRIDDEN_BY_OPERATOR.value == "OVERRIDDEN_BY_OPERATOR"
    assert Verdict.OVERRIDDEN_BY_OPERATOR is not Verdict.ACCEPTED


def test_signoff_states():
    assert SignoffState.PENDING.value == "PENDING_SIGNOFF"
    assert SignoffState.SIGNED_OFF.value == "SIGNED_OFF"
