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
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield from _walk_without_nested_definitions(child)


def evaluate_test_evidence(
    test_fn: ast.FunctionDef | ast.AsyncFunctionDef | None,
    boundary_names: set[str],
    suppresses: tuple[str, ...],
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
                # NB: do NOT skip `node is test_fn`. The runtime gate checks the
                # test function's own parameters for boundary-name shadowing
                # (e.g. `def test_x(guarded):` where `guarded` is the boundary).
                # The name check below is harmless for the test fn (its name is
                # never a boundary name), and the arg check below must run on it.
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

    # Policy co-occurrence (runtime semantics): a policy reference must co-occur
    # with boundary evidence inside the same assert, AND the boundary result
    # must be the assertion SUBJECT — it must appear in the assert's test
    # condition, not merely in the assert message. Otherwise a test asserting
    # something unrelated, with the boundary result and policy name dropped into
    # the message string, would falsely satisfy the gate (Q-M8). The policy
    # reference itself may still live in the message (the established honesty
    # pattern names the policy there). Reaching here implies func_called is
    # True, hence test_fn is not None.
    assert test_fn is not None
    policy_referenced = False
    for node in ast.walk(test_fn):
        if not isinstance(node, ast.Assert):
            continue
        boundary_in_subject = _contains_boundary_call(node.test, boundary_names) or any(
            isinstance(child, ast.Name) and child.id in call_result_names
            for child in ast.walk(node.test)
        )
        if boundary_in_subject and _contains_policy_reference(node, suppresses):
            policy_referenced = True
            break

    if not policy_referenced:
        return EvidenceResult(
            False,
            "policy_not_asserted",
            "test does not assert a suppressed policy against the boundary result",
        )
    return EvidenceResult(True, "ok", "ok")
