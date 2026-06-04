import ast

from legis.policy.evidence import EvidenceResult, evaluate_test_evidence


def _fn(src: str) -> ast.FunctionDef:
    mod = ast.parse(src)
    return next(n for n in mod.body if isinstance(n, ast.FunctionDef))


def test_ok_when_boundary_called_and_policy_asserted_together():
    fn = _fn(
        'def test_x():\n'
        '    result = guarded({"p": "PY-WL-101"})\n'
        '    assert result == "ok", "PY-WL-101"\n'
    )
    res = evaluate_test_evidence(fn, {"guarded"}, ("PY-WL-101",))
    assert res == EvidenceResult(True, "ok", "ok")


def test_not_exercised_when_subject_never_called():
    fn = _fn('def test_x():\n    assert "PY-WL-101"\n')
    res = evaluate_test_evidence(fn, {"guarded"}, ("PY-WL-101",))
    assert res.code == "not_exercised"


def test_policy_not_asserted_when_mention_is_outside_the_assert():
    fn = _fn(
        'def test_x():\n'
        '    note = "see PY-WL-101 docs"\n'
        '    result = guarded(1)\n'
        '    assert result == "ok"\n'
    )
    res = evaluate_test_evidence(fn, {"guarded"}, ("PY-WL-101",))
    assert res.code == "policy_not_asserted"


def test_shadowed_when_boundary_name_redefined():
    fn = _fn(
        'def test_x():\n'
        '    def guarded(p):\n'
        '        return "ok"\n'
        '    assert guarded(1) == "ok", "PY-WL-101"\n'
    )
    res = evaluate_test_evidence(fn, {"guarded"}, ("PY-WL-101",))
    assert res.code == "shadowed"


def test_exercise_excludes_uninvoked_nested_helper():
    fn = _fn(
        'def test_x():\n'
        '    def helper():\n'
        '        return guarded(1)\n'
        '    assert True\n'
    )
    res = evaluate_test_evidence(fn, {"guarded"}, ("PY-WL-101",))
    assert res.code == "not_exercised"


def test_shadowed_when_test_parameter_is_named_after_the_boundary():
    # A pytest fixture parameter named after the boundary shadows the import;
    # `guarded(...)` then refers to the fixture, not the real boundary. The
    # runtime gate flags this today, so the shared evaluator must too.
    fn = _fn(
        'def test_x(guarded):\n'
        '    assert guarded(1) == "ok", "PY-WL-101"\n'
    )
    res = evaluate_test_evidence(fn, {"guarded"}, ("PY-WL-101",))
    assert res.code == "shadowed"


# AnnAssign / AugAssign / For-target shadowing and attribute-form boundary calls
# round out the evaluator's coverage — it is now the single point of failure for
# both gates. Add one case per construct mirroring the patterns above.
def test_shadowed_via_for_target():
    fn = _fn(
        'def test_x():\n'
        '    for guarded in range(2):\n'
        '        pass\n'
        '    assert True, "PY-WL-101"\n'
    )
    res = evaluate_test_evidence(fn, {"guarded"}, ("PY-WL-101",))
    assert res.code == "shadowed"


def test_attribute_form_boundary_call_counts_as_exercise():
    fn = _fn(
        'def test_x():\n'
        '    result = obj.guarded(1)\n'
        '    assert result == "ok", "PY-WL-101"\n'
    )
    res = evaluate_test_evidence(fn, {"guarded"}, ("PY-WL-101",))
    assert res.ok is True


def test_shadowed_via_assign():
    fn = _fn(
        'def test_x():\n'
        '    guarded = 1\n'
        '    assert True, "PY-WL-101"\n'
    )
    res = evaluate_test_evidence(fn, {"guarded"}, ("PY-WL-101",))
    assert res.code == "shadowed"


def test_shadowed_via_ann_assign():
    fn = _fn(
        'def test_x():\n'
        '    guarded: int = 1\n'
        '    assert True, "PY-WL-101"\n'
    )
    res = evaluate_test_evidence(fn, {"guarded"}, ("PY-WL-101",))
    assert res.code == "shadowed"


def test_shadowed_via_aug_assign():
    fn = _fn(
        'def test_x():\n'
        '    guarded = 1\n'
        '    guarded += 1\n'
        '    assert True, "PY-WL-101"\n'
    )
    res = evaluate_test_evidence(fn, {"guarded"}, ("PY-WL-101",))
    assert res.code == "shadowed"
