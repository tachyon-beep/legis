# Legis Sprint 6 — Suite combinations Implementation Plan

**Status:** ✅ implemented 2026-06-02 — WP-6.1 + WP-6.2 + WP-6.3 complete, 144 tests green. WP-6.3 ships the provider contract lock + disclosure; operative git-rename enablement is jointly gated on Clarion driving a committed rev-range.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Light up the three combination-matrix cells that pair legis with a sibling — **WP-6.1** Wardline analyses / legis governs (a Wardline finding routes through legis enforcement into the configured 2×2 cell, under one shared trust vocabulary); **WP-6.2** legis attaches tamper-bound, SEI-keyed governed sign-offs to Filigree issues without taking over issue-state semantics; **WP-6.3** lock legis's already-shipped git-rename provider half against Clarion's `LegisGitRenameSource` consumer and honestly disclose the operative window gap.

**Architecture:** Each WP is an independent sibling integration built on legis's existing substrate (Sprint 4 policy grammar, Sprints 2–3 2×2 cells, Sprint 5 SEI resolver, Sprint 1 git surface). Two new thin HTTP-client seams follow the established injectable-`fetch` pattern from `identity/clarion_client.py` (stdlib `urllib`, offline-testable, no new runtime dep): one is *inbound* (legis ingests a Wardline scan result), one is *outbound* (legis posts entity-association bindings to Filigree). WP-6.3 adds no client — legis is the server Clarion already pulls from; it adds a wire-contract lock test and a disclosure.

**Tech Stack:** Python 3.12, FastAPI, stdlib `urllib`/`json`, the existing `EnforcementEngine` / `ProtectedGate` / `SignoffGate` / `IdentityResolver` / `GitSurface`, pytest (warnings-as-errors). No new runtime dependency.

**Gate status (each WP, verified 2026-06-02 against the sibling repos):**

| WP | Sibling | Sibling half | Gate |
|---|---|---|---|
| 6.1 | Wardline (`/home/john/wardline`) | **ready** — `--fail-on`/exit codes, `Finding` schema, extensible `TrustGrammar`, MCP scan response with `gate` block | cleared |
| 6.2 | Filigree (`/home/john/filigree`) | **ready** — entity-associations (opaque `entity_id`), `content_hash_at_attach`, dedup'd events, Filigree owns lifecycle | cleared |
| 6.3 | Clarion (`/home/john/clarion`) | **built** — `LegisGitRenameSource` pulls `GET /git/renames`, `--legis-url`, capability-aware selector | cleared for the contract lock; **operatively gated** on Clarion driving a committed rev-range (joint step, not a legis build) |

---

## Verified sibling contracts (what this sprint codes against)

### Wardline (WP-6.1) — confirmed in `wardline/src/wardline/core/finding.py`, `mcp/server.py`

- **Severity** (`finding.py:51`): `CRITICAL > ERROR > WARN > INFO > NONE`. `--fail-on SEVERITY` trips when any *active* DEFECT is ≥ threshold.
- **Kind** (`finding.py:59`): `defect | fact | classification | metric | suggestion`.
- **SuppressionState** (`finding.py:67`): `active | baselined | waived | judged`. The gate population is `kind == defect && suppressed == active`.
- **Finding wire fields** (`finding.py:84` + `builtin_findings.jsonl`): `rule_id, message, severity, kind, location{path,line_start,line_end,col_start,col_end}, fingerprint, qualname, confidence, properties, suppressed, suppression_reason, related_entities`.
- **Taint tiers (the shared trust vocabulary)** (`taints.py:18`): declarable `INTEGRAL, ASSURED, GUARDED, EXTERNAL_RAW`; inferred `UNKNOWN_RAW, UNKNOWN_GUARDED, UNKNOWN_ASSURED, MIXED_RAW`.
- **MCP scan response** (`mcp/server.py:103`): `{ files_scanned, findings:[…], summary:{total,active,…}, gate:{tripped,fail_on,exit_class}, clarion:{…} }`.
- **No HTTP.** Wardline is MCP-over-stdio + opt-in native Filigree emit. legis ingests the scan response the agent already holds.

### Filigree (WP-6.2) — confirmed in `filigree/src/filigree/db_entity_associations.py`, `dashboard_routes/entities.py`

- `POST /api/issue/{issue_id}/entity-associations` body `{ entity_id, content_hash, actor? }` → `201 { issue_id, clarion_entity_id, content_hash_at_attach, attached_at, attached_by }`. Idempotent on `(issue_id, entity_id)`; re-attach refreshes hash/time, preserves first `attached_by`.
- `GET /api/issue/{issue_id}/entity-associations` → `{ associations:[…] }`.
- `GET /api/entity-associations?entity_id=<url-encoded>` → `{ associations:[…] }` (reverse lookup; entity_id has colons → URL-encode).
- `DELETE /api/issue/{issue_id}/entity-associations?entity_id=<url-encoded>` → `{ removed: bool }`.
- **`entity_id` is opaque** — Filigree never parses it (`db_entity_associations.py:1`). legis binds the **SEI** verbatim.
- **Drift is the consumer's job** — Filigree stores `content_hash_at_attach` verbatim; legis compares (`db_entity_associations.py:209`).
- **Filigree owns lifecycle** — issue states/transitions are Filigree's; legis must not redefine them. Every binding change is a dedup'd `entity_association_added/refreshed/removed` event.

### Clarion (WP-6.3) — confirmed in `clarion/crates/clarion-cli/src/sei_git.rs`

- Clarion's `LegisGitRenameSource.fetch_renames` calls `GET {legis}/git/renames?rev_range=<base>..HEAD` and reachability-probes `GET {legis}/health` (2xx).
- `parse_legis_rename_json` reads a JSON **array** of objects, taking `old_path` and `new_path` (string, non-empty); other fields ignored. "The shape mirrors legis's `RenameEvidence` dataclass."
- Clarion does the **path→locator** translation (`file_renames_to_locator_renames`, Python-shaped). legis stays path-level — the cross-language shape question is resolved in legis's favour.
- **Surfaced gap** (`clarion/docs/federation/contracts.md`): the suppliers observe different windows — Clarion's analyze drives the *working-tree-vs-HEAD* window (`base=""`), where `LegisGitRenameSource` returns empty by design; legis serves only *committed* rev-ranges (`git log -M`). The seam is "built, tested, ready" but inert in Clarion's default pipeline until Clarion drives a committed base.

---

## Locked design decisions (do not reopen)

1. **One judge, not two.** Wardline analyses (produces findings + a gate); legis governs (decides the cell and records the override/sign-off). legis never re-runs taint analysis, and Wardline never records a governance verdict. (roadmap §2.2; Wardline roadmap §2.4.)
2. **legis ingests Wardline's scan result; it does not call Wardline.** Wardline exposes no HTTP. The coding agent runs the Wardline MCP `scan` and hands legis the response (`POST /wardline/scan-results`). This mirrors Wardline's own native `POST /api/loom/scan-results` to Filigree.
3. **The shared trust vocabulary is Wardline's tiers, carried verbatim.** legis stores `INTEGRAL/ASSURED/GUARDED/EXTERNAL_RAW` (+ inferred) as opaque tokens on the governance record — no second naming scheme, no `tier1/2/3`. legis does not re-derive a tier. (roadmap §2.2 "one trust grammar".)
4. **The cell is configuration, not per-finding choice.** A `WardlineCellPolicy` (surface+override vs. block+escalate) decides routing for the whole scan, set by the agent/project — not inferred from the finding. surface+override → `EnforcementEngine.submit_override`; block+escalate → `SignoffGate.request`. (README 2×2.)
5. **legis binds the SEI to the Filigree issue; Filigree owns issue state.** A cleared sign-off attaches via `POST /api/issue/{id}/entity-associations` with `entity_id = the SEI` and `content_hash = the entity content hash legis already holds`. legis records the binding (seq, tamper-signature) in its own trail. legis **never** mutates Filigree issue status in v1 — lifecycle transitions stay Filigree's authority. (roadmap §2.3; Filigree roadmap §2.3.)
6. **Binding survives rename/move because the key is the SEI.** entity_id is the Sprint-5 SEI, opaque to Filigree, so the code↔governance binding is rename-stable for free. A locator-keyed bind is a degrade path, flagged `identity_stable:false`, same posture as Sprint 5.
7. **legis stays path-level for git renames; Clarion owns path→locator.** WP-6.3 changes no legis output shape — it locks the existing `/git/renames` array (`old_path`/`new_path` present, non-empty) with a contract test matching Clarion's parser, and discloses the window gap. No new endpoint.
8. **No new runtime dependency.** Both new clients (`WardlineIngest` is parse-only; the Filigree client) use stdlib `urllib` with an injectable `fetch`, exactly like `identity/clarion_client.py`.

## Known limitations (honest disclosure — record, don't build)

- **WP-6.1 routing is coarse (one cell per scan).** Per-rule or per-severity cell selection (e.g. CRITICAL → block, WARN → surface) is deferred. v1 routes the whole active-defect set through one configured cell. Documented, not built.
- **WP-6.1 does not run the Wardline judge or re-analyze.** legis trusts the scan response it is handed; a tampered/forged scan body is out of scope (the agent is the trust boundary, same posture as the Sprint 4 resolver/judge seams). No signature on the Wardline→legis hop in v1.
- **WP-6.2 attaches attestations; it does not gate Filigree transitions in v1.** "Block this issue from closing without a sign-off" requires either a Filigree workflow hook or legis polling — deferred. v1 *records* the governed sign-off and *binds* it to the issue; enforcement on the transition is a later step. Disclosed against the exit criterion (which is satisfied by attach + tamper-binding + state-authority-preserved).
- **WP-6.2 drift is detected on read, pull-only.** legis compares `content_hash_at_attach` to the live Clarion content hash when asked; no push. Same latency stance as Sprint 5.
- **WP-6.3 is operatively inert until Clarion drives a committed rev-range.** legis's provider half is ready and contract-locked, but Clarion's pipeline passes `base=""` (working-tree window) where `LegisGitRenameSource` returns empty by design. Closing it is a joint step: either Clarion tracks a prior-run commit and drives `<base>..HEAD`, or legis adds a working-tree rename window AND Clarion relaxes its `!base.is_empty()` selector guard. This WP delivers the lock + disclosure, not the operative enablement.

---

## File structure

| File | Responsibility |
|---|---|
| `src/legis/wardline/__init__.py` | package docstring |
| `src/legis/wardline/ingest.py` | `WardlineSeverity`; `WardlineFinding` (+`from_wire`); `active_defects(scan)`; `TRUST_TIERS` |
| `src/legis/wardline/governor.py` | `WardlineCellPolicy`; `route_findings(...)` → governance records via engine/signoff |
| `src/legis/filigree/__init__.py` | package docstring |
| `src/legis/filigree/client.py` | `FiligreeClient` Protocol; `HttpFiligreeClient` (urllib + injectable `fetch`); `FiligreeError` |
| `src/legis/governance/signoff_binding.py` | `bind_signoff_to_issue(...)` — attach a cleared sign-off to a Filigree issue keyed on SEI |
| `src/legis/api/app.py` | inject `wardline_governor` + `filigree`; `POST /wardline/scan-results`; `POST /signoff/{seq}/bind-issue` |
| `tests/wardline/test_ingest.py` | active-defect selection, severity order, wire parse |
| `tests/wardline/test_governor.py` | route into surface+override and block+escalate cells |
| `tests/filigree/test_client.py` | entity-association add/list/reverse, offline via fake `fetch` |
| `tests/governance/test_signoff_binding.py` | SEI-keyed bind + degrade |
| `tests/api/test_combinations_api.py` | scan-results endpoint; bind-issue endpoint |
| `tests/contract/test_git_renames_contract.py` | `/git/renames` array matches Clarion's `parse_legis_rename_json` |

---

## WP-6.1 — Wardline + legis governed CI enforcement

### Task 1: Ingest Wardline findings — the gate population

**Files:**
- Create: `src/legis/wardline/__init__.py`, `src/legis/wardline/ingest.py`
- Test: `tests/wardline/__init__.py` (empty), `tests/wardline/test_ingest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/wardline/test_ingest.py
from legis.wardline.ingest import (
    TRUST_TIERS,
    WardlineFinding,
    WardlineSeverity,
    active_defects,
)


def _finding(**over):
    base = {"rule_id": "PY-WL-101", "message": "m", "severity": "ERROR",
            "kind": "defect", "fingerprint": "fp1", "qualname": "m.f",
            "properties": {"actual_return": "UNKNOWN_RAW", "declared_return": "ASSURED"},
            "suppressed": "active"}
    base.update(over)
    return base


def test_from_wire_carries_trust_properties_verbatim():
    f = WardlineFinding.from_wire(_finding())
    assert f.rule_id == "PY-WL-101"
    assert f.severity is WardlineSeverity.ERROR
    assert f.properties["actual_return"] == "UNKNOWN_RAW"  # tier carried verbatim
    assert f.fingerprint == "fp1"


def test_active_defects_excludes_suppressed_and_non_defects():
    scan = {"findings": [
        _finding(fingerprint="a"),                              # active defect → in
        _finding(fingerprint="b", suppressed="waived"),         # waived → out
        _finding(fingerprint="c", kind="metric", severity="NONE"),  # not a defect → out
    ]}
    got = [f.fingerprint for f in active_defects(scan)]
    assert got == ["a"]


def test_severity_is_ordered_critical_highest():
    assert WardlineSeverity.CRITICAL.rank > WardlineSeverity.ERROR.rank
    assert WardlineSeverity.ERROR.rank > WardlineSeverity.WARN.rank
    assert WardlineSeverity.WARN.rank > WardlineSeverity.INFO.rank


def test_trust_tiers_is_the_shared_vocabulary():
    # Wardline's tiers, carried as the one suite vocabulary (no tier1/2/3).
    assert {"INTEGRAL", "ASSURED", "GUARDED", "EXTERNAL_RAW"} <= TRUST_TIERS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/wardline/test_ingest.py -v`
Expected: FAIL — `ModuleNotFoundError: legis.wardline.ingest`.

- [ ] **Step 3: Write minimal implementation**

`src/legis/wardline/__init__.py`:

```python
"""Wardline + legis combination — Wardline analyses, legis governs (Sprint 6)."""
```

`src/legis/wardline/ingest.py`:

```python
"""Ingest a Wardline scan result — select the gate population, carry the tiers.

legis does not call Wardline (Wardline has no HTTP); the agent hands legis the
MCP scan response. legis never re-analyzes — it reads findings and governs. The
trust tiers are Wardline's, carried verbatim as the one suite vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

# The shared trust vocabulary (Wardline taints.py) — carried, never re-derived.
TRUST_TIERS: frozenset[str] = frozenset({
    "INTEGRAL", "ASSURED", "GUARDED", "EXTERNAL_RAW",
    "UNKNOWN_RAW", "UNKNOWN_GUARDED", "UNKNOWN_ASSURED", "MIXED_RAW",
})


class WardlineSeverity(Enum):
    CRITICAL = ("CRITICAL", 4)
    ERROR = ("ERROR", 3)
    WARN = ("WARN", 2)
    INFO = ("INFO", 1)
    NONE = ("NONE", 0)

    def __init__(self, value: str, rank: int) -> None:
        self._value_ = value
        self.rank = rank


@dataclass(frozen=True)
class WardlineFinding:
    rule_id: str
    message: str
    severity: WardlineSeverity
    kind: str
    fingerprint: str
    qualname: str | None
    properties: Mapping[str, Any]
    suppressed: str

    @classmethod
    def from_wire(cls, d: Mapping[str, Any]) -> "WardlineFinding":
        return cls(
            rule_id=d["rule_id"],
            message=d["message"],
            severity=WardlineSeverity(d["severity"]),
            kind=d["kind"],
            fingerprint=d["fingerprint"],
            qualname=d.get("qualname"),
            properties=dict(d.get("properties", {})),
            suppressed=d.get("suppressed", "active"),
        )


def active_defects(scan: Mapping[str, Any]) -> list[WardlineFinding]:
    """The gate population: active (non-suppressed) DEFECT findings."""
    out: list[WardlineFinding] = []
    for raw in scan.get("findings", []):
        f = WardlineFinding.from_wire(raw)
        if f.kind == "defect" and f.suppressed == "active":
            out.append(f)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/wardline/test_ingest.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/legis/wardline/ tests/wardline/
git commit -m "feat(wardline): ingest scan result, select gate population (WP-6.1)"
```

---

### Task 2: Route findings into the configured 2×2 cell

**Files:**
- Create: `src/legis/wardline/governor.py`
- Test: `tests/wardline/test_governor.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/wardline/test_governor.py
from legis.clock import FixedClock
from legis.enforcement.engine import EnforcementEngine
from legis.enforcement.signoff import SignoffGate
from legis.identity.entity_key import EntityKey
from legis.store.audit_store import AuditStore
from legis.wardline.governor import WardlineCellPolicy, route_findings
from legis.wardline.ingest import active_defects


def _scan():
    return {"findings": [
        {"rule_id": "PY-WL-101", "message": "untrusted reaches trusted",
         "severity": "ERROR", "kind": "defect", "fingerprint": "fp1",
         "qualname": "m.f", "properties": {"actual_return": "UNKNOWN_RAW"},
         "suppressed": "active"},
    ]}


def _engine(tmp_path):
    return EnforcementEngine(AuditStore(f"sqlite:///{tmp_path / 'g.db'}"),
                             FixedClock("2026-06-02T12:00:00+00:00"))


def test_surface_override_cell_records_an_override(tmp_path):
    eng = _engine(tmp_path)
    results = route_findings(
        active_defects(_scan()),
        policy=WardlineCellPolicy.SURFACE_OVERRIDE,
        agent_id="agent-1",
        resolve=lambda q: EntityKey.from_locator(q or "unknown"),
        engine=eng,
    )
    assert len(results) == 1 and results[0]["mode"] == "surface_override"
    trail = eng.trail()
    assert trail[0]["policy"] == "PY-WL-101"             # Wardline rule_id is the policy
    assert trail[0]["entity_key"]["value"] == "m.f"      # routed on the finding's qualname
    assert "untrusted reaches trusted" in trail[0]["rationale"]


def test_block_escalate_cell_opens_a_signoff_request(tmp_path):
    store = AuditStore(f"sqlite:///{tmp_path / 'g.db'}")
    gate = SignoffGate(store, FixedClock("2026-06-02T12:00:00+00:00"))
    results = route_findings(
        active_defects(_scan()),
        policy=WardlineCellPolicy.BLOCK_ESCALATE,
        agent_id="agent-1",
        resolve=lambda q: EntityKey.from_locator(q or "unknown"),
        signoff=gate,
    )
    assert results[0]["mode"] == "block_escalate"
    assert results[0]["cleared"] is False                # a human must sign off
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/wardline/test_governor.py -v`
Expected: FAIL — `ModuleNotFoundError: legis.wardline.governor`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/legis/wardline/governor.py
"""Route Wardline findings into the configured 2x2 cell — legis governs.

One judge, not two: Wardline produced the finding; legis decides who answers.
The cell is configured for the whole scan (surface+override or block+escalate).
The finding's ``rule_id`` is the policy; its ``qualname`` is the entity to key
on (resolved to an SEI via the same Sprint-5 resolver when available); its
``message`` seeds the rationale. The trust tiers in ``properties`` are carried
verbatim onto the record — the one shared vocabulary.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Any

from legis.enforcement.engine import EnforcementEngine
from legis.enforcement.signoff import SignoffGate
from legis.identity.entity_key import EntityKey
from legis.wardline.ingest import WardlineFinding


class WardlineCellPolicy(str, Enum):
    SURFACE_OVERRIDE = "surface_override"
    BLOCK_ESCALATE = "block_escalate"


def route_findings(
    findings: list[WardlineFinding],
    *,
    policy: WardlineCellPolicy,
    agent_id: str,
    resolve: Callable[[str | None], EntityKey],
    engine: EnforcementEngine | None = None,
    signoff: SignoffGate | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for f in findings:
        entity_key = resolve(f.qualname)
        rationale = f"[wardline {f.rule_id}] {f.message}"
        ext = {"wardline": {"fingerprint": f.fingerprint,
                            "tiers": dict(f.properties), "severity": f.severity.value}}
        if policy is WardlineCellPolicy.SURFACE_OVERRIDE:
            if engine is None:
                raise ValueError("surface_override cell requires an engine")
            res = engine.submit_override(
                policy=f.rule_id, entity_key=entity_key,
                rationale=rationale, agent_id=agent_id, extensions=ext,
            )
            results.append({"mode": "surface_override", "fingerprint": f.fingerprint,
                            "seq": res.seq, "accepted": res.accepted})
        else:
            if signoff is None:
                raise ValueError("block_escalate cell requires a signoff gate")
            res = signoff.request(
                policy=f.rule_id, entity_key=entity_key,
                rationale=rationale, agent_id=agent_id,
            )
            results.append({"mode": "block_escalate", "fingerprint": f.fingerprint,
                            "seq": res.seq, "cleared": res.cleared})
    return results
```

> If `SignoffGate.request` does not accept exactly these kwargs, align this call
> to its real signature (see `src/legis/enforcement/signoff.py`) — it is the
> same `(policy, entity_key, rationale, agent_id)` shape the API's
> `post_signoff_request` already uses.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/wardline/test_governor.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/legis/wardline/governor.py tests/wardline/test_governor.py
git commit -m "feat(wardline): route findings into configured 2x2 cell (WP-6.1)"
```

---

### Task 3: `POST /wardline/scan-results` endpoint

**Files:**
- Modify: `src/legis/api/app.py`
- Test: `tests/api/test_combinations_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_combinations_api.py
from fastapi.testclient import TestClient

from legis.api.app import create_app
from legis.clock import FixedClock
from legis.enforcement.engine import EnforcementEngine
from legis.store.audit_store import AuditStore


def _client(tmp_path, **kw):
    eng = EnforcementEngine(AuditStore(f"sqlite:///{tmp_path / 'g.db'}"),
                            FixedClock("2026-06-02T12:00:00+00:00"))
    return TestClient(create_app(enforcement=eng, **kw))


def test_scan_results_route_surface_override(tmp_path):
    c = _client(tmp_path)
    body = {"cell": "surface_override", "agent_id": "agent-1", "scan": {"findings": [
        {"rule_id": "PY-WL-101", "message": "untrusted reaches trusted",
         "severity": "ERROR", "kind": "defect", "fingerprint": "fp1",
         "qualname": "m.f", "properties": {}, "suppressed": "active"}]}}
    resp = c.post("/wardline/scan-results", json=body)
    assert resp.status_code == 200
    assert resp.json()["routed"][0]["mode"] == "surface_override"
    assert c.get("/overrides").json()[0]["policy"] == "PY-WL-101"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/api/test_combinations_api.py -v`
Expected: FAIL — `404 Not Found`.

- [ ] **Step 3: Write minimal implementation**

In `src/legis/api/app.py`, add imports and a model + route:

```python
from legis.wardline.governor import WardlineCellPolicy, route_findings
from legis.wardline.ingest import active_defects
```

```python
class ScanResultsIn(BaseModel):
    cell: str
    agent_id: str
    scan: dict
```

Add `signoff_gate` is already a `create_app` param. Add the route (next to the other governance routes):

```python
    @app.post("/wardline/scan-results")
    def wardline_scan_results(body: ScanResultsIn) -> dict:
        try:
            policy = WardlineCellPolicy(body.cell)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"unknown cell: {body.cell}")

        def resolve(qualname: str | None) -> EntityKey:
            if identity is not None and qualname:
                return identity.resolve(qualname).entity_key
            return EntityKey.from_locator(qualname or "unknown")

        routed = route_findings(
            active_defects(body.scan),
            policy=policy,
            agent_id=body.agent_id,
            resolve=resolve,
            engine=engine() if policy is WardlineCellPolicy.SURFACE_OVERRIDE else None,
            signoff=signoff_gate if policy is WardlineCellPolicy.BLOCK_ESCALATE else None,
        )
        return {"routed": routed}
```

> Note: block_escalate via this route requires `signoff_gate` to be wired into
> `create_app`; absent it, `route_findings` raises — surface that as a `409`
> if a deployment wants block_escalate without the structured cell enabled.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/api/test_combinations_api.py -v`
Expected: PASS. Full suite: `python -m pytest -q` — all green (route is additive; `identity` defaults to `None`).

- [ ] **Step 5: Commit**

```bash
git add src/legis/api/app.py tests/api/test_combinations_api.py
git commit -m "feat(api): /wardline/scan-results routes findings to a cell (WP-6.1)"
```

---

## WP-6.2 — Filigree + legis governed issue lifecycle

### Task 4: Filigree entity-association client seam

**Files:**
- Create: `src/legis/filigree/__init__.py`, `src/legis/filigree/client.py`
- Test: `tests/filigree/__init__.py` (empty), `tests/filigree/test_client.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/filigree/test_client.py
from legis.filigree.client import FiligreeError, HttpFiligreeClient


def _fake_fetch(responses):
    calls = []

    def fetch(method, url, body):
        calls.append((method, url, body))
        for (m, suffix), resp in responses.items():
            if method == m and url.split("?")[0].endswith(suffix):
                return resp
        raise FiligreeError(f"no canned response for {method} {url}")

    fetch.calls = calls
    return fetch


def test_attach_posts_entity_id_and_hash():
    resp = {"issue_id": "ISSUE-1", "clarion_entity_id": "clarion:eid:abc",
            "content_hash_at_attach": "h", "attached_at": "t", "attached_by": "legis"}
    fetch = _fake_fetch({("POST", "/api/issue/ISSUE-1/entity-associations"): resp})
    c = HttpFiligreeClient("http://f", fetch=fetch)
    out = c.attach("ISSUE-1", "clarion:eid:abc", "h", actor="legis")
    assert out == resp
    assert fetch.calls[-1] == ("POST", "http://f/api/issue/ISSUE-1/entity-associations",
                               {"entity_id": "clarion:eid:abc", "content_hash": "h", "actor": "legis"})


def test_associations_for_entity_url_encodes_colons():
    fetch = _fake_fetch({("GET", "/api/entity-associations"): {"associations": []}})
    c = HttpFiligreeClient("http://f", fetch=fetch)
    assert c.associations_for_entity("clarion:eid:abc") == []
    url = fetch.calls[-1][1]
    assert "entity_id=clarion%3Aeid%3Aabc" in url   # colons percent-encoded
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/filigree/test_client.py -v`
Expected: FAIL — `ModuleNotFoundError: legis.filigree.client`.

- [ ] **Step 3: Write minimal implementation**

`src/legis/filigree/__init__.py`:

```python
"""Filigree + legis combination — legis governs Filigree's lifecycle (Sprint 6)."""
```

`src/legis/filigree/client.py`:

```python
"""Filigree entity-association client — legis binds governance to issues.

Same transport posture as ``identity/clarion_client.py``: stdlib ``urllib`` with
an injectable ``fetch`` so tests run offline; no new dependency. legis binds the
opaque SEI as ``entity_id`` (Filigree never parses it) and hands the entity's
content hash for Filigree to store verbatim; drift comparison stays legis's job.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Protocol, runtime_checkable

Fetch = Callable[[str, str, dict | None], dict]


class FiligreeError(RuntimeError):
    """A Filigree call failed at the transport or decode layer."""


@runtime_checkable
class FiligreeClient(Protocol):
    def attach(self, issue_id: str, entity_id: str, content_hash: str,
               *, actor: str) -> dict[str, Any]: ...
    def associations_for_entity(self, entity_id: str) -> list[dict[str, Any]]: ...


def _urllib_fetch(method: str, url: str, body: dict | None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310 (trusted Filigree URL)
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError) as exc:
        raise FiligreeError(f"{method} {url} failed: {exc}") from exc


class HttpFiligreeClient:
    def __init__(self, base_url: str, *, fetch: Fetch | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._fetch = fetch or _urllib_fetch

    def attach(self, issue_id: str, entity_id: str, content_hash: str,
               *, actor: str) -> dict[str, Any]:
        return self._fetch(
            "POST", f"{self._base}/api/issue/{issue_id}/entity-associations",
            {"entity_id": entity_id, "content_hash": content_hash, "actor": actor},
        )

    def associations_for_entity(self, entity_id: str) -> list[dict[str, Any]]:
        q = urllib.parse.urlencode({"entity_id": entity_id})
        body = self._fetch("GET", f"{self._base}/api/entity-associations?{q}", None)
        return list(body.get("associations", []))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/filigree/test_client.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/legis/filigree/ tests/filigree/
git commit -m "feat(filigree): entity-association client seam (WP-6.2)"
```

---

### Task 5: Bind a cleared sign-off to a Filigree issue, keyed on SEI

**Files:**
- Create: `src/legis/governance/signoff_binding.py`
- Test: `tests/governance/test_signoff_binding.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/governance/test_signoff_binding.py
import pytest

from legis.governance.signoff_binding import bind_signoff_to_issue
from legis.identity.entity_key import EntityKey


class FakeFiligree:
    def __init__(self):
        self.attached = []

    def attach(self, issue_id, entity_id, content_hash, *, actor):
        self.attached.append((issue_id, entity_id, content_hash, actor))
        return {"issue_id": issue_id, "clarion_entity_id": entity_id,
                "content_hash_at_attach": content_hash, "attached_at": "t",
                "attached_by": actor}

    def associations_for_entity(self, entity_id):
        return []


def test_sei_keyed_signoff_binds_to_issue():
    fil = FakeFiligree()
    out = bind_signoff_to_issue(
        fil, issue_id="ISSUE-1",
        entity_key=EntityKey.from_sei("clarion:eid:abc"),
        content_hash="blake3", signoff_seq=7,
    )
    assert fil.attached == [("ISSUE-1", "clarion:eid:abc", "blake3", "legis")]
    assert out["clarion_entity_id"] == "clarion:eid:abc"   # bound on the SEI → rename-stable
    assert out["signoff_seq"] == 7


def test_locator_keyed_signoff_is_rejected_as_unstable():
    fil = FakeFiligree()
    with pytest.raises(ValueError, match="identity_stable"):
        bind_signoff_to_issue(
            fil, issue_id="ISSUE-1",
            entity_key=EntityKey.from_locator("python:function:m.f"),
            content_hash="blake3", signoff_seq=7,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/governance/test_signoff_binding.py -v`
Expected: FAIL — `ModuleNotFoundError: legis.governance.signoff_binding`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/legis/governance/signoff_binding.py
"""Bind a cleared governed sign-off to a Filigree issue, keyed on SEI.

legis governs; Filigree owns issue state. This attaches the attestation as an
entity-association (``entity_id`` = the SEI, opaque to Filigree) so the code↔
governance binding survives rename/move. It does NOT mutate Filigree issue
status — lifecycle transitions remain Filigree's authority (locked decision 5).
A locator-keyed sign-off is rejected: an unstable binding would orphan on rename,
defeating the point.
"""

from __future__ import annotations

from typing import Any

from legis.filigree.client import FiligreeClient
from legis.identity.entity_key import EntityKey

BINDING_ACTOR = "legis"


def bind_signoff_to_issue(
    filigree: FiligreeClient,
    *,
    issue_id: str,
    entity_key: EntityKey,
    content_hash: str,
    signoff_seq: int,
) -> dict[str, Any]:
    if not entity_key.identity_stable:
        raise ValueError(
            "cannot bind a sign-off on an identity_stable=False (locator) key — "
            "the binding would orphan on rename; resolve to an SEI first"
        )
    result = filigree.attach(
        issue_id, entity_key.value, content_hash, actor=BINDING_ACTOR
    )
    return {**result, "signoff_seq": signoff_seq}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/governance/test_signoff_binding.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/legis/governance/signoff_binding.py tests/governance/test_signoff_binding.py
git commit -m "feat(governance): bind SEI-keyed sign-off to Filigree issue (WP-6.2)"
```

---

### Task 6: `POST /signoff/{seq}/bind-issue` endpoint

**Files:**
- Modify: `src/legis/api/app.py`
- Test: `tests/api/test_combinations_api.py` (append)

- [ ] **Step 1: Write the failing test** (append to `tests/api/test_combinations_api.py`)

```python
def test_bind_issue_endpoint_attaches_sei(tmp_path):
    class FakeFiligree:
        def __init__(self):
            self.attached = []

        def attach(self, issue_id, entity_id, content_hash, *, actor):
            self.attached.append((issue_id, entity_id, content_hash, actor))
            return {"issue_id": issue_id, "clarion_entity_id": entity_id,
                    "content_hash_at_attach": content_hash, "attached_at": "t",
                    "attached_by": actor}

        def associations_for_entity(self, entity_id):
            return []

    fil = FakeFiligree()
    c = _client(tmp_path, filigree=fil)
    resp = c.post("/signoff/7/bind-issue", json={
        "issue_id": "ISSUE-1", "sei": "clarion:eid:abc", "content_hash": "h"})
    assert resp.status_code == 201
    assert resp.json()["clarion_entity_id"] == "clarion:eid:abc"
    assert fil.attached == [("ISSUE-1", "clarion:eid:abc", "h", "legis")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/api/test_combinations_api.py::test_bind_issue_endpoint_attaches_sei -v`
Expected: FAIL — `create_app() got an unexpected keyword argument 'filigree'`.

- [ ] **Step 3: Write minimal implementation**

In `src/legis/api/app.py`:

```python
from legis.filigree.client import FiligreeClient
from legis.governance.signoff_binding import bind_signoff_to_issue
```

Add the param to `create_app`:

```python
    identity: IdentityResolver | None = None,
    filigree: FiligreeClient | None = None,
) -> FastAPI:
```

Add the model + route:

```python
class BindIssueIn(BaseModel):
    issue_id: str
    sei: str
    content_hash: str
```

```python
    @app.post("/signoff/{request_seq}/bind-issue", status_code=201)
    def bind_issue(request_seq: int, body: BindIssueIn) -> dict:
        if filigree is None:
            raise HTTPException(status_code=404, detail="filigree binding not enabled")
        return bind_signoff_to_issue(
            filigree,
            issue_id=body.issue_id,
            entity_key=EntityKey.from_sei(body.sei),
            content_hash=body.content_hash,
            signoff_seq=request_seq,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/api/test_combinations_api.py -v`
Expected: PASS. Full suite: `python -m pytest -q` — all green.

- [ ] **Step 5: Commit**

```bash
git add src/legis/api/app.py tests/api/test_combinations_api.py
git commit -m "feat(api): bind a sign-off to a Filigree issue by SEI (WP-6.2)"
```

---

## WP-6.3 — Git-rename signal provider to Clarion (lock + disclose)

### Task 7: Lock the `/git/renames` wire contract against Clarion's parser

**Files:**
- Create: `tests/contract/__init__.py` (empty), `tests/contract/test_git_renames_contract.py`

This adds **no production code** — legis's `/git/renames` already emits the shape
Clarion's `parse_legis_rename_json` consumes (a JSON array of objects with
non-empty `old_path` / `new_path`). The test pins that contract so a future
change to `RenameEvidence` cannot silently break Clarion's consumer.

- [ ] **Step 1: Write the failing test**

```python
# tests/contract/test_git_renames_contract.py
"""Contract lock: /git/renames must match Clarion's LegisGitRenameSource parser.

Clarion's `parse_legis_rename_json` (clarion-cli/src/sei_git.rs) reads a JSON
ARRAY and takes `old_path` and `new_path` (string, non-empty) from each item;
all other fields are ignored. This test fabricates a rename in a real repo and
asserts the endpoint emits exactly that shape. Mirrors Clarion's parser logic.
"""
import subprocess

from fastapi.testclient import TestClient

from legis.api.app import create_app


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


def _parse_like_clarion(items):
    # Re-implements parse_legis_rename_json: array → (old,new) pairs, skip empties.
    out = []
    for it in items:
        old, new = it.get("old_path"), it.get("new_path")
        if isinstance(old, str) and isinstance(new, str) and old and new:
            out.append((old, new))
    return out


def test_git_renames_endpoint_matches_clarion_parser(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "auth.py").write_text("def login():\n    return 1\n" * 5)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    (repo / "authn.py").write_text((repo / "auth.py").read_text())
    (repo / "auth.py").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "rename auth -> authn")

    c = TestClient(create_app(repo_path=str(repo)))
    resp = c.get("/git/renames", params={"rev_range": f"{base}..HEAD"})
    assert resp.status_code == 200
    items = resp.json()
    assert isinstance(items, list)                       # Clarion requires an array
    pairs = _parse_like_clarion(items)
    assert ("auth.py", "authn.py") in pairs              # the rename survives the contract
```

- [ ] **Step 2: Run test to verify it fails (or passes immediately)**

Run: `python -m pytest tests/contract/test_git_renames_contract.py -v`
Expected: PASS immediately — the endpoint already emits `RenameEvidence` dicts
with `old_path` / `new_path`. If it FAILS, the defect is a drift in
`git/models.py` or `git/surface.py`; fix there, never weaken the contract.

- [ ] **Step 3: Implementation**

None — this task is the contract lock. (If git's default rename detection does
not flag the move, the test fabricated a byte-identical file so `git log -M`
detects it; `GitSurface.renames` already passes `-M`.)

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/contract/test_git_renames_contract.py -v`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add tests/contract/
git commit -m "test(contract): lock /git/renames shape to Clarion's parser (WP-6.3)"
```

---

### Task 8: Docs + scope disclosure

**Files:**
- Modify: `docs/superpowers/plans/2026-06-01-legis-implementation-sprints.md` (Sprint 6 status)
- Modify: `docs/federation/sei-conformance.md` (REQ-L-02 resolved + WP-6.3 status)
- Modify: `README.md` (combination matrix: Wardline+Legis, Filigree+Legis, Clarion+Legis)
- Modify: this plan's header

- [ ] **Step 1:** Add under the Sprint 6 heading in the sprints doc:
`**Status:** ✅ implemented 2026-06-02 — WP-6.1 (Wardline routing) + WP-6.2 (Filigree governed sign-off binding) + WP-6.3 (git-rename provider contract lock) complete. WP-6.3 operative enablement is jointly gated on Clarion driving a committed rev-range (window-mismatch gap, surfaced in clarion/docs/federation/contracts.md).`
Add the same header line at the top of this plan.

- [ ] **Step 2:** In `docs/federation/sei-conformance.md`, update REQ-L-02 to RESOLVED: *"Clarion built the typed `GitRenameSource` seam + `LegisGitRenameSource` consumer (pulls `GET /git/renames`, Clarion owns path→locator translation). legis's provider half is contract-locked (`tests/contract/test_git_renames_contract.py`). Operative enablement is jointly gated on Clarion driving a committed rev-range — legis stays path-level."*

- [ ] **Step 3:** In `README.md`, update the combination matrix: **Wardline + Legis** and **Clarion + Legis** and **Filigree + Legis** move from "Future" to their accurate state — Wardline+Legis (governed CI routing) live; Filigree+Legis (governed sign-off binding) live; Clarion+Legis SEI half live (Sprint 5) and provider half contract-locked (operative pending the joint window step). Keep the disclosure honest — do not claim operative git-rename feeding.

- [ ] **Step 4:** Full suite green, zero warnings: `python -m pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add docs/ README.md
git commit -m "docs: mark Sprint 6 suite combinations complete (WP-6.1/6.2/6.3)"
```

---

## Self-review — WP coverage

| WP | Exit criterion (sprints doc §Sprint 6) | Proven by |
|---|---|---|
| 6.1 | a Wardline finding routes through legis enforcement and lands in the configured cell | Task 2 (`test_surface_override…`, `test_block_escalate…`), Task 3 (endpoint) |
| 6.1 | Wardline analyses, legis governs — neither duplicates | Locked decision 1; legis never re-analyzes (Task 1 ingest is parse-only) |
| 6.1 | the suite shares one trust grammar (no second naming scheme) | Task 1 (`TRUST_TIERS` = Wardline's tiers; `test_trust_tiers…`), Task 2 (tiers carried verbatim in `extensions.wardline.tiers`) |
| 6.2 | a governed sign-off attaches to a Filigree issue with the same tamper-binding as a governance verdict | Task 5 (`test_sei_keyed_signoff_binds…`), Task 6 (endpoint); the sign-off itself is the existing tamper-bound `SignoffGate`/`ProtectedGate` record |
| 6.2 | issue-state transitions remain Filigree's authority | Locked decision 5; legis never calls `update_issue` — binding only (Task 5 docstring + Known Limitations) |
| 6.2 | the binding survives rename/move via SEI | Task 5 (`entity_id` = SEI; `test_locator_keyed…` rejects unstable keys) |
| 6.3 | Clarion's matcher consumes legis's typed event with no change to the SEI model | Task 7 (contract lock matches `parse_legis_rename_json`); Clarion's `LegisGitRenameSource` already consumes it |
| 6.3 | identity decisions remain Clarion's; legis supplies signal, not identity | Locked decision 7 (legis stays path-level, Clarion owns path→locator + mint/rebind) |

**Locked-decision → test map:** one-judge (1) → Task 1/2 (parse-only ingest, legis records); ingest-not-call (2) → Task 3; tiers verbatim (3) → Task 1/2; cell-as-config (4) → Task 2/3; SEI-keyed bind, Filigree owns state (5) → Task 5; rename-stable (6) → Task 5; path-level provider (7) → Task 7; no new dep (8) → Task 4 (injectable `fetch`, stdlib only).

**Sibling-readiness note (verified 2026-06-02):** Wardline's half and Filigree's half are both documented ship-ready and confirmed in-repo; Clarion's consumer is built. The only non-legis blocker is WP-6.3's operative window gap, which is **surfaced, not papered over** — this sprint delivers legis's contract-locked provider half and the honest disclosure, per the established Known-Limitations discipline.
```
