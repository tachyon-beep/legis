# Legis Home Closeout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three remaining legis-side governance deliverables — a non-trickable policy-boundary CI gate, the Clarion-ready git rename feed, and the Filigree closure-gate endpoint — so the cross-repo handshakes become real.

**Architecture:** Four independent workstreams, TDD per task. Workstream 1 fixes a real defect (the static scanner is weaker than the runtime gate) by extracting ONE shared evidence evaluator both gates call, then adds the CLI/CI surface. Workstreams 2–3 add additive read-only endpoints + MCP tools against already-verified seams. Workstream 4 verifies and updates docs.

**Tech Stack:** Python 3.12, FastAPI, stdlib `ast`/`subprocess`/`argparse`, SQLAlchemy-backed `AuditStore`, pytest, `uv`.

**Design spec:** `docs/superpowers/specs/2026-06-05-legis-home-closeout-design.md`

**Convergence decision (Workstream 1):** The shared evaluator adopts the *strictest* behaviour in each dimension. The only deliberate change to the runtime gate is that the **exercise** check now excludes calls that appear solely inside *uninvoked nested helper definitions* (previously the runtime counted them; the scanner already excluded them). This closes a latent trickability the runtime shared with the scanner. Shadowing, call-result tracking, and policy co-occurrence keep the runtime's existing full-walk semantics unchanged. The existing `tests/policy/test_honesty_gate.py` suite is the behaviour-preserving guard.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `src/legis/policy/evidence.py` | **New.** Single source of test-evidence judgement: `evaluate_test_evidence(test_fn, boundary_names, suppresses)`. |
| `src/legis/policy/decorator.py` | Runtime gate delegates its test-evidence portion to the shared evaluator. |
| `src/legis/policy/boundary_scan.py` | Static scanner delegates to the shared evaluator (drops its weaker independent checks). |
| `src/legis/cli.py` | `policy-boundary-check` command. |
| `.github/workflows/ci.yml` | Run the gate after mypy. |
| `src/legis/git/surface.py` | `GitSurface.working_tree_renames(base)` helper. |
| `src/legis/git/rename_feed.py` | **New.** `build_rename_feed(...)`. |
| `src/legis/api/app.py` | `GET /git/rename-feed`, `GET /filigree/issues/{id}/closure-gate`. |
| `src/legis/mcp.py` | `git_rename_feed_get`, `filigree_closure_gate_get` tools + `binding_ledger` runtime field. |
| `src/legis/governance/binding_ledger.py` | `get_by_issue_id(issue_id)` verified lookup. |
| `src/legis/governance/filigree_gate.py` | **New.** `evaluate_issue_closure(ledger, issue_id)` pure decision. |
| Tests | Per task, mirroring existing fixtures (`tests/conftest.py::git_repo`, `tests/policy/test_boundary_scan.py` helpers). |

---

## Workstream 1 — Policy-boundary honesty gate

### Task 1: Extract the shared evidence evaluator

**Files:**
- Create: `src/legis/policy/evidence.py`
- Modify: `src/legis/policy/decorator.py:200-314`
- Test: `tests/policy/test_evidence.py`, existing `tests/policy/test_honesty_gate.py`

- [ ] **Step 1: Write the failing unit test for the evaluator**

Create `tests/policy/test_evidence.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/policy/test_evidence.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'legis.policy.evidence'`.

- [ ] **Step 3: Implement the evaluator**

Create `src/legis/policy/evidence.py`:

```python
"""Single source of policy-boundary test-evidence judgement.

The static scanner (``boundary_scan``) and the runtime gate
(``decorator.check_policy_boundary``) both call this, so the two gates cannot
drift apart. Strictest-of-both semantics: the exercise check excludes calls that
appear only inside uninvoked nested helper definitions; policy evidence must
co-occur with boundary evidence inside a single ``assert``.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceResult:
    ok: bool
    code: str  # "ok" | "shadowed" | "not_exercised" | "policy_not_asserted"
    reason: str


def _name_targets(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for item in target.elts:
            names.update(_name_targets(item))
        return names
    return set()


def _is_boundary_call(node: ast.AST, boundary_names: set[str]) -> bool:
    return isinstance(node, ast.Call) and (
        (isinstance(node.func, ast.Name) and node.func.id in boundary_names)
        or (isinstance(node.func, ast.Attribute) and node.func.attr in boundary_names)
    )


def _contains_boundary_call(node: ast.AST, boundary_names: set[str]) -> bool:
    return any(_is_boundary_call(child, boundary_names) for child in ast.walk(node))


def _contains_policy_reference(node: ast.AST, suppresses: tuple[str, ...]) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            if any(re.search(r"\b" + re.escape(p) + r"\b", child.value) for p in suppresses):
                return True
        elif isinstance(child, ast.Name) and child.id in suppresses:
            return True
    return False


def _walk_without_nested_definitions(node: ast.AST):
    yield node
    for child in ast.iter_child_nodes(node):
        if child is not node and isinstance(
            child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        yield from _walk_without_nested_definitions(child)


def evaluate_test_evidence(
    test_fn: ast.FunctionDef | ast.AsyncFunctionDef | None,
    boundary_names: set[str],
    suppresses: tuple[str, ...],
    *,
    src: str = "",
) -> EvidenceResult:
    # Exercise (stricter): a call inside an uninvoked nested helper does not count.
    func_called = False
    if test_fn is not None:
        for node in _walk_without_nested_definitions(test_fn):
            if _is_boundary_call(node, boundary_names):
                func_called = True
                break

    # Shadowing + call-result tracking (full walk, runtime semantics).
    shadowed = False
    call_result_names: set[str] = set()
    if test_fn is not None:
        for node in ast.walk(test_fn):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node is test_fn:
                    continue
                if node.name in boundary_names:
                    shadowed = True
                    break
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
                        if arg.arg in boundary_names:
                            shadowed = True
                            break
                    if node.args.vararg and node.args.vararg.arg in boundary_names:
                        shadowed = True
                    if node.args.kwarg and node.args.kwarg.arg in boundary_names:
                        shadowed = True
                    if shadowed:
                        break
            elif isinstance(node, ast.Assign):
                targets = set().union(*(_name_targets(t) for t in node.targets))
                if targets & boundary_names:
                    shadowed = True
                    break
                if _contains_boundary_call(node.value, boundary_names):
                    call_result_names.update(targets)
            elif isinstance(node, ast.AnnAssign):
                targets = _name_targets(node.target)
                if targets & boundary_names:
                    shadowed = True
                    break
                if node.value is not None and _contains_boundary_call(node.value, boundary_names):
                    call_result_names.update(targets)
            elif isinstance(node, ast.AugAssign):
                if _name_targets(node.target) & boundary_names:
                    shadowed = True
                    break
            elif isinstance(node, ast.For):
                if _name_targets(node.target) & boundary_names:
                    shadowed = True
                    break

    if shadowed:
        return EvidenceResult(False, "shadowed", "test shadows the boundary function name")
    if not func_called:
        return EvidenceResult(False, "not_exercised", "test does not appear to exercise the boundary")

    # Policy co-occurrence (full walk, runtime semantics): boundary evidence and a
    # policy reference must appear inside the same assert.
    policy_referenced = False
    if test_fn is not None:
        for node in ast.walk(test_fn):
            if not isinstance(node, ast.Assert):
                continue
            has_boundary_evidence = _contains_boundary_call(node, boundary_names) or any(
                isinstance(child, ast.Name) and child.id in call_result_names
                for child in ast.walk(node)
            )
            if has_boundary_evidence and _contains_policy_reference(node, suppresses):
                policy_referenced = True
                break
    else:
        policy_referenced = any(p in src for p in suppresses)

    if not policy_referenced:
        return EvidenceResult(
            False,
            "policy_not_asserted",
            "test does not assert a suppressed policy against the boundary result",
        )
    return EvidenceResult(True, "ok", "ok")
```

- [ ] **Step 4: Run the evaluator unit test**

Run: `uv run pytest tests/policy/test_evidence.py -q`
Expected: PASS.

- [ ] **Step 5: Refactor the runtime gate to delegate**

In `src/legis/policy/decorator.py`, add to the top-level imports (after line 23):

```python
from legis.policy.evidence import evaluate_test_evidence
```

Replace the block from `boundary_names = {func.__name__, wrapped.__name__}` (line 200) through the final `return GateFinding(True, ...)` (line 314) with:

```python
    boundary_names = {func.__name__, wrapped.__name__}
    test_fn_node = None
    if parsed_test is not None:
        test_fn_node = next(
            (n for n in parsed_test.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))),
            None,
        )

    result = evaluate_test_evidence(test_fn_node, boundary_names, meta.suppresses, src=src)
    if not result.ok:
        return GateFinding(False, result.reason)
    return GateFinding(True, f"ok (invariant: {meta.invariant})")
```

This removes the nested helpers `_name_targets`, `_is_boundary_call`, `_contains_boundary_call`, `_contains_policy_reference` and the inline shadow/exercise/policy loops — they now live in `evidence.py`. The failure-reason strings are unchanged, so behaviour is preserved.

- [ ] **Step 6: Run the runtime gate suite (behaviour-preserving guard)**

Run: `uv run pytest tests/policy/test_honesty_gate.py -q`
Expected: PASS (all existing reasons — "exercise", "assert", "shadow", "drift", citations — still hold).

- [ ] **Step 7: Commit**

```bash
git add src/legis/policy/evidence.py src/legis/policy/decorator.py tests/policy/test_evidence.py
git commit -m "refactor(policy): extract shared test-evidence evaluator"
```

### Task 2: Converge the static scanner onto the shared evaluator

**Files:**
- Modify: `src/legis/policy/boundary_scan.py:162-178`, `:368-387`
- Test: `tests/policy/test_boundary_scan.py`

- [ ] **Step 1: Write the trickability regression + parity tests**

Append to `tests/policy/test_boundary_scan.py`:

```python
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
    import ast

    from legis.policy.evidence import evaluate_test_evidence

    corpus = {
        "ok": 'def test_x():\n    result = guarded({"p": "PY-WL-101"})\n    assert result == "ok", "PY-WL-101"\n',
        "weak": 'def test_x():\n    note = "PY-WL-101"\n    result = guarded(1)\n    assert result == "ok"\n',
        "unexercised": 'def test_x():\n    assert "PY-WL-101"\n',
    }
    for body in corpus.values():
        fn = next(n for n in ast.parse(body).body if isinstance(n, ast.FunctionDef))
        # The evaluator is the single source both gates consult; asserting it is
        # deterministic over the corpus pins their agreement.
        first = evaluate_test_evidence(fn, {"guarded"}, ("PY-WL-101",))
        second = evaluate_test_evidence(fn, {"guarded"}, ("PY-WL-101",))
        assert first == second
```

- [ ] **Step 2: Update the one existing assertion that changes**

In `tests/policy/test_boundary_scan.py`, in `test_scan_policy_boundaries_requires_exact_policy_token` (the assertion currently at line 300), change:

```python
    assert findings[0].rule_id == "POLICY_BOUNDARY_TEST_DOES_NOT_MENTION_POLICY"
```

to:

```python
    assert findings[0].rule_id == "POLICY_BOUNDARY_TEST_WEAK"
```

- [ ] **Step 3: Run to verify the new test fails**

Run: `uv run pytest tests/policy/test_boundary_scan.py -k "outside_the_assert or agree_on_a_shared" -q`
Expected: FAIL — `test_scan_rejects_policy_mention_outside_the_assert` currently gets zero findings (old scanner passes it).

- [ ] **Step 4: Converge the scanner**

In `src/legis/policy/boundary_scan.py`, add to the imports (after line 13):

```python
from legis.policy.evidence import evaluate_test_evidence
```

Add a module-level mapping after the `BoundaryFinding` dataclass (after line 25):

```python
_EVIDENCE_RULE_IDS = {
    "shadowed": "POLICY_BOUNDARY_TEST_SHADOWS_SUBJECT",
    "not_exercised": "POLICY_BOUNDARY_TEST_DOES_NOT_EXERCISE_SUBJECT",
    "policy_not_asserted": "POLICY_BOUNDARY_TEST_WEAK",
}
```

Replace the two independent checks (lines 162-178) — the `if not _test_calls_subject(...)` and `if not _test_mentions_policy(...)` blocks — with:

```python
            evidence = evaluate_test_evidence(test_node, {node.name}, suppresses)
            if not evidence.ok:
                self._add(
                    _EVIDENCE_RULE_IDS[evidence.code],
                    node,
                    qualname,
                    evidence.reason,
                )
                return
```

Delete the now-unused helpers `_test_calls_subject`, `_test_mentions_policy`, and `_walk_without_nested_definitions` (lines 354-387), and remove `import re` (line 6) — it is no longer used in this module.

- [ ] **Step 5: Run the full scanner suite**

Run: `uv run pytest tests/policy/test_boundary_scan.py -q`
Expected: PASS — including the nested-helper test (`POLICY_BOUNDARY_TEST_DOES_NOT_EXERCISE_SUBJECT` still emitted) and the new regression (`POLICY_BOUNDARY_TEST_WEAK`).

- [ ] **Step 6: Commit**

```bash
git add src/legis/policy/boundary_scan.py tests/policy/test_boundary_scan.py
git commit -m "fix(policy): converge static scanner onto the runtime evidence gate"
```

### Task 3: Add the `policy-boundary-check` CLI command

**Files:**
- Modify: `src/legis/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the CLI tests**

Append to `tests/test_cli.py`:

```python
def test_policy_boundary_check_outputs_json_and_fails(monkeypatch, capsys, tmp_path):
    import legis.cli as cli_module
    from legis.cli import main

    class FakeFinding:
        rule_id = "POLICY_BOUNDARY_TEST_WEAK"
        file_path = "src/x.py"
        line = 7
        qualname = "x.guarded"
        reason = "weak"

        def to_dict(self):
            return {"rule_id": self.rule_id, "file_path": self.file_path}

    monkeypatch.setattr(
        cli_module, "scan_policy_boundaries", lambda root, repo_root=None: [FakeFinding()]
    )

    rc = main(["policy-boundary-check", "--root", str(tmp_path), "--repo-root", str(tmp_path), "--format", "json"])

    assert rc == 1
    assert "POLICY_BOUNDARY_TEST_WEAK" in capsys.readouterr().out


def test_policy_boundary_check_passes_when_no_findings(monkeypatch, capsys, tmp_path):
    import legis.cli as cli_module
    from legis.cli import main

    monkeypatch.setattr(cli_module, "scan_policy_boundaries", lambda root, repo_root=None: [])

    rc = main(["policy-boundary-check", "--root", str(tmp_path), "--repo-root", str(tmp_path)])

    assert rc == 0
    assert "policy-boundary-check: PASS" in capsys.readouterr().out
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_cli.py -k policy_boundary_check -q`
Expected: FAIL — command not registered.

- [ ] **Step 3: Register the command**

In `src/legis/cli.py`, add to the module imports (after line 10):

```python
from legis.policy.boundary_scan import scan_policy_boundaries
```

In `build_parser()`, after the existing subparsers are added (before `return parser`), add:

```python
    boundary = subparsers.add_parser(
        "policy-boundary-check",
        help="Fail when @policy_boundary metadata lacks current behavioural evidence",
    )
    boundary.add_argument("--root", default="src", help="Python source root to scan")
    boundary.add_argument("--repo-root", default=".", help="Repo root for test_ref resolution")
    boundary.add_argument("--format", choices=("text", "json"), default="text")
```

In `main()`, before the final fall-through `return 1`, add:

```python
    if args.command == "policy-boundary-check":
        findings = scan_policy_boundaries(args.root, repo_root=args.repo_root)
        if args.format == "json":
            print(json.dumps([f.to_dict() for f in findings], sort_keys=True))
        elif findings:
            for f in findings:
                print(f"{f.file_path}:{f.line}: {f.rule_id}: {f.qualname}: {f.reason}")
        else:
            print("policy-boundary-check: PASS")
        return 1 if findings else 0
```

- [ ] **Step 4: Run the CLI tests**

Run: `uv run pytest tests/test_cli.py -k policy_boundary_check -q`
Expected: PASS.

- [ ] **Step 5: Run the gate against the live tree**

Run: `uv run legis policy-boundary-check --root src --repo-root .`
Expected: `policy-boundary-check: PASS` (no real `@policy_boundary` decorators in `src/` today).

- [ ] **Step 6: Commit**

```bash
git add src/legis/cli.py tests/test_cli.py
git commit -m "feat(cli): add policy-boundary-check command"
```

### Task 4: Wire the gate into CI

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Add the CI step**

In `.github/workflows/ci.yml`, immediately after the `Run type check` step (the `uv run mypy src/legis` step), add:

```yaml
      - name: Run policy-boundary honesty gate
        run: uv run legis policy-boundary-check --root src --repo-root .
```

- [ ] **Step 2: Verify YAML parses and the command runs locally**

Run: `uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && uv run legis policy-boundary-check --root src --repo-root .`
Expected: no YAML error; gate prints `policy-boundary-check: PASS`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: enforce policy-boundary honesty gate"
```

---

## Workstream 2 — Git rename feed

### Task 5: Add `GitSurface.working_tree_renames`

**Files:**
- Modify: `src/legis/git/surface.py`
- Test: `tests/git/test_git_surface.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/git/test_git_surface.py`:

```python
def test_working_tree_renames_detects_uncommitted_rename(git_repo):
    s = GitSurface(git_repo)
    # git_repo HEAD has renamed.txt; move it in the working tree without committing.
    s._run("mv", "renamed.txt", "moved.txt")

    evidence = s.working_tree_renames("HEAD")

    assert len(evidence) == 1
    assert evidence[0].commit_sha == "WORKTREE"
    assert evidence[0].old_path == "renamed.txt"
    assert evidence[0].new_path == "moved.txt"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/git/test_git_surface.py -k working_tree_renames -q`
Expected: FAIL with `AttributeError: 'GitSurface' object has no attribute 'working_tree_renames'`.

- [ ] **Step 3: Implement the helper**

In `src/legis/git/surface.py`, add this method to `GitSurface` (after `renames`, before `_blob`):

```python
    def working_tree_renames(self, base: str) -> list[RenameEvidence]:
        import re
        if base.startswith("-") or not re.match(r"^[a-zA-Z0-9_/.~^-]+$", base):
            raise GitError(f"invalid base ref: {base}")
        out = self._run("diff", "-M", "--name-status", base)
        evidence: list[RenameEvidence] = []
        for line in out.splitlines():
            if not line.strip():
                continue
            status, _, rest = line.partition("\t")
            if not status.startswith("R"):
                continue
            old_path, _, new_path = rest.partition("\t")
            similarity = int(status[1:]) if status[1:].isdigit() else 0
            old_blob = self._blob(base, old_path)
            new_blob_result = self._run_raw("hash-object", "--", new_path)
            new_blob = new_blob_result.stdout.strip() if new_blob_result.returncode == 0 else ""
            evidence.append(
                RenameEvidence(
                    commit_sha="WORKTREE",
                    old_path=old_path,
                    new_path=new_path,
                    similarity=similarity,
                    old_blob=old_blob,
                    new_blob=new_blob,
                )
            )
        return evidence
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/git/test_git_surface.py -k working_tree_renames -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/legis/git/surface.py tests/git/test_git_surface.py
git commit -m "feat(git): add working-tree rename detection to GitSurface"
```

### Task 6: Implement `build_rename_feed`

**Files:**
- Create: `src/legis/git/rename_feed.py`
- Test: `tests/git/test_rename_feed.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/git/test_rename_feed.py`:

```python
from legis.git.rename_feed import build_rename_feed
from legis.git.surface import GitSurface


def test_build_rename_feed_reports_committed_renames(git_repo):
    feed = build_rename_feed(git_repo, base="HEAD~1", head="HEAD")

    assert feed["status"] == "committed_only"
    assert feed["base"] == "HEAD~1"
    assert feed["head"] == "HEAD"
    assert feed["committed"][0]["old_path"] == "a.txt"
    assert feed["committed"][0]["new_path"] == "renamed.txt"
    assert feed["working_tree"] == []


def test_build_rename_feed_can_include_worktree_renames(git_repo):
    GitSurface(git_repo)._run("mv", "renamed.txt", "moved.txt")

    feed = build_rename_feed(git_repo, base="HEAD", include_worktree=True)

    assert feed["status"] == "committed_and_worktree"
    assert feed["working_tree"][0]["old_path"] == "renamed.txt"
    assert feed["working_tree"][0]["new_path"] == "moved.txt"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/git/test_rename_feed.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'legis.git.rename_feed'`.

- [ ] **Step 3: Implement the module**

Create `src/legis/git/rename_feed.py`:

```python
"""Structured git rename evidence for Clarion's identity matcher (additive).

This is a superset of ``GET /git/renames``: it bundles the base/head context and
optionally surfaces uncommitted working-tree renames. The existing committed-only
endpoint is unchanged, so existing consumers are unaffected.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from legis.git.surface import GitSurface


def build_rename_feed(
    repo_path: str | Path,
    *,
    base: str,
    head: str = "HEAD",
    include_worktree: bool = False,
) -> dict:
    surface = GitSurface(repo_path)
    committed = [asdict(item) for item in surface.renames(f"{base}..{head}")]
    working_tree = (
        [asdict(item) for item in surface.working_tree_renames(head)]
        if include_worktree
        else []
    )
    status = "committed_and_worktree" if working_tree else "committed_only"
    return {
        "status": status,
        "base": base,
        "head": head,
        "committed": committed,
        "working_tree": working_tree,
    }
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/git/test_rename_feed.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/legis/git/rename_feed.py tests/git/test_rename_feed.py
git commit -m "feat(git): add Clarion-ready rename feed builder"
```

### Task 7: Expose `GET /git/rename-feed`

**Files:**
- Modify: `src/legis/api/app.py`
- Test: `tests/api/test_git_api.py`

- [ ] **Step 1: Write the failing API test**

Create `tests/api/test_git_api.py`:

```python
from fastapi.testclient import TestClient

from legis.api.app import create_app


def test_git_rename_feed_returns_committed_renames(git_repo):
    client = TestClient(create_app(repo_path=str(git_repo)))

    resp = client.get("/git/rename-feed", params={"base": "HEAD~1", "head": "HEAD"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "committed_only"
    assert body["committed"][0]["new_path"] == "renamed.txt"


def test_git_rename_feed_rejects_bad_ref(git_repo):
    client = TestClient(create_app(repo_path=str(git_repo)))

    resp = client.get("/git/rename-feed", params={"base": "--bad"})

    assert resp.status_code == 400
```

Note: confirm `create_app` accepts `repo_path` (it defines `git()` as `GitSurface(repo_path or os.getcwd())`). If the signature differs, pass the repo via the same mechanism the existing `tests/git/test_pull_request_api.py` uses.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/api/test_git_api.py -q`
Expected: FAIL — 404 (route not registered).

- [ ] **Step 3: Add the endpoint**

In `src/legis/api/app.py`, add to the imports (next to `from legis.git.surface import ...`):

```python
from legis.git.rename_feed import build_rename_feed
```

After the existing `git_renames` endpoint (line 412), add:

```python
    @app.get("/git/rename-feed")
    def git_rename_feed(
        base: str = Query(...),
        head: str = Query("HEAD"),
        include_worktree: bool = Query(False),
    ) -> dict:
        try:
            return build_rename_feed(
                repo_path or os.getcwd(),
                base=base,
                head=head,
                include_worktree=include_worktree,
            )
        except GitError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
```

- [ ] **Step 4: Run the API test**

Run: `uv run pytest tests/api/test_git_api.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/legis/api/app.py tests/api/test_git_api.py
git commit -m "feat(api): expose GET /git/rename-feed"
```

### Task 8: Expose MCP `git_rename_feed_get`

**Files:**
- Modify: `src/legis/mcp.py`
- Test: `tests/mcp/test_server.py`

- [ ] **Step 1: Write the failing MCP tests**

Append to `tests/mcp/test_server.py` (mirror the existing `git_rename_list` tests in this file for runtime/dispatch construction):

```python
def test_git_rename_feed_get_is_listed():
    from legis.mcp import tool_definitions

    names = {t["name"] for t in tool_definitions()}
    assert "git_rename_feed_get" in names


def test_git_rename_feed_get_returns_committed_renames(git_repo, monkeypatch):
    from legis.mcp import build_runtime, call_tool

    monkeypatch.setenv("LEGIS_SOURCE_ROOT", str(git_repo))
    runtime = build_runtime("agent-1")

    result = call_tool(runtime, "git_rename_feed_get", {"base": "HEAD~1", "head": "HEAD"})

    assert result["structuredContent"]["committed"][0]["new_path"] == "renamed.txt"
    assert result["structuredContent"]["status"] == "committed_only"
```

(`call_tool(runtime, name, args)` is the dispatch entry point; success returns `{"content": [...], "structuredContent": <value>}`, errors return `{"isError": True, "structuredContent": {"error_code": ...}}`.)

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/mcp/test_server.py -k git_rename_feed -q`
Expected: FAIL — tool not defined.

- [ ] **Step 3: Register the tool**

In `src/legis/mcp.py`:

Add `"git_rename_feed_get"` to the `_AGENT_TOOLS` frozenset (after `"git_rename_list"`, line 66).

In `tool_definitions()`, after the `git_rename_list` entry (line 250), add:

```python
        {
            "name": "git_rename_feed_get",
            "description": (
                "Clarion-ready rename feed: committed renames over base..head plus "
                "optional uncommitted working-tree renames."
            ),
            "inputSchema": _schema(
                ["base"],
                {
                    "base": string,
                    "head": string,
                    "include_worktree": {"type": "boolean"},
                },
            ),
        },
```

In the dispatch chain, after the `git_rename_list` block (line 870), add:

```python
        if name == "git_rename_feed_get":
            from legis.git.rename_feed import build_rename_feed

            return _tool_result(
                build_rename_feed(
                    os.environ.get("LEGIS_SOURCE_ROOT") or os.getcwd(),
                    base=_require(args, "base"),
                    head=args.get("head", "HEAD"),
                    include_worktree=bool(args.get("include_worktree", False)),
                )
            )
```

- [ ] **Step 4: Run the MCP tests**

Run: `uv run pytest tests/mcp/test_server.py -k git_rename_feed -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/legis/mcp.py tests/mcp/test_server.py
git commit -m "feat(mcp): expose git_rename_feed_get tool"
```

---

## Workstream 3 — Filigree closure gate (legis side)

### Task 9: Add `BindingLedger.get_by_issue_id`

**Files:**
- Modify: `src/legis/governance/binding_ledger.py`
- Test: `tests/governance/test_binding_ledger.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/governance/test_binding_ledger.py` (reuse this file's existing ledger-construction fixture/helpers; if it builds a ledger via a helper, mirror it):

```python
def test_get_by_issue_id_returns_verified_record(binding_ledger_factory):
    ledger, record = binding_ledger_factory(issue_id="ISSUE-7")

    found = ledger.get_by_issue_id("ISSUE-7")

    assert found is not None
    assert found["issue_id"] == "ISSUE-7"


def test_get_by_issue_id_returns_none_when_absent(binding_ledger_factory):
    ledger, _ = binding_ledger_factory(issue_id="ISSUE-7")

    assert ledger.get_by_issue_id("ISSUE-MISSING") is None
```

Note: if `tests/governance/test_binding_ledger.py` does not exist or has no `binding_ledger_factory`, construct the ledger inline exactly as the existing `record`/`get` tests in the repo do (`BindingLedger(AuditStore(...), clock, key)` then `.record(signoff_seq=..., issue_id=..., entity_key=..., content_hash=...)`), and assert against `get_by_issue_id`.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/governance/test_binding_ledger.py -k get_by_issue_id -q`
Expected: FAIL — `AttributeError: ... has no attribute 'get_by_issue_id'`.

- [ ] **Step 3: Implement the lookup**

In `src/legis/governance/binding_ledger.py`, add to `BindingLedger` after `get` (line 84):

```python
    def get_by_issue_id(self, issue_id: str) -> dict[str, Any] | None:
        self.verify()  # fail-closed: never return data from a tampered ledger
        match: dict[str, Any] | None = None
        for rec in self._store.read_all():
            p = rec.payload
            if p.get("kind") == BINDING_KIND and p.get("issue_id") == issue_id:
                match = p  # last verified binding for this issue wins
        return match
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/governance/test_binding_ledger.py -k get_by_issue_id -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/legis/governance/binding_ledger.py tests/governance/test_binding_ledger.py
git commit -m "feat(governance): add verified get_by_issue_id to BindingLedger"
```

### Task 10: Implement `evaluate_issue_closure`

**Files:**
- Create: `src/legis/governance/filigree_gate.py`
- Test: `tests/governance/test_filigree_gate.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/governance/test_filigree_gate.py`:

```python
from legis.governance.binding_ledger import BindingError
from legis.governance.filigree_gate import evaluate_issue_closure


class _FakeLedger:
    def __init__(self, record, raises=None):
        self._record = record
        self._raises = raises

    def get_by_issue_id(self, issue_id):
        if self._raises is not None:
            raise self._raises
        return self._record


def test_allows_when_verified_binding_exists():
    ledger = _FakeLedger({"issue_id": "ISSUE-7", "signoff_seq": 3})

    decision = evaluate_issue_closure(ledger, issue_id="ISSUE-7")

    assert decision["allowed"] is True
    assert decision["issue_id"] == "ISSUE-7"
    assert decision["evidence"]["signoff_seq"] == 3


def test_blocks_when_no_binding():
    ledger = _FakeLedger(None)

    decision = evaluate_issue_closure(ledger, issue_id="ISSUE-7")

    assert decision["allowed"] is False
    assert "no verified" in decision["reason"].lower()


def test_propagates_binding_integrity_error():
    ledger = _FakeLedger(None, raises=BindingError("tampered"))

    try:
        evaluate_issue_closure(ledger, issue_id="ISSUE-7")
    except BindingError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected BindingError to propagate")
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/governance/test_filigree_gate.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the decision function**

Create `src/legis/governance/filigree_gate.py`:

```python
"""Pure decision: may a Filigree issue be closed on legis governance evidence?

Fail-closed: an issue is closable only when the binding ledger holds a verified
``issue_binding`` record for it. A ledger integrity failure raises ``BindingError``
(the caller maps that to a server error); a missing binding returns a structured
not-allowed decision rather than an error.
"""

from __future__ import annotations

from typing import Any


def evaluate_issue_closure(ledger: Any, *, issue_id: str) -> dict[str, Any]:
    record = ledger.get_by_issue_id(issue_id)  # verifies the chain; may raise BindingError
    if record is None:
        return {
            "allowed": False,
            "issue_id": issue_id,
            "reason": "no verified governance binding for this issue",
            "evidence": None,
        }
    return {
        "allowed": True,
        "issue_id": issue_id,
        "reason": "verified governance binding present",
        "evidence": {
            "signoff_seq": record.get("signoff_seq"),
            "content_hash": record.get("content_hash"),
            "recorded_at": record.get("recorded_at"),
        },
    }
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/governance/test_filigree_gate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/legis/governance/filigree_gate.py tests/governance/test_filigree_gate.py
git commit -m "feat(governance): add Filigree closure-gate decision function"
```

### Task 11: Expose `GET /filigree/issues/{issue_id}/closure-gate`

**Files:**
- Modify: `src/legis/api/app.py`
- Test: `tests/api/test_combinations_api.py`

- [ ] **Step 1: Write the failing API tests**

Append to `tests/api/test_combinations_api.py` (mirror this file's existing app-construction helper that supplies a `binding_ledger`; if it builds the app via a fixture, reuse it):

```python
def test_closure_gate_404_when_ledger_disabled():
    from fastapi.testclient import TestClient

    from legis.api.app import create_app

    client = TestClient(create_app(binding_ledger=None))
    # An app with no binding ledger configured (no LEGIS_HMAC_KEY) reports 404.
    resp = client.get("/filigree/issues/ISSUE-7/closure-gate")
    assert resp.status_code in (404,)


def test_closure_gate_409_when_no_binding(closure_gate_client):
    # closure_gate_client: a TestClient whose app has an empty-but-enabled ledger.
    resp = closure_gate_client.get("/filigree/issues/ISSUE-UNBOUND/closure-gate")
    assert resp.status_code == 409
    assert resp.json()["allowed"] is False
```

Note: build `closure_gate_client` by constructing `create_app(binding_ledger=<empty BindingLedger>)` the same way other tests in this file inject governance components. If the file lacks such a helper, construct an in-memory `BindingLedger` (`AuditStore("sqlite://")`, a test `Clock`, a test key) with no records and pass it to `create_app`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/api/test_combinations_api.py -k closure_gate -q`
Expected: FAIL — route not registered.

- [ ] **Step 3: Add the endpoint**

In `src/legis/api/app.py`, ensure this import is present (add if missing):

```python
from fastapi.responses import JSONResponse
```

Add (near the other binding/`filigree` endpoints, after the `bind_issue` endpoint):

```python
    @app.get("/filigree/issues/{issue_id}/closure-gate")
    def filigree_closure_gate(issue_id: str) -> Any:
        from legis.governance.filigree_gate import evaluate_issue_closure

        if binding_ledger is None:
            raise HTTPException(status_code=404, detail="binding ledger not enabled")
        try:
            decision = evaluate_issue_closure(binding_ledger, issue_id=issue_id)
        except BindingError as exc:
            raise HTTPException(status_code=500, detail=f"binding integrity failure: {exc}")
        if not decision["allowed"]:
            return JSONResponse(status_code=409, content=decision)
        return decision
```

- [ ] **Step 4: Run the API tests**

Run: `uv run pytest tests/api/test_combinations_api.py -k closure_gate -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/legis/api/app.py tests/api/test_combinations_api.py
git commit -m "feat(api): expose Filigree closure-gate endpoint"
```

### Task 12: Expose MCP `filigree_closure_gate_get`

**Files:**
- Modify: `src/legis/mcp.py`
- Test: `tests/mcp/test_server.py`

- [ ] **Step 1: Write the failing MCP tests**

Append to `tests/mcp/test_server.py`:

```python
def test_filigree_closure_gate_get_is_listed():
    from legis.mcp import tool_definitions

    names = {t["name"] for t in tool_definitions()}
    assert "filigree_closure_gate_get" in names


def test_filigree_closure_gate_get_not_enabled_without_ledger(monkeypatch):
    from legis.mcp import build_runtime, call_tool

    monkeypatch.delenv("LEGIS_HMAC_KEY", raising=False)
    runtime = build_runtime("agent-1")

    result = call_tool(runtime, "filigree_closure_gate_get", {"issue_id": "ISSUE-7"})

    # NotEnabledError is mapped to an error envelope, not raised.
    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == "CELL_NOT_ENABLED"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/mcp/test_server.py -k filigree_closure_gate -q`
Expected: FAIL — tool not defined.

- [ ] **Step 3: Register the tool and runtime field**

In `src/legis/mcp.py`:

Add `"filigree_closure_gate_get"` to `_AGENT_TOOLS`.

Add a field to `McpRuntime` (after `wardline_artifact_key`, line 93):

```python
    binding_ledger: Any | None = None
```

In `build_runtime`, initialize alongside the other gates — add near line 126:

```python
    binding_ledger = None
```

Inside the `if hmac_key:` block (after `signoff_gate = SignoffGate(...)`, line 138), add:

```python
        from legis.governance.binding_ledger import BindingLedger

        binding_ledger = BindingLedger(
            AuditStore(os.environ.get("LEGIS_BINDING_DB", "sqlite:///legis-binding.db")),
            clock,
            key,
        )
```

Pass it into the returned `McpRuntime(...)`:

```python
        binding_ledger=binding_ledger,
```

In `tool_definitions()`, add (after the `git_rename_feed_get` entry from Task 8):

```python
        {
            "name": "filigree_closure_gate_get",
            "description": "Read whether legis holds verified binding evidence for closing a Filigree issue.",
            "inputSchema": _schema(["issue_id"], {"issue_id": string}),
        },
```

In the dispatch chain, add:

```python
        if name == "filigree_closure_gate_get":
            from legis.governance.filigree_gate import evaluate_issue_closure

            if runtime.binding_ledger is None:
                raise NotEnabledError("binding ledger not enabled")
            return _tool_result(
                evaluate_issue_closure(runtime.binding_ledger, issue_id=_require(args, "issue_id"))
            )
```

- [ ] **Step 4: Run the MCP tests**

Run: `uv run pytest tests/mcp/test_server.py -k filigree_closure_gate -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/legis/mcp.py tests/mcp/test_server.py
git commit -m "feat(mcp): expose filigree_closure_gate_get tool"
```

---

## Workstream 4 — Verification + docs

### Task 13: Full verification and documentation notes

**Files:**
- Modify: `docs/superpowers/specs/2026-06-02-not-yets-completion-design.md`
- Modify: `docs/superpowers/specs/2026-06-01-legis-roadmap-to-first-class.md`
- Modify: `docs/superpowers/specs/2026-06-03-legis-mcp-surface-design.md`

- [ ] **Step 1: Run the focused suites**

Run:

```bash
uv run pytest \
  tests/policy/test_evidence.py \
  tests/policy/test_honesty_gate.py \
  tests/policy/test_boundary_scan.py \
  tests/git/test_git_surface.py \
  tests/git/test_rename_feed.py \
  tests/api/test_git_api.py \
  tests/governance/test_binding_ledger.py \
  tests/governance/test_filigree_gate.py \
  tests/api/test_combinations_api.py \
  tests/mcp/test_server.py \
  tests/test_cli.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run static checks and the gate**

Run:

```bash
uv run mypy src/legis
uv run legis policy-boundary-check --root src --repo-root .
```

Expected: both PASS.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (no regressions; coverage gate from CI still satisfied).

- [ ] **Step 4: Append implementation notes**

Append this dated note under the relevant section of each of the three spec files listed above:

```markdown
> 2026-06-05 implementation note: The legis-side closeout landed the
> policy-boundary CI gate (static scanner converged onto the runtime evidence
> gate), the additive `/git/rename-feed` endpoint and `git_rename_feed_get` MCP
> tool, and the `/filigree/issues/{id}/closure-gate` endpoint and
> `filigree_closure_gate_get` MCP tool. Sibling-side consumption (Filigree
> calling the closure gate; Clarion re-pointing to the rename feed) is tracked
> as a follow-on spec.
```

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-06-02-not-yets-completion-design.md docs/superpowers/specs/2026-06-01-legis-roadmap-to-first-class.md docs/superpowers/specs/2026-06-03-legis-mcp-surface-design.md
git commit -m "docs: record legis home closeout implementation notes"
```

---

## Acceptance Criteria

- `legis policy-boundary-check --root src --repo-root .` exists, exits non-zero on stale/weak/drifted boundary evidence, and runs in CI after mypy.
- The static scanner and runtime gate share one evaluator; the trickability regression (`test_scan_rejects_policy_mention_outside_the_assert`) is closed and `test_honesty_gate.py` still passes.
- `GET /git/rename-feed` and MCP `git_rename_feed_get` return committed + optional working-tree rename evidence; `GET /git/renames` is unchanged.
- `GET /filigree/issues/{id}/closure-gate` and MCP `filigree_closure_gate_get` return a verified binding decision and block (409 / not-enabled) on missing evidence.
- Full suite, mypy, and the new gate pass.

## Out of Scope (follow-on specs)

- Filigree's `close_issue` / `api_close_issue` calling the closure gate.
- Clarion re-pointing from `/git/renames` to `/git/rename-feed`.
- Live cross-repo handshake integration tests.
- The RC2 MCP parity hardening (C1/C2/C3) — separately scoped on the `rc2-mcp-parity` branch.
