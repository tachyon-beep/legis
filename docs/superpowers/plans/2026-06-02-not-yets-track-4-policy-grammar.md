# Not-Yets Track 4 (WP-A7/A8) — Policy Grammar Completeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the two inert decorator fields teeth (WP-A7: the honesty gate now *requires* `source` + `invariant`, shape-checking `source` as a real citation), and add the decorator's documented companion for one-off exemptions (WP-A8: a TOML-backed exemption surface the policy grammar consumes).

**Architecture:** WP-A7 extends `check_policy_boundary` (`policy/decorator.py`) with two evidence checks mirroring the existing `test_ref`/`test_fingerprint` discipline — `source` must be a non-empty, well-formed citation (a URL, a git SHA, or an in-repo path-with-extension), and `invariant` must be non-empty (and is echoed on the passing finding). WP-A8 adds `policy/exemptions.py`: a frozen `Exemption`, an `ExemptionRegistry` keyed on `(policy, value)`, and `load_exemptions(path)` parsing a TOML file via stdlib `tomllib` (no new dependency). `PolicyGrammar` gains an optional registry: when a boundary returns `VIOLATION` for an explicitly-exempted `(policy, value)`, evaluation returns `CLEAR` with the exemption reason as provenance — a recorded, attributable one-off bypass. Fail-closed is preserved: an exemption never rescues `UNKNOWN`.

**Tech Stack:** Python 3.12 (stdlib `tomllib`, `re`, `inspect`), the existing `PolicyGrammar`/`policy_boundary` decorator, pytest (warnings-as-errors). **No new runtime dependency** (TOML chosen over YAML specifically to hold that posture; the roadmap's "YAML allowlist" wording is satisfied in substance by a TOML exemption file — documented).

**Implements (design spec `2026-06-02-not-yets-completion-design.md`):** WP-A7 (R-1.4-06, R-1.4-08), WP-A8 (R-1.4-11).

**Locked design decisions (do not reopen):**
1. **`source` is a citation, shape-checked (not existence-checked).** Well-formed = a URL (`http(s)://…`), a git SHA (`[0-9a-f]{7,40}`), or an in-repo path with an extension and optional `:line` (`[\w./-]+\.[A-Za-z0-9]+(:\d+)?`). The gate does not touch the filesystem to confirm the path resolves (it has no repo handle); it rejects vibe strings (spaces, bare words like `"s"`). The accepted forms are documented in the gate's error message.
2. **`invariant` is required non-empty and surfaced.** The gate rejects an empty invariant and echoes the invariant text on the passing `GateFinding.reason` so a consumer logging the finding sees it. (Threading the invariant onto a runtime `OverrideRecord` is a separate concern — the honesty gate is a code/CI-time check, not the runtime override path — and is out of scope.)
3. **Tightening the gate updates existing decorations.** Honesty-gate tests that used placeholder sources (`"external payload"`, `"s"`) are updated to real citations — the fields were placeholders only because they were unchecked. The `test_ref`-failure test keeps its targeted failure by using a valid source/invariant so it still reaches the `test_ref` check.
4. **Exemptions are explicit, attributable, VIOLATION→CLEAR only.** An `Exemption{policy, value, reason}` turns a proven `VIOLATION` into `CLEAR` with the reason as `detail` and `provenance_gap=False`. It never rescues `UNKNOWN` (that stays fail-closed) and never fabricates a CLEAR for a value the boundary did not actually evaluate to VIOLATION.
5. **TOML via stdlib, no new dependency.** `load_exemptions` uses `tomllib.load` (binary mode). A malformed file or a malformed `[[exemption]]` entry fails closed (raises a clear error), never silently yields an empty/partial registry.

---

## File structure

| File | Change |
|---|---|
| `src/legis/policy/decorator.py` | `check_policy_boundary` enforces `source` (shape-checked) + `invariant`; add `_is_citation` |
| `src/legis/policy/exemptions.py` | `Exemption`; `ExemptionRegistry`; `load_exemptions(path)` (stdlib `tomllib`) |
| `src/legis/policy/grammar.py` | `PolicyGrammar(exemptions=None)`; `evaluate` consults the registry on VIOLATION |
| `tests/policy/test_honesty_gate.py` | update placeholder sources; add source/invariant enforcement tests |
| `tests/policy/test_exemptions.py` | load/parse, is_exempt, malformed-fails-closed |
| `tests/policy/test_grammar.py` | (append) exemption turns VIOLATION→CLEAR; never rescues UNKNOWN |
| `docs/superpowers/specs/2026-06-02-not-yets-completion-design.md` | mark WP-A7/A8 done |

---

## WP-A7 — Enforce `source` + `invariant` in the honesty gate

### Task 1: `check_policy_boundary` requires a well-formed `source` and a non-empty `invariant`

**Files:**
- Modify: `src/legis/policy/decorator.py`
- Test: `tests/policy/test_honesty_gate.py`

- [ ] **Step 1: Write the failing tests** (update + append in `tests/policy/test_honesty_gate.py`)

First, update the two helpers/tests that used placeholder sources so they use real citations (the fields now have teeth):

```python
# In _decorate(...): change source="external payload" → a real citation:
        source="src/legis/handlers.py:42",
```

```python
# test_gate_rejects_missing_test_ref_as_vibe_justification: give it a valid
# source + invariant so the gate reaches (and fails at) the test_ref check:
    @policy_boundary(source="src/legis/x.py:1", suppresses=("no-eval",), invariant="rejects bad input")
    def handler(payload):
        return payload
```

Then append the new enforcement tests:

```python
def _decorate_src(source, invariant="rejects bad input"):
    good = fingerprint(fake_boundary_test)

    @policy_boundary(source=source, suppresses=("no-eval",), invariant=invariant,
                     test_ref="tests::fake", test_fingerprint=good)
    def handler(payload):
        return payload

    return handler


def test_gate_rejects_empty_source():
    finding = check_policy_boundary(_decorate_src(""), resolver)
    assert finding.ok is False
    assert "source" in finding.reason.lower()


def test_gate_rejects_vibe_source_that_is_not_a_citation():
    finding = check_policy_boundary(_decorate_src("because I tested it"), resolver)
    assert finding.ok is False
    assert "citation" in finding.reason.lower()


def test_gate_accepts_url_sha_and_repo_path_citations():
    for src in ("https://github.com/o/r/pull/9", "a1b2c3d", "src/legis/x.py:42", "README.md"):
        assert check_policy_boundary(_decorate_src(src), resolver).ok is True, src


def test_gate_rejects_empty_invariant():
    finding = check_policy_boundary(_decorate_src("src/legis/x.py:1", invariant=""), resolver)
    assert finding.ok is False
    assert "invariant" in finding.reason.lower()


def test_passing_finding_surfaces_the_invariant():
    finding = check_policy_boundary(_decorate_src("src/legis/x.py:1", invariant="rejects bad input"), resolver)
    assert finding.ok is True
    assert "rejects bad input" in finding.reason
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/policy/test_honesty_gate.py -v`
Expected: the new tests FAIL (gate does not yet check source/invariant); `test_gate_accepts_url_sha_and_repo_path_citations` and `test_passing_finding_surfaces_the_invariant` fail because the reason has no invariant / the checks don't exist.

- [ ] **Step 3: Write minimal implementation**

In `src/legis/policy/decorator.py`, add `re` import and a citation matcher near the top:

```python
import re

# A well-formed source citation: a URL, a git SHA (short..full), or an in-repo
# path with an extension and optional :line. Shape-checked, not filesystem-resolved.
_CITATION_RE = re.compile(
    r"^(https?://\S+|[0-9a-f]{7,40}|[\w./-]+\.[A-Za-z0-9]+(:\d+)?)$"
)


def _is_citation(source: str) -> bool:
    return bool(_CITATION_RE.match(source))
```

In `check_policy_boundary`, add the source + invariant checks immediately after the
qualname check (before the `test_ref` check), and surface the invariant on success:

```python
    if meta.qualname != func.__qualname__:
        return GateFinding(False, f"scope/qualname mismatch: {meta.qualname!r}")
    if not meta.source:
        return GateFinding(False, "no source citation: source is required")
    if not _is_citation(meta.source):
        return GateFinding(
            False,
            f"source is not a resolvable citation (URL, git SHA, or repo path): {meta.source!r}",
        )
    if not meta.invariant:
        return GateFinding(False, "no invariant: a non-empty invariant statement is required")
    if not meta.test_ref:
        return GateFinding(False, "no behavioural evidence: test_ref is required")
    # ... (existing test_fingerprint / resolve / fingerprint / src checks unchanged) ...
```

And change the final success line:

```python
    return GateFinding(True, f"ok (invariant: {meta.invariant})")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/policy/test_honesty_gate.py -v`
Expected: PASS (updated + new). Then `python -m pytest -q` — full suite green (the only behavioural change is the gate is stricter; decorations now must carry a real source + invariant).

- [ ] **Step 5: Commit**

```bash
git add src/legis/policy/decorator.py tests/policy/test_honesty_gate.py
git commit -m "feat(policy): honesty gate enforces source citation + invariant (WP-A7)"
```

---

## WP-A8 — TOML-backed one-off exemption surface

### Task 2: `ExemptionRegistry` + `load_exemptions` + grammar integration

**Files:**
- Create: `src/legis/policy/exemptions.py`
- Modify: `src/legis/policy/grammar.py`
- Test: `tests/policy/test_exemptions.py`, `tests/policy/test_grammar.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
# tests/policy/test_exemptions.py
import pytest

from legis.policy.exemptions import Exemption, ExemptionRegistry, load_exemptions


def _write(tmp_path, text):
    p = tmp_path / "exemptions.toml"
    p.write_text(text)
    return p


def test_load_parses_exemptions(tmp_path):
    path = _write(tmp_path, """
[[exemption]]
policy = "import-allowlist"
value = "requests"
reason = "approved 2026-06-02, ticket-123"
""")
    reg = load_exemptions(path)
    ex = reg.is_exempt("import-allowlist", "requests")
    assert ex == Exemption("import-allowlist", "requests", "approved 2026-06-02, ticket-123")
    assert reg.is_exempt("import-allowlist", "os") is None
    assert reg.is_exempt("other-policy", "requests") is None


def test_malformed_entry_fails_closed(tmp_path):
    path = _write(tmp_path, '[[exemption]]\npolicy = "p"\nvalue = "v"\n')  # no reason
    with pytest.raises(ValueError, match="reason"):
        load_exemptions(path)


def test_malformed_toml_fails_closed(tmp_path):
    path = _write(tmp_path, "this is not = valid = toml = [[[")
    with pytest.raises(Exception):
        load_exemptions(path)


def test_empty_file_is_an_empty_registry(tmp_path):
    reg = load_exemptions(_write(tmp_path, ""))
    assert reg.is_exempt("import-allowlist", "requests") is None
```

```python
# append to tests/policy/test_grammar.py
def test_exemption_turns_violation_into_clear():
    from legis.policy.exemptions import ExemptionRegistry, Exemption
    from legis.policy.grammar import (
        AllowlistBoundary, PolicyGrammar, PolicyResult,
    )
    reg = ExemptionRegistry([Exemption("import-allowlist", "requests", "ticket-123")])
    g = PolicyGrammar(exemptions=reg)
    g.register(AllowlistBoundary("import-allowlist", frozenset({"json"})))
    ev = g.evaluate("import-allowlist", {"value": "requests"})  # not allowlisted → VIOLATION, but exempted
    assert ev.result is PolicyResult.CLEAR
    assert ev.provenance_gap is False
    assert "ticket-123" in ev.detail
    # A non-exempted violation still fires.
    assert g.evaluate("import-allowlist", {"value": "pickle"}).result is PolicyResult.VIOLATION


def test_exemption_never_rescues_unknown():
    from legis.policy.exemptions import ExemptionRegistry, Exemption
    from legis.policy.grammar import PolicyGrammar, PolicyResult
    reg = ExemptionRegistry([Exemption("unregistered", "x", "r")])
    g = PolicyGrammar(exemptions=reg)
    ev = g.evaluate("unregistered", {"value": "x"})  # no boundary → UNKNOWN, fail-closed
    assert ev.result is PolicyResult.UNKNOWN
    assert ev.provenance_gap is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/policy/test_exemptions.py tests/policy/test_grammar.py -v`
Expected: FAIL — `ModuleNotFoundError: legis.policy.exemptions`; `PolicyGrammar() got an unexpected keyword argument 'exemptions'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/legis/policy/exemptions.py
"""One-off policy exemptions — the decorator's companion (WP-A8).

A TOML file lists explicit, attributable exemptions: a proven VIOLATION for an
exempted ``(policy, value)`` is downgraded to CLEAR with the exemption reason as
provenance. Loaded via stdlib ``tomllib`` (no new dependency). A malformed file
or entry fails closed — it raises rather than yielding a partial registry, so a
typo can never silently widen what is exempt. (The roadmap names this a "YAML
allowlist"; TOML is the substance-equivalent that holds legis's no-new-dependency
posture.)
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Exemption:
    policy: str
    value: str
    reason: str


class ExemptionRegistry:
    def __init__(self, exemptions: Iterable[Exemption]) -> None:
        self._by_key: dict[tuple[str, str], Exemption] = {
            (e.policy, e.value): e for e in exemptions
        }

    def is_exempt(self, policy: str, value: str) -> Exemption | None:
        return self._by_key.get((policy, value))


def load_exemptions(path: str | Path) -> ExemptionRegistry:
    with open(path, "rb") as fh:
        data = tomllib.load(fh)  # malformed TOML raises tomllib.TOMLDecodeError
    raw = data.get("exemption", [])
    exemptions: list[Exemption] = []
    for i, entry in enumerate(raw):
        missing = [k for k in ("policy", "value", "reason") if not entry.get(k)]
        if missing:
            raise ValueError(
                f"exemption[{i}] is malformed: missing/empty {', '.join(missing)}"
            )
        exemptions.append(Exemption(entry["policy"], entry["value"], entry["reason"]))
    return ExemptionRegistry(exemptions)
```

In `src/legis/policy/grammar.py`, import the registry type and thread it through:

```python
from legis.policy.exemptions import ExemptionRegistry
```

```python
class PolicyGrammar:
    def __init__(self, exemptions: ExemptionRegistry | None = None) -> None:
        self._boundaries: dict[str, BoundaryType] = {}
        self._exemptions = exemptions
```

In `evaluate`, after computing `result`/`detail` from the boundary (the success path
at the bottom), consult the registry before returning — but only to downgrade a
VIOLATION, never to rescue UNKNOWN:

```python
        if (
            result is PolicyResult.VIOLATION
            and self._exemptions is not None
            and "value" in target
        ):
            ex = self._exemptions.is_exempt(policy, target["value"])
            if ex is not None:
                return PolicyEvaluation(
                    policy, PolicyResult.CLEAR,
                    f"exempted (one-off): {ex.reason}", False,
                )
        return PolicyEvaluation(
            policy, result, str(detail), result is PolicyResult.UNKNOWN
        )
```

(`default_grammar()` is unchanged — exemptions are opt-in per deployment via
`PolicyGrammar(exemptions=load_exemptions(path))`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/policy/ -v`
Expected: PASS. Then `python -m pytest -q` — full suite green (the grammar change is additive; `exemptions` defaults to `None`).

- [ ] **Step 5: Commit**

```bash
git add src/legis/policy/exemptions.py src/legis/policy/grammar.py tests/policy/test_exemptions.py tests/policy/test_grammar.py
git commit -m "feat(policy): TOML one-off exemption surface consumed by the grammar (WP-A8)"
```

---

## Task 3: Docs + full-suite verification

**Files:**
- Modify: `docs/superpowers/specs/2026-06-02-not-yets-completion-design.md`

- [ ] **Step 1:** Append " — ✅ done 2026-06-02" to the WP-A7 and WP-A8 headings (under "### Track 4 — Policy grammar completeness"). In the WP-A8 bullet, note the YAML→TOML substitution (stdlib `tomllib`, no new dependency).

- [ ] **Step 2: Full suite green, zero warnings**

Run: `python -m pytest -q`
Expected: all green (was 190; +~11 new tests). Confirm count + zero warnings.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-06-02-not-yets-completion-design.md
git commit -m "docs: mark WP-A7/A8 policy grammar completeness complete"
```

---

## Self-review — WP coverage

| WP | Exit criterion (design spec) | Proven by |
|---|---|---|
| A7 | a decorator missing/empty `source` or `invariant` is rejected by the honesty gate | Task 1 (`test_gate_rejects_empty_source`, `test_gate_rejects_empty_invariant`) |
| A7 | `source` is shape-checked as a resolvable citation (URL / SHA / repo path); vibe strings rejected | Task 1 (`test_gate_rejects_vibe_source_that_is_not_a_citation`, `test_gate_accepts_url_sha_and_repo_path_citations`) |
| A7 | `invariant` appears on the record (the passing finding) | Task 1 (`test_passing_finding_surfaces_the_invariant`) |
| A8 | a YAML/TOML-backed one-off exemption surface is parsed and consumed; listed entity exempted, unlisted not, malformed fails closed | Task 2 (`test_load_parses_exemptions`, `test_malformed_entry_fails_closed`, `test_malformed_toml_fails_closed`, `test_exemption_turns_violation_into_clear`) |
| A8 | exemptions never rescue UNKNOWN (fail-closed preserved) | Task 2 (`test_exemption_never_rescues_unknown`) |

**Out of scope:** threading `invariant` onto the runtime `OverrideRecord` (the honesty gate is a CI/code-time check); wiring a default exemptions-file path into `create_app` (deployment config). Other tracks per the design spec.
