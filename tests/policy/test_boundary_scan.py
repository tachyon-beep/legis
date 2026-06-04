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
    assert findings[0].rule_id == "POLICY_BOUNDARY_TEST_DOES_NOT_MENTION_POLICY"


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
