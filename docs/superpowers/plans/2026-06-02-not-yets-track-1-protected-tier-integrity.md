# Not-Yets Track 1 — Protected-Tier Identity Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Carry the `clarion` two-axis + lineage-snapshot block onto **protected** and **sign-off** records (today only the simple-tier `/overrides` record carries it), and point orphan-gap + lineage-integrity detection at the verified governance trail so protected attestations are orphan-detectable.

**Architecture:** Thread an optional `extensions` dict through `ProtectedGate.submit` / `operator_override` and `SignoffGate.request`, merged so the fixed signed fields always win and the existing HMAC signature is unaffected (the `clarion` block is an unsigned extension; the signed identity binding `entity_key` is unchanged). Wire the three API write paths to pass the `clarion` ext that `resolve_for_record` already computes. Then switch the two gap-detection endpoints from `engine().records()` to `verified_governance_records()`, which returns the protected store (fail-closed, HMAC-verified) when a protected gate is wired.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/SQLite (`AuditStore`), pytest (warnings-as-errors). No new runtime dependency.

**Implements (from the design spec `2026-06-02-not-yets-completion-design.md`):** WP-A1, WP-A2. Closes Sprint 5 Known Limitations ("clarion block carried on the simple-tier record only"; "gap detection reads the simple-tier engine trail only").

**Locked design decisions (do not reopen):**
1. The `clarion` block rides as an **unsigned** extension on protected records; the signed identity binding (`entity_key`, via `signing_fields`) is unchanged. `signing_fields` does not read `extensions["clarion"]`, so adding it cannot change a signature — a test proves the existing signature still verifies.
2. Caller-supplied `extensions` must **never** override a gate's fixed fields (`judge_verdict`, `file_fingerprint`, `ast_path`, `signoff_state`, …). Merge caller extensions first, fixed fields last.
3. Gap detection consumes `verified_governance_records()` — the protected store when a protected gate is wired, the engine store otherwise. The two stores are never unioned (the protected gate owns the trail when present). Verification stays fail-closed (a tampered protected trail → HTTP 500, never a silent scan).

---

## File structure

| File | Change |
|---|---|
| `src/legis/enforcement/protected.py` | `submit` / `operator_override` / `_record_signed` gain optional `extensions: dict \| None` merged into the record's `ext` |
| `src/legis/enforcement/signoff.py` | `request` gains optional `extensions: dict \| None` merged into the record's `ext` |
| `src/legis/api/app.py` | three write paths pass the `clarion` ext from `resolve_for_record`; two gap endpoints read `verified_governance_records()` |
| `tests/enforcement/test_protected_extensions.py` | protected record carries `clarion` block AND signature still verifies |
| `tests/enforcement/test_signoff_extensions.py` | sign-off request carries `clarion` block |
| `tests/api/test_sei_api.py` | (append) protected + sign-off API paths persist the `clarion` block |
| `tests/api/test_complex_api.py` | (append) gap endpoints surface an orphan from the protected trail |

---

## WP-A1 — Carry the `clarion` block onto protected & sign-off records

### Task 1: `ProtectedGate` carries an optional `extensions` block (signature unaffected)

**Files:**
- Modify: `src/legis/enforcement/protected.py`
- Test: `tests/enforcement/test_protected_extensions.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/enforcement/test_protected_extensions.py
from legis.clock import FixedClock
from legis.enforcement.protected import ProtectedGate, signing_fields
from legis.enforcement.signing import verify
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.identity.entity_key import EntityKey
from legis.store.audit_store import AuditStore

KEY = b"protected-key-1"
CLARION = {"clarion": {"alive": True, "content_hash": "blake3h",
                       "lineage_snapshot": {"length": 1, "hash": "lh"}}}


class ScriptedJudge:
    def __init__(self, opinion):
        self.opinion = opinion

    def evaluate(self, record):
        return self.opinion


def _gate(tmp_path):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    g = ProtectedGate(store, FixedClock("2026-06-02T12:00:00+00:00"),
                      judge=ScriptedJudge(JudgeOpinion(Verdict.ACCEPTED, "judge@1", "ok")),
                      key=KEY)
    return g, store


def test_submit_carries_clarion_block(tmp_path):
    g, store = _gate(tmp_path)
    g.submit(policy="no-eval", entity_key=EntityKey.from_sei("clarion:eid:abc"),
             rationale="r", agent_id="a", file_fingerprint="fp", ast_path="ap",
             extensions=CLARION)
    ext = store.read_all()[0].payload["extensions"]
    assert ext["clarion"] == CLARION["clarion"]
    # Fixed signed fields are untouched by the caller's extensions.
    assert ext["judge_verdict"] == "ACCEPTED"
    assert ext["file_fingerprint"] == "fp"


def test_clarion_block_does_not_break_the_signature(tmp_path):
    g, store = _gate(tmp_path)
    g.submit(policy="no-eval", entity_key=EntityKey.from_sei("clarion:eid:abc"),
             rationale="r", agent_id="a", file_fingerprint="fp", ast_path="ap",
             extensions=CLARION)
    payload = store.read_all()[0].payload
    sig = payload["extensions"]["judge_metadata_signature"]
    assert verify(signing_fields(payload), sig, KEY) is True


def test_caller_extensions_cannot_override_fixed_fields(tmp_path):
    g, store = _gate(tmp_path)
    g.submit(policy="no-eval", entity_key=EntityKey.from_sei("clarion:eid:abc"),
             rationale="r", agent_id="a", file_fingerprint="fp", ast_path="ap",
             extensions={"judge_verdict": "TAMPERED", "file_fingerprint": "evil"})
    ext = store.read_all()[0].payload["extensions"]
    assert ext["judge_verdict"] == "ACCEPTED"   # gate wins
    assert ext["file_fingerprint"] == "fp"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/enforcement/test_protected_extensions.py -v`
Expected: FAIL — `submit() got an unexpected keyword argument 'extensions'`.

- [ ] **Step 3: Write minimal implementation**

In `src/legis/enforcement/protected.py`, add `extensions` to `_record_signed` and merge it **before** the fixed fields (so the fixed fields win, decision 2):

```python
    def _record_signed(
        self,
        *,
        policy: str,
        entity_key: EntityKey,
        rationale: str,
        actor_id: str,
        verdict: Verdict,
        model: str | None,
        judge_rationale: str | None,
        file_fingerprint: str,
        ast_path: str,
        extensions: dict[str, Any] | None = None,
    ) -> ProtectedResult:
        ext: dict[str, Any] = {
            **(extensions or {}),
            "judge_verdict": verdict.value,
            "judge_model": model,
            "judge_rationale": judge_rationale,
            "file_fingerprint": file_fingerprint,
            "ast_path": ast_path,
        }
```

(The rest of `_record_signed` is unchanged — `signing_fields(payload)` reads only
the fixed keys, so the signature is computed over the same dict as before.)

Add `extensions` to `submit` and pass it through:

```python
    def submit(
        self,
        *,
        policy: str,
        entity_key: EntityKey,
        rationale: str,
        agent_id: str,
        file_fingerprint: str,
        ast_path: str,
        extensions: dict[str, Any] | None = None,
    ) -> ProtectedResult:
        proposed = OverrideRecord(
            policy=policy,
            entity_key=entity_key,
            rationale=rationale,
            agent_id=agent_id,
            recorded_at=self._clock.now_iso(),
        )
        opinion = self._judge.evaluate(proposed)
        return self._record_signed(
            policy=policy,
            entity_key=entity_key,
            rationale=rationale,
            actor_id=agent_id,
            verdict=opinion.verdict,
            model=opinion.model,
            judge_rationale=opinion.rationale,
            file_fingerprint=file_fingerprint,
            ast_path=ast_path,
            extensions=extensions,
        )
```

Add the same `extensions` param to `operator_override` and forward it to `_record_signed`:

```python
    def operator_override(
        self,
        *,
        policy: str,
        entity_key: EntityKey,
        rationale: str,
        operator_id: str,
        file_fingerprint: str,
        ast_path: str,
        extensions: dict[str, Any] | None = None,
    ) -> ProtectedResult:
        return self._record_signed(
            policy=policy,
            entity_key=entity_key,
            rationale=rationale,
            actor_id=operator_id,
            verdict=Verdict.OVERRIDDEN_BY_OPERATOR,
            model=None,
            judge_rationale=None,
            file_fingerprint=file_fingerprint,
            ast_path=ast_path,
            extensions=extensions,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/enforcement/test_protected_extensions.py -v`
Expected: PASS (3 tests). Then `python -m pytest tests/enforcement/ -q` — all existing protected tests still green (signature unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/legis/enforcement/protected.py tests/enforcement/test_protected_extensions.py
git commit -m "feat(protected): carry optional clarion extension, signature unaffected (WP-A1)"
```

---

### Task 2: `SignoffGate.request` carries an optional `extensions` block

**Files:**
- Modify: `src/legis/enforcement/signoff.py`
- Test: `tests/enforcement/test_signoff_extensions.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/enforcement/test_signoff_extensions.py
from legis.clock import FixedClock
from legis.enforcement.signoff import SignoffGate
from legis.identity.entity_key import EntityKey
from legis.store.audit_store import AuditStore

CLARION = {"clarion": {"alive": True, "content_hash": "blake3h",
                       "lineage_snapshot": {"length": 1, "hash": "lh"}}}


def _gate(tmp_path):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    return SignoffGate(store, FixedClock("2026-06-02T12:00:00+00:00")), store


def test_request_carries_clarion_block(tmp_path):
    g, store = _gate(tmp_path)
    g.request(policy="no-eval", entity_key=EntityKey.from_sei("clarion:eid:abc"),
              rationale="r", agent_id="a", extensions=CLARION)
    ext = store.read_all()[0].payload["extensions"]
    assert ext["clarion"] == CLARION["clarion"]
    assert ext["signoff_state"] == "PENDING_SIGNOFF"


def test_caller_extensions_cannot_override_signoff_state(tmp_path):
    g, store = _gate(tmp_path)
    g.request(policy="no-eval", entity_key=EntityKey.from_sei("clarion:eid:abc"),
              rationale="r", agent_id="a", extensions={"signoff_state": "SIGNED_OFF"})
    ext = store.read_all()[0].payload["extensions"]
    assert ext["signoff_state"] == "PENDING_SIGNOFF"   # gate wins
```

> Note: `SignoffState.PENDING.value` is `"PENDING_SIGNOFF"`. If the test fails on
> that literal, read `src/legis/enforcement/verdict.py` for the exact enum value
> and use it verbatim — do not change the enum.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/enforcement/test_signoff_extensions.py -v`
Expected: FAIL — `request() got an unexpected keyword argument 'extensions'`.

- [ ] **Step 3: Write minimal implementation**

In `src/legis/enforcement/signoff.py`, add `extensions` to `request` and merge it
into `ext` with `signoff_state` last (so the gate's state wins, decision 2):

```python
    def request(
        self,
        *,
        policy: str,
        entity_key: EntityKey,
        rationale: str,
        agent_id: str,
        extensions: dict[str, Any] | None = None,
    ) -> SignoffResult:
        seq = self._append(
            policy=policy,
            entity_key=entity_key,
            rationale=rationale,
            actor_id=agent_id,
            ext={**(extensions or {}), "signoff_state": SignoffState.PENDING.value},
        )
        return SignoffResult(seq=seq, cleared=False)
```

(The `signoff_signature` in `_append` signs a fixed field list that does not
include `extensions["clarion"]`, so a protected sign-off's signature is unaffected.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/enforcement/test_signoff_extensions.py -v`
Expected: PASS (2 tests). Then `python -m pytest tests/enforcement/test_signoff.py -q` — still green.

- [ ] **Step 5: Commit**

```bash
git add src/legis/enforcement/signoff.py tests/enforcement/test_signoff_extensions.py
git commit -m "feat(signoff): request carries optional clarion extension (WP-A1)"
```

---

### Task 3: Wire the three API write paths to pass the `clarion` ext

**Files:**
- Modify: `src/legis/api/app.py` (`post_protected_override`, `post_operator_override`, `post_signoff_request`)
- Test: `tests/api/test_sei_api.py` (append)

- [ ] **Step 1: Write the failing test** (append to `tests/api/test_sei_api.py`)

```python
def test_protected_and_signoff_paths_carry_clarion_block(tmp_path):
    from legis.clock import FixedClock
    from legis.enforcement.protected import ProtectedGate, TrailVerifier
    from legis.enforcement.signoff import SignoffGate
    from legis.enforcement.verdict import JudgeOpinion, Verdict
    from legis.store.audit_store import AuditStore

    class _Judge:
        def evaluate(self, record):
            return JudgeOpinion(Verdict.ACCEPTED, "judge@1", "ok")

    alive = {"sei": "clarion:eid:abc123", "current_locator": "python:function:m.f",
             "content_hash": "blake3hash", "alive": True}
    key = b"k"
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    clock = FixedClock("2026-06-02T12:00:00+00:00")
    pg = ProtectedGate(store, clock, judge=_Judge(), key=key)
    sg = SignoffGate(store, clock)
    app = create_app(
        protected_gate=pg, signoff_gate=sg,
        trail_verifier=TrailVerifier(key, frozenset({"no-eval"})),
        identity=IdentityResolver(FakeClient(alive, lineage=[{"event": "born"}])),
    )
    c = TestClient(app)

    pr = c.post("/protected/overrides", json={
        "policy": "no-eval", "entity": "python:function:m.f", "rationale": "r",
        "agent_id": "agent-1", "file_fingerprint": "fp", "ast_path": "ap"})
    assert pr.status_code == 201
    protected_rec = c.get("/overrides").json()[0]
    assert protected_rec["entity_key"]["value"] == "clarion:eid:abc123"
    assert protected_rec["extensions"]["clarion"]["content_hash"] == "blake3hash"
    # The signed identity binding survived the added extension.
    assert protected_rec["extensions"]["judge_metadata_signature"].startswith("hmac-sha256:")

    sr = c.post("/signoff/request", json={
        "policy": "no-eval", "entity": "python:function:m.f", "rationale": "r",
        "agent_id": "agent-1"})
    assert sr.status_code == 202
    signoff_rec = c.get("/overrides").json()[1]
    assert signoff_rec["extensions"]["clarion"]["content_hash"] == "blake3hash"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/api/test_sei_api.py::test_protected_and_signoff_paths_carry_clarion_block -v`
Expected: FAIL — `KeyError: 'clarion'` (the protected/signoff paths drop the ext via `resolve_entity`).

- [ ] **Step 3: Write minimal implementation**

In `src/legis/api/app.py`, change the three write paths from `resolve_entity(body.entity)`
to the two-value `resolve_for_record(body.entity)` and pass `extensions=ext`.

`post_protected_override`:

```python
        entity_key, ext = resolve_for_record(body.entity)
        result = protected_gate.submit(
            policy=body.policy,
            entity_key=entity_key,
            rationale=body.rationale,
            agent_id=body.agent_id,
            file_fingerprint=body.file_fingerprint,
            ast_path=body.ast_path,
            extensions=ext,
        )
```

`post_operator_override`:

```python
        entity_key, ext = resolve_for_record(body.entity)
        result = protected_gate.operator_override(
            policy=body.policy,
            entity_key=entity_key,
            rationale=body.rationale,
            operator_id=body.operator_id,
            file_fingerprint=body.file_fingerprint,
            ast_path=body.ast_path,
            extensions=ext,
        )
```

`post_signoff_request`:

```python
        entity_key, ext = resolve_for_record(body.entity)
        result = signoff_gate.request(
            policy=body.policy,
            entity_key=entity_key,
            rationale=body.rationale,
            agent_id=body.agent_id,
            extensions=ext,
        )
```

(`resolve_entity` is now used only where a bare key is needed; leave its definition
in place — other call sites may still use it. If a lint flags it as unused after this
change, delete the `resolve_entity` helper at `app.py:178-179`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/api/test_sei_api.py -v`
Expected: PASS. Then `python -m pytest tests/api/ -q` — all green (paths are additive; `identity`/gates default to `None`).

- [ ] **Step 5: Commit**

```bash
git add src/legis/api/app.py tests/api/test_sei_api.py
git commit -m "feat(api): protected + signoff write paths carry the clarion block (WP-A1)"
```

---

## WP-A2 — Point gap + lineage-integrity detection at the verified trail

### Task 4: Gap endpoints read `verified_governance_records()` (protected trail included)

**Files:**
- Modify: `src/legis/api/app.py` (`identity_gaps`, `lineage_integrity`)
- Test: `tests/api/test_complex_api.py` (append)

- [ ] **Step 1: Write the failing test** (append to `tests/api/test_complex_api.py`)

```python
def test_identity_gaps_scan_the_protected_trail(tmp_path):
    from legis.identity.resolver import IdentityResolver

    class OrphanClient:
        def capability(self):
            return True

        def resolve_locator(self, locator):
            return {"sei": "clarion:eid:abc123", "current_locator": locator,
                    "content_hash": "h", "alive": True}

        def resolve_sei(self, sei):
            return {"sei": sei, "alive": False, "lineage": [{"event": "orphaned"}]}

        def lineage(self, sei):
            return [{"event": "born"}]

    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    clock = FixedClock("2026-06-02T12:00:00+00:00")
    pg = ProtectedGate(store, clock, judge=ScriptedJudge(
        JudgeOpinion(Verdict.ACCEPTED, "judge@1", "ok")), key=KEY)
    app = create_app(protected_gate=pg, trail_verifier=TrailVerifier(KEY, PROTECTED),
                     identity=IdentityResolver(OrphanClient()))
    c = TestClient(app)
    # A protected override keyed on an SEI Clarion now reports dead.
    assert c.post("/protected/overrides", json=PBODY).status_code == 201
    gaps = c.get("/governance/identity-gaps").json()
    assert [g["sei"] for g in gaps] == ["clarion:eid:abc123"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/api/test_complex_api.py::test_identity_gaps_scan_the_protected_trail -v`
Expected: FAIL — `gaps == []` (the endpoint scans `engine().records()`, an empty separate store, not the protected trail).

- [ ] **Step 3: Write minimal implementation**

In `src/legis/api/app.py`, change both gap endpoints to read the verified trail
instead of the simple-tier engine trail:

```python
    @app.get("/governance/identity-gaps")
    def identity_gaps() -> list[dict]:
        if identity is None or identity.client is None:
            return []
        gaps = find_orphan_gaps(verified_governance_records(), identity.client)
        return [{"sei": g.sei, "reason": g.reason, "lineage": g.lineage} for g in gaps]

    @app.get("/governance/lineage-integrity")
    def lineage_integrity() -> dict:
        if identity is None or identity.client is None:
            return {"divergences": []}
        divs = find_lineage_divergence(verified_governance_records(), identity.client)
        return {"divergences": [
            {"sei": d.sei, "recorded_length": d.recorded_length,
             "current_length": d.current_length} for d in divs]}
```

Update the module comment above `identity_gaps` (currently states detection consumes
the simple-tier trail and cross-store detection is a follow-up) to state that detection
now consumes `verified_governance_records()` — the protected store when wired, the engine
store otherwise — and stays fail-closed (a tampered protected trail raises 500 before any scan).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/api/test_complex_api.py -v`
Expected: PASS. Then `python -m pytest tests/api/test_sei_api.py -q` — the simple-tier
gap tests still pass (no protected gate wired there → `verified_governance_records()`
falls through to `engine().records()`, identical to before).

- [ ] **Step 5: Commit**

```bash
git add src/legis/api/app.py tests/api/test_complex_api.py
git commit -m "feat(api): gap + lineage-integrity detection scans the verified trail (WP-A2)"
```

---

### Task 5: Docs + full-suite verification

**Files:**
- Modify: `docs/superpowers/specs/2026-06-02-roadmap-conformance-findings.md` (mark the two closed limitations)
- Modify: `docs/superpowers/specs/2026-06-02-not-yets-completion-design.md` (mark WP-A1, WP-A2 done)

- [ ] **Step 1:** In the design spec, add `✅ done 2026-06-02` to the WP-A1 and WP-A2 headers.

- [ ] **Step 2:** In the findings doc, annotate the two Sprint-5 limitations (clarion block on protected/signoff; gap detection over the protected store) as closed by this plan, with a pointer to it.

- [ ] **Step 3: Full suite green, zero warnings**

Run: `python -m pytest -q`
Expected: all green (was 147; +10 new tests from Tasks 1–4 → 157 passing). Confirm the count and zero warnings before committing.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/
git commit -m "docs: mark WP-A1/A2 protected-tier integrity complete"
```

---

## Self-review — WP coverage

| WP | Exit criterion (from design spec) | Proven by |
|---|---|---|
| A1 | protected + sign-off records persist `extensions["clarion"]` identical in shape to the simple-tier record | Task 1 (`test_submit_carries_clarion_block`), Task 2 (`test_request_carries_clarion_block`), Task 3 (API round-trip) |
| A1 | the existing protected signature still verifies with the block added | Task 1 (`test_clarion_block_does_not_break_the_signature`), Task 3 (asserts `judge_metadata_signature` present after round-trip) |
| A1 | caller extensions cannot override fixed signed fields | Task 1 + Task 2 (`test_caller_extensions_cannot_override_*`) |
| A2 | a protected attestation on an orphaned SEI surfaces an orphan gap | Task 4 (`test_identity_gaps_scan_the_protected_trail`) |
| A2 | protected-trail reads remain HMAC-verified at load (no weakening) | `verified_governance_records()` is reused verbatim (fail-closed 500 on tamper); existing `test_trail_verify.py` + complex-api tests stay green (Task 4 Step 4) |

**Out of scope for this plan (other WPs):** signing the binding (WP-A3), Wardline breadth (A4–A6), policy grammar (A7–A8), git/CI surface (A9–A11), SEI backfill (A12), sibling-gated B-track, doc drift (C1). Each is its own plan per the design spec's sequencing.
