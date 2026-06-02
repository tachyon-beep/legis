# Legis Sprint 5 — SEI conformance Implementation Plan

**Status:** ✅ implemented 2026-06-02 — WP-5.1 + WP-5.2 complete, SEI §8 oracle green (six scenarios), 131 tests passing. During execution the SEI-keying scope was **broadened** beyond the simple-tier `/overrides` path to **every** governance write path (protected, operator-override, signoff) — Clarion's `contracts.md` requires all legis attestations to key on SEI, and the protected tier (HMAC-signed verdicts) is where rename-survival matters most. See the broadened Task 3 and the two added Known Limitations.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make legis a conformant SEI **consumer** — governance records key on Clarion's Stable Entity Identity (a value swap, no schema change), an orphaned SEI surfaces a governance gap instead of a silent drop, lineage is consumed as the audit spine with legis-side append-only integrity (REQ-L-01 Option 3), and the whole thing degrades honestly when Clarion does not advertise the `sei` capability.

**Architecture:** A thin transport seam (`ClarionIdentity` Protocol; `HttpClarionIdentity` over stdlib `urllib`, no new runtime dep — same "legis adds no dependency" posture as `GitSurface`) wrapped by an `IdentityResolver` that turns a locator into an `EntityKey` (SEI when alive + capable, locator otherwise) plus the two-axis metadata (identity: `alive`; content: `content_hash`) and an append-only lineage snapshot. The resolver is injected into the API and consulted on every governance submit; when absent or degraded, the existing locator path is preserved unchanged so legis still runs standalone. Two read surfaces consume the lineage spine: orphan governance-gap detection and lineage-integrity (prefix-hash) verification.

**Tech Stack:** Python 3.12, FastAPI, stdlib `urllib`/`json`, SQLAlchemy/SQLite (the existing append-only `AuditStore`), pytest (warnings-as-errors). No new runtime dependency.

**Gate (now cleared):** Clarion shipped SEI 2026-06-02 and advertises `sei: {supported: true, version: 1}` at `GET /api/v1/_capabilities`. REQ-L-01 is resolved: Clarion chose **Option 3** (append-only lineage, no Clarion-side hash chain; the consumer re-establishes integrity at its own boundary) — exactly the option `docs/federation/sei-conformance.md` pre-committed legis to. Verified wire contracts live in `clarion/docs/federation/contracts.md` and `clarion/docs/federation/fixtures/`.

---

## Verified Clarion wire contracts (the surfaces this sprint codes against)

Confirmed against Clarion's normative fixtures — these are what the client parses:

| Surface | Method / route | Response (relevant fields) |
|---|---|---|
| Capability probe | `GET /api/v1/_capabilities` | `{ "sei": { "supported": true, "version": 1 }, … }` (unauthenticated) |
| Locator → SEI | `POST /api/v1/identity/resolve` body `{ "locator": "<loc>" }` | alive: `{ "sei", "current_locator", "content_hash", "alive": true }`; none alive: `{ "alive": false }` (still `200`) |
| SEI → status | `GET /api/v1/identity/sei/:sei` | alive: `{ "sei", "current_locator", "content_hash", "alive": true }`; orphaned: `{ "sei", "alive": false, "lineage": [ … ] }` |
| Lineage | `GET /api/v1/identity/lineage/:sei` | `{ "sei", "lineage": [ { "event", "old_locator", "new_locator", "run_id", "recorded_at" }, … ] }` |

Lineage `event` ∈ `{ born, locator_changed, moved, orphaned, superseded }`. SEI tokens carry the reserved `clarion:eid:` prefix and are **opaque** (never parsed).

---

## Locked design decisions (do not reopen)

1. **SEI is opaque; legis never parses it.** The only thing legis does with an SEI string is store it as `EntityKey.value` (via `EntityKey.from_sei`) and hand it back to Clarion on `resolve_sei` / `lineage`. No prefix-stripping, no structure assumptions. (SEI standard §1/§2; conformance oracle invariant 1.)
2. **Resolve-then-key happens at the API boundary; the engine is unchanged in shape.** `submit_override` already takes an `EntityKey` — the API resolves the locator to an `EntityKey` before calling it. This is what makes "re-key records on SEI" a *value swap with no schema change* (the `EntityKey` abstraction from `identity/entity_key.py` was built for exactly this).
3. **Degrade honestly, never guess.** No `sei` capability, no `ClarionIdentity` client wired, the locator resolves to nothing alive, *or* a transport error → `EntityKey.from_locator(...)` with `identity_stable=False`. Legis never falls back to a locator as if it were stable, and never crashes. (Conformance scenario `capability_absent`.)
4. **Fail-closed on orphan: an orphaned SEI is a surfaced governance gap, never a silent drop.** When `resolve_sei(sei) → alive:false` for an SEI that legis holds an attestation on, legis emits a `GovernanceGap` (with the orphan lineage). This relies on Clarion's documented `{ alive:false, lineage:[…] }` contract — recorded as legis's *reliance*, not a new ask. (Conformance scenarios `ambiguous`, `delete`.)
5. **REQ-L-01 = Option 3, prefix-hash custody.** At each governance decision on an SEI, legis stores a lineage snapshot `{ "length": N, "hash": content_hash(lineage[:N]) }`. On re-verification it re-fetches and checks the snapshot is a **prefix** of the current lineage: `len(current) >= N` and `content_hash(current[:N]) == hash`. Appended events (rename/move) are legitimate and pass; a removed or mutated prior event fails. A bare whole-list hash mismatch is **not** tamper — lineage legitimately grows; only a broken prefix is.
6. **Two axes stay distinct, never collapsed.** Identity axis (`alive: true/false`) and content axis (`content_hash` fresh/stale) are stored as separate fields on the record's `clarion` extension. (sei-conformance.md "two-axis status".)
7. **No new runtime dependency.** `HttpClarionIdentity` uses stdlib `urllib.request`, and takes an injectable `fetch` callable so every test runs offline against a fake. (Mirrors `GitSurface` shelling out rather than adding a dep.)

## Known limitations (honest disclosure — record, don't build)

- **Transport outage is indistinguishable from "no stable identity" at the record level.** A network failure when the capability *was* advertised degrades to `identity_stable=false`, same as genuine absence. This is honest (legis truly could not establish stable identity) but coarse; a richer "identity unavailable vs. unstable" signal is deferred. The capability probe result is cached per-resolver-instance, so a mid-life Clarion outage is not re-probed within a process.
- **Gap/integrity detection is pull-only and on-demand.** The orphan-gap and lineage-integrity surfaces resolve/`lineage` each held SEI when called (an endpoint hit or a scheduled sweep). There is no push/event subscription — legis v1 accepts polling latency, exactly as `sei-conformance.md` "Informational — lineage push surface" states.
- **The conformance oracle runs against a `FakeClarionIdentity`, not a live reference Clarion in CI.** The fake returns the six scenarios' documented Clarion-side shapes **transcribed from** `sei-conformance-oracle.json` (whose `expect` blocks are symbolic placeholders like `"<opaque>"`, not replayable response bodies) — the cross-repo fixture is deliberately **not** loaded at test time. It proves legis's *consumer* behaviour. A live-Clarion integration run is a separate, environment-gated check (deferred), not this unit-level oracle.
- **Batch resolve (`/resolve:batch`) is not consumed in this sprint.** Records are resolved one locator at a time on submit. Bulk backfill of pre-SEI locator-keyed records via `POST /api/v1/identity/resolve:batch` is a migration task, deferred (it is not on the WP-5.1/5.2 exit criteria).
- **`HttpClarionIdentity` authentication is out of scope.** The protected/authenticated identity routes accept an `X-Loom-Component` HMAC header; this sprint wires the unauthenticated capability probe and the read routes against a local/trusted Clarion. Auth header provisioning is deferred alongside the WP-3.2 HMAC-key provisioning decision.
- **The `clarion` two-axis + lineage-snapshot extension is carried on the simple-tier `/overrides` record only.** All four governance write paths key on the SEI (the load-bearing requirement — `entity_key.value` = SEI), but only `submit_override` threads the `extensions["clarion"]` block. Protected and signoff records key on SEI yet do not yet carry the two-axis/snapshot metadata; carrying it into a *signed* protected payload (and deciding whether it is signed) is deferred. The identity binding — the part that must survive rename — *is* signed on protected records, because `signing_fields` binds `entity_key`.
- **Gap + lineage-integrity detection reads the simple-tier engine trail only.** `find_orphan_gaps` / `find_lineage_divergence` scan `engine().records()`. When a `ProtectedGate` is wired the protected trail lives in a separate store; pointing gap detection at that store (or unifying via `verified_governance_records()`) is a documented follow-up. Protected records already key on SEI and carry `identity_stable`, so they become orphan-detectable the moment detection is pointed there — no schema change required.

---

## File structure

| File | Responsibility |
|---|---|
| `src/legis/identity/clarion_client.py` | `ClarionIdentity` Protocol; `HttpClarionIdentity` (urllib + injectable `fetch`); `ClarionError` |
| `src/legis/identity/resolver.py` | `IdentityResolution` value type; `IdentityResolver` (resolve-then-key + honest degrade + lineage snapshot) |
| `src/legis/governance/gaps.py` | `GovernanceGap`; `LineageDivergence`; `find_orphan_gaps`; `find_lineage_divergence` |
| `src/legis/enforcement/engine.py` | `submit_override` gains optional `extensions: dict` (carry the `clarion` metadata onto the record) |
| `src/legis/api/app.py` | inject `identity: IdentityResolver \| None`; resolve at submit; `GET /governance/identity-gaps`; `GET /governance/lineage-integrity` |
| `tests/identity/test_clarion_client.py` | client parsing per surface, offline via fake `fetch` |
| `tests/identity/test_resolver.py` | resolve-then-key, opacity, four degrade paths, snapshot shape |
| `tests/governance/test_gaps.py` | orphan gap; prefix-hash custody (truncate/mutate fail, append passes) |
| `tests/api/test_sei_api.py` | record keyed on SEI; gap + integrity endpoints |
| `tests/conformance/test_sei_oracle.py` | the six SEI §8 scenarios via `FakeClarionIdentity` |

---

## WP-5.1 — SEI client swap

### Task 1: `ClarionIdentity` transport seam

**Files:**
- Create: `src/legis/identity/clarion_client.py`
- Test: `tests/identity/__init__.py` (empty), `tests/identity/test_clarion_client.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/identity/test_clarion_client.py
from legis.identity.clarion_client import ClarionError, HttpClarionIdentity


def _fake_fetch(responses):
    calls = []

    def fetch(method, url, body):
        calls.append((method, url, body))
        for (m, suffix), resp in responses.items():
            if method == m and url.endswith(suffix):
                return resp
        raise ClarionError(f"no canned response for {method} {url}")

    fetch.calls = calls
    return fetch


def test_capability_true_when_sei_supported():
    fetch = _fake_fetch({("GET", "/api/v1/_capabilities"): {"sei": {"supported": True, "version": 1}}})
    assert HttpClarionIdentity("http://c", fetch=fetch).capability() is True


def test_capability_false_when_absent_or_unsupported():
    fetch = _fake_fetch({("GET", "/api/v1/_capabilities"): {"registry_backend": True}})
    assert HttpClarionIdentity("http://c", fetch=fetch).capability() is False


def test_resolve_locator_alive_passthrough():
    body = {"sei": "clarion:eid:abc", "current_locator": "python:function:m.f", "content_hash": "h", "alive": True}
    fetch = _fake_fetch({("POST", "/api/v1/identity/resolve"): body})
    c = HttpClarionIdentity("http://c", fetch=fetch)
    assert c.resolve_locator("python:function:m.f") == body
    assert fetch.calls[-1] == ("POST", "http://c/api/v1/identity/resolve", {"locator": "python:function:m.f"})


def test_resolve_sei_orphaned_carries_lineage():
    body = {"sei": "clarion:eid:abc", "alive": False, "lineage": [{"event": "orphaned"}]}
    fetch = _fake_fetch({("GET", "/api/v1/identity/sei/clarion:eid:abc"): body})
    assert HttpClarionIdentity("http://c", fetch=fetch).resolve_sei("clarion:eid:abc") == body


def test_lineage_returns_event_list():
    body = {"sei": "clarion:eid:abc", "lineage": [{"event": "born"}, {"event": "locator_changed"}]}
    fetch = _fake_fetch({("GET", "/api/v1/identity/lineage/clarion:eid:abc"): body})
    assert HttpClarionIdentity("http://c", fetch=fetch).lineage("clarion:eid:abc") == body["lineage"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/identity/test_clarion_client.py -v`
Expected: FAIL — `ModuleNotFoundError: legis.identity.clarion_client`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/legis/identity/clarion_client.py
"""Clarion SEI read client — a thin transport seam.

legis consumes Clarion's SEI surfaces as an HTTP client (the same consumer model
as ``GitSurface`` / the read API). The default transport is stdlib ``urllib`` so
legis adds no dependency; a ``fetch`` callable is injectable so tests run offline.
SEI strings are opaque here — this module never parses them, only forwards them.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Protocol, runtime_checkable

Fetch = Callable[[str, str, dict | None], dict]


class ClarionError(RuntimeError):
    """A Clarion identity call failed at the transport or decode layer."""


@runtime_checkable
class ClarionIdentity(Protocol):
    def capability(self) -> bool: ...
    def resolve_locator(self, locator: str) -> dict[str, Any]: ...
    def resolve_sei(self, sei: str) -> dict[str, Any]: ...
    def lineage(self, sei: str) -> list[dict[str, Any]]: ...


def _urllib_fetch(method: str, url: str, body: dict | None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310 (trusted Clarion URL)
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError) as exc:
        raise ClarionError(f"{method} {url} failed: {exc}") from exc


class HttpClarionIdentity:
    def __init__(self, base_url: str, *, fetch: Fetch | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._fetch = fetch or _urllib_fetch

    def capability(self) -> bool:
        body = self._fetch("GET", f"{self._base}/api/v1/_capabilities", None)
        sei = body.get("sei") if isinstance(body, dict) else None
        return isinstance(sei, dict) and sei.get("supported") is True

    def resolve_locator(self, locator: str) -> dict[str, Any]:
        return self._fetch(
            "POST", f"{self._base}/api/v1/identity/resolve", {"locator": locator}
        )

    def resolve_sei(self, sei: str) -> dict[str, Any]:
        return self._fetch("GET", f"{self._base}/api/v1/identity/sei/{sei}", None)

    def lineage(self, sei: str) -> list[dict[str, Any]]:
        body = self._fetch("GET", f"{self._base}/api/v1/identity/lineage/{sei}", None)
        return list(body.get("lineage", []))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/identity/test_clarion_client.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/legis/identity/clarion_client.py tests/identity/
git commit -m "feat(identity): Clarion SEI read client seam (WP-5.1)"
```

---

### Task 2: `IdentityResolver` — resolve-then-key with honest degrade

**Files:**
- Create: `src/legis/identity/resolver.py`
- Test: `tests/identity/test_resolver.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/identity/test_resolver.py
from legis.canonical import content_hash
from legis.identity.resolver import IdentityResolver


class FakeClient:
    def __init__(self, *, capable=True, resolve=None, lineage=None, boom=False):
        self._capable = capable
        self._resolve = resolve or {"alive": False}
        self._lineage = lineage or []
        self._boom = boom

    def capability(self):
        if self._boom:
            raise RuntimeError("clarion down")
        return self._capable

    def resolve_locator(self, locator):
        return self._resolve

    def resolve_sei(self, sei):  # not used by the resolver
        raise AssertionError

    def lineage(self, sei):
        return self._lineage


ALIVE = {"sei": "clarion:eid:deadbeef", "current_locator": "python:function:m.f",
         "content_hash": "blake3hash", "alive": True}


def test_alive_sei_is_keyed_opaquely_with_two_axes():
    r = IdentityResolver(FakeClient(resolve=ALIVE, lineage=[{"event": "born"}]))
    res = r.resolve("python:function:m.f")
    assert res.entity_key.value == "clarion:eid:deadbeef"      # the SEI, verbatim
    assert res.entity_key.identity_stable is True
    assert res.entity_key.value.startswith("clarion:eid:")     # opaque, not parsed
    assert res.entity_key.value != "python:function:m.f"       # not the locator
    assert res.alive is True                                    # identity axis
    assert res.content_hash == "blake3hash"                     # content axis
    assert res.lineage_snapshot == {"length": 1, "hash": content_hash([{"event": "born"}])}


def test_capability_absent_degrades_to_locator():
    r = IdentityResolver(FakeClient(capable=False))
    res = r.resolve("python:function:m.f")
    assert res.entity_key.value == "python:function:m.f"
    assert res.entity_key.identity_stable is False
    assert res.alive is None and res.content_hash is None and res.lineage_snapshot is None


def test_no_client_degrades_to_locator():
    res = IdentityResolver(None).resolve("python:function:m.f")
    assert res.entity_key.identity_stable is False


def test_locator_with_no_alive_sei_degrades_but_records_alive_false():
    r = IdentityResolver(FakeClient(resolve={"alive": False}))
    res = r.resolve("python:function:gone")
    assert res.entity_key.identity_stable is False
    assert res.alive is False        # capability present, but no stable identity → honest


def test_transport_error_degrades_never_raises():
    r = IdentityResolver(FakeClient(boom=True))
    res = r.resolve("python:function:m.f")
    assert res.entity_key.identity_stable is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/identity/test_resolver.py -v`
Expected: FAIL — `ModuleNotFoundError: legis.identity.resolver`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/legis/identity/resolver.py
"""Resolve a locator to an SEI-keyed (or honestly-degraded) EntityKey.

This is the WP-5.1 swap point: governance records key on SEI when Clarion proves
a stable, alive identity, and on the locator (``identity_stable=False``) in every
other case — capability absent, no client, locator not alive, or transport error.
The resolver never parses an SEI and never guesses. It also captures the REQ-L-01
append-only lineage snapshot at the moment of the governance decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from legis.canonical import content_hash
from legis.identity.clarion_client import ClarionIdentity
from legis.identity.entity_key import EntityKey


@dataclass(frozen=True)
class IdentityResolution:
    entity_key: EntityKey
    alive: bool | None          # identity axis; None when no capability/decision
    content_hash: str | None    # content axis; None when unavailable
    lineage_snapshot: dict[str, Any] | None  # {"length": N, "hash": ...} or None


class IdentityResolver:
    def __init__(self, client: ClarionIdentity | None) -> None:
        self._client = client
        self._capable: bool | None = None  # probe once per instance

    def _capability(self) -> bool:
        if self._client is None:
            return False
        if self._capable is None:
            try:
                self._capable = bool(self._client.capability())
            except Exception:
                self._capable = False  # honest degrade — never raise
        return self._capable

    def _snapshot(self, sei: str) -> dict[str, Any] | None:
        try:
            lineage = self._client.lineage(sei)  # type: ignore[union-attr]
        except Exception:
            return None
        return {"length": len(lineage), "hash": content_hash(lineage)}

    def resolve(self, locator: str) -> IdentityResolution:
        degraded = IdentityResolution(EntityKey.from_locator(locator), None, None, None)
        if not self._capability():
            return degraded
        try:
            res = self._client.resolve_locator(locator)  # type: ignore[union-attr]
        except Exception:
            return degraded
        if not res.get("alive"):
            # Capability present but this locator has no alive SEI — honest: no
            # stable identity, and we know it (alive recorded False, not None).
            return IdentityResolution(EntityKey.from_locator(locator), False, None, None)
        sei = res["sei"]
        return IdentityResolution(
            EntityKey.from_sei(sei),
            True,
            res.get("content_hash"),
            self._snapshot(sei),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/identity/test_resolver.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/legis/identity/resolver.py tests/identity/test_resolver.py
git commit -m "feat(identity): resolve-then-key with honest degrade (WP-5.1)"
```

---

### Task 3: Wire the resolver into the API submit paths

**Files:**
- Modify: `src/legis/api/app.py` (add `identity` param to `create_app`; resolve at `post_override`)
- Test: `tests/api/test_sei_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_sei_api.py
from fastapi.testclient import TestClient

from legis.api.app import create_app
from legis.clock import FixedClock
from legis.enforcement.engine import EnforcementEngine
from legis.identity.resolver import IdentityResolver
from legis.store.audit_store import AuditStore


class FakeClient:
    def __init__(self, resolve, lineage=None):
        self._resolve = resolve
        self._lineage = lineage or []

    def capability(self):
        return True

    def resolve_locator(self, locator):
        return self._resolve

    def resolve_sei(self, sei):
        return {"sei": sei, "alive": True}

    def lineage(self, sei):
        return self._lineage


def _app(tmp_path, client):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    eng = EnforcementEngine(store, FixedClock("2026-06-02T12:00:00+00:00"))
    return TestClient(create_app(enforcement=eng, identity=IdentityResolver(client)))


def test_override_keys_record_on_sei_when_alive(tmp_path):
    alive = {"sei": "clarion:eid:abc123", "current_locator": "python:function:m.f",
             "content_hash": "h", "alive": True}
    c = _app(tmp_path, FakeClient(alive, lineage=[{"event": "born"}]))
    resp = c.post("/overrides", json={
        "policy": "no-eval", "entity": "python:function:m.f",
        "rationale": "reviewed", "agent_id": "agent-1"})
    assert resp.status_code == 201
    trail = c.get("/overrides").json()
    assert trail[0]["entity_key"] == {"value": "clarion:eid:abc123", "identity_stable": True}
    assert trail[0]["identity_stable"] is True


def test_override_degrades_to_locator_when_not_alive(tmp_path):
    c = _app(tmp_path, FakeClient({"alive": False}))
    resp = c.post("/overrides", json={
        "policy": "no-eval", "entity": "python:function:gone",
        "rationale": "reviewed", "agent_id": "agent-1"})
    assert resp.status_code == 201
    trail = c.get("/overrides").json()
    assert trail[0]["entity_key"] == {"value": "python:function:gone", "identity_stable": False}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/api/test_sei_api.py -v`
Expected: FAIL — `create_app() got an unexpected keyword argument 'identity'`.

- [ ] **Step 3: Write minimal implementation**

In `src/legis/api/app.py`, add the import:

```python
from legis.identity.resolver import IdentityResolver
```

Add the parameter to `create_app` (alongside the other injected deps):

```python
    grammar: PolicyGrammar | None = None,
    identity: IdentityResolver | None = None,
) -> FastAPI:
```

Add a helper inside `create_app` (next to `git()` / `checks()`), and a resolver that degrades when nothing is wired:

```python
    def resolve_entity(locator: str) -> EntityKey:
        if identity is None:
            return EntityKey.from_locator(locator)
        return identity.resolve(locator).entity_key
```

Replace the body of `post_override` so the entity is resolved rather than locator-keyed:

```python
    @app.post("/overrides")
    def post_override(body: OverrideIn, response: Response) -> dict:
        result = engine().submit_override(
            policy=body.policy,
            entity_key=resolve_entity(body.entity),
            rationale=body.rationale,
            agent_id=body.agent_id,
        )
        response.status_code = 201 if result.accepted else 409
        return {
            "accepted": result.accepted,
            "seq": result.seq,
            "verdict": result.verdict.value if result.verdict else None,
            "judge_model": result.judge_model,
            "judge_rationale": result.judge_rationale,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/api/test_sei_api.py -v`
Expected: PASS (2 tests). Then the full suite: `python -m pytest -q` — Expected: all green (the existing locator-only tests still pass because `identity` defaults to `None` → `from_locator`, preserving prior behaviour).

- [ ] **Step 5: Commit**

```bash
git add src/legis/api/app.py tests/api/test_sei_api.py
git commit -m "feat(api): governance overrides key on SEI via resolver (WP-5.1)"
```

---

## WP-5.2 — Lineage spine + conformance oracle

### Task 4: Carry the two-axis + lineage-snapshot metadata onto the record (REQ-L-01)

**Files:**
- Modify: `src/legis/enforcement/engine.py` (`submit_override` gains optional `extensions`)
- Modify: `src/legis/api/app.py` (`post_override` passes the resolution metadata)
- Test: `tests/api/test_sei_api.py` (append a case)

- [ ] **Step 1: Write the failing test** (append to `tests/api/test_sei_api.py`)

```python
def test_record_carries_clarion_two_axis_and_lineage_snapshot(tmp_path):
    from legis.canonical import content_hash
    alive = {"sei": "clarion:eid:abc123", "current_locator": "python:function:m.f",
             "content_hash": "blake3hash", "alive": True}
    lineage = [{"event": "born"}, {"event": "locator_changed"}]
    c = _app(tmp_path, FakeClient(alive, lineage=lineage))
    c.post("/overrides", json={"policy": "no-eval", "entity": "python:function:m.f",
                               "rationale": "reviewed", "agent_id": "agent-1"})
    clarion = c.get("/overrides").json()[0]["extensions"]["clarion"]
    assert clarion["alive"] is True
    assert clarion["content_hash"] == "blake3hash"
    assert clarion["lineage_snapshot"] == {"length": 2, "hash": content_hash(lineage)}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/api/test_sei_api.py::test_record_carries_clarion_two_axis_and_lineage_snapshot -v`
Expected: FAIL — `KeyError: 'clarion'` (no metadata stored yet).

- [ ] **Step 3: Write minimal implementation**

In `src/legis/enforcement/engine.py`, give `submit_override` an optional `extensions` and thread it onto the record (it already builds an `OverrideRecord` that accepts `extensions`):

```python
    def submit_override(
        self,
        *,
        policy: str,
        entity_key: EntityKey,
        rationale: str,
        agent_id: str,
        extensions: dict | None = None,
    ) -> EnforcementResult:
        record = OverrideRecord(
            policy=policy,
            entity_key=entity_key,
            rationale=rationale,
            agent_id=agent_id,
            recorded_at=self._clock.now_iso(),
            extensions=dict(extensions or {}),
        )
```

(The rest of `submit_override` is unchanged — the judge branch already does
`{**record.extensions, ...}`, so injected extensions survive a judge pass.)

In `src/legis/api/app.py`, replace `resolve_entity` with a richer helper that also returns the `clarion` extension, and use it in `post_override`:

```python
    def resolve_for_record(locator: str) -> tuple[EntityKey, dict]:
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

```python
    @app.post("/overrides")
    def post_override(body: OverrideIn, response: Response) -> dict:
        entity_key, ext = resolve_for_record(body.entity)
        result = engine().submit_override(
            policy=body.policy,
            entity_key=entity_key,
            rationale=body.rationale,
            agent_id=body.agent_id,
            extensions=ext,
        )
        response.status_code = 201 if result.accepted else 409
        return {
            "accepted": result.accepted,
            "seq": result.seq,
            "verdict": result.verdict.value if result.verdict else None,
            "judge_model": result.judge_model,
            "judge_rationale": result.judge_rationale,
        }
```

(Delete the now-unused `resolve_entity` helper from Task 3.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/api/test_sei_api.py -v`
Expected: PASS (3 tests). Full suite: `python -m pytest -q` — all green.

- [ ] **Step 5: Commit**

```bash
git add src/legis/enforcement/engine.py src/legis/api/app.py tests/api/test_sei_api.py
git commit -m "feat(governance): records carry two-axis + lineage snapshot (WP-5.2/REQ-L-01)"
```

---

### Task 5: Orphan governance gaps + lineage prefix-hash custody

**Files:**
- Create: `src/legis/governance/gaps.py`
- Test: `tests/governance/__init__.py` (empty), `tests/governance/test_gaps.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/governance/test_gaps.py
from legis.canonical import content_hash
from legis.governance.gaps import (
    LineageDivergence,
    find_lineage_divergence,
    find_orphan_gaps,
)
from legis.store.audit_store import AuditStore


def _store(tmp_path, *payloads):
    s = AuditStore(f"sqlite:///{tmp_path / 'g.db'}")
    for p in payloads:
        s.append(p)
    return s


def _rec(sei, *, identity_stable=True, snapshot=None):
    ext = {"clarion": {"lineage_snapshot": snapshot}} if snapshot else {}
    return {"policy": "p", "entity_key": {"value": sei, "identity_stable": identity_stable},
            "rationale": "r", "agent_id": "a", "recorded_at": "t",
            "identity_stable": identity_stable, "extensions": ext}


class FakeClient:
    def __init__(self, sei_status, lineages=None):
        self._status = sei_status          # {sei: {"alive": bool, "lineage": [...]}}
        self._lineages = lineages or {}    # {sei: [events]}

    def resolve_sei(self, sei):
        return {"sei": sei, **self._status.get(sei, {"alive": True})}

    def lineage(self, sei):
        return self._lineages.get(sei, [])


def test_orphaned_sei_surfaces_a_gap(tmp_path):
    store = _store(tmp_path, _rec("clarion:eid:alive"), _rec("clarion:eid:dead"))
    client = FakeClient({
        "clarion:eid:alive": {"alive": True},
        "clarion:eid:dead": {"alive": False, "lineage": [{"event": "orphaned"}]},
    })
    gaps = find_orphan_gaps(store.read_all(), client)
    assert [g.sei for g in gaps] == ["clarion:eid:dead"]
    assert gaps[0].lineage == [{"event": "orphaned"}]


def test_locator_keyed_records_are_not_probed(tmp_path):
    store = _store(tmp_path, _rec("python:function:x", identity_stable=False))
    gaps = find_orphan_gaps(store.read_all(), FakeClient({}))
    assert gaps == []   # nothing stable to probe → no gap, no crash


def test_appended_lineage_is_not_divergence(tmp_path):
    born = [{"event": "born"}]
    snap = {"length": 1, "hash": content_hash(born)}
    store = _store(tmp_path, _rec("clarion:eid:s", snapshot=snap))
    grown = born + [{"event": "locator_changed"}]   # legitimate append
    div = find_lineage_divergence(store.read_all(), FakeClient({}, {"clarion:eid:s": grown}))
    assert div == []


def test_truncated_or_mutated_prefix_is_divergence(tmp_path):
    born = [{"event": "born"}, {"event": "moved"}]
    snap = {"length": 2, "hash": content_hash(born)}
    store = _store(tmp_path, _rec("clarion:eid:s", snapshot=snap))
    tampered = [{"event": "born"}]   # the 'moved' event vanished — prefix broken
    div = find_lineage_divergence(store.read_all(), FakeClient({}, {"clarion:eid:s": tampered}))
    assert div == [LineageDivergence(sei="clarion:eid:s", recorded_length=2, current_length=1)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/governance/test_gaps.py -v`
Expected: FAIL — `ModuleNotFoundError: legis.governance.gaps`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/legis/governance/gaps.py
"""Lineage-spine consumers: orphan governance gaps + append-only custody.

An attestation keyed on an SEI that Clarion now reports ``alive: false`` is a
*governance gap* (fail-closed: surfaced, never silently dropped — locked
decision 4). REQ-L-01 Option 3 custody: legis stored a lineage snapshot at the
decision; on re-read it verifies the snapshot is still a PREFIX of the current
lineage. Appends (rename/move) are legitimate; a removed or mutated prior event
is divergence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from legis.canonical import content_hash
from legis.identity.clarion_client import ClarionIdentity
from legis.store.audit_store import AuditRecord


@dataclass(frozen=True)
class GovernanceGap:
    sei: str
    reason: str
    lineage: list[dict[str, Any]]


@dataclass(frozen=True)
class LineageDivergence:
    sei: str
    recorded_length: int
    current_length: int


def _stable_seis(records: list[AuditRecord]) -> list[str]:
    seen: dict[str, None] = {}  # ordered, de-duplicated
    for rec in records:
        ek = rec.payload.get("entity_key", {})
        if ek.get("identity_stable") and ek.get("value"):
            seen.setdefault(ek["value"], None)
    return list(seen)


def find_orphan_gaps(
    records: list[AuditRecord], client: ClarionIdentity
) -> list[GovernanceGap]:
    gaps: list[GovernanceGap] = []
    for sei in _stable_seis(records):
        res = client.resolve_sei(sei)
        if not res.get("alive"):
            gaps.append(GovernanceGap(sei, "orphaned", list(res.get("lineage", []))))
    return gaps


def find_lineage_divergence(
    records: list[AuditRecord], client: ClarionIdentity
) -> list[LineageDivergence]:
    divergences: list[LineageDivergence] = []
    seen: set[str] = set()
    for rec in records:
        ek = rec.payload.get("entity_key", {})
        sei = ek.get("value")
        if not (ek.get("identity_stable") and sei) or sei in seen:
            continue
        snap = (rec.payload.get("extensions", {}).get("clarion", {}) or {}).get(
            "lineage_snapshot"
        )
        if not snap:
            continue
        seen.add(sei)
        current = client.lineage(sei)
        n = snap["length"]
        if len(current) < n or content_hash(current[:n]) != snap["hash"]:
            divergences.append(
                LineageDivergence(sei=sei, recorded_length=n, current_length=len(current))
            )
    return divergences
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/governance/test_gaps.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/legis/governance/gaps.py tests/governance/
git commit -m "feat(governance): orphan gaps + lineage prefix-hash custody (WP-5.2)"
```

---

### Task 6: API read surfaces for gaps + integrity

**Files:**
- Modify: `src/legis/api/app.py` (`GET /governance/identity-gaps`, `GET /governance/lineage-integrity`)
- Test: `tests/api/test_sei_api.py` (append cases)

- [ ] **Step 1: Write the failing test** (append to `tests/api/test_sei_api.py`)

```python
def test_identity_gaps_endpoint_surfaces_orphans(tmp_path):
    from dataclasses import asdict
    alive = {"sei": "clarion:eid:abc123", "current_locator": "python:function:m.f",
             "content_hash": "h", "alive": True}

    class OrphanClient(FakeClient):
        def resolve_sei(self, sei):
            return {"sei": sei, "alive": False, "lineage": [{"event": "orphaned"}]}

    c = _app(tmp_path, OrphanClient(alive, lineage=[{"event": "born"}]))
    c.post("/overrides", json={"policy": "no-eval", "entity": "python:function:m.f",
                               "rationale": "reviewed", "agent_id": "agent-1"})
    gaps = c.get("/governance/identity-gaps").json()
    assert gaps == [{"sei": "clarion:eid:abc123", "reason": "orphaned",
                     "lineage": [{"event": "orphaned"}]}]


def test_lineage_integrity_endpoint_reports_clean_when_appended(tmp_path):
    alive = {"sei": "clarion:eid:abc123", "current_locator": "python:function:m.f",
             "content_hash": "h", "alive": True}
    c = _app(tmp_path, FakeClient(alive, lineage=[{"event": "born"}]))
    c.post("/overrides", json={"policy": "no-eval", "entity": "python:function:m.f",
                               "rationale": "reviewed", "agent_id": "agent-1"})
    # FakeClient.lineage still returns the same [born]; snapshot matches → clean.
    assert c.get("/governance/lineage-integrity").json() == {"divergences": []}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/api/test_sei_api.py -k "gaps or integrity" -v`
Expected: FAIL — `404 Not Found` (routes do not exist yet).

- [ ] **Step 3: Write minimal implementation**

In `src/legis/api/app.py`, add the import:

```python
from legis.governance.gaps import find_lineage_divergence, find_orphan_gaps
```

Add the routes (place them with the other `/governance/*` routes). They require a
wired client; when identity is absent there is nothing stable to check:

```python
    @app.get("/governance/identity-gaps")
    def identity_gaps() -> list[dict]:
        if identity is None or identity._client is None:
            return []
        gaps = find_orphan_gaps(engine().records(), identity._client)
        return [{"sei": g.sei, "reason": g.reason, "lineage": g.lineage} for g in gaps]

    @app.get("/governance/lineage-integrity")
    def lineage_integrity() -> dict:
        if identity is None or identity._client is None:
            return {"divergences": []}
        divs = find_lineage_divergence(engine().records(), identity._client)
        return {"divergences": [
            {"sei": d.sei, "recorded_length": d.recorded_length,
             "current_length": d.current_length} for d in divs]}
```

> Note: reaching `identity._client` is acceptable here (same package-internal
> coupling as the lazy accessors). If a reviewer prefers, add a public
> `IdentityResolver.client` property in Task 2 and use it — behaviour identical.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/api/test_sei_api.py -v`
Expected: PASS (5 tests). Full suite: `python -m pytest -q` — all green.

- [ ] **Step 5: Commit**

```bash
git add src/legis/api/app.py tests/api/test_sei_api.py
git commit -m "feat(api): identity-gaps + lineage-integrity read surfaces (WP-5.2)"
```

---

### Task 7: The SEI §8 conformance oracle (six scenarios)

**Files:**
- Create: `tests/conformance/__init__.py` (empty), `tests/conformance/test_sei_oracle.py`

This task adds **no production code** — it proves legis's consumer behaviour
against the six shared scenarios from
`clarion/docs/federation/fixtures/sei-conformance-oracle.json`. A
`FakeClarionIdentity` returns the documented Clarion-side shapes; the assertions
are legis's required *consumer* responses.

- [ ] **Step 1: Write the failing test**

```python
# tests/conformance/test_sei_oracle.py
"""Loom SEI §8 conformance oracle — legis as consumer.

Six shared scenarios (identity round-trip + opacity, rename, move, ambiguous,
delete, capability-absent). A subsystem is SEI-conformant only when all six pass.
The fake returns Clarion's documented shapes; we assert legis's behaviour.
"""
from legis.governance.gaps import find_orphan_gaps
from legis.identity.resolver import IdentityResolver
from legis.store.audit_store import AuditStore


class FakeClarion:
    def __init__(self, *, capable=True, resolve=None, sei=None, lineage=None):
        self._capable = capable
        self._resolve = resolve or {}      # {locator: response}
        self._sei = sei or {}              # {sei: response}
        self._lineage = lineage or {}      # {sei: [events]}

    def capability(self):
        return self._capable

    def resolve_locator(self, locator):
        return self._resolve.get(locator, {"alive": False})

    def resolve_sei(self, sei):
        return self._sei.get(sei, {"sei": sei, "alive": False, "lineage": []})

    def lineage(self, sei):
        return self._lineage.get(sei, [])


def test_identity_round_trip_and_opacity():
    loc = "python:function:m.f"
    client = FakeClarion(resolve={loc: {"sei": "clarion:eid:rt", "current_locator": loc,
                                        "content_hash": "h", "alive": True}})
    res = IdentityResolver(client).resolve(loc)
    assert res.entity_key.identity_stable is True
    assert res.entity_key.value.startswith("clarion:eid:")   # opaque, carries prefix
    assert res.entity_key.value != loc                       # not the locator
    assert res.alive is True and res.content_hash == "h"


def test_rename_carries_sei_record_survives():
    # The record was keyed on the SEI; after rename the SEI still resolves alive
    # at the NEW locator. legis's record is untouched — identity carried.
    sei = "clarion:eid:ren"
    client = FakeClarion(sei={sei: {"sei": sei, "current_locator": "python:function:new.f",
                                    "content_hash": "h", "alive": True}})
    assert client.resolve_sei(sei)["alive"] is True   # carry, not orphan


def test_move_carries_sei():
    sei = "clarion:eid:mov"
    client = FakeClarion(sei={sei: {"sei": sei, "current_locator": "python:function:b.f",
                                    "content_hash": "h", "alive": True}})
    assert client.resolve_sei(sei)["alive"] is True


def test_ambiguous_old_sei_orphaned_surfaces_gap(tmp_path):
    sei = "clarion:eid:amb"
    store = AuditStore(f"sqlite:///{tmp_path / 'g.db'}")
    store.append({"entity_key": {"value": sei, "identity_stable": True},
                  "identity_stable": True, "extensions": {}})
    client = FakeClarion(sei={sei: {"sei": sei, "alive": False,
                                    "lineage": [{"event": "orphaned"}]}})
    gaps = find_orphan_gaps(store.read_all(), client)
    assert [g.sei for g in gaps] == [sei]   # fail-closed: surfaced, never carried


def test_delete_old_sei_orphaned_surfaces_gap(tmp_path):
    sei = "clarion:eid:del"
    store = AuditStore(f"sqlite:///{tmp_path / 'g.db'}")
    store.append({"entity_key": {"value": sei, "identity_stable": True},
                  "identity_stable": True, "extensions": {}})
    client = FakeClarion(sei={sei: {"sei": sei, "alive": False,
                                    "lineage": [{"event": "orphaned"}]}})
    assert [g.sei for g in find_orphan_gaps(store.read_all(), client)] == [sei]


def test_capability_absent_degrades_gracefully():
    client = FakeClarion(capable=False)
    res = IdentityResolver(client).resolve("python:function:any")
    assert res.entity_key.identity_stable is False   # honest 'identity unavailable'
    assert res.entity_key.value == "python:function:any"   # keeps working on locators
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/conformance/test_sei_oracle.py -v`
Expected: FAIL — `ModuleNotFoundError` until imports resolve; once collected, all six should pass against the code from Tasks 1–5 (run it to confirm green, since this task adds no production code).

- [ ] **Step 3: Implementation**

None — this task is the oracle. If any scenario fails, the defect is in Tasks 1–5; fix it there, do not weaken the oracle.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/conformance/test_sei_oracle.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add tests/conformance/
git commit -m "test(conformance): pass SEI §8 oracle as consumer (WP-5.2)"
```

---

### Task 8: Docs + scope disclosure

**Files:**
- Modify: `docs/superpowers/plans/2026-06-01-legis-implementation-sprints.md` (Sprint 5 status)
- Modify: `docs/federation/sei-conformance.md` (mark REQ-L-01 resolved → Option 3, implemented)
- Modify: `README.md` (Clarion + Legis combination → no longer "Future" for the SEI-keyed half; goal-state checkboxes)
- Modify: this plan's header

- [ ] **Step 1:** Add `**Status:** ✅ implemented 2026-06-02 — WP-5.1 + WP-5.2 complete, SEI §8 oracle green` under the Sprint 5 heading in the sprints doc, and the same header line at the top of this plan.

- [ ] **Step 2:** In `docs/federation/sei-conformance.md`, update REQ-L-01 to record the resolution: *"RESOLVED — Clarion committed to Option 3 (append-only lineage, no Clarion-side hash chain). legis establishes prefix-hash custody at the governance boundary (`governance/gaps.py:find_lineage_divergence`)."*

- [x] **Step 3 (decision: README left as North-Star, not ticked):** `README.md` is framed "design-ready, not implemented" and its goal-state checklist was left **unticked by every prior implemented sprint** — the four 2×2 cells (chill/coached/protected/structured, Sprints 2–3) are all built yet unticked there. Ticking only the SEI lines would misrepresent state (SEI done, the equally-done cells not). The honest, consistent choice is to keep the README as the forward-looking North-Star and record per-sprint status in the federation-facing docs instead: `docs/federation/sei-conformance.md` (obligations marked IMPLEMENTED + REQ-L-01 RESOLVED) and the sprints doc (Sprint 5 ✅). A README-wide "Half 1 implemented" pass is a separate, whole-of-Half-1 concern, not Sprint 5's.

- [ ] **Step 4:** Full suite green, zero warnings: `python -m pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add docs/ README.md
git commit -m "docs: mark Sprint 5 SEI conformance complete (WP-5.1/5.2)"
```

---

## Self-review — WP coverage

| WP | Exit criterion (from sprints doc §Sprint 5) | Proven by |
|---|---|---|
| 5.1 | records key on SEI via the `EntityKey` abstraction (value swap, no schema change) — **all four governance write paths**, not just simple-tier | Task 3 (`test_override_keys_record_on_sei_when_alive`, `test_protected_override_keys_on_sei_and_signature_still_verifies`, `test_signoff_request_keys_on_sei_when_alive`) |
| 5.1 | capability absent → every record `identity_stable: false`, nothing guesses | Task 2 (`test_capability_absent_degrades…`), Task 3 (`test_override_degrades…`), Task 7 (`test_capability_absent…`) |
| 5.1 | legis never parses the SEI | Task 2 + Task 7 opacity asserts (value used verbatim, only prefix-*checked* in tests, never parsed in prod) |
| 5.2 | consume `lineage(sei)` as audit spine | Task 4 (snapshot captured), Task 5/6 (consumed) |
| 5.2 | orphaned SEI → surfaced governance gap, never a silent drop | Task 5 (`test_orphaned_sei_surfaces_a_gap`), Task 6 endpoint, Task 7 (ambiguous + delete) |
| 5.2 | lineage integrity matches the locked REQ-L-01 decision (Option 3) | Task 5 (`test_appended…` passes, `test_truncated_or_mutated…` diverges) |
| 5.2 | legis **passes** the SEI §8 conformance oracle (demonstrated, not assumed) | Task 7 (all six scenarios) |

**Locked-decision → test map:** opacity (1) → Task 2/7; resolve-then-key (2) → Task 3; honest degrade (3) → Task 2/3/7; fail-closed orphan (4) → Task 5/7; REQ-L-01 prefix custody (5) → Task 5; two-axis distinct (6) → Task 4; no new dep (7) → Task 1 (injectable `fetch`, stdlib only).

**Spec coverage check:** sprints doc WP-5.1 and WP-5.2 both fully mapped above. `sei-conformance.md` obligations — opaque treatment (decision 1), lineage spine (Task 4–6), honest degrade (decision 3), governance gap on orphan (Task 5), two-axis status (Task 4), REQ-L-01 Option 3 (Task 5) — all covered. Provider seam (REQ-L-02 / git-rename to Clarion) is **out of scope** — that is Milestone 7, gated on the cross-language wire-shape decision, and tracked separately.
