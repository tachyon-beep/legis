# Legis Sprint 4 — Agent-programmable policy grammar Implementation Plan

> **Status:** ✅ implemented 2026-06-02 — all tasks complete, 104 tests green. Half 1 is done: legis is a first-class tool in its own right.

> **For agentic workers:** REQUIRED SUB-SKILL: TDD / executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Turn legis from fixed-rule enforcement into a **grammar** — one shared contract for what a policy boundary *is* and what fail-closed means, with an open, agent-authored set of boundary types and builtins as defaults (WP-4.1) — plus **in-code policy expression**: a `@policy_boundary` decorator carrying behavioural evidence, gated by honesty checks (WP-4.2). Serves roadmap §1.4.

**Architecture:** Two modules under `legis/policy/`. The grammar (`grammar.py`) is a registry of `BoundaryType` seams (same shape as Wardline's `TaintSourceProvider` / Clarion `Transport`); `PolicyGrammar.evaluate` returns CLEAR / VIOLATION / **UNKNOWN**, and **fails closed to UNKNOWN** on anything it cannot prove — an unregistered policy, a boundary that returns UNKNOWN, *or a boundary that raises/returns garbage*. The decorator (`decorator.py`) is metadata-only passthrough (elspeth `@trust_boundary` ancestry — effects, not vocabulary); its honesty gate's real teeth are a `inspect.getsource` content-hash fingerprint that detects test drift. Builtins cannot be shadowed by agent registrations.

**Tech Stack:** Python 3.12, FastAPI, stdlib `inspect`/`functools`, SQLAlchemy/SQLite (UNKNOWN_POLICY events to the governance trail), pytest (warnings-as-errors).

---

## Locked design decisions (advisor-reviewed — do not reopen)

1. **Untrusted boundary code fails closed to UNKNOWN.** Agent-authored `BoundaryType.evaluate` is arbitrary in-process code (zero human config = no sandbox). `PolicyGrammar.evaluate` wraps every call: any raised exception, or any return that is not a `(PolicyResult, str)` with a real `PolicyResult`, becomes `UNKNOWN` with the failure as provenance detail. Never propagate, never CLEAR. This is the §1.4 invariant "a boundary the engine cannot prove emits UNKNOWN_POLICY, never a false-green" — an exception *is* "cannot prove."
2. **Builtins cannot be shadowed.** `register()` on a name already present **raises** `PolicyConflictError`. An agent weakening a builtin by registering a permissive same-named boundary is "trusted-by-fiat," which §1.4 rejects. Who may (re)define a policy is the security decision.
3. **`BoundaryType` (grammar) vs `@policy_boundary` (decorator).** The grammar protocol is `BoundaryType` (spec term: "agents define new policy boundary *types*"); the decorator marks code as a governed boundary. Distinct names, sibling modules.
4. **UNKNOWN is an honest answer recorded to the trail, not an error.** `POST /policy/evaluate` returns HTTP 200 for all three results; on UNKNOWN it records an `UNKNOWN_POLICY` event (with `provenance_gap: true`) to the append-only governance trail. The no-false-green proof is that UNKNOWN ≠ CLEAR *and* leaves a recorded provenance gap.
5. **The honesty gate's teeth are the fingerprint.** A `content_hash(inspect.getsource(test))` pins a specific, unmodified test — genuinely hard to fake. The secondary "test source references the function + a suppressed policy" check is a foolable heuristic, included but not over-claimed.

## Known limitations (honest disclosure — record, don't build)

- **The suppression has no analyzer-consumer yet.** No legis static analyzer reads `__policy_boundary__` to actually narrow suppression scope. "Suppresses only its declared scope" is enforced as **metadata integrity** (qualname match defends against metadata transplant), not real code-scope dataflow. The decorator + gate ship; the consuming analyzer is deferred, like the judge `LLMClient` seam.
- **The gate proves "a specific real test is pinned and unmodified," not "the test meaningfully exercises the boundary."** Fingerprint drift is the real mechanism; the "exercises" check is a heuristic.
- **The injected `resolver` is a trust boundary.** A permissive resolver (returns a dummy that always matches) voids the gate — same posture as the judge seam and the HMAC key.
- **The YAML external allowlist is positioning, not a build target.** The decorator reduces its surface; "reserved for one-offs" is a relationship statement. No allowlist subsystem is built here.
- **Judge / Wardline convergence is Sprint 6.** This sprint ships the grammar as substrate; wiring it into the judge's override evaluation and the suite trust-vocabulary is deferred (and gated on siblings).
- **The honesty gate has no caller yet.** `check_policy_boundary` is unit-proven, but nothing in legis *discovers* `@policy_boundary`-decorated functions and runs the gate over them (no CI runner, no endpoint, no enforcement path). The gate *logic* ships and is tested; invocation over a codebase is production wiring, deferred — so "a stale decorator fails the gate" holds for the function, not yet for the repo. (Distinct from the no-analyzer-consumes-`suppresses` note above.)
- **`/policy/evaluate` is a query surface, not a commit surface (deliberate).** CLEAR and VIOLATION record nothing; only UNKNOWN records a provenance-gap event. A VIOLATION leaving no trail is *not* a silent path — the violation is returned honestly; actual enforcement recording happens through the Sprint 2/3 override/protected paths. Evaluate answers "what does the grammar say?", it does not enforce.
- **`suppresses` is not validated against the registered grammar.** A decorator may declare `suppresses=("policy-that-doesn't-exist",)` and pass the gate (harmless today — suppressing a non-policy does nothing). elspeth constrained this to a `Literal` rule set; legis widened to free strings deliberately. When the suppression-consuming analyzer lands, it owes a check that suppressed names are registered policies.
- **UNKNOWN_POLICY events accumulate per call.** Polling the same unprovable policy N times writes N append-only events (by design). A consumer counting provenance gaps must dedup by policy or treat them as a stream — same shape as the Sprint 2 record-both denominator note.

---

## File structure

| File | Responsibility |
|---|---|
| `src/legis/policy/__init__.py` | package docstring |
| `src/legis/policy/grammar.py` | `PolicyResult`; `PolicyEvaluation`; `BoundaryType` Protocol; `PolicyGrammar`; `PolicyConflictError`; builtins (`AllowlistBoundary`); `default_grammar()` |
| `src/legis/policy/decorator.py` | `PolicyBoundaryMetadata`; `policy_boundary`; `GateFinding`; `fingerprint`; `check_policy_boundary` |
| `src/legis/enforcement/engine.py` | +`record_event(payload)` |
| `src/legis/api/app.py` | inject `grammar`; `POST /policy/evaluate` (records UNKNOWN_POLICY) |
| tests under `tests/policy/` and `tests/api/` | one per behaviour |

---

## Task 1: Grammar value types + fail-closed registry (WP-4.1)

**Files:** Create `src/legis/policy/__init__.py`, `src/legis/policy/grammar.py`; Test `tests/policy/__init__.py`, `tests/policy/test_grammar.py`

- [ ] **Step 1 — failing test**

```python
import pytest

from legis.policy.grammar import (
    AllowlistBoundary,
    BoundaryType,
    PolicyConflictError,
    PolicyGrammar,
    PolicyResult,
    default_grammar,
)


def test_unregistered_policy_is_unknown_not_clear():
    ev = PolicyGrammar().evaluate("nonexistent", {})
    assert ev.result is PolicyResult.UNKNOWN
    assert ev.provenance_gap is True
    assert ev.result is not PolicyResult.CLEAR


def test_allowlist_builtin_clears_violates_and_unknowns():
    g = PolicyGrammar()
    g.register(AllowlistBoundary("imports", frozenset({"json", "os"})))
    assert g.evaluate("imports", {"value": "json"}).result is PolicyResult.CLEAR
    assert g.evaluate("imports", {"value": "socket"}).result is PolicyResult.VIOLATION
    # Missing provenance → cannot prove → UNKNOWN, not CLEAR.
    miss = g.evaluate("imports", {})
    assert miss.result is PolicyResult.UNKNOWN
    assert miss.provenance_gap is True


def test_agent_can_register_a_new_boundary_type_zero_config():
    g = PolicyGrammar()

    class NoTodoBoundary:
        name = "no-todo"

        def evaluate(self, target):
            text = target.get("text", "")
            if "TODO" in text:
                return (PolicyResult.VIOLATION, "contains TODO")
            return (PolicyResult.CLEAR, "clean")

    g.register(NoTodoBoundary())
    assert g.evaluate("no-todo", {"text": "x TODO y"}).result is PolicyResult.VIOLATION
    assert g.evaluate("no-todo", {"text": "clean"}).result is PolicyResult.CLEAR


def test_builtins_cannot_be_shadowed():
    g = default_grammar()
    name = next(iter(g.registered()))

    class Permissive:
        def evaluate(self, target):
            return (PolicyResult.CLEAR, "always ok")

    p = Permissive()
    p.name = name
    with pytest.raises(PolicyConflictError):
        g.register(p)


def test_a_boundary_that_raises_fails_closed_to_unknown():
    g = PolicyGrammar()

    class Exploding:
        name = "boom"

        def evaluate(self, target):
            raise RuntimeError("boundary blew up")

    g.register(Exploding())
    ev = g.evaluate("boom", {})
    assert ev.result is PolicyResult.UNKNOWN     # never propagates, never CLEAR
    assert ev.provenance_gap is True
    assert "boundary blew up" in ev.detail


def test_a_boundary_returning_garbage_fails_closed_to_unknown():
    g = PolicyGrammar()

    class Garbage:
        name = "garbage"

        def evaluate(self, target):
            return "definitely not a (PolicyResult, str)"

    g.register(Garbage())
    assert g.evaluate("garbage", {}).result is PolicyResult.UNKNOWN
```

- [ ] **Step 2 — run, expect FAIL** (module missing).
- [ ] **Step 3 — implement** `policy/__init__.py` (`"""Agent-programmable policy grammar (Sprint 4)."""`) and `policy/grammar.py`:

```python
"""The policy grammar — one shared contract, an open agent-authored instance set.

The grammar defines what a policy boundary *is* (a ``BoundaryType`` that, given a
target, returns CLEAR / VIOLATION / UNKNOWN) and what fail-closed means. Boundary
types are registered: builtins as defaults, agents adding their own with zero
human config. Soundness is inherited, not waived — anything the engine cannot
prove (an unregistered policy, a boundary that returns UNKNOWN, or one that
raises / returns garbage) yields UNKNOWN with a provenance gap, never a
false-green. Same seam shape as Wardline's ``TaintSourceProvider`` and Clarion's
``Transport``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class PolicyResult(str, Enum):
    CLEAR = "CLEAR"          # boundary proven satisfied
    VIOLATION = "VIOLATION"  # boundary proven violated — a policy fires
    UNKNOWN = "UNKNOWN"      # cannot prove either way — honest gap, fail-closed


@dataclass(frozen=True)
class PolicyEvaluation:
    policy: str
    result: PolicyResult
    detail: str
    provenance_gap: bool


@runtime_checkable
class BoundaryType(Protocol):
    name: str

    def evaluate(self, target: Mapping[str, Any]) -> tuple[PolicyResult, str]: ...


class PolicyConflictError(RuntimeError):
    """A registration would shadow an already-registered boundary type."""


class PolicyGrammar:
    def __init__(self) -> None:
        self._boundaries: dict[str, BoundaryType] = {}

    def register(self, boundary: BoundaryType) -> None:
        name = boundary.name
        if name in self._boundaries:
            raise PolicyConflictError(
                f"policy {name!r} is already registered; boundaries are immutable "
                "(an agent may not shadow a builtin or another boundary)"
            )
        self._boundaries[name] = boundary

    def registered(self) -> frozenset[str]:
        return frozenset(self._boundaries)

    def evaluate(self, policy: str, target: Mapping[str, Any]) -> PolicyEvaluation:
        boundary = self._boundaries.get(policy)
        if boundary is None:
            return PolicyEvaluation(
                policy, PolicyResult.UNKNOWN,
                f"no boundary type registered for policy {policy!r}", True,
            )
        try:
            raw = boundary.evaluate(target)
            result, detail = raw  # may raise ValueError/TypeError if malformed
            if not isinstance(result, PolicyResult):
                raise TypeError(f"boundary returned non-PolicyResult: {result!r}")
        except Exception as exc:  # untrusted in-process code — fail closed
            return PolicyEvaluation(
                policy, PolicyResult.UNKNOWN,
                f"boundary could not prove policy {policy!r}: {exc}", True,
            )
        return PolicyEvaluation(
            policy, result, str(detail), result is PolicyResult.UNKNOWN
        )


class AllowlistBoundary:
    """Builtin: CLEAR iff ``target['value']`` is allowlisted; missing value → UNKNOWN."""

    def __init__(self, name: str, allowed: frozenset[str]) -> None:
        self.name = name
        self._allowed = allowed

    def evaluate(self, target: Mapping[str, Any]) -> tuple[PolicyResult, str]:
        if "value" not in target:
            return (PolicyResult.UNKNOWN, "target has no 'value' to evaluate")
        value = target["value"]
        if value in self._allowed:
            return (PolicyResult.CLEAR, f"{value!r} is allowlisted")
        return (PolicyResult.VIOLATION, f"{value!r} is not allowlisted")


def default_grammar() -> PolicyGrammar:
    """A grammar preloaded with builtin boundary types (the defaults)."""
    g = PolicyGrammar()
    g.register(AllowlistBoundary("import-allowlist", frozenset({"json", "os", "sys"})))
    return g
```

- [ ] **Step 4 — run, expect PASS** (6 tests).
- [ ] **Step 5 — commit:** `feat(policy): fail-closed policy grammar + builtins (WP-4.1)`

---

## Task 2: record_event on the engine + UNKNOWN_POLICY at the API (WP-4.1)

**Files:** Modify `src/legis/enforcement/engine.py`, `src/legis/api/app.py`; Test `tests/api/test_policy_api.py`

- [ ] **Step 1 — failing test**

```python
from fastapi.testclient import TestClient

from legis.api.app import create_app
from legis.clock import FixedClock
from legis.enforcement.engine import EnforcementEngine
from legis.policy.grammar import AllowlistBoundary, PolicyGrammar
from legis.store.audit_store import AuditStore


def _app(tmp_path):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    eng = EnforcementEngine(store, FixedClock("2026-06-02T12:00:00+00:00"))
    grammar = PolicyGrammar()
    grammar.register(AllowlistBoundary("imports", frozenset({"json"})))
    return TestClient(create_app(enforcement=eng, grammar=grammar))


def test_clear_evaluation_records_no_event(tmp_path):
    c = _app(tmp_path)
    resp = c.post("/policy/evaluate", json={"policy": "imports", "target": {"value": "json"}})
    assert resp.status_code == 200
    assert resp.json()["result"] == "CLEAR"
    assert resp.json()["provenance_gap"] is False
    assert c.get("/overrides").json() == []   # nothing recorded for a clean pass


def test_unknown_policy_is_not_a_pass_and_records_a_provenance_gap(tmp_path):
    c = _app(tmp_path)
    resp = c.post("/policy/evaluate", json={"policy": "unregistered", "target": {}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"] == "UNKNOWN"          # never CLEAR
    assert body["provenance_gap"] is True
    trail = c.get("/overrides").json()
    assert len(trail) == 1
    assert trail[0]["event"] == "UNKNOWN_POLICY"
    assert trail[0]["policy"] == "unregistered"
    assert trail[0]["provenance_gap"] is True
    assert trail[0]["recorded_at"] == "2026-06-02T12:00:00+00:00"


def test_violation_is_reported(tmp_path):
    c = _app(tmp_path)
    resp = c.post("/policy/evaluate", json={"policy": "imports", "target": {"value": "socket"}})
    assert resp.json()["result"] == "VIOLATION"
```

- [ ] **Step 2 — run, expect FAIL** (`create_app` rejects `grammar`).
- [ ] **Step 3 — implement.** In `engine.py` add:

```python
    def record_event(self, payload: dict) -> int:
        """Append a raw governance event (e.g. UNKNOWN_POLICY) to the trail.

        Stamps ``recorded_at`` from the injected clock when the caller omits it,
        so non-override governance events share the one append-only trail.
        """
        body = {**payload}
        body.setdefault("recorded_at", self._clock.now_iso())
        return self._store.append(body)
```

In `app.py`: import `from legis.policy.grammar import PolicyGrammar, PolicyResult, default_grammar`; add `grammar: PolicyGrammar | None = None` to `create_app`; add a lazy accessor + input model + route:

```python
class PolicyEvalIn(BaseModel):
    policy: str
    target: dict = {}
```

```python
    def grammar_() -> PolicyGrammar:
        if state["grammar"] is None:
            state["grammar"] = default_grammar()
        return state["grammar"]
```

(add `"grammar": grammar` to `state`)

```python
    @app.post("/policy/evaluate")
    def policy_evaluate(body: PolicyEvalIn) -> dict:
        ev = grammar_().evaluate(body.policy, body.target)
        if ev.result is PolicyResult.UNKNOWN:
            # Honest event + provenance gap — never a silent false-green.
            engine().record_event({
                "event": "UNKNOWN_POLICY",
                "policy": ev.policy,
                "detail": ev.detail,
                "provenance_gap": True,
            })
        return {
            "policy": ev.policy,
            "result": ev.result.value,
            "detail": ev.detail,
            "provenance_gap": ev.provenance_gap,
        }
```

- [ ] **Step 4 — run, expect PASS.** Then full suite.
- [ ] **Step 5 — commit:** `feat(api): /policy/evaluate records UNKNOWN_POLICY, never false-green (WP-4.1)`

---

## Task 3: `@policy_boundary` decorator + metadata (WP-4.2)

**Files:** Create `src/legis/policy/decorator.py`; Test `tests/policy/test_decorator.py`

- [ ] **Step 1 — failing test**

```python
import pytest

from legis.policy.decorator import PolicyBoundaryMetadata, policy_boundary


def test_decorator_is_passthrough_and_attaches_metadata():
    @policy_boundary(
        source="external webhook payload",
        suppresses=("no-eval",),
        invariant="rejects non-dict payloads",
        test_ref="tests.policy.test_decorator::test_handler_rejects",
        test_fingerprint="abc123",
    )
    def handler(payload):
        return payload["ok"]

    assert handler({"ok": 42}) == 42  # strict passthrough
    meta = handler.__policy_boundary__
    assert isinstance(meta, PolicyBoundaryMetadata)
    assert meta.suppresses == ("no-eval",)
    assert meta.qualname.endswith("handler")
    assert meta.test_ref.endswith("test_handler_rejects")


def test_empty_suppresses_is_rejected_at_decoration():
    with pytest.raises(TypeError):
        @policy_boundary(source="s", suppresses=(), invariant="i")
        def f(x):
            return x


def test_stacking_is_rejected():
    with pytest.raises(TypeError):
        @policy_boundary(source="s", suppresses=("p",), invariant="i")
        @policy_boundary(source="s", suppresses=("p",), invariant="i")
        def f(x):
            return x
```

- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement** `decorator.py` (metadata + decorator only; gate in Task 4):

```python
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
```

- [ ] **Step 4 — run, expect PASS.**
- [ ] **Step 5 — commit:** `feat(policy): in-code @policy_boundary decorator (WP-4.2)`

---

## Task 4: honesty gate — fingerprint drift is the teeth (WP-4.2)

**Files:** Modify `src/legis/policy/decorator.py`; Test `tests/policy/test_honesty_gate.py`

- [ ] **Step 1 — failing test** (includes the discriminating drift test + test_ref=None rejection)

```python
from legis.policy.decorator import (
    check_policy_boundary,
    fingerprint,
    policy_boundary,
)


# A real, resolvable "test" function the gate will fingerprint.
def fake_boundary_test():
    # references the decorated function name 'handler' and the policy 'no-eval'
    handler_under_test = "handler exercises no-eval boundary"
    assert "no-eval" in handler_under_test


def resolver(ref):
    return {"tests::fake": fake_boundary_test}.get(ref)


def _decorate(test_fingerprint):
    @policy_boundary(
        source="external payload",
        suppresses=("no-eval",),
        invariant="rejects bad input",
        test_ref="tests::fake",
        test_fingerprint=test_fingerprint,
    )
    def handler(payload):
        return payload

    return handler


def test_gate_passes_with_a_pinned_unmodified_test():
    good = fingerprint(fake_boundary_test)
    finding = check_policy_boundary(_decorate(good), resolver)
    assert finding.ok is True, finding.reason


def test_gate_fails_on_fingerprint_drift():
    # THE discriminating test: a stale fingerprint means the test changed after
    # review — behavioural evidence no longer pinned.
    finding = check_policy_boundary(_decorate("stale-old-hash"), resolver)
    assert finding.ok is False
    assert "drift" in finding.reason.lower()


def test_gate_rejects_missing_test_ref_as_vibe_justification():
    @policy_boundary(source="s", suppresses=("no-eval",), invariant="i")
    def handler(payload):
        return payload

    finding = check_policy_boundary(handler, resolver)
    assert finding.ok is False
    assert "test_ref" in finding.reason


def test_gate_fails_when_test_ref_resolves_to_nothing():
    good = fingerprint(fake_boundary_test)
    h = _decorate(good)
    finding = check_policy_boundary(h, lambda ref: None)
    assert finding.ok is False


def test_gate_fails_on_metadata_transplant():
    # qualname mismatch = metadata copied onto a different function.
    good = fingerprint(fake_boundary_test)
    h = _decorate(good)
    object.__setattr__(h.__policy_boundary__, "qualname", "some.other.func")
    finding = check_policy_boundary(h, resolver)
    assert finding.ok is False
    assert "scope" in finding.reason.lower() or "qualname" in finding.reason.lower()
```

- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement** in `decorator.py`:

```python
import inspect

from legis.canonical import content_hash


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
    # Scope/metadata-integrity: the record must belong to this function.
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
```

- [ ] **Step 4 — run, expect PASS.** Then full suite, zero warnings.
- [ ] **Step 5 — commit:** `feat(policy): honesty gate — fingerprint drift detection (WP-4.2)`

---

## Task 5: docs + scope disclosure

- [ ] **Step 1:** add `**Status:** ✅ implemented 2026-06-02` under Sprint 4 in `docs/superpowers/plans/2026-06-01-legis-implementation-sprints.md` (note: *— End of Half 1: legis is a first-class tool in its own right. —* now holds), and `**Status:** ✅ implemented` on this plan's header. The known-limitations section above is the scope disclosure.
- [ ] **Step 2:** full suite green, zero warnings.
- [ ] **Step 3 — commit:** `docs: mark Sprint 4 policy grammar complete — Half 1 done`

---

## Self-review — WP coverage

| WP | Exit criterion | Proven by |
|---|---|---|
| 4.1 grammar | agent defines a new policy type, zero config | Task 1 (`test_agent_can_register...`) |
| 4.1 grammar | unprovable boundary emits UNKNOWN_POLICY, not a pass | Task 1 (raises/garbage/unregistered → UNKNOWN), Task 2 (records UNKNOWN_POLICY event) |
| 4.1 grammar | builtins + agent rules share one grammar; no shadowing | Task 1 (`test_builtins_cannot_be_shadowed`) |
| 4.2 in-code | passing behavioural-evidence gate; stale decorator fails | Task 4 (`test_gate_passes...`, `test_gate_fails_on_fingerprint_drift`) |
| 4.2 in-code | scope match; vibe-justification rejected | Task 4 (transplant, missing test_ref) |

Advisor decisions 1–5 each map to a test; the WP-4.1 fail-closed discriminator is Task 1's raises→UNKNOWN; the WP-4.2 teeth discriminator is Task 4's fingerprint-drift.
