from pathlib import Path

from legis.canonical import content_hash
from legis.policy.boundary_scan import scan_policy_boundaries
from legis.policy.decorator import get_normalized_ast_str


def _test_fingerprint(source: str) -> str:
    return content_hash(get_normalized_ast_str(source))


def _write_boundary_subject(
    src: Path,
    *,
    test_ref: str | None,
    test_fingerprint: str | None,
    suppresses: tuple[str, ...] = ("PY-WL-101",),
) -> None:
    test_ref_line = "" if test_ref is None else f'    test_ref="{test_ref}",\n'
    fingerprint_line = (
        "" if test_fingerprint is None else f'    test_fingerprint="{test_fingerprint}",\n'
    )
    src.mkdir(parents=True)
    (src / "subject.py").write_text(
        f'''
from legis.policy.decorator import policy_boundary

@policy_boundary(
    source="docs/spec.md:12",
    suppresses={suppresses!r},
    invariant="guarded input rejects malformed records",
{test_ref_line}{fingerprint_line})
def guarded(payload):
    return "ok"
''',
        encoding="utf-8",
    )


def test_scan_policy_boundaries_accepts_pinned_exercising_test(tmp_path: Path) -> None:
    test_source = '''
def test_policy_boundary_exercises_subject():
    assert guarded({"policy": "PY-WL-101"}) == "ok"
'''
    fp = _test_fingerprint(test_source)
    src = tmp_path / "src" / "pkg"
    tests = tmp_path / "tests"
    tests.mkdir()
    _write_boundary_subject(
        src,
        test_ref="tests/test_subject.py::test_policy_boundary_exercises_subject",
        test_fingerprint=fp,
    )
    (tests / "test_subject.py").write_text(test_source, encoding="utf-8")

    findings = scan_policy_boundaries(src, repo_root=tmp_path)

    assert findings == []


def test_scan_policy_boundaries_reports_missing_test_ref(tmp_path: Path) -> None:
    src = tmp_path / "src" / "pkg"
    _write_boundary_subject(src, test_ref=None, test_fingerprint="pinned")

    findings = scan_policy_boundaries(src, repo_root=tmp_path)

    assert len(findings) == 1
    assert findings[0].rule_id == "POLICY_BOUNDARY_TEST_REF_MISSING"
    assert findings[0].reason == "test_ref is required"


def test_scan_policy_boundaries_reports_stale_fingerprint(tmp_path: Path) -> None:
    test_source = '''
def test_policy_boundary_exercises_subject():
    assert guarded({"policy": "PY-WL-101"}) == "ok"
'''
    src = tmp_path / "src" / "pkg"
    tests = tmp_path / "tests"
    tests.mkdir()
    _write_boundary_subject(
        src,
        test_ref="tests/test_subject.py::test_policy_boundary_exercises_subject",
        test_fingerprint="stale",
    )
    (tests / "test_subject.py").write_text(test_source, encoding="utf-8")

    findings = scan_policy_boundaries(src, repo_root=tmp_path)

    assert len(findings) == 1
    assert findings[0].rule_id == "POLICY_BOUNDARY_TEST_FINGERPRINT_MISMATCH"


def test_scan_policy_boundaries_reports_test_that_does_not_exercise_subject(
    tmp_path: Path,
) -> None:
    test_source = '''
def test_policy_boundary_mentions_policy_only():
    assert "PY-WL-101"
'''
    fp = _test_fingerprint(test_source)
    src = tmp_path / "src" / "pkg"
    tests = tmp_path / "tests"
    tests.mkdir()
    _write_boundary_subject(
        src,
        test_ref="tests/test_subject.py::test_policy_boundary_mentions_policy_only",
        test_fingerprint=fp,
    )
    (tests / "test_subject.py").write_text(test_source, encoding="utf-8")

    findings = scan_policy_boundaries(src, repo_root=tmp_path)

    assert len(findings) == 1
    assert findings[0].rule_id == "POLICY_BOUNDARY_TEST_DOES_NOT_EXERCISE_SUBJECT"


def test_scan_policy_boundaries_rejects_test_ref_outside_tests_directory(
    tmp_path: Path,
) -> None:
    test_source = '''
def test_policy_boundary_exercises_subject():
    assert guarded({"policy": "PY-WL-101"}) == "ok"
'''
    src = tmp_path / "src" / "pkg"
    other = tmp_path / "src" / "test_subject.py"
    other.parent.mkdir(parents=True)
    other.write_text(test_source, encoding="utf-8")
    _write_boundary_subject(
        src,
        test_ref="src/test_subject.py::test_policy_boundary_exercises_subject",
        test_fingerprint=_test_fingerprint(test_source),
    )

    findings = scan_policy_boundaries(src, repo_root=tmp_path)

    assert len(findings) == 1
    assert findings[0].rule_id == "POLICY_BOUNDARY_TEST_REF_MALFORMED"


def test_scan_policy_boundaries_rejects_non_python_test_ref_file(tmp_path: Path) -> None:
    test_source = '''
def test_policy_boundary_exercises_subject():
    assert guarded({"policy": "PY-WL-101"}) == "ok"
'''
    src = tmp_path / "src" / "pkg"
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_subject.txt").write_text(test_source, encoding="utf-8")
    _write_boundary_subject(
        src,
        test_ref="tests/test_subject.txt::test_policy_boundary_exercises_subject",
        test_fingerprint=_test_fingerprint(test_source),
    )

    findings = scan_policy_boundaries(src, repo_root=tmp_path)

    assert len(findings) == 1
    assert findings[0].rule_id == "POLICY_BOUNDARY_TEST_REF_MALFORMED"


def test_scan_policy_boundaries_rejects_tests_path_traversal(tmp_path: Path) -> None:
    test_source = '''
def test_policy_boundary_exercises_subject():
    assert guarded({"policy": "PY-WL-101"}) == "ok"
'''
    src = tmp_path / "src" / "pkg"
    other = tmp_path / "src" / "test_subject.py"
    other.parent.mkdir(parents=True)
    other.write_text(test_source, encoding="utf-8")
    _write_boundary_subject(
        src,
        test_ref="tests/../src/test_subject.py::test_policy_boundary_exercises_subject",
        test_fingerprint=_test_fingerprint(test_source),
    )

    findings = scan_policy_boundaries(src, repo_root=tmp_path)

    assert len(findings) == 1
    assert findings[0].rule_id == "POLICY_BOUNDARY_TEST_REF_MALFORMED"


def test_scan_policy_boundaries_rejects_traversal_syntax_even_inside_tests(
    tmp_path: Path,
) -> None:
    test_source = '''
def test_policy_boundary_exercises_subject():
    assert guarded({"policy": "PY-WL-101"}) == "ok"
'''
    src = tmp_path / "src" / "pkg"
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_subject.py").write_text(test_source, encoding="utf-8")
    _write_boundary_subject(
        src,
        test_ref="tests/../tests/test_subject.py::test_policy_boundary_exercises_subject",
        test_fingerprint=_test_fingerprint(test_source),
    )

    findings = scan_policy_boundaries(src, repo_root=tmp_path)

    assert len(findings) == 1
    assert findings[0].rule_id == "POLICY_BOUNDARY_TEST_REF_MALFORMED"


def test_scan_policy_boundaries_rejects_non_test_function_ref(tmp_path: Path) -> None:
    test_source = '''
def helper():
    assert guarded({"policy": "PY-WL-101"}) == "ok"
'''
    src = tmp_path / "src" / "pkg"
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_subject.py").write_text(test_source, encoding="utf-8")
    _write_boundary_subject(
        src,
        test_ref="tests/test_subject.py::helper",
        test_fingerprint=_test_fingerprint(test_source),
    )

    findings = scan_policy_boundaries(src, repo_root=tmp_path)

    assert len(findings) == 1
    assert findings[0].rule_id == "POLICY_BOUNDARY_TEST_REF_MALFORMED"


def test_scan_policy_boundaries_rejects_non_test_method_ref(tmp_path: Path) -> None:
    test_source = '''
class TestPolicyBoundary:
    def helper(self):
        assert guarded({"policy": "PY-WL-101"}) == "ok"
'''
    fp = _test_fingerprint(
        '''
def helper(self):
    assert guarded({"policy": "PY-WL-101"}) == "ok"
'''
    )
    src = tmp_path / "src" / "pkg"
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_subject.py").write_text(test_source, encoding="utf-8")
    _write_boundary_subject(
        src,
        test_ref="tests/test_subject.py::TestPolicyBoundary::helper",
        test_fingerprint=fp,
    )

    findings = scan_policy_boundaries(src, repo_root=tmp_path)

    assert len(findings) == 1
    assert findings[0].rule_id == "POLICY_BOUNDARY_TEST_REF_MALFORMED"


def test_scan_policy_boundaries_rejects_subject_call_hidden_in_nested_helper(
    tmp_path: Path,
) -> None:
    test_source = '''
def test_policy_boundary_exercises_subject():
    def dead_helper():
        return guarded({"policy": "PY-WL-101"})
    assert "PY-WL-101"
'''
    fp = _test_fingerprint(test_source)
    src = tmp_path / "src" / "pkg"
    tests = tmp_path / "tests"
    tests.mkdir()
    _write_boundary_subject(
        src,
        test_ref="tests/test_subject.py::test_policy_boundary_exercises_subject",
        test_fingerprint=fp,
    )
    (tests / "test_subject.py").write_text(test_source, encoding="utf-8")

    findings = scan_policy_boundaries(src, repo_root=tmp_path)

    assert len(findings) == 1
    assert findings[0].rule_id == "POLICY_BOUNDARY_TEST_DOES_NOT_EXERCISE_SUBJECT"


def test_scan_policy_boundaries_requires_exact_policy_token(tmp_path: Path) -> None:
    test_source = '''
def test_policy_boundary_exercises_subject():
    assert guarded({"policy": "PY-WL-101"}) == "ok"
'''
    fp = _test_fingerprint(test_source)
    src = tmp_path / "src" / "pkg"
    tests = tmp_path / "tests"
    tests.mkdir()
    _write_boundary_subject(
        src,
        test_ref="tests/test_subject.py::test_policy_boundary_exercises_subject",
        test_fingerprint=fp,
        suppresses=("PY-WL-10",),
    )
    (tests / "test_subject.py").write_text(test_source, encoding="utf-8")

    findings = scan_policy_boundaries(src, repo_root=tmp_path)

    assert len(findings) == 1
    assert findings[0].rule_id == "POLICY_BOUNDARY_TEST_WEAK"


def test_scan_policy_boundaries_accepts_class_method_test_ref(tmp_path: Path) -> None:
    test_source = '''
class TestPolicyBoundary:
    def test_policy_boundary_exercises_subject(self):
        assert guarded({"policy": "PY-WL-101"}) == "ok"
'''
    fp = _test_fingerprint(
        '''
def test_policy_boundary_exercises_subject(self):
    assert guarded({"policy": "PY-WL-101"}) == "ok"
'''
    )
    src = tmp_path / "src" / "pkg"
    tests = tmp_path / "tests"
    tests.mkdir()
    _write_boundary_subject(
        src,
        test_ref="tests/test_subject.py::TestPolicyBoundary::test_policy_boundary_exercises_subject",
        test_fingerprint=fp,
    )
    (tests / "test_subject.py").write_text(test_source, encoding="utf-8")

    findings = scan_policy_boundaries(src, repo_root=tmp_path)

    assert findings == []


def test_scan_policy_boundaries_reports_nonliteral_decorator(tmp_path: Path) -> None:
    src = tmp_path / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "subject.py").write_text(
        '''
from legis.policy.decorator import policy_boundary

POLICY = "PY-WL-101"

@policy_boundary(
    source="docs/spec.md:12",
    suppresses=(POLICY,),
    invariant="guarded input rejects malformed records",
    test_ref="tests/test_subject.py::test_policy_boundary_exercises_subject",
    test_fingerprint="pinned",
)
def guarded(payload):
    return "ok"
''',
        encoding="utf-8",
    )

    findings = scan_policy_boundaries(src, repo_root=tmp_path)

    assert len(findings) == 1
    assert findings[0].rule_id == "POLICY_BOUNDARY_NONLITERAL"


def test_scan_policy_boundaries_reports_source_decode_error(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "bad.py").write_bytes(b"\xff")

    findings = scan_policy_boundaries(src, repo_root=tmp_path)

    assert len(findings) == 1
    assert findings[0].rule_id == "POLICY_BOUNDARY_PARSE_ERROR"


def test_scan_policy_boundaries_reports_test_file_decode_error(tmp_path: Path) -> None:
    src = tmp_path / "src" / "pkg"
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_subject.py").write_bytes(b"\xff")
    _write_boundary_subject(
        src,
        test_ref="tests/test_subject.py::test_policy_boundary_exercises_subject",
        test_fingerprint="pinned",
    )

    findings = scan_policy_boundaries(src, repo_root=tmp_path)

    assert len(findings) == 1
    assert findings[0].rule_id == "POLICY_BOUNDARY_TEST_PARSE_ERROR"


def test_scan_rejects_policy_mention_outside_the_assert(tmp_path: Path) -> None:
    # Calls the subject and mentions the policy, but only in a throwaway string
    # not bound to the assert. The OLD scanner passed this; the new one must not.
    test_source = '''
def test_policy_boundary_exercises_subject():
    note = "see PY-WL-101 in the docs"
    result = guarded({"x": 1})
    assert result == "ok"
'''
    fp = _test_fingerprint(test_source)
    src = tmp_path / "src" / "pkg"
    tests = tmp_path / "tests"
    tests.mkdir()
    _write_boundary_subject(
        src,
        test_ref="tests/test_subject.py::test_policy_boundary_exercises_subject",
        test_fingerprint=fp,
    )
    (tests / "test_subject.py").write_text(test_source, encoding="utf-8")

    findings = scan_policy_boundaries(src, repo_root=tmp_path)

    assert len(findings) == 1
    assert findings[0].rule_id == "POLICY_BOUNDARY_TEST_WEAK"


def test_scan_and_runtime_gate_agree_on_a_shared_corpus(tmp_path: Path) -> None:
    """Convergence keystone: drive BOTH real public gates over the same corpus
    and assert their allow/block verdicts match. This invokes scan_policy_boundaries
    AND check_policy_boundary end-to-end (not the shared evaluator alone), so it
    verifies the WIRING of both callers: that each correctly resolves/extracts the
    test function node, constructs boundary_names, and that the scanner's on-disk
    fingerprint recompute aligns with the runtime's in-memory fingerprint. After
    the Task-2-Step-4 convergence both callers delegate to evaluate_test_evidence,
    so this test goes from RED (pre-convergence: the old _test_mentions_policy
    allows `test_weak` while the runtime blocks it) to GREEN. It is the regression
    guard that keeps the two callers wired to the single evaluator. (It does NOT
    test independent semantics — by design there is only one implementation now;
    `test_weak` is the case that proves the old token-anywhere scanner is gone.)
    """
    import inspect
    import textwrap

    from legis.policy.decorator import check_policy_boundary, fingerprint, policy_boundary

    # Corpus: each is a real test function; `guarded` is the boundary subject.
    # They are inspected, never executed, so the free `guarded`/`note` names are fine.
    def test_ok():
        result = guarded({"p": "PY-WL-101"})  # noqa: F821
        assert result == "ok", "PY-WL-101"

    def test_weak():
        note = "PY-WL-101"  # noqa: F841 — policy mentioned outside the assert
        result = guarded(1)  # noqa: F821
        assert result == "ok"

    def test_unexercised():
        assert "PY-WL-101"

    def test_shadowed():
        def guarded(payload):  # noqa: F811 — shadows the boundary name
            return "ok"
        assert guarded(1) == "ok", "PY-WL-101"

    for test_fn in (test_ok, test_weak, test_unexercised, test_shadowed):
        name = test_fn.__name__
        fp = fingerprint(test_fn)

        @policy_boundary(
            source="docs/spec.md:1",
            suppresses=("PY-WL-101",),
            invariant="boundary holds",
            test_ref=f"tests/test_case.py::{name}",
            test_fingerprint=fp,
        )
        def guarded(payload):
            return "ok"

        runtime_ok = check_policy_boundary(guarded, lambda ref, _fn=test_fn: _fn).ok

        src_dir = tmp_path / name / "src" / "pkg"
        tests_dir = tmp_path / name / "tests"
        tests_dir.mkdir(parents=True)
        _write_boundary_subject(
            src_dir, test_ref=f"tests/test_case.py::{name}", test_fingerprint=fp
        )
        # Same source on disk (dedented top-level) → matching fingerprint.
        (tests_dir / "test_case.py").write_text(
            textwrap.dedent(inspect.getsource(test_fn)), encoding="utf-8"
        )
        scanner_ok = scan_policy_boundaries(src_dir, repo_root=tmp_path / name) == []

        assert runtime_ok == scanner_ok, (
            f"gates disagree on {name!r}: runtime={runtime_ok}, scanner={scanner_ok}"
        )
