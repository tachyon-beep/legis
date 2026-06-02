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
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


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
