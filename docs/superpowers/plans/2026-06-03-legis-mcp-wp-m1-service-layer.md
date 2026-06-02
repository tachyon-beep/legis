# WP-M1: Transport-Agnostic Service Layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the cross-cutting governance logic currently inlined in the FastAPI route closures into a transport-agnostic `legis.service` layer, with a domain-exception vocabulary, so the same logic can later be driven by the MCP stdio adapter (WP-M3) as well as HTTP — behavior-preserving, existing suite stays green.

**Architecture:** Today `create_app` (`src/legis/api/app.py`) holds governance decision logic in route closures that raise `HTTPException` — i.e. the logic is welded to the HTTP transport. This WP pulls the decision logic (resolve-then-key, tamper-verified record reads, override-rate, the override submit orchestration) into `legis.service`, which raises *domain* exceptions (`ServiceError` subclasses) and returns the existing result objects. The FastAPI handlers become thin: call the service function, translate `ServiceError → HTTPException`. No new endpoints, no behavior change.

**Tech Stack:** Python 3.12, FastAPI (adapter only), pytest. Stdlib only — no new dependency.

**Spec:** `docs/superpowers/specs/2026-06-03-legis-mcp-surface-design.md` (§Architecture; WP-M1).

**Baseline:** 214 tests green at HEAD `ffbda95`. The whole-suite gate `uv run pytest -q` must stay green after every task.

---

## File structure

- **Create** `src/legis/service/__init__.py` — package marker + re-exports of the public service API.
- **Create** `src/legis/service/errors.py` — transport-agnostic domain exception vocabulary.
- **Create** `src/legis/service/governance.py` — the extracted decision functions.
- **Create** `tests/service/test_errors.py`, `tests/service/test_governance.py` — isolated unit tests (no FastAPI, no TestClient) proving the service layer works without a transport.
- **Modify** `src/legis/api/app.py` — refactor the affected route closures to delegate to `legis.service` and translate domain exceptions to HTTP.

What this WP deliberately does **not** touch (extracted later, per their own WPs as M3/M4 need them): the protected-override, signoff-request/sign, bind-issue, policy-evaluate, wardline, and git/check read handlers stay as-is. M1 establishes the seam and moves only the cross-cutting logic plus the one write path (`POST /overrides`) that M3's vertical slice rebuilds over MCP.

---

### Task 1: Domain exception vocabulary

**Files:**
- Create: `src/legis/service/__init__.py`
- Create: `src/legis/service/errors.py`
- Test: `tests/service/__init__.py`, `tests/service/test_errors.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/service/test_errors.py
import pytest

from legis.service.errors import (
    AuditIntegrityError,
    NotEnabledError,
    NotFoundError,
    ServiceError,
)


def test_all_service_errors_are_serviceerror_subclasses():
    for cls in (AuditIntegrityError, NotEnabledError, NotFoundError):
        assert issubclass(cls, ServiceError)


def test_service_error_carries_a_message():
    err = NotEnabledError("protected cell not enabled")
    assert str(err) == "protected cell not enabled"


def test_service_errors_are_distinct_types():
    # An adapter switches on type, not message text.
    assert AuditIntegrityError is not NotEnabledError
    with pytest.raises(AuditIntegrityError):
        raise AuditIntegrityError("tampered")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/service/test_errors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'legis.service'`

- [ ] **Step 3: Create the package and error vocabulary**

```python
# src/legis/service/__init__.py
"""Transport-agnostic governance service layer.

The decision logic that both the HTTP adapter (``legis.api.app``) and the MCP
adapter (``legis.mcp``, WP-M3) drive. Functions here raise ``ServiceError``
subclasses — never ``HTTPException`` and never a JSON-RPC error — so each
transport adapter owns its own error translation.
"""

from legis.service.errors import (
    AuditIntegrityError,
    NotEnabledError,
    NotFoundError,
    ServiceError,
)

__all__ = [
    "ServiceError",
    "AuditIntegrityError",
    "NotEnabledError",
    "NotFoundError",
]
```

```python
# src/legis/service/errors.py
"""Domain exceptions for the service layer.

Adapters switch on the exception *type*, never on message text. The HTTP
adapter maps these to status codes; the MCP adapter maps them to ``isError``
result envelopes (WP-M3).
"""

from __future__ import annotations


class ServiceError(Exception):
    """Base for every governance service error."""


class AuditIntegrityError(ServiceError):
    """A verified trail failed tamper verification — non-retryable.

    HTTP maps this to 500; MCP maps it to ``error_code: AUDIT_INTEGRITY_FAILURE``.
    """


class NotEnabledError(ServiceError):
    """A required gate/dependency is not wired on this deployment."""


class NotFoundError(ServiceError):
    """A referenced resource (record, request, PR) does not exist."""
```

```python
# tests/service/__init__.py
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/service/test_errors.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/legis/service/__init__.py src/legis/service/errors.py tests/service/__init__.py tests/service/test_errors.py
git commit -m "feat(service): add transport-agnostic domain exception vocabulary (WP-M1)"
```

---

### Task 2: Extract `resolve_for_record` (the resolve-then-key boundary)

The closure currently at `src/legis/api/app.py` (`def resolve_for_record(locator)`, the one resolve-then-key boundary) moves verbatim into the service layer, taking the optional `IdentityResolver` explicitly instead of closing over it.

**Files:**
- Modify: `src/legis/service/governance.py` (create)
- Modify: `src/legis/service/__init__.py`
- Modify: `src/legis/api/app.py`
- Test: `tests/service/test_governance.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/service/test_governance.py
from legis.identity.entity_key import EntityKey
from legis.service.governance import resolve_for_record


class _FakeResult:
    def __init__(self, entity_key, alive, content_hash, lineage_snapshot):
        self.entity_key = entity_key
        self.alive = alive
        self.content_hash = content_hash
        self.lineage_snapshot = lineage_snapshot


class _FakeIdentity:
    def __init__(self, result):
        self._result = result

    def resolve(self, locator):
        return self._result


def test_no_identity_keys_on_locator_with_empty_extensions():
    key, ext = resolve_for_record(None, "src/foo.py:bar")
    assert key == EntityKey.from_locator("src/foo.py:bar")
    assert ext == {}


def test_identity_resolution_carries_clarion_extension_when_alive_known():
    resolved_key = EntityKey.from_locator("resolved")
    identity = _FakeIdentity(
        _FakeResult(resolved_key, alive=True, content_hash="abc", lineage_snapshot=["e1"])
    )
    key, ext = resolve_for_record(identity, "src/foo.py:bar")
    assert key == resolved_key
    assert ext["clarion"] == {
        "alive": True,
        "content_hash": "abc",
        "lineage_snapshot": ["e1"],
    }


def test_identity_with_unknown_alive_omits_clarion_extension():
    resolved_key = EntityKey.from_locator("resolved")
    identity = _FakeIdentity(
        _FakeResult(resolved_key, alive=None, content_hash=None, lineage_snapshot=None)
    )
    key, ext = resolve_for_record(identity, "x")
    assert key == resolved_key
    assert ext == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/service/test_governance.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'legis.service.governance'`

- [ ] **Step 3: Create the service function**

```python
# src/legis/service/governance.py
"""Extracted governance decision logic — transport-agnostic.

Each function takes its dependencies explicitly (no closures, no globals) and
raises ``ServiceError`` subclasses, never a transport error.
"""

from __future__ import annotations

from legis.identity.entity_key import EntityKey
from legis.identity.resolver import IdentityResolver


def resolve_for_record(
    identity: IdentityResolver | None, locator: str
) -> tuple[EntityKey, dict]:
    """The one resolve-then-key boundary.

    Keys on the SEI when Clarion proves a stable identity, on the locator
    otherwise. When no resolver is wired legis runs standalone (locator-keyed).
    The ``clarion`` extension carries the two distinct axes (identity: ``alive``,
    content: ``content_hash``) plus the REQ-L-01 lineage snapshot, never
    collapsed — present only when a resolution decision was actually made.
    """
    if identity is None:
        return EntityKey.from_locator(locator), {}
    res = identity.resolve(locator)
    ext: dict = {}
    if res.alive is not None:
        ext["clarion"] = {
            "alive": res.alive,
            "content_hash": res.content_hash,
            "lineage_snapshot": res.lineage_snapshot,
        }
    return res.entity_key, ext
```

Add to `src/legis/service/__init__.py` imports and `__all__`:

```python
from legis.service.governance import resolve_for_record
```
```python
__all__ = [
    "ServiceError",
    "AuditIntegrityError",
    "NotEnabledError",
    "NotFoundError",
    "resolve_for_record",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/service/test_governance.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Refactor `app.py` to delegate**

In `src/legis/api/app.py`, add the import near the other `legis.*` imports:

```python
from legis.service.governance import resolve_for_record as _resolve_for_record
```

Delete the inline `def resolve_for_record(locator): ...` closure inside `create_app`, and replace it with a thin closure that binds `identity`:

```python
    def resolve_for_record(locator: str) -> tuple[EntityKey, dict]:
        return _resolve_for_record(identity, locator)
```

(All existing call sites — `post_override`, `post_protected_override`, `post_operator_override`, `post_signoff_request`, the wardline `resolve` helper — keep calling the local `resolve_for_record(locator)` unchanged.)

- [ ] **Step 6: Run the whole suite to verify behavior is preserved**

Run: `uv run pytest -q`
Expected: PASS — 217 passed (214 baseline + 3 new). Zero warnings.

- [ ] **Step 7: Commit**

```bash
git add src/legis/service/governance.py src/legis/service/__init__.py src/legis/api/app.py tests/service/test_governance.py
git commit -m "refactor(service): extract resolve_for_record into the service layer (WP-M1)"
```

---

### Task 3: Extract `verified_records` (tamper-verified trail read → domain error)

The closure `verified_governance_records()` raises `HTTPException(500)` on `TamperError`. The service version raises the domain `AuditIntegrityError`; the HTTP adapter translates it back to 500, preserving behavior.

**Files:**
- Modify: `src/legis/service/governance.py`
- Modify: `src/legis/service/__init__.py`
- Modify: `src/legis/api/app.py`
- Test: `tests/service/test_governance.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/service/test_governance.py
import pytest

from legis.enforcement.protected import TamperError
from legis.service.errors import AuditIntegrityError
from legis.service.governance import verified_records


class _FakeEngine:
    def __init__(self, records):
        self._records = records

    def records(self):
        return self._records


class _FakeProtectedGate:
    def __init__(self, records):
        self._records = records

    def records(self):
        return self._records


class _OkVerifier:
    def verify(self, records):
        return None


class _TamperVerifier:
    def verify(self, records):
        raise TamperError("record 4 hash mismatch")


def test_verified_records_uses_engine_store_when_no_protected_gate():
    engine = _FakeEngine(["r1", "r2"])
    assert verified_records(None, None, engine) == ["r1", "r2"]


def test_verified_records_uses_protected_store_when_gate_present():
    engine = _FakeEngine(["engine"])
    gate = _FakeProtectedGate(["protected"])
    assert verified_records(gate, _OkVerifier(), engine) == ["protected"]


def test_verified_records_skips_verification_when_no_verifier():
    gate = _FakeProtectedGate(["protected"])
    assert verified_records(gate, None, engine=_FakeEngine([])) == ["protected"]


def test_verified_records_raises_audit_integrity_error_on_tamper():
    gate = _FakeProtectedGate(["bad"])
    with pytest.raises(AuditIntegrityError):
        verified_records(gate, _TamperVerifier(), engine=_FakeEngine([]))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/service/test_governance.py -k verified_records -v`
Expected: FAIL — `ImportError: cannot import name 'verified_records'`

- [ ] **Step 3: Add the service function**

```python
# add to src/legis/service/governance.py
from legis.enforcement.protected import TamperError
from legis.service.errors import AuditIntegrityError


def verified_records(protected_gate, trail_verifier, engine):
    """The verified governance trail.

    The protected gate (when wired) owns the governance trail; otherwise the
    simple-tier engine does. Never mix the two stores. Verification is
    fail-closed and applies to EVERY consumer of the protected trail, so a
    tampered record is an honest integrity error (``AuditIntegrityError``),
    never silently read or scored.
    """
    if protected_gate is not None:
        records = protected_gate.records()
        if trail_verifier is not None:
            try:
                trail_verifier.verify(records)
            except TamperError as exc:
                raise AuditIntegrityError(f"audit integrity failure: {exc}") from exc
        return records
    return engine.records()
```

Add `verified_records` to `src/legis/service/__init__.py` imports and `__all__` (alongside `resolve_for_record`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/service/test_governance.py -k verified_records -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Refactor `app.py` to delegate and translate**

In `src/legis/api/app.py`, add the import:

```python
from legis.service.governance import verified_records as _verified_records
from legis.service.errors import AuditIntegrityError
```

Replace the inline `def verified_governance_records(): ...` closure body with delegation that translates the domain error to the *same* HTTP 500 it raised before:

```python
    def verified_governance_records():
        try:
            return _verified_records(protected_gate, trail_verifier, engine())
        except AuditIntegrityError as exc:
            raise HTTPException(status_code=500, detail=str(exc))
```

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS — 221 passed (217 + 4 new). The existing tamper test (forged protected record → HTTP 500) still passes via the translated path.

- [ ] **Step 7: Commit**

```bash
git add src/legis/service/governance.py src/legis/service/__init__.py src/legis/api/app.py tests/service/test_governance.py
git commit -m "refactor(service): extract verified_records with AuditIntegrityError (WP-M1)"
```

---

### Task 4: Extract `compute_override_rate`

The `/governance/override-rate` handler evaluates the gate with policy constants (not query params, so the agent can't tune it). Move that into the service layer.

**Files:**
- Modify: `src/legis/service/governance.py`
- Modify: `src/legis/service/__init__.py`
- Modify: `src/legis/api/app.py`
- Test: `tests/service/test_governance.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/service/test_governance.py
from legis.service.governance import compute_override_rate


def test_compute_override_rate_returns_status_rate_sample_below_min_sample():
    # An empty trail is below min-sample → the gate is not FAIL; rate is 0.
    res = compute_override_rate([])
    assert hasattr(res, "status")
    assert res.rate == 0.0
    assert res.sample_size == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/service/test_governance.py -k override_rate -v`
Expected: FAIL — `ImportError: cannot import name 'compute_override_rate'`

- [ ] **Step 3: Add the service function**

```python
# add to src/legis/service/governance.py
from legis.enforcement.lifecycle import evaluate_override_rate
from legis.governance import params


def compute_override_rate(records):
    """Evaluate the override-rate gate against the policy constants.

    Threshold/window/floor come from ADR-0002 constants — NOT caller input — so
    the gate an agent is measured against cannot be tuned by it.
    """
    return evaluate_override_rate(
        records,
        threshold=params.OVERRIDE_RATE_THRESHOLD,
        window=params.OVERRIDE_RATE_WINDOW,
        min_sample=params.OVERRIDE_RATE_MIN_SAMPLE,
    )
```

Add `compute_override_rate` to `src/legis/service/__init__.py` imports and `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/service/test_governance.py -k override_rate -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Refactor `app.py` to delegate**

In `src/legis/api/app.py`, add the import:

```python
from legis.service.governance import compute_override_rate as _compute_override_rate
```

Replace the body of the `override_rate()` handler so it calls the service function (keep the identical response mapping):

```python
    @app.get("/governance/override-rate")
    def override_rate() -> dict:
        res = _compute_override_rate(verified_governance_records())
        return {
            "status": res.status.value,
            "rate": res.rate,
            "sample_size": res.sample_size,
        }
```

The now-unused module-level imports `evaluate_override_rate` and `params` in `app.py` may remain if other code uses them; if `evaluate_override_rate` is no longer referenced in `app.py`, remove its import line to keep the adapter clean. Verify with: `uv run ruff check src/legis/api/app.py`.

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS — 222 passed.

- [ ] **Step 7: Commit**

```bash
git add src/legis/service/governance.py src/legis/service/__init__.py src/legis/api/app.py tests/service/test_governance.py
git commit -m "refactor(service): extract compute_override_rate into the service layer (WP-M1)"
```

---

### Task 5: Extract `submit_override` (the seam WP-M3's chill slice calls)

This is the load-bearing extraction: the override submit orchestration (`resolve_for_record` then `engine.submit_override`) becomes a single service function the MCP adapter will call in WP-M3. Returns the existing `EnforcementResult` unchanged — the adapter owns the 201/409 mapping.

**Files:**
- Modify: `src/legis/service/governance.py`
- Modify: `src/legis/service/__init__.py`
- Modify: `src/legis/api/app.py`
- Test: `tests/service/test_governance.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/service/test_governance.py
from legis.clock import SystemClock
from legis.enforcement.engine import EnforcementEngine
from legis.store.audit_store import AuditStore
from legis.service.governance import submit_override


def _mem_engine():
    # in-memory sqlite store, no judge → chill cell
    return EnforcementEngine(AuditStore("sqlite://"), SystemClock())


def test_submit_override_chill_records_and_accepts():
    engine = _mem_engine()
    result = submit_override(
        engine,
        identity=None,
        policy="no-direct-push",
        entity="src/foo.py:bar",
        rationale="generated file; lint N/A",
        agent_id="agent-7",
    )
    assert result.accepted is True
    assert result.seq >= 0
    # The recorded payload keys on the locator and attributes the agent.
    trail = engine.trail()
    assert trail[-1]["agent_id"] == "agent-7"
    assert trail[-1]["policy"] == "no-direct-push"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/service/test_governance.py -k submit_override -v`
Expected: FAIL — `ImportError: cannot import name 'submit_override'`

- [ ] **Step 3: Add the service function**

```python
# add to src/legis/service/governance.py
from legis.enforcement.engine import EnforcementEngine, EnforcementResult
from legis.identity.resolver import IdentityResolver  # already imported above — keep one import


def submit_override(
    engine: EnforcementEngine,
    *,
    identity: IdentityResolver | None,
    policy: str,
    entity: str,
    rationale: str,
    agent_id: str,
) -> EnforcementResult:
    """Resolve-then-key, then submit the override to the simple-tier engine.

    Cell semantics live in the engine: judge absent → chill (always accepted);
    judge present → coached (ACCEPTED records, BLOCKED records the attempt). The
    adapter maps ``EnforcementResult.accepted`` to its transport's success/blocked
    signal (HTTP 201/409; MCP ACCEPTED_*/BLOCKED).
    """
    entity_key, ext = resolve_for_record(identity, entity)
    return engine.submit_override(
        policy=policy,
        entity_key=entity_key,
        rationale=rationale,
        agent_id=agent_id,
        extensions=ext,
    )
```

Add `submit_override` to `src/legis/service/__init__.py` imports and `__all__`. Ensure `IdentityResolver` is imported once at the top of `governance.py` (it was added in Task 2); do not duplicate the import.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/service/test_governance.py -k submit_override -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Refactor the `POST /overrides` handler to delegate**

In `src/legis/api/app.py`, add the import:

```python
from legis.service.governance import submit_override as _submit_override
```

Replace the body of `post_override` so it calls the service function and keeps the identical 201/409 mapping and response shape:

```python
    @app.post("/overrides")
    def post_override(body: OverrideIn, response: Response) -> dict:
        result = _submit_override(
            engine(),
            identity=identity,
            policy=body.policy,
            entity=body.entity,
            rationale=body.rationale,
            agent_id=body.agent_id,
        )
        # ACCEPTED → 201 (the override took effect); BLOCKED → 409 (it did not,
        # the agent must correct or convince). Full body either way so the agent
        # gets the judge's reasoning to revise.
        response.status_code = 201 if result.accepted else 409
        return {
            "accepted": result.accepted,
            "seq": result.seq,
            "verdict": result.verdict.value if result.verdict else None,
            "judge_model": result.judge_model,
            "judge_rationale": result.judge_rationale,
        }
```

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS — 223 passed. The existing `/overrides` behavior tests (chill 201, coached ACCEPTED 201 / BLOCKED 409) pass unchanged through the new seam.

- [ ] **Step 7: Commit**

```bash
git add src/legis/service/governance.py src/legis/service/__init__.py src/legis/api/app.py tests/service/test_governance.py
git commit -m "refactor(service): extract submit_override seam for the MCP adapter (WP-M1)"
```

---

### Task 6: Whole-WP verification gate

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite with warnings-as-errors (the project's pytest config sets `filterwarnings = error`)**

Run: `uv run pytest -q`
Expected: PASS — 223 passed, 0 warnings.

- [ ] **Step 2: Lint and type-check the touched files**

Run: `uv run ruff check src/legis/service src/legis/api/app.py tests/service`
Expected: no findings.

Run: `uv run mypy src/legis/service src/legis/api/app.py`
Expected: no new errors versus baseline.

- [ ] **Step 3: Confirm the seam is transport-free**

Run: `grep -rn "HTTPException\|fastapi\|JsonRpc\|mcp" src/legis/service`
Expected: NO matches — the service layer must not import any transport. (This is the WP's structural invariant; if anything matches, the extraction leaked a transport dependency and must be fixed before M3 builds on it.)

- [ ] **Step 4: Final commit if any lint/type fixes were applied**

```bash
git add -A
git commit -m "chore(service): lint/type cleanup for WP-M1 service layer"
```

---

## Self-review notes (author pass)

- **Spec coverage:** WP-M1's spec text ("extract `legis.service`; refactor FastAPI to a thin adapter; behavior-preserving; existing tests stay green") is covered by Tasks 1–6. The four cross-cutting pieces named in the spec architecture (resolve-then-key, tamper-verified records, override-rate, the override submit seam) each get a task. Thin pass-through handlers (protected/signoff/bind/wardline/git/checks) are explicitly out of scope and deferred to later WPs, matching the spec's "moves only the cross-cutting logic plus the one write path M3 rebuilds."
- **Type consistency:** `EnforcementResult` (from `legis.enforcement.engine`), `EntityKey`, and the `ServiceError` subclasses are referenced with the same names/signatures across tasks. `verified_records(protected_gate, trail_verifier, engine)` arg order is identical in test, implementation, and the `app.py` call site. `submit_override` keyword args match the engine's `submit_override` signature.
- **Structural invariant:** Task 6 Step 3 enforces that `legis.service` imports no transport — the property WP-M3 depends on.
- **Test counts** are cumulative assuming the baseline of 214; if the baseline differs at execution time, assert "baseline + N new" rather than the absolute number.
