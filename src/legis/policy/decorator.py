"""In-code policy expression — a metadata-only decorator (elspeth ancestry).

Moves common governance patterns out of external config into the code they
govern. The decorator is a strict passthrough; its frozen metadata
(``__policy_boundary__``) carries behavioural *evidence* — ``source``,
``suppresses``, ``invariant``, ``test_ref``, ``test_fingerprint`` — not
vibe-justification. The honesty gate (``check_policy_boundary``) is what gives
the evidence teeth: it enforces that ``source`` is a well-formed citation
(URL, git SHA, or in-repo path), ``invariant`` is non-empty, ``test_ref``
resolves to a real test, and the test fingerprint matches.
Decoration-time checks catch misuse at the decoration site.
"""

from __future__ import annotations

import functools
import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from legis.canonical import content_hash

# A well-formed source citation: a URL, a git SHA (short..full), or an in-repo
# path with an extension and optional :line. Shape-checked, not filesystem-resolved.
#
# The path arm is POSIX-style: a Windows ``C:\...`` path is intentionally not
# matched (the backslash and drive-colon fall outside the character class). A
# bare ``filename.ext`` (e.g. ``README.md``) is intentionally accepted — because
# the gate shape-checks rather than resolving against the filesystem, it cannot
# distinguish a real root-level file from a coincidental ``word.ext``. The bar
# this enforces is rejecting multi-word / whitespace vibe strings, not proving
# the path exists.
_CITATION_RE = re.compile(r"^(https?://\S+|[0-9a-fA-F]{7,64}|[\w./-]+\.[A-Za-z0-9]+(:\d+)?)$")


def _is_citation(source: str) -> bool:
    return bool(_CITATION_RE.match(source))


@dataclass(frozen=True)
class PolicyBoundaryMetadata:
    source: str
    suppresses: tuple[str, ...]
    invariant: str
    qualname: str
    func: Callable[..., Any]
    test_ref: str | None = None
    test_fingerprint: str | None = None


def policy_boundary(
    *,
    source: str,
    suppresses: tuple[str, ...],
    invariant: str,
    test_ref: str | None = None,
    test_fingerprint: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    if not source or not source.strip():
        raise TypeError(
            "@policy_boundary requires a non-empty source; empty provenance "
            "is vibe-justification."
        )
    if not suppresses:
        raise TypeError(
            "@policy_boundary must declare at least one suppressed policy; "
            "an empty boundary is a whole-function exemption cloak."
        )
    if not invariant or not invariant.strip():
        raise TypeError(
            "@policy_boundary requires a non-empty invariant; empty evidence "
            "is vibe-justification."
        )

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if "__policy_boundary__" in getattr(func, "__dict__", {}):
            raise TypeError(
                f"@policy_boundary cannot be stacked on {func.__qualname__}; "
                "a function carries exactly one boundary metadata record."
            )
        metadata = PolicyBoundaryMetadata(
            source=source,
            suppresses=tuple(suppresses),
            invariant=invariant,
            qualname=func.__qualname__,
            func=func,
            test_ref=test_ref,
            test_fingerprint=test_fingerprint,
        )

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        wrapper.__policy_boundary__ = metadata  # type: ignore[attr-defined]
        return wrapper

    return decorator


def get_normalized_ast_str(source: str) -> str:
    import ast
    parsed = ast.parse(source)
    # Strip docstrings
    for node in ast.walk(parsed):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Module)):
            if node.body and isinstance(node.body[0], ast.Expr):
                val = node.body[0].value
                if isinstance(val, ast.Constant) and isinstance(val.value, str):
                    node.body.pop(0)
    return ast.dump(parsed)


def fingerprint(test_fn: Callable[..., Any]) -> str:
    """Content hash of a test function's source — the gate's anti-vibe teeth.

    A specific, unmodified test is genuinely hard to fake: you need the real
    test, unchanged since review. (This proves the test is *pinned*, not that it
    *meaningfully* exercises the boundary — see the plan's known limitations.)
    """
    try:
        source = inspect.getsource(test_fn)
    except (OSError, TypeError) as exc:
        raise OSError(f"Source code not available for test: {exc}") from exc

    # Normalize CRLF to LF to handle platform line ending differences
    source = source.replace("\r\n", "\n")

    try:
        import textwrap
        source = textwrap.dedent(source)
        normalized = get_normalized_ast_str(source)
        return content_hash(normalized)
    except Exception:
        return content_hash(source)


@dataclass(frozen=True)
class GateFinding:
    ok: bool
    reason: str


def check_policy_boundary(func: Callable[..., Any], resolver) -> GateFinding:
    """Honesty gate. The decorator's evidence must be real and current."""
    if hasattr(func, "__func__"):
        func = func.__func__

    meta = getattr(func, "__policy_boundary__", None)
    if meta is None:
        return GateFinding(False, "not a @policy_boundary function")

    # Scope / metadata-integrity: the record must belong to this function.
    wrapped = getattr(func, "__wrapped__", func)
    if wrapped is not meta.func:
        return GateFinding(False, "metadata transplant detected: function object identity mismatch")

    if meta.qualname != func.__qualname__:
        return GateFinding(False, f"scope/qualname mismatch: {meta.qualname!r}")
    if not meta.source or not meta.source.strip():
        return GateFinding(False, "no source citation: source is required")
    if not _is_citation(meta.source):
        return GateFinding(
            False,
            f"source is not a resolvable citation (URL, git SHA, or repo path): {meta.source!r}",
        )
    if not meta.invariant or not meta.invariant.strip():
        return GateFinding(False, "no invariant: a non-empty invariant statement is required")
    if not meta.test_ref:
        return GateFinding(False, "no behavioural evidence: test_ref is required")
    if not meta.test_fingerprint:
        return GateFinding(False, "no test_fingerprint to pin the evidence")
    test_fn = resolver(meta.test_ref)
    if test_fn is None:
        return GateFinding(False, f"test_ref {meta.test_ref!r} points to no test")

    try:
        fp = fingerprint(test_fn)
    except OSError as exc:
        return GateFinding(False, str(exc))

    if fp != meta.test_fingerprint:
        return GateFinding(False, "test drifted: fingerprint does not match")

    try:
        src = inspect.getsource(test_fn)
    except (OSError, TypeError) as exc:
        return GateFinding(False, f"source code not available for test: {exc}")

    import textwrap
    src = textwrap.dedent(src.replace("\r\n", "\n"))
    import ast
    try:
        parsed_test = ast.parse(src)
    except Exception:
        parsed_test = None

    boundary_names = {func.__name__, wrapped.__name__}

    def _name_targets(target) -> set[str]:
        import ast
        if isinstance(target, ast.Name):
            return {target.id}
        if isinstance(target, (ast.Tuple, ast.List)):
            names: set[str] = set()
            for item in target.elts:
                names.update(_name_targets(item))
            return names
        return set()

    def _is_boundary_call(node) -> bool:
        import ast
        return isinstance(node, ast.Call) and (
            (isinstance(node.func, ast.Name) and node.func.id in boundary_names)
            or (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in boundary_names
            )
        )

    def _contains_boundary_call(node) -> bool:
        import ast
        return any(_is_boundary_call(child) for child in ast.walk(node))

    def _contains_policy_reference(node) -> bool:
        import ast
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                if any(
                    re.search(r"\b" + re.escape(p) + r"\b", child.value)
                    for p in meta.suppresses
                ):
                    return True
            elif isinstance(child, ast.Name) and child.id in meta.suppresses:
                return True
        return False

    shadowed = False
    call_result_names: set[str] = set()
    func_called = False
    if parsed_test is not None:
        for node in ast.walk(parsed_test):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
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
                targets = set().union(*(_name_targets(target) for target in node.targets))
                if targets & boundary_names:
                    shadowed = True
                    break
                if _contains_boundary_call(node.value):
                    call_result_names.update(targets)
            elif isinstance(node, ast.AnnAssign):
                targets = _name_targets(node.target)
                if targets & boundary_names:
                    shadowed = True
                    break
                if node.value is not None and _contains_boundary_call(node.value):
                    call_result_names.update(targets)
            elif isinstance(node, ast.AugAssign):
                if _name_targets(node.target) & boundary_names:
                    shadowed = True
                    break
            elif isinstance(node, ast.For):
                if _name_targets(node.target) & boundary_names:
                    shadowed = True
                    break
            if _is_boundary_call(node):
                func_called = True
    else:
        func_called = False

    if shadowed:
        return GateFinding(False, "test shadows the boundary function name")

    if not func_called:
        return GateFinding(False, "test does not appear to exercise the boundary")

    policy_referenced = False
    if parsed_test is not None:
        for node in ast.walk(parsed_test):
            if not isinstance(node, ast.Assert):
                continue
            has_boundary_evidence = _contains_boundary_call(node) or any(
                isinstance(child, ast.Name) and child.id in call_result_names
                for child in ast.walk(node)
            )
            if has_boundary_evidence and _contains_policy_reference(node):
                policy_referenced = True
                break
    else:
        policy_referenced = any(p in src for p in meta.suppresses)

    if not policy_referenced:
        return GateFinding(
            False,
            "test does not assert a suppressed policy against the boundary result",
        )

    return GateFinding(True, f"ok (invariant: {meta.invariant})")
