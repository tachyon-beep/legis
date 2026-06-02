# Not-Yets Track 3 (WP-A4/A5/A6) — Wardline Routing Breadth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Widen the Wardline→legis routing seam: add a recorded-but-non-gating `SURFACE_ONLY` outcome (WP-A4), let routing pick the cell per-finding by severity instead of one cell per scan (WP-A5), and lock the coached surface-override path with a test (WP-A6).

**Architecture:** `route_findings` gains a third `WardlineCellPolicy` member, `SURFACE_ONLY`, routed through the existing `EnforcementEngine.record_event()` seam — an attributable governance event with no override/sign-off *gate* (honoring the README's "never a silent pass": surfaced findings are still recorded, just not gated). Routing changes from a single whole-scan `policy` to an optional per-severity `cell_map`; the single-`policy` form stays as a degenerate map for backward compatibility. The API's `POST /wardline/scan-results` keeps its single-`cell` field (the proven Wardline `legis_e2e` handshake must not regress) and adds an optional `cell_by_severity` map.

**Tech Stack:** Python 3.12, FastAPI, the existing `EnforcementEngine`/`SignoffGate`/`WardlineFinding`, pytest (warnings-as-errors). No new runtime dependency.

**Implements (design spec `2026-06-02-not-yets-completion-design.md`):** WP-A4 (R-2.2-08), WP-A5 (R-2.2-05 + coarse-routing limitation), WP-A6 (R-2.2-07).

**Locked design decisions (do not reopen):**
1. **SURFACE_ONLY is recorded, not silent.** It calls `engine.record_event(...)` with `kind: "wardline_surfaced"` — an attributable event carrying the finding's `rule_id`, resolved `entity_key`, rationale, and the `wardline` + `clarion` extensions. No `submit_override` (no judge), no `signoff.request` (no gate). "Never a silent pass" holds; "no hard gate" is satisfied by the absence of override/sign-off. (Advisor-confirmed reading of WP-A4; the "map to the README 2×2" parenthetical is dropped — that conflates the routing axis with the governance grid, and Wardline→protected is a separate, unrequested WP.)
2. **SURFACE_ONLY carries the SEI `entity_key` + `clarion` ext, so it is orphan-detectable — intentionally and consistently** with `surface_override` (it flows through the same `resolve()` boundary). A surfaced finding on an SEI Clarion later orphans is a legitimate governance gap. Documented, not incidental.
3. **Severity routing is additive and backward-compatible.** `route_findings` keeps the single `policy=` form (degenerate "all findings → one cell") and adds an optional `cell_map: {WardlineSeverity: WardlineCellPolicy}`. Exactly one of `policy`/`cell_map`. A severity absent from the map falls back to `SURFACE_OVERRIDE` (records an attributable override — never a silent pass — never a surprise hard gate). The `--fail-on`/exit-class from the scan's `gate` block is the *input* a caller uses to build the map.
4. **The API's single-`cell` request shape is preserved verbatim.** `ScanResultsIn` keeps `cell`; `cell_by_severity` is a new optional field. Exactly one of the two. The proven `legis_e2e` handshake (`cell: surface_override`) is unchanged.
5. **Per-cell dependency guards, surfaced as 409.** A cell needing an unwired dependency (`block_escalate` without a `signoff_gate`, any surface cell without an engine) raises `ValueError` in `route_findings`; the endpoint maps that to HTTP 409.

---

## File structure

| File | Change |
|---|---|
| `src/legis/wardline/governor.py` | `WardlineCellPolicy.SURFACE_ONLY`; `route_findings` handles SURFACE_ONLY via `record_event`, accepts `cell_map`, per-cell guards |
| `src/legis/api/app.py` | `ScanResultsIn.cell_by_severity`; endpoint builds a cell_map, wires engine for surface cells, maps `ValueError`→409 |
| `tests/wardline/test_governor.py` | (append) SURFACE_ONLY records non-gating; severity map routes per-finding; fallback; guards |
| `tests/wardline/test_coached_routing.py` | WP-A6: coached surface-override path records a judge verdict |
| `tests/api/test_combinations_api.py` | (append) scan-results SURFACE_ONLY + cell_by_severity; single-cell unchanged |
| `docs/superpowers/specs/2026-06-02-not-yets-completion-design.md` | mark WP-A4/A5/A6 done |

---

## Task 1: `SURFACE_ONLY` cell — recorded, non-gating

**Files:**
- Modify: `src/legis/wardline/governor.py`
- Test: `tests/wardline/test_governor.py` (append)

- [ ] **Step 1: Write the failing test** (append to `tests/wardline/test_governor.py`)

```python
def test_surface_only_records_a_non_gating_event(tmp_path):
    eng = _engine(tmp_path)  # judge-less engine; see helper at top of file
    results = route_findings(
        active_defects(_scan()),
        policy=WardlineCellPolicy.SURFACE_ONLY,
        agent_id="agent-1",
        resolve=lambda q: (EntityKey.from_locator(q or "unknown"), {}),
        engine=eng,
    )
    assert results[0]["mode"] == "surface_only"
    assert results[0]["surfaced"] is True
    assert "accepted" not in results[0] and "cleared" not in results[0]  # no gate
    # The finding is still RECORDED (never a silent pass).
    trail = eng.trail()
    assert trail[0]["kind"] == "wardline_surfaced"
    assert trail[0]["policy"] == "PY-WL-101"
    assert trail[0]["extensions"]["wardline"]["fingerprint"] == "fp1"


def test_surface_only_needs_no_signoff_gate(tmp_path):
    # The whole point: it routes with signoff=None and a judge-less engine.
    eng = _engine(tmp_path)
    results = route_findings(
        active_defects(_scan()), policy=WardlineCellPolicy.SURFACE_ONLY,
        agent_id="a", resolve=lambda q: (EntityKey.from_locator(q or "unknown"), {}),
        engine=eng, signoff=None)
    assert results[0]["mode"] == "surface_only"
```

> Confirm the existing `_scan()` / `_engine()` helpers at the top of `test_governor.py`
> and the `resolve=` lambda shape (it returns a `(EntityKey, dict)` tuple — the current
> tests already use that shape). If `_engine` builds a judge-less `EnforcementEngine`,
> use it as-is.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/wardline/test_governor.py -k surface_only -v`
Expected: FAIL — `AttributeError: SURFACE_ONLY` (enum member missing).

- [ ] **Step 3: Write minimal implementation**

In `src/legis/wardline/governor.py`, add the enum member:

```python
class WardlineCellPolicy(str, Enum):
    SURFACE_OVERRIDE = "surface_override"
    BLOCK_ESCALATE = "block_escalate"
    SURFACE_ONLY = "surface_only"
```

Rewrite `route_findings` to per-cell dispatch with per-cell guards (this replaces the
current up-front guard + two-branch body; the SURFACE_OVERRIDE and BLOCK_ESCALATE
behaviour is unchanged):

```python
def route_findings(
    findings: list[WardlineFinding],
    *,
    policy: WardlineCellPolicy,
    agent_id: str,
    resolve: Callable[[str | None], tuple[EntityKey, dict[str, Any]]],
    engine: EnforcementEngine | None = None,
    signoff: SignoffGate | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for f in findings:
        entity_key, clarion_ext = resolve(f.qualname)
        rationale = f"[wardline {f.rule_id}] {f.message}"
        wardline_ext = {"fingerprint": f.fingerprint, "tiers": dict(f.properties),
                        "severity": f.severity.value}
        if policy is WardlineCellPolicy.BLOCK_ESCALATE:
            if signoff is None:
                raise ValueError("block_escalate cell requires a signoff gate")
            res = signoff.request(policy=f.rule_id, entity_key=entity_key,
                                  rationale=rationale, agent_id=agent_id)
            results.append({"mode": policy.value, "fingerprint": f.fingerprint,
                            "seq": res.seq, "cleared": res.cleared})
        elif policy is WardlineCellPolicy.SURFACE_OVERRIDE:
            if engine is None:
                raise ValueError("surface_override cell requires an engine")
            ext = {**clarion_ext, "wardline": wardline_ext}
            res = engine.submit_override(policy=f.rule_id, entity_key=entity_key,
                                         rationale=rationale, agent_id=agent_id,
                                         extensions=ext)
            results.append({"mode": policy.value, "fingerprint": f.fingerprint,
                            "seq": res.seq, "accepted": res.accepted})
        else:  # SURFACE_ONLY — recorded, non-gating
            if engine is None:
                raise ValueError("surface_only cell requires an engine")
            ext = {**clarion_ext, "wardline": wardline_ext}
            seq = engine.record_event({"kind": "wardline_surfaced", "policy": f.rule_id,
                                       "entity_key": entity_key.to_dict(),
                                       "rationale": rationale, "agent_id": agent_id,
                                       "extensions": ext})
            results.append({"mode": policy.value, "fingerprint": f.fingerprint,
                            "seq": seq, "surfaced": True})
    return results
```

Update the module docstring to add the SURFACE_ONLY bullet: a recorded, non-gating
`wardline_surfaced` event (no judge, no sign-off) that still carries the SEI entity_key
+ clarion/wardline extensions, so it is orphan-detectable like an override.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/wardline/test_governor.py -v`
Expected: PASS (existing governor tests + 2 new). Then `python -m pytest -q` — full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/legis/wardline/governor.py tests/wardline/test_governor.py
git commit -m "feat(wardline): SURFACE_ONLY recorded non-gating routing cell (WP-A4)"
```

---

## Task 2: Severity-driven cell selection

**Files:**
- Modify: `src/legis/wardline/governor.py`
- Test: `tests/wardline/test_governor.py` (append)

- [ ] **Step 1: Write the failing test** (append to `tests/wardline/test_governor.py`)

```python
def _mixed_scan():
    def fnd(rule, sev, fp):
        return {"rule_id": rule, "message": "m", "severity": sev, "kind": "defect",
                "fingerprint": fp, "qualname": "m.f", "properties": {}, "suppressed": "active"}
    return {"findings": [fnd("R-CRIT", "CRITICAL", "c"),
                         fnd("R-WARN", "WARN", "w"),
                         fnd("R-INFO", "INFO", "i")]}


def test_cell_map_routes_each_finding_by_severity(tmp_path):
    from legis.enforcement.signoff import SignoffGate
    from legis.store.audit_store import AuditStore
    from legis.wardline.ingest import WardlineSeverity

    eng = _engine(tmp_path)
    gate = SignoffGate(AuditStore(f"sqlite:///{tmp_path / 's.db'}"),
                       FixedClock("2026-06-02T12:00:00+00:00"))
    cell_map = {
        WardlineSeverity.CRITICAL: WardlineCellPolicy.BLOCK_ESCALATE,
        WardlineSeverity.WARN: WardlineCellPolicy.SURFACE_OVERRIDE,
        WardlineSeverity.INFO: WardlineCellPolicy.SURFACE_ONLY,
    }
    results = route_findings(
        active_defects(_mixed_scan()), cell_map=cell_map, agent_id="a",
        resolve=lambda q: (EntityKey.from_locator(q or "unknown"), {}),
        engine=eng, signoff=gate)
    by_fp = {r["fingerprint"]: r["mode"] for r in results}
    assert by_fp == {"c": "block_escalate", "w": "surface_override", "i": "surface_only"}


def test_unmapped_severity_falls_back_to_surface_override(tmp_path):
    from legis.wardline.ingest import WardlineSeverity
    eng = _engine(tmp_path)
    cell_map = {WardlineSeverity.CRITICAL: WardlineCellPolicy.SURFACE_ONLY}
    # The _scan() finding is ERROR severity — not in the map → fallback.
    results = route_findings(
        active_defects(_scan()), cell_map=cell_map, agent_id="a",
        resolve=lambda q: (EntityKey.from_locator(q or "unknown"), {}), engine=eng)
    assert results[0]["mode"] == "surface_override"


def test_exactly_one_of_policy_or_cell_map(tmp_path):
    import pytest
    eng = _engine(tmp_path)
    with pytest.raises(ValueError, match="exactly one"):
        route_findings(active_defects(_scan()), agent_id="a",
                       resolve=lambda q: (EntityKey.from_locator("x"), {}), engine=eng)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/wardline/test_governor.py -k "cell_map or unmapped or exactly_one" -v`
Expected: FAIL — `route_findings() got an unexpected keyword argument 'cell_map'`.

- [ ] **Step 3: Write minimal implementation**

In `route_findings`, make `policy` optional, add `cell_map`, and resolve the cell per
finding. Change the signature and add the validation + `cell_for` at the top; the
per-cell dispatch body from Task 1 stays, but keyed on `cell` instead of `policy`:

```python
def route_findings(
    findings: list[WardlineFinding],
    *,
    agent_id: str,
    resolve: Callable[[str | None], tuple[EntityKey, dict[str, Any]]],
    policy: WardlineCellPolicy | None = None,
    cell_map: dict["WardlineSeverity", WardlineCellPolicy] | None = None,
    engine: EnforcementEngine | None = None,
    signoff: SignoffGate | None = None,
) -> list[dict[str, Any]]:
    if (policy is None) == (cell_map is None):
        raise ValueError("exactly one of policy or cell_map must be given")

    def cell_for(f: WardlineFinding) -> WardlineCellPolicy:
        if cell_map is not None:
            return cell_map.get(f.severity, WardlineCellPolicy.SURFACE_OVERRIDE)
        assert policy is not None
        return policy

    results: list[dict[str, Any]] = []
    for f in findings:
        cell = cell_for(f)
        entity_key, clarion_ext = resolve(f.qualname)
        ...  # the same per-cell dispatch from Task 1, with `policy` replaced by `cell`
```

Add the import at the top of the module:

```python
from legis.wardline.ingest import WardlineFinding, WardlineSeverity
```

> Replace every `policy is WardlineCellPolicy.X` and `policy.value` inside the loop
> with `cell is …` / `cell.value`. The dispatch logic is otherwise identical to Task 1.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/wardline/test_governor.py -v`
Expected: PASS (all prior + 3 new). Then `python -m pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add src/legis/wardline/governor.py tests/wardline/test_governor.py
git commit -m "feat(wardline): severity-driven per-finding cell selection (WP-A5)"
```

---

## Task 3: API — `cell_by_severity` + SURFACE_ONLY wiring

**Files:**
- Modify: `src/legis/api/app.py`
- Test: `tests/api/test_combinations_api.py` (append)

- [ ] **Step 1: Write the failing test** (append to `tests/api/test_combinations_api.py`)

```python
def test_scan_results_surface_only_records_non_gating(tmp_path):
    c = _client(tmp_path)
    body = {"cell": "surface_only", "agent_id": "agent-1", "scan": {"findings": [
        {"rule_id": "PY-WL-101", "message": "m", "severity": "INFO", "kind": "defect",
         "fingerprint": "fp1", "qualname": "m.f", "properties": {}, "suppressed": "active"}]}}
    resp = c.post("/wardline/scan-results", json=body)
    assert resp.status_code == 200
    assert resp.json()["routed"][0]["mode"] == "surface_only"
    trail = c.get("/overrides").json()
    assert trail[0]["kind"] == "wardline_surfaced"


def test_scan_results_cell_by_severity_routes_per_finding(tmp_path):
    from legis.clock import FixedClock
    from legis.enforcement.signoff import SignoffGate
    from legis.store.audit_store import AuditStore
    sg = SignoffGate(AuditStore(f"sqlite:///{tmp_path / 's.db'}"),
                     FixedClock("2026-06-02T12:00:00+00:00"))
    c = _client(tmp_path, signoff_gate=sg)
    body = {"agent_id": "a",
            "cell_by_severity": {"CRITICAL": "block_escalate", "INFO": "surface_only"},
            "scan": {"findings": [
                {"rule_id": "R-C", "message": "m", "severity": "CRITICAL", "kind": "defect",
                 "fingerprint": "c", "qualname": "m.f", "properties": {}, "suppressed": "active"},
                {"rule_id": "R-I", "message": "m", "severity": "INFO", "kind": "defect",
                 "fingerprint": "i", "qualname": "m.g", "properties": {}, "suppressed": "active"}]}}
    resp = c.post("/wardline/scan-results", json=body)
    assert resp.status_code == 200
    modes = {r["fingerprint"]: r["mode"] for r in resp.json()["routed"]}
    assert modes == {"c": "block_escalate", "i": "surface_only"}


def test_scan_results_single_cell_still_works(tmp_path):
    # Backward-compat: the proven Wardline handshake shape must not regress.
    c = _client(tmp_path)
    body = {"cell": "surface_override", "agent_id": "agent-1", "scan": {"findings": [
        {"rule_id": "PY-WL-101", "message": "m", "severity": "ERROR", "kind": "defect",
         "fingerprint": "fp1", "qualname": "m.f", "properties": {}, "suppressed": "active"}]}}
    resp = c.post("/wardline/scan-results", json=body)
    assert resp.status_code == 200
    assert resp.json()["routed"][0]["mode"] == "surface_override"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/api/test_combinations_api.py -k "surface_only or cell_by_severity" -v`
Expected: FAIL — surface_only routes but the endpoint passes `engine=None` for it (KeyError/None), and `cell_by_severity` is an unknown field.

- [ ] **Step 3: Write minimal implementation**

In `src/legis/api/app.py`, extend the model:

```python
class ScanResultsIn(BaseModel):
    agent_id: str
    scan: dict
    cell: str | None = None
    cell_by_severity: dict[str, str] | None = None
```

Add the import:

```python
from legis.wardline.ingest import WardlineSeverity, active_defects
```

Rewrite `wardline_scan_results` to support both forms, wire the engine for both surface
cells, and map `ValueError` → 409:

```python
    @app.post("/wardline/scan-results")
    def wardline_scan_results(body: ScanResultsIn) -> dict:
        if (body.cell is None) == (body.cell_by_severity is None):
            raise HTTPException(status_code=422,
                                detail="provide exactly one of cell or cell_by_severity")

        def resolve(qualname: str | None) -> tuple[EntityKey, dict]:
            if qualname:
                return resolve_for_record(qualname)
            return EntityKey.from_locator("unknown"), {}

        kwargs: dict = {"agent_id": body.agent_id, "resolve": resolve,
                        "engine": engine(), "signoff": signoff_gate}
        try:
            if body.cell_by_severity is not None:
                kwargs["cell_map"] = {
                    WardlineSeverity[sev]: WardlineCellPolicy(cell)
                    for sev, cell in body.cell_by_severity.items()
                }
            else:
                kwargs["policy"] = WardlineCellPolicy(body.cell)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"unknown cell/severity: {exc}")

        try:
            routed = route_findings(active_defects(body.scan), **kwargs)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"routed": routed}
```

> Note: the endpoint now always passes `engine=engine()` and `signoff=signoff_gate`;
> `route_findings`'s per-cell guards raise only for the cells actually used, mapped to
> 409. A `surface_only`/`surface_override`-only scan therefore needs no `signoff_gate`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/api/test_combinations_api.py -v`
Expected: PASS. Then `python -m pytest -q` — full suite green (the existing single-`cell` scan-results test still passes; `cell` is now optional but the old body still provides it).

- [ ] **Step 5: Commit**

```bash
git add src/legis/api/app.py tests/api/test_combinations_api.py
git commit -m "feat(api): scan-results supports SURFACE_ONLY + cell_by_severity routing (WP-A4/A5)"
```

---

## Task 4: Coached Wardline path coverage (WP-A6)

**Files:**
- Test: `tests/wardline/test_coached_routing.py` (new) — test-only unless a defect surfaces.

- [ ] **Step 1: Write the test**

```python
# tests/wardline/test_coached_routing.py
"""WP-A6: a Wardline finding routed surface_override through a JUDGE-enabled engine
records a coached verdict — the coached cell is reachable from the Wardline seam."""
from legis.clock import FixedClock
from legis.enforcement.engine import EnforcementEngine
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.identity.entity_key import EntityKey
from legis.store.audit_store import AuditStore
from legis.wardline.governor import WardlineCellPolicy, route_findings
from legis.wardline.ingest import active_defects


class BlockingJudge:
    def evaluate(self, record):
        return JudgeOpinion(Verdict.BLOCKED, "judge@1", "untrusted reaches trusted")


def _scan():
    return {"findings": [
        {"rule_id": "PY-WL-101", "message": "untrusted reaches trusted",
         "severity": "ERROR", "kind": "defect", "fingerprint": "fp1",
         "qualname": "m.f", "properties": {"actual_return": "UNKNOWN_RAW"},
         "suppressed": "active"}]}


def test_coached_wardline_path_records_a_judge_verdict(tmp_path):
    eng = EnforcementEngine(AuditStore(f"sqlite:///{tmp_path / 'g.db'}"),
                            FixedClock("2026-06-02T12:00:00+00:00"),
                            judge=BlockingJudge())
    results = route_findings(
        active_defects(_scan()), policy=WardlineCellPolicy.SURFACE_OVERRIDE,
        agent_id="agent-1", resolve=lambda q: (EntityKey.from_locator(q or "unknown"), {}),
        engine=eng)
    # The judge blocked the wardline-routed override; the coached verdict is recorded.
    assert results[0]["accepted"] is False
    rec = eng.trail()[0]
    assert rec["extensions"]["judge_verdict"] == "BLOCKED"
    assert rec["extensions"]["wardline"]["fingerprint"] == "fp1"
```

- [ ] **Step 2: Run to verify**

Run: `python -m pytest tests/wardline/test_coached_routing.py -v`
Expected: PASS. (The governor passes through to `engine.submit_override`, which runs the
judge when one is injected — so the coached path is reachable. If it does NOT pass, the
defect is in the governor's override call; fix it there, do not weaken the test.)

- [ ] **Step 3: Commit**

```bash
git add tests/wardline/test_coached_routing.py
git commit -m "test(wardline): coached surface-override path records a judge verdict (WP-A6)"
```

---

## Task 5: Docs + full-suite verification

**Files:**
- Modify: `docs/superpowers/specs/2026-06-02-not-yets-completion-design.md`

- [ ] **Step 1:** Append " — ✅ done 2026-06-02" to the WP-A4, WP-A5, and WP-A6 headings (under "### Track 3 — Wardline routing breadth").

- [ ] **Step 2: Full suite green, zero warnings**

Run: `python -m pytest -q`
Expected: all green (was 175; +~9 new tests). Confirm the count and zero warnings.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-06-02-not-yets-completion-design.md
git commit -m "docs: mark WP-A4/A5/A6 Wardline routing breadth complete"
```

---

## Self-review — WP coverage

| WP | Exit criterion (design spec) | Proven by |
|---|---|---|
| A4 | `WardlineCellPolicy` gains a no-gate member; routes without opening an override/sign-off; the finding is still logged | Task 1 (`test_surface_only_records_a_non_gating_event` asserts `wardline_surfaced` in the trail + no `accepted`/`cleared`), Task 3 (endpoint) |
| A4 | the no-gate cell needs no sign-off gate | Task 1 (`test_surface_only_needs_no_signoff_gate`) |
| A5 | per-severity cell selection; `--fail-on`/exit-class is an input; mixed-severity scan routes each finding to the right cell; one-cell-per-scan remains | Task 2 (`test_cell_map_routes_each_finding_by_severity`, fallback, exactly-one), Task 3 (`test_scan_results_cell_by_severity_routes_per_finding`, `…single_cell_still_works`) |
| A6 | coached surface-override path records a judge verdict | Task 4 (`test_coached_wardline_path_records_a_judge_verdict`) |
| (constraint) | the proven `legis_e2e` single-`cell` handshake does not regress | Task 3 (`test_scan_results_single_cell_still_works`); `ScanResultsIn.cell` preserved |

**Out of scope:** Wardline→protected-cell routing (conflates routing axis with the 2×2 grid — separate WP if ever wanted); Wardline→legis hop signature (WP-B4, sibling-gated). Other tracks per the design spec.
