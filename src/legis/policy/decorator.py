"""In-code policy expression — a metadata-only decorator (elspeth ancestry).

Moves common governance patterns out of external config into the code they
govern. The decorator is a strict passthrough; its frozen metadata
(``__policy_boundary__``) carries behavioural *evidence* — ``source``,
``suppresses``, ``invariant``, ``test_ref``, ``test_fingerprint`` — not
vibe-justification. The honesty gate (``check_policy_boundary``) is what gives
the evidence teeth. Decoration-time checks catch misuse at the decoration site.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from legis.canonical import content_hash


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
    if not suppresses:
        raise TypeError(
            "@policy_boundary must declare at least one suppressed policy; "
            "an empty boundary is a whole-function exemption cloak."
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


def fingerprint(test_fn: Callable[..., Any]) -> str:
    """Content hash of a test function's source — the gate's anti-vibe teeth.

    A specific, unmodified test is genuinely hard to fake: you need the real
    test, unchanged since review. (This proves the test is *pinned*, not that it
    *meaningfully* exercises the boundary — see the plan's known limitations.)
    """
    return content_hash(inspect.getsource(test_fn))


@dataclass(frozen=True)
class GateFinding:
    ok: bool
    reason: str


def check_policy_boundary(func: Callable[..., Any], resolver) -> GateFinding:
    """Honesty gate. The decorator's evidence must be real and current."""
    meta = getattr(func, "__policy_boundary__", None)
    if meta is None:
        return GateFinding(False, "not a @policy_boundary function")
    # Scope / metadata-integrity: the record must belong to this function.
    if meta.qualname != func.__qualname__:
        return GateFinding(False, f"scope/qualname mismatch: {meta.qualname!r}")
    if not meta.test_ref:
        return GateFinding(False, "no behavioural evidence: test_ref is required")
    if not meta.test_fingerprint:
        return GateFinding(False, "no test_fingerprint to pin the evidence")
    test_fn = resolver(meta.test_ref)
    if test_fn is None:
        return GateFinding(False, f"test_ref {meta.test_ref!r} points to no test")
    if fingerprint(test_fn) != meta.test_fingerprint:
        return GateFinding(False, "test drifted: fingerprint does not match")
    src = inspect.getsource(test_fn)
    if func.__name__ not in src:
        return GateFinding(False, "test does not appear to exercise the boundary")
    if not any(p in src for p in meta.suppresses):
        return GateFinding(False, "test does not assert any suppressed policy")
    return GateFinding(True, "ok")
