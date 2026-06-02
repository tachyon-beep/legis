# Roadmap Gap Remediation — Implementation Plan

## Context

The conformance audit (`docs/superpowers/specs/2026-06-02-roadmap-conformance-findings.md`)
compared the roadmap (`2026-06-01-legis-roadmap-to-first-class.md`) against `src/legis` and found
the engine genuinely built (147/147 green) but with **4 Missing** and **12 Partial** claims. This
plan closes all 16, ordered by the findings doc's triage priority. Tests are green now and must stay
green at every commit; every task is TDD (failing test → run → implement → run → commit).

**Confirmed design decisions:**
- **PR metadata (R-1.1-10):** build a recorded POST surface mirroring `CheckSurface` (new
  `src/legis/pulls/`), not a GitHub-API fetch — consistent with how `CheckRun` (also forge-reported)
  is handled, no new network/auth deps.
- **Scope:** all findings, phased.
- **Decorator `source`/`invariant` (R-1.4-06/08):** required + non-empty validated at decoration and
  gate. No fixture churn; deeper invariant↔test semantic linkage is explicitly out of scope.

**Execution sub-skill:** use `superpowers:subagent-driven-development` (fresh subagent per task,
review between) or `superpowers:executing-plans`. Steps use `- [ ]` checkboxes.

**Run tests with:** `.venv/bin/python -m pytest -q` (full) or a path for one file. `filterwarnings=error`
is set, so a new warning fails the suite.

---

## Phase 1 — Protected-cell & suite guarantees

### Task 1: Filigree binding carries an HMAC signature + persists the attestation leg (R-2.3-01c, R-2.3-02)

**Why:** The roadmap says the Filigree sign-off binding has "the same tamper-binding structure as a
governance verdict — HMAC-signed." Today `attach()` transmits only `{entity_id, content_hash, actor}`
(no signature), and `signoff_seq` is added to the return dict but never sent to Filigree. Fix: sign the
binding tuple and transmit both `signoff_seq` and the signature.

**Files:**
- Modify: `src/legis/filigree/client.py` (Protocol + `HttpFiligreeClient.attach`)
- Modify: `src/legis/governance/signoff_binding.py` (`bind_signoff_to_issue`)
- Modify: `src/legis/api/app.py` (`create_app` gains `binding_key`; `bind_issue` passes it)
- Modify tests: `tests/governance/test_signoff_binding.py`, `tests/api/test_combinations_api.py`,
  `tests/filigree/test_client.py`

- [ ] **Step 1: Write failing test** in `tests/governance/test_signoff_binding.py`. Update the existing
  `FakeFiligree.attach` to accept the new kwargs and record them, then add a signed-binding test:

```python
# FakeFiligree.attach becomes:
def attach(self, issue_id, entity_id, content_hash, *, actor, signoff_seq=None, signature=None):
    self.attached.append((issue_id, entity_id, content_hash, actor, signoff_seq, signature))
    return {"issue_id": issue_id, "clarion_entity_id": entity_id,
            "content_hash_at_attach": content_hash, "attached_at": "t", "attached_by": actor}

def test_binding_is_hmac_signed_when_a_key_is_supplied():
    from legis.enforcement.signing import verify
    fil = FakeFiligree()
    key = b"k" * 32
    out = bind_signoff_to_issue(
        fil, issue_id="ISSUE-1", entity_key=EntityKey.from_sei("clarion:eid:abc"),
        content_hash="blake3", signoff_seq=7, key=key,
    )
    sig = out["binding_signature"]
    assert sig.startswith("hmac-sha256:v1:")
    # the same fields, re-signed, verify — and the signature reached Filigree
    assert verify({"issue_id": "ISSUE-1", "entity_id": "clarion:eid:abc",
                   "content_hash": "blake3", "signoff_seq": 7}, sig, key)
    assert fil.attached[0][4] == 7 and fil.attached[0][5] == sig
```

  Also update the two existing assertions in this file (`test_sei_keyed_signoff_binds_to_issue`,
  `test_locator_keyed_signoff_is_rejected_as_unstable`) to the 6-tuple shape; the unsigned case sends
  `signoff_seq=7, signature=None`.

- [ ] **Step 2: Run, expect FAIL** — `.venv/bin/python -m pytest tests/governance/test_signoff_binding.py -q`
  (TypeError: unexpected `key`).

- [ ] **Step 3: Implement.** In `src/legis/filigree/client.py`, extend the Protocol and client:

```python
# Protocol
def attach(self, issue_id: str, entity_id: str, content_hash: str, *, actor: str,
           signoff_seq: int | None = None, signature: str | None = None) -> dict[str, Any]: ...

# HttpFiligreeClient.attach
def attach(self, issue_id, entity_id, content_hash, *, actor,
           signoff_seq=None, signature=None):
    body = {"entity_id": entity_id, "content_hash": content_hash, "actor": actor}
    if signoff_seq is not None:
        body["signoff_seq"] = signoff_seq
    if signature is not None:
        body["signature"] = signature
    return self._fetch(
        "POST", f"{self._base}/api/issue/{issue_id}/entity-associations", body)
```

  In `src/legis/governance/signoff_binding.py`:

```python
from legis.enforcement.signing import sign

def bind_signoff_to_issue(filigree, *, issue_id, entity_key, content_hash, signoff_seq,
                          key: bytes | None = None) -> dict[str, Any]:
    if not entity_key.identity_stable:
        raise ValueError(
            "cannot bind a sign-off on an identity_stable=False (locator) key — "
            "the binding would orphan on rename; resolve to an SEI first")
    signature = None
    if key is not None:
        signature = sign({"issue_id": issue_id, "entity_id": entity_key.value,
                          "content_hash": content_hash, "signoff_seq": signoff_seq}, key)
    result = filigree.attach(issue_id, entity_key.value, content_hash,
                             actor=BINDING_ACTOR, signoff_seq=signoff_seq, signature=signature)
    return {**result, "signoff_seq": signoff_seq, "binding_signature": signature}
```

  In `src/legis/api/app.py`: add `binding_key: bytes | None = None` to `create_app(...)`, and pass
  `key=binding_key` in the `bind_signoff_to_issue(...)` call inside `bind_issue`.

- [ ] **Step 4: Run, expect PASS** — same path. Then update `tests/api/test_combinations_api.py`
  (`_FakeFiligree.attach` to the new kwargs; the `fil.attached == [...]` assertion to the 6-tuple with
  `signoff_seq=req.seq, signature=None`) and `tests/filigree/test_client.py` (the `attach` round-trip
  asserts `signoff_seq`/`signature` ride the POST body when supplied). Run full suite, expect PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat(filigree): HMAC-sign the SEI sign-off binding and transmit the attestation seq (R-2.3-01c/02)"`

---

### Task 2: Wire the override-rate gate into CI via a CLI + GitHub workflow (R-1.3c-17)

**Why:** The gate is computed and exposed at `GET /governance/override-rate` but nothing turns a `FAIL`
into a failing build — there is no `.github/`, Makefile, or CLI consuming it. (Note: `pyproject.toml`
declares `[project.scripts] legis = "legis.cli:main"` but **`src/legis/cli.py` does not exist** — this
task also fixes that latent broken entry point.)

**Files:**
- Create: `src/legis/cli.py`
- Create: `tests/cli/__init__.py`, `tests/cli/test_governance_gate.py`
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Write failing test** `tests/cli/test_governance_gate.py`:

```python
from legis.cli import main
from legis.store.audit_store import AuditStore

def _seed(db_url, *, overrides, accepts):
    store = AuditStore(db_url)
    for _ in range(overrides):
        store.append({"policy": "p", "recorded_at": "t",
                      "extensions": {"judge_verdict": "OVERRIDDEN_BY_OPERATOR"}})
    for _ in range(accepts):
        store.append({"policy": "p", "recorded_at": "t",
                      "extensions": {"judge_verdict": "ACCEPTED"}})

def test_gate_exits_nonzero_when_override_rate_breached(tmp_path, capsys):
    db = f"sqlite:///{tmp_path / 'g.db'}"
    _seed(db, overrides=10, accepts=10)          # 50% > 0.2 threshold, n=20 >= min_sample
    code = main(["governance-gate", "--db", db])
    assert code == 1
    assert "FAIL" in capsys.readouterr().out

def test_gate_exits_zero_when_within_threshold(tmp_path, capsys):
    db = f"sqlite:///{tmp_path / 'g.db'}"
    _seed(db, overrides=1, accepts=29)           # ~3% < 0.2, n=30
    assert main(["governance-gate", "--db", db]) == 0
```

- [ ] **Step 2: Run, expect FAIL** — `.venv/bin/python -m pytest tests/cli -q` (ModuleNotFoundError: legis.cli).

- [ ] **Step 3: Implement** `src/legis/cli.py`:

```python
"""Legis CLI — the build-time consumers of the governance surfaces.

`governance-gate` runs the ADR-0002 override-rate gate against a governance DB and
exits non-zero on FAIL, so CI fails the build rather than merely exposing the status
over HTTP (closes the roadmap's "wired into CI" requirement for the protected cell).
"""
from __future__ import annotations

import argparse
import sys

from legis.enforcement.lifecycle import GateStatus, evaluate_override_rate
from legis.governance import params
from legis.store.audit_store import AuditStore


def _governance_gate(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="legis governance-gate")
    ap.add_argument("--db", required=True, help="governance audit DB url (sqlite:///path)")
    args = ap.parse_args(argv)
    res = evaluate_override_rate(
        AuditStore(args.db).read_all(),
        threshold=params.OVERRIDE_RATE_THRESHOLD,
        window=params.OVERRIDE_RATE_WINDOW,
        min_sample=params.OVERRIDE_RATE_MIN_SAMPLE,
    )
    print(f"override-rate gate: {res.status.value} rate={res.rate:.3f} n={res.sample_size}")
    return 1 if res.status is GateStatus.FAIL else 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: legis <command> [args]", file=sys.stderr)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "governance-gate":
        return _governance_gate(rest)
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2
```

- [ ] **Step 4: Run, expect PASS** — `.venv/bin/python -m pytest tests/cli -q`.

- [ ] **Step 5: Create `.github/workflows/ci.yml`** (runs the suite and the gate on a non-empty DB path;
  the gate is a no-op `PASS_WITH_NOTICE` on an empty/missing trail, which is correct — wiring is what was
  missing):

```yaml
name: ci
on: [push, pull_request]
jobs:
  test-and-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        uses: astral-sh/setup-uv@v3
      - run: uv sync --all-extras --dev
      - name: Tests
        run: uv run pytest -q
      - name: Override-rate governance gate
        run: uv run legis governance-gate --db "sqlite:///${GITHUB_WORKSPACE}/legis-governance.db"
```

- [ ] **Step 6: Commit** — `git commit -m "feat(cli): governance-gate command + CI workflow wiring the override-rate gate to fail the build (R-1.3c-17)"`

---

## Phase 2 — Agent-programmable policy grammar

### Task 3: Make decorator `source` and `invariant` non-inert (R-1.4-06, R-1.4-08)

**Why:** The roadmap calls all five decorator fields "behavioural evidence, not vibe-justification," but
`source` and `invariant` are carried and never read. Make them required + validated (fail-closed on
empty), the same discipline the decorator already applies to empty `suppresses`.

**Files:**
- Modify: `src/legis/policy/decorator.py` (`policy_boundary` decoration check; `check_policy_boundary` gate)
- Modify: `tests/policy/test_decorator.py`, `tests/policy/test_honesty_gate.py` (add cases only)

- [ ] **Step 1: Write failing tests.** In `tests/policy/test_honesty_gate.py`:

```python
import pytest
from legis.policy.decorator import policy_boundary, check_policy_boundary, fingerprint

def test_decoration_rejects_empty_source():
    with pytest.raises(TypeError, match="source"):
        @policy_boundary(source="  ", suppresses=("no-eval",), invariant="i")
        def h(p): return p

def test_decoration_rejects_empty_invariant():
    with pytest.raises(TypeError, match="invariant"):
        @policy_boundary(source="s", suppresses=("no-eval",), invariant="")
        def h(p): return p

def test_gate_fails_on_blank_source_metadata():
    good = fingerprint(fake_boundary_test)
    h = _decorate(good)
    object.__setattr__(h.__policy_boundary__, "source", "   ")
    finding = check_policy_boundary(h, resolver)
    assert finding.ok is False and "source" in finding.reason
```

- [ ] **Step 2: Run, expect FAIL** — `.venv/bin/python -m pytest tests/policy/test_honesty_gate.py -q`.

- [ ] **Step 3: Implement.** In `policy_boundary(...)`, alongside the existing empty-`suppresses` guard:

```python
    if not source or not source.strip():
        raise TypeError("@policy_boundary requires a non-empty source (where the policy "
                        "comes from) — empty provenance is vibe-justification.")
    if not invariant or not invariant.strip():
        raise TypeError("@policy_boundary requires a non-empty invariant (the property the "
                        "suppression preserves) — empty is vibe-justification.")
```

  In `check_policy_boundary(...)`, after the `qualname` check and before the `test_ref` check:

```python
    if not meta.source or not meta.source.strip():
        return GateFinding(False, "no provenance: source is empty")
    if not meta.invariant or not meta.invariant.strip():
        return GateFinding(False, "no invariant: the preserved property is undeclared")
```

- [ ] **Step 4: Run, expect PASS** — full suite (`tests/policy` plus the rest; confirm no existing
  fixture used an empty source/invariant — all current fixtures use non-empty values).

- [ ] **Step 5: Commit** — `git commit -m "feat(policy): gate reads + requires decorator source/invariant — no longer inert (R-1.4-06/08)"`

---

### Task 4: YAML one-off exemption allowlist — the decorator's companion (R-1.4-11)

**Why:** The roadmap pairs the in-code decorator with "a YAML allowlist reserved for genuinely one-off
exemptions." No YAML-backed exemption surface exists. Build a minimal, honesty-enforcing one (each
exemption must carry a rationale).

**Files:**
- Modify: `pyproject.toml` (add `pyyaml>=6.0` to `dependencies`)
- Create: `src/legis/policy/exemptions.py`
- Create: `tests/policy/test_exemptions.py`

- [ ] **Step 1: Add dependency** — append `"pyyaml>=6.0",` to `[project].dependencies` in
  `pyproject.toml`, then `uv sync` (run by the executor; in plan mode this is noted, not executed).

- [ ] **Step 2: Write failing test** `tests/policy/test_exemptions.py`:

```python
import pytest
from legis.policy.exemptions import ExemptionAllowlist, ExemptionError

YAML = """
exemptions:
  - policy: import-allowlist
    entity: "python:function:m.legacy"
    rationale: "one-off: vendored module pending rewrite, tracked in ISSUE-42"
"""

def test_loads_and_matches_a_one_off_exemption(tmp_path):
    p = tmp_path / "exemptions.yaml"; p.write_text(YAML)
    al = ExemptionAllowlist.from_file(p)
    assert al.is_exempt("import-allowlist", "python:function:m.legacy") is True
    assert al.is_exempt("import-allowlist", "python:function:m.other") is False
    assert al.is_exempt("other-policy", "python:function:m.legacy") is False

def test_exemption_without_rationale_is_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text('exemptions:\n  - policy: p\n    entity: e\n')
    with pytest.raises(ExemptionError, match="rationale"):
        ExemptionAllowlist.from_file(p)

def test_missing_file_is_an_empty_allowlist(tmp_path):
    al = ExemptionAllowlist.from_file(tmp_path / "nope.yaml")
    assert al.is_exempt("any", "thing") is False
```

- [ ] **Step 3: Run, expect FAIL** — `.venv/bin/python -m pytest tests/policy/test_exemptions.py -q`.

- [ ] **Step 4: Implement** `src/legis/policy/exemptions.py`:

```python
"""YAML one-off exemption allowlist — the decorator's companion (roadmap §1.4).

The decorator carries reusable, in-code policy with behavioural evidence; this file
is reserved for *genuinely one-off* exemptions that do not belong in code. Every
entry MUST carry a rationale — an exemption without a reason is the vibe-justification
the grammar exists to prevent. A missing file is an empty allowlist (exempts nothing).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


class ExemptionError(RuntimeError):
    """A malformed exemption entry (e.g. missing rationale)."""


@dataclass(frozen=True)
class Exemption:
    policy: str
    entity: str
    rationale: str


class ExemptionAllowlist:
    def __init__(self, exemptions: list[Exemption]) -> None:
        self._by_key = {(e.policy, e.entity): e for e in exemptions}

    @classmethod
    def from_file(cls, path: str | Path) -> "ExemptionAllowlist":
        p = Path(path)
        if not p.exists():
            return cls([])
        raw = yaml.safe_load(p.read_text()) or {}
        out: list[Exemption] = []
        for i, entry in enumerate(raw.get("exemptions", [])):
            try:
                policy, entity = entry["policy"], entry["entity"]
                rationale = entry["rationale"]
            except (KeyError, TypeError) as exc:
                raise ExemptionError(
                    f"exemption #{i} missing required field (policy/entity/rationale): {exc}")
            if not str(rationale).strip():
                raise ExemptionError(f"exemption #{i} has an empty rationale")
            out.append(Exemption(policy=policy, entity=entity, rationale=str(rationale)))
        return cls(out)

    def is_exempt(self, policy: str, entity: str) -> bool:
        return (policy, entity) in self._by_key

    def exemption(self, policy: str, entity: str) -> Exemption | None:
        return self._by_key.get((policy, entity))
```

- [ ] **Step 5: Run, expect PASS**, then full suite.

- [ ] **Step 6: Commit** — `git commit -m "feat(policy): YAML one-off exemption allowlist companion to the decorator (R-1.4-11)"`

---

## Phase 3 — Wardline routing breadth (§2.2)

### Task 5: Add the `surface_only` (no hard gate) cell (R-2.2-08)

**Why:** The roadmap lists four routing outcomes; only `surface_override` and `block_escalate` are
routable. Add the "plain surface to the agent, no hard gate" outcome. Per the throughline ("nothing is
silent"), it records a lightweight `WARDLINE_SURFACED` event rather than nothing.

**Files:**
- Modify: `src/legis/wardline/governor.py`
- Modify: `src/legis/api/app.py` (`wardline_scan_results` passes `engine` for the new cell too)
- Modify: `tests/wardline/test_governor.py` (add case)

- [ ] **Step 1: Write failing test** in `tests/wardline/test_governor.py`:

```python
def test_surface_only_cell_records_a_surfaced_event_no_gate(tmp_path):
    eng = _engine(tmp_path)
    results = route_findings(
        active_defects(_scan()),
        policy=WardlineCellPolicy.SURFACE_ONLY,
        agent_id="agent-1",
        resolve=lambda q: (EntityKey.from_locator(q or "unknown"), {}),
        engine=eng,
    )
    assert results[0]["mode"] == "surface_only" and results[0]["surfaced"] is True
    ev = eng.trail()[0]
    assert ev["event"] == "WARDLINE_SURFACED" and ev["policy"] == "PY-WL-101"
```

- [ ] **Step 2: Run, expect FAIL** (AttributeError: SURFACE_ONLY).

- [ ] **Step 3: Implement** in `governor.py`: add the enum member and a branch:

```python
class WardlineCellPolicy(str, Enum):
    SURFACE_OVERRIDE = "surface_override"
    BLOCK_ESCALATE = "block_escalate"
    SURFACE_ONLY = "surface_only"
```

  In `route_findings`, add a guard `if policy is WardlineCellPolicy.SURFACE_ONLY and engine is None:
  raise ValueError("surface_only cell requires an engine")`, and inside the loop add the branch:

```python
        elif policy is WardlineCellPolicy.SURFACE_ONLY:
            assert engine is not None
            seq = engine.record_event({
                "event": "WARDLINE_SURFACED", "policy": f.rule_id,
                "entity_key": entity_key.to_dict(), "rationale": rationale,
                "extensions": {**clarion_ext,
                               "wardline": {"fingerprint": f.fingerprint,
                                            "tiers": dict(f.properties),
                                            "severity": f.severity.value}}})
            results.append({"mode": policy.value, "fingerprint": f.fingerprint,
                            "seq": seq, "surfaced": True})
```

  (Restructure the existing `if/else` into `if SURFACE_OVERRIDE / elif SURFACE_ONLY / else BLOCK_ESCALATE`.)
  In `app.py`'s `wardline_scan_results`, pass `engine` when the cell is `SURFACE_OVERRIDE` **or**
  `SURFACE_ONLY`:

```python
        engine=engine() if policy in (WardlineCellPolicy.SURFACE_OVERRIDE,
                                      WardlineCellPolicy.SURFACE_ONLY) else None,
```

- [ ] **Step 4: Run, expect PASS** — `tests/wardline` and `tests/api/test_combinations_api.py`.

- [ ] **Step 5: Commit** — `git commit -m "feat(wardline): add surface_only (no-gate) routing cell, recorded not silent (R-2.2-08)"`

---

### Task 6: Severity `fail_on` resolves the cell per finding (R-2.2-05)

**Why:** The roadmap: "Wardline `--fail-on` / exit codes become inputs to a legis policy that resolves
into whichever 2×2 cell the project has configured." Today the caller hardcodes one cell for the whole
scan and severity is parsed but never used. Add a per-finding resolver: findings at/above a `fail_on`
severity get the configured gate cell; below it, `surface_only`.

**Files:**
- Create: `src/legis/wardline/policy.py` (the cell-resolution function)
- Modify: `src/legis/api/app.py` (`ScanResultsIn` gains optional `fail_on`; route per finding)
- Create: `tests/wardline/test_policy.py`; modify `tests/api/test_combinations_api.py` (add case)

- [ ] **Step 1: Write failing test** `tests/wardline/test_policy.py`:

```python
from legis.wardline.ingest import WardlineSeverity, active_defects
from legis.wardline.policy import resolve_cell
from legis.wardline.governor import WardlineCellPolicy

def _finding(sev):
    return active_defects({"findings": [
        {"rule_id": "R", "message": "m", "severity": sev, "kind": "defect",
         "fingerprint": "fp", "qualname": "q", "properties": {}, "suppressed": "active"}]})[0]

def test_at_or_above_fail_on_gets_the_gate_cell():
    assert resolve_cell(_finding("ERROR"), fail_on=WardlineSeverity.ERROR,
                        gate_cell=WardlineCellPolicy.BLOCK_ESCALATE) is WardlineCellPolicy.BLOCK_ESCALATE
    assert resolve_cell(_finding("CRITICAL"), fail_on=WardlineSeverity.ERROR,
                        gate_cell=WardlineCellPolicy.BLOCK_ESCALATE) is WardlineCellPolicy.BLOCK_ESCALATE

def test_below_fail_on_is_surface_only():
    assert resolve_cell(_finding("WARN"), fail_on=WardlineSeverity.ERROR,
                        gate_cell=WardlineCellPolicy.BLOCK_ESCALATE) is WardlineCellPolicy.SURFACE_ONLY
```

- [ ] **Step 2: Run, expect FAIL** (ModuleNotFoundError).

- [ ] **Step 3: Implement** `src/legis/wardline/policy.py`:

```python
"""Map a Wardline finding's severity to a 2x2 cell — the `--fail-on` input.

Findings whose severity rank is >= the configured `fail_on` get the project's
configured gate cell; lower-severity findings drop to surface_only (recorded,
no hard gate). This is the legis-side wiring of Wardline's exit-code threshold.
"""
from __future__ import annotations

from legis.wardline.governor import WardlineCellPolicy
from legis.wardline.ingest import WardlineFinding, WardlineSeverity


def resolve_cell(finding: WardlineFinding, *, fail_on: WardlineSeverity,
                 gate_cell: WardlineCellPolicy) -> WardlineCellPolicy:
    if finding.severity.rank >= fail_on.rank:
        return gate_cell
    return WardlineCellPolicy.SURFACE_ONLY
```

  In `app.py`: add `fail_on: str | None = None` to `ScanResultsIn`. In `wardline_scan_results`, when
  `body.fail_on` is set, group findings by `resolve_cell(...)` and route each group with its cell
  (calling `route_findings` per group, passing `engine`/`signoff` as that group's cell requires);
  when unset, keep today's single-cell behaviour. Validate `fail_on` against `WardlineSeverity[...]`
  (422 on unknown). `body.cell` is the `gate_cell` for the at/above-threshold group.

- [ ] **Step 4: Run, expect PASS.** Add an API test in `tests/api/test_combinations_api.py`: a scan with
  one `ERROR` + one `WARN` finding and `fail_on="ERROR"`, `cell="block_escalate"` → the ERROR opens a
  sign-off (needs `signoff_gate` wired) and the WARN records a `WARDLINE_SURFACED` event.

- [ ] **Step 5: Commit** — `git commit -m "feat(wardline): severity fail_on resolves the 2x2 cell per finding (R-2.2-05)"`

---

### Task 7: Cover the coached Wardline path + document the cell axis (R-2.2-07)

**Why:** The simple cell splits coached/chill by whether the engine has a judge — a per-scan capability
that exists but is **untested through the Wardline seam**, and the "coached vs chill is selectable
per-scan" expectation conflicts with the design (the cell is configured for the whole scan, per the
governor docstring). Close the test gap and make the design intent explicit; no behaviour change.

**Files:**
- Modify: `tests/wardline/test_governor.py` (add coached-path test)
- Modify: `src/legis/wardline/governor.py` (docstring note only)

- [ ] **Step 1: Write failing/█new test** in `tests/wardline/test_governor.py` (uses a stub judge, same
  shape as `tests/enforcement/test_engine_coached.py`'s judge):

```python
from legis.enforcement.verdict import JudgeOpinion, Verdict

class _BlockingJudge:
    def evaluate(self, record):
        return JudgeOpinion(verdict=Verdict.BLOCKED, model="stub-1", rationale="insufficient")

def test_surface_override_through_a_coached_engine_records_judge_verdict(tmp_path):
    eng = EnforcementEngine(AuditStore(f"sqlite:///{tmp_path / 'g.db'}"),
                            FixedClock("2026-06-02T12:00:00+00:00"), judge=_BlockingJudge())
    results = route_findings(
        active_defects(_scan()), policy=WardlineCellPolicy.SURFACE_OVERRIDE, agent_id="a",
        resolve=lambda q: (EntityKey.from_locator(q or "unknown"), {}), engine=eng)
    assert results[0]["accepted"] is False           # judge BLOCKED the wardline-routed override
    assert eng.trail()[0]["extensions"]["judge_verdict"] == "BLOCKED"
```

- [ ] **Step 2: Run, expect PASS immediately** (the mechanism already works — this is a coverage test
  that documents the coached seam). If it fails, the seam is broken and must be fixed; if it passes,
  the gap was purely missing coverage.

- [ ] **Step 3: Document.** Add to `governor.py`'s module docstring: "The simple cell's coached-vs-chill
  split is an engine-config axis (whether a judge is injected), configured per server, not a per-finding
  request flag — consistent with §1.3's single-config-flag model."

- [ ] **Step 4: Commit** — `git commit -m "test(wardline): cover the coached surface_override seam; document the cell axis (R-2.2-07)"`

---

## Phase 4 — Git/CI surface (§1.1, §1.2)

### Task 8: Branch upstream status — ahead/behind/tracking (R-1.1-04)

**Why:** Roadmap §1.1: branches report "status relative to the upstream." `BranchInfo` carries only
name/head_sha/is_current. Add upstream + ahead/behind via `for-each-ref` format tokens.

**Files:**
- Modify: `src/legis/git/models.py` (`BranchInfo` gains 3 optional fields)
- Modify: `src/legis/git/surface.py` (`branches()` parses upstream tokens)
- Modify: `tests/git/test_git_surface.py` (add a real-repo upstream test)

- [ ] **Step 1: Write failing test** in `tests/git/test_git_surface.py` — create a repo, a branch with
  an upstream that is ahead by 1 (use the existing tmp-repo fixture style: `git init`, commit, create a
  "remote" branch, set upstream, add a local commit). Assert `BranchInfo.ahead == 1`, `behind == 0`,
  `upstream` is set. (Mirror the rename test's real-repo construction at `test_git_surface.py:62-71`.)

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Implement.** `BranchInfo` adds `upstream: str | None = None`, `ahead: int | None = None`,
  `behind: int | None = None`. In `branches()`, extend the `for-each-ref` format and parse
  `%(upstream:short)` and `%(upstream:track,nobracket)` (yields e.g. `ahead 1, behind 2`, or empty):

```python
        out = self._run(
            "for-each-ref",
            "--format=%(refname:short)%09%(objectname)%09%(upstream:short)%09%(upstream:track,nobracket)",
            "refs/heads")
        ...
        name, sha, upstream, track = (line.split("\t") + ["", "", ""])[:4]
        ahead = behind = None
        if upstream:
            ahead, behind = 0, 0
            for part in track.split(","):
                part = part.strip()
                if part.startswith("ahead "): ahead = int(part[6:])
                elif part.startswith("behind "): behind = int(part[7:])
        branches.append(BranchInfo(name=name, head_sha=sha, is_current=(name == current),
                                   upstream=upstream or None, ahead=ahead, behind=behind))
```

- [ ] **Step 4: Run, expect PASS** — `tests/git` and `tests/api/test_git_api.py` (the `/git/branches`
  route uses `asdict`, so new fields appear automatically).

- [ ] **Step 5: Commit** — `git commit -m "feat(git): branch upstream tracking status (ahead/behind) (R-1.1-04)"`

---

### Task 9: Pull-request metadata surface — recorded POST surface (R-1.1-10)

**Why:** Roadmap §1.1: "Pull-request context. PR metadata and the check outcomes associated with it."
PRs are forge-reported (not in git), so mirror `CheckSurface`: a relational store the agent/CI POSTs to.

**Files:**
- Create: `src/legis/pulls/__init__.py`, `src/legis/pulls/models.py`, `src/legis/pulls/surface.py`
- Modify: `src/legis/api/app.py` (inject `pull_surface`; add `/git/pulls` routes joining check outcomes)
- Create: `tests/pulls/test_pull_surface.py`; modify `tests/api/test_git_api.py` (route test)

- [ ] **Step 1: Write failing test** `tests/pulls/test_pull_surface.py` (mirror
  `tests/checks/test_check_surface.py`):

```python
from legis.pulls.models import PullRequest, PullRequestState
from legis.pulls.surface import PullSurface

def test_record_then_get_round_trips(tmp_path):
    s = PullSurface(f"sqlite:///{tmp_path / 'pulls.db'}")
    s.record(PullRequest(number=7, title="Add X", base="main", head="feature",
                         state=PullRequestState.OPEN, url="https://forge/pr/7"))
    pr = s.get(7)
    assert pr.title == "Add X" and pr.base == "main" and pr.state is PullRequestState.OPEN

def test_get_unknown_pr_is_none(tmp_path):
    assert PullSurface(f"sqlite:///{tmp_path / 'pulls.db'}").get(999) is None
```

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Implement.** `src/legis/pulls/models.py`:

```python
"""Pull-request facts (forge-reported, like CheckRun — not in git)."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum

class PullRequestState(str, Enum):
    OPEN = "open"; CLOSED = "closed"; MERGED = "merged"

@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    base: str
    head: str
    state: PullRequestState
    url: str | None = None
```

  `src/legis/pulls/surface.py` — model on `CheckSurface` (NullPool engine, a `pull_requests` table keyed
  by `number` as primary key; `record()` upserts so a PR's state can advance open→merged;
  `get(number) -> PullRequest | None`). Use SQLAlchemy `insert` + on-conflict-replace or a delete+insert
  in one transaction (SQLite); keep it simple and indexed by `number`.

  In `app.py`: add `pull_surface: PullSurface | None = None` to `create_app`, a lazy `pulls()` accessor
  (default `PullSurface("sqlite:///legis-pulls.db")`), and routes:

```python
    @app.post("/git/pulls", status_code=201)
    def post_pull(pr: PullRequestIn) -> dict: ...        # records, returns the PR dict

    @app.get("/git/pulls/{number}")
    def get_pull(number: int) -> dict:                   # 404 if unknown
        pr = pulls().get(number)
        if pr is None: raise HTTPException(404, f"unknown PR: {number}")
        return {**_pr_to_dict(pr),
                "checks": [_check_to_dict(r) for r in checks().for_pr(number)]}
```

  (`PullRequestIn` BaseModel mirrors `PullRequest`; `_pr_to_dict` mirrors `_check_to_dict`. The GET joins
  check outcomes via the existing `CheckSurface.for_pr`, satisfying "PR metadata **and** the check
  outcomes associated with it.")

- [ ] **Step 4: Run, expect PASS** — `tests/pulls`, then add a `/git/pulls` round-trip + checks-join
  test to `tests/api/test_git_api.py` and run it.

- [ ] **Step 5: Commit** — `git commit -m "feat(pulls): recorded PR metadata surface joined to check outcomes (R-1.1-10)"`

---

### Task 10: Rename evidence carries pre/post blob state (R-1.1-14)

**Why:** Roadmap §1.1: rename evidence "with what pre- and post-rename state." Today `RenameEvidence`
has only `old_path`/`new_path`/`similarity` — no content/object state. Add `old_blob`/`new_blob` (the
git object SHAs), which is exactly the pre/post identity Clarion's matcher combines with body hashes.
Additive — the WP-6.3 contract parser reads only `old_path`/`new_path`, so extra fields are safe.

**Files:**
- Modify: `src/legis/git/models.py` (`RenameEvidence` gains 2 fields)
- Modify: `src/legis/git/surface.py` (`renames()` switches to `--raw` to capture blob SHAs)
- Modify: `tests/git/test_git_surface.py` (assert blobs); confirm `tests/contract/test_git_renames_contract.py` still passes

- [ ] **Step 1: Write failing test** — extend the existing rename test to assert `old_blob` and
  `new_blob` are 40-hex and differ when the file content changed during the rename (or equal for a pure
  rename with `R100`).

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Implement.** `RenameEvidence` adds `old_blob: str = ""`, `new_blob: str = ""`. In
  `renames()`, switch the `git log` format from `--name-status` to `--raw -M` and parse the raw line
  `:<oldmode> <newmode> <oldblob> <newblob> R<sim>\t<old>\t<new>`:

```python
        out = self._run("log", "-M", "--diff-filter=R", "--raw",
                        f"--format=COMMIT{US}%H", rev_range)
        ...
        # raw line starts with ':' — split off the meta block, then the tab-joined paths
        if line.startswith(":"):
            meta, _, paths = line.partition("\t")
            cols = meta[1:].split()          # oldmode newmode oldblob newblob R<sim>
            old_blob, new_blob, status = cols[2], cols[3], cols[4]
            if not status.startswith("R"): continue
            old_path, _, new_path = paths.partition("\t")
            similarity = int(status[1:]) if status[1:].isdigit() else 0
            evidence.append(RenameEvidence(commit_sha=current_sha, old_path=old_path,
                            new_path=new_path, similarity=similarity,
                            old_blob=old_blob, new_blob=new_blob))
```

  (The `--raw` paths column is itself tab-separated for renames; verify the `partition("\t")` split
  against a real fixture. Keep `commit_sha`/`old_path`/`new_path`/`similarity` byte-identical to today.)

- [ ] **Step 4: Run, expect PASS** — `tests/git/test_git_surface.py` **and**
  `tests/contract/test_git_renames_contract.py` (must stay green — the contract parser ignores the new
  fields). Then `tests/api/test_git_api.py`.

- [ ] **Step 5: Commit** — `git commit -m "feat(git): rename evidence carries pre/post blob SHAs (R-1.1-14)"`

---

### Task 11: Assert rule_set/policy_version survive the check round-trip (R-1.2-04/05/11)

**Why:** `rule_set` and `policy_version` are persisted and returned but **no test asserts they survive
readback** — a write-path bug nulling them passes the whole suite, and the "enough provenance to re-run"
claim (R-1.2-11) leans on them. Pure test hardening; no source change expected.

**Files:**
- Modify: `tests/checks/test_check_surface.py`, `tests/api/test_check_api.py`

- [ ] **Step 1: Write the tests.** In `tests/checks/test_check_surface.py`:

```python
def test_rule_set_and_policy_version_survive_round_trip(tmp_path):
    s = surface(tmp_path)
    s.record(make_run(rule_set="wardline@3", policy_version="p7"))
    r = s.for_commit("a" * 40)[0]
    assert r.rule_set == "wardline@3"
    assert r.policy_version == "p7"
```

  In `tests/api/test_check_api.py`, add an assertion that a POSTed `rule_set`/`policy_version` reappear
  on the corresponding GET (`/checks/commit/{sha}`).

- [ ] **Step 2: Run, expect PASS** (the fields are already wired; if either FAILS it is a real
  write-path bug to fix in `src/legis/checks/surface.py`).

- [ ] **Step 3: Commit** — `git commit -m "test(checks): lock rule_set/policy_version round-trip provenance (R-1.2-04/05/11)"`

---

## Phase 5 — Documentation drift

### Task 12: Correct the stale "not implemented" framing (doc-drift findings)

**Why:** Roadmap line 58 ("design-ready, not implemented") and §3 lines 335–339 ("none [of milestones
1–3] built either"), plus `README.md:7`, contradict reality (implemented, 147/147 green, README matrix
says "Live"). Update the status framing; do not touch the substantive design prose.

**Files:**
- Modify: `docs/superpowers/specs/2026-06-01-legis-roadmap-to-first-class.md` (lines ~58, ~335–339)
- Modify: `README.md` (the `## Status` block, ~line 7)

- [ ] **Step 1:** Replace roadmap line 58's "design-ready, not implemented; every milestone in §1 is
  greenfield construction" with an as-built note: milestones 1–3 implemented (Sprints 0–6, 147 passing
  tests); milestones 4–6 live (SEI Sprint 5, suite combinations Sprint 6); milestone 7 contract-locked,
  operative pending Clarion driving. Soften §3's "none are built either" to "milestones 1–3 are built
  (Sprints 0–6); 4–7 layer on siblings."
- [ ] **Step 2:** Update `README.md`'s `## Status` from "design-ready, not implemented" to reflect the
  implemented engine + the combination-matrix "Live/Partial" rows already in the README.
- [ ] **Step 3: Commit** — `git commit -m "docs: correct stale 'not implemented' framing to as-built status (doc-drift)"`

---

## Verification (end-to-end, after all phases)

1. **Full suite green incl. new tests:** `.venv/bin/python -m pytest -q` — expect the current 147 plus
   the ~18 added tests, all passing, no warnings (`filterwarnings=error`).
2. **CLI gate exit codes:** `.venv/bin/python -m pytest tests/cli -q`, and a manual
   `.venv/bin/python -m legis.cli governance-gate --db sqlite:///$(mktemp -u).db` → prints
   `PASS_WITH_NOTICE`, exits 0.
3. **New surfaces over HTTP** (via `fastapi.testclient`, as existing API tests do): `POST/GET /git/pulls`
   round-trips and joins check outcomes; `/git/branches` shows `ahead`/`behind`; `/git/renames` shows
   `old_blob`/`new_blob`; `/wardline/scan-results` with `cell=surface_only` and with `fail_on` routes
   per finding.
4. **Tamper-binding:** the Task 1 test proves the Filigree binding signature verifies and that
   `signoff_seq` + signature reach `attach`.
5. **Regression guard:** `tests/contract/test_git_renames_contract.py` stays green (Task 10 must not
   change the rename contract shape Clarion's parser consumes).
6. **Re-audit (optional):** re-run the §2.3, §1.4, §2.2, §1.1 reviewers from the audit method doc against
   the changed tree; the 4 Missing + 12 Partial should now grade Implemented (PR surface, branch status,
   blobs, exemptions, surface_only cell, fail_on, signed binding, etc.), with R-2.2-04's `@trust_boundary`
   grammar correctly remaining Gated on Wardline.

## Notes / risks

- **Task 10** is the only change touching the WP-6.3 contract surface — switching `renames()` to `--raw`
  must preserve the existing four fields exactly; the contract test is the guard. If `--raw` path-parsing
  proves fiddly on a real fixture, fall back to keeping `--name-status` for paths and a second
  `git diff-tree --raw` call for blobs.
- **Task 4** adds the first new runtime dependency (`pyyaml`); run `uv sync` and confirm `uv.lock` updates.
- **Task 6** restructures `wardline_scan_results` from one-cell to per-finding grouping; keep the no-`fail_on`
  path byte-identical so existing combination-API tests are untouched.
- **R-2.2-04** (one `@trust_boundary` grammar) stays **Gated**, not fixed here — the decorator grammar is
  Wardline's Milestone 5 deliverable; legis's verbatim-tier-carrying side is already built.
