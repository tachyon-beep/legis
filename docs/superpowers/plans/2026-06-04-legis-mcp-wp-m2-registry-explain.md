# WP-M2: Policy Cell Registry + Explain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the declarative `policy/cells.toml` registry from the MCP surface design spec and expose a service-layer `legis_explain` contract that reports the mapped cell, required inputs, legal moves, and whether the mapped cell is actually wired.

**Architecture:** Add a small stdlib-only `legis.policy.cells` module that loads policy-name or glob rules from TOML and fails closed on malformed configuration. Add a focused `legis.service.explain` module that turns a registry decision plus deployment wiring (`EnforcementEngine`, `ProtectedGate`, `SignoffGate`) into the exact discovery payload shape required by the MCP surface. The existing MCP runtime gains a startup-loaded registry field, but this WP does not rename or complete the full MCP tool surface; WP-M3 uses this seam to expose `legis_explain` over JSON-RPC.

**Tech Stack:** Python 3.12, stdlib `tomllib`, stdlib `fnmatch`, dataclasses, pytest. No new runtime dependency.

**Spec:** `docs/superpowers/specs/2026-06-03-legis-mcp-surface-design.md:77-90` and WP-M2 at `docs/superpowers/specs/2026-06-03-legis-mcp-surface-design.md:181-182`.

**Baseline:** M1 service-layer files are present in this tree. The existing suite must stay green after this WP: run `uv run pytest -q`.

---

## File Structure

- **Create** `src/legis/policy/cells.py` — registry dataclasses, exact/glob matching, TOML loader, fail-closed validation, built-in chill default.
- **Create** `policy/cells.toml` — repository default registry file loaded by local startup when no explicit path is provided.
- **Create** `tests/policy/test_cells.py` — unit tests for registry matching, config loading, and malformed TOML entries.
- **Create** `src/legis/service/explain.py` — service-level `explain_policy` function and payload dataclasses.
- **Create** `tests/service/test_explain.py` — service tests for `chill`, `coached`, `structured`, and `protected` discovery behavior.
- **Modify** `src/legis/enforcement/engine.py:37-48` — add a read-only `has_judge` property so explain can report whether the coached cell is wired without poking at private state.
- **Modify** `src/legis/service/__init__.py:16-43` — re-export `PolicyExplanation`, `RequiredInput`, and `explain_policy`.
- **Modify** `src/legis/mcp.py:18-117` — add a startup-loaded `cell_registry` field to `McpRuntime`, using `LEGIS_POLICY_CELLS` when set and `policy/cells.toml` under `LEGIS_SOURCE_ROOT` or cwd when present.
- **Modify** `tests/mcp/test_server.py` — add a focused startup-loading test for `LEGIS_POLICY_CELLS`.

This WP delivers the service-level explain contract and runtime-loaded registry. The JSON-RPC tool name cleanup and final `legis_explain` tool exposure are part of WP-M3, which also rewrites the existing pre-spec MCP tool names into the ratified `legis_*` surface.

---

### Task 1: Policy Cell Registry Loader

**Files:**
- Create: `src/legis/policy/cells.py`
- Create/Modify: `tests/policy/test_cells.py`

- [ ] **Step 1: Write the failing registry tests**

```python
# tests/policy/test_cells.py
import tomllib

import pytest

from legis.policy.cells import (
    PolicyCellRegistry,
    PolicyCellRule,
    default_policy_cells,
    load_policy_cells,
)


def test_policy_cell_registry_uses_exact_then_glob_then_default():
    registry = PolicyCellRegistry(
        default_cell="chill",
        rules=(
            PolicyCellRule(pattern="security.*", cell="protected"),
            PolicyCellRule(pattern="security.low", cell="coached"),
            PolicyCellRule(pattern="human.release", cell="structured"),
        ),
    )

    assert registry.cell_for("security.low") == "coached"
    assert registry.cell_for("security.sql-injection") == "protected"
    assert registry.cell_for("human.release") == "structured"
    assert registry.cell_for("unlisted.policy") == "chill"


def test_default_policy_cells_is_chill_for_unlisted_policies():
    registry = default_policy_cells()

    assert registry.cell_for("anything") == "chill"


def test_load_policy_cells_reads_default_exact_and_glob_rules(tmp_path):
    path = tmp_path / "cells.toml"
    path.write_text(
        """
default_cell = "chill"

[[policy]]
pattern = "import-allowlist"
cell = "coached"

[[policy]]
pattern = "protected.*"
cell = "protected"

[[policy]]
pattern = "human.*"
cell = "structured"
""",
        encoding="utf-8",
    )

    registry = load_policy_cells(path)

    assert registry.cell_for("import-allowlist") == "coached"
    assert registry.cell_for("protected.source-integrity") == "protected"
    assert registry.cell_for("human.release-signoff") == "structured"
    assert registry.cell_for("ordinary.policy") == "chill"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('[[policy]]\npattern = "x"\ncell = "chill"\n', "missing/empty default_cell"),
        ('default_cell = "invalid"\n', "unknown cell"),
        ('default_cell = "chill"\npolicy = "x"\n', "policy table must be an array"),
        ('default_cell = "chill"\n[[policy]]\ncell = "chill"\n', "missing/empty pattern"),
        ('default_cell = "chill"\n[[policy]]\npattern = "x"\ncell = "invalid"\n', "unknown cell"),
    ],
)
def test_load_policy_cells_fails_closed_on_malformed_entries(tmp_path, body, message):
    path = tmp_path / "cells.toml"
    path.write_text(body, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_policy_cells(path)


def test_load_policy_cells_propagates_toml_decode_errors(tmp_path):
    path = tmp_path / "cells.toml"
    path.write_text("default_cell = [", encoding="utf-8")

    with pytest.raises(tomllib.TOMLDecodeError):
        load_policy_cells(path)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/policy/test_cells.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'legis.policy.cells'`.

- [ ] **Step 3: Implement the registry loader**

```python
# src/legis/policy/cells.py
"""Policy-to-cell registry for the agent-facing MCP surface.

The registry is deliberately declarative and stdlib-only. Agents submit opaque
policy names; the server maps them to governance cells and reports the mapping
back through ``legis_explain`` and ``legis_submit_override``.
"""

from __future__ import annotations

import fnmatch
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


VALID_CELLS = frozenset({"chill", "coached", "structured", "protected"})


@dataclass(frozen=True)
class PolicyCellRule:
    pattern: str
    cell: str


class PolicyCellRegistry:
    def __init__(
        self, default_cell: str, rules: Iterable[PolicyCellRule] = ()
    ) -> None:
        self.default_cell = _validate_cell(default_cell, "default_cell")
        self._rules = tuple(_validate_rule(i, rule) for i, rule in enumerate(rules))

    def cell_for(self, policy: str) -> str:
        for rule in self._rules:
            if not _has_glob(rule.pattern) and rule.pattern == policy:
                return rule.cell
        for rule in self._rules:
            if _has_glob(rule.pattern) and fnmatch.fnmatchcase(policy, rule.pattern):
                return rule.cell
        return self.default_cell


def default_policy_cells() -> PolicyCellRegistry:
    return PolicyCellRegistry(default_cell="chill")


def load_policy_cells(path: str | Path) -> PolicyCellRegistry:
    with open(path, "rb") as fh:
        data = tomllib.load(fh)

    default_cell = data.get("default_cell")
    if not isinstance(default_cell, str) or not default_cell.strip():
        raise ValueError("missing/empty default_cell")

    raw_rules = data.get("policy", [])
    if not isinstance(raw_rules, list):
        raise ValueError(
            "policy table must be an array of tables ([[policy]]), "
            f"got {type(raw_rules).__name__!r}"
        )

    rules: list[PolicyCellRule] = []
    for i, entry in enumerate(raw_rules):
        if not isinstance(entry, dict):
            raise ValueError(
                f"policy[{i}] is malformed: expected a table ([[policy]]), "
                f"got {type(entry).__name__!r}"
            )
        pattern = entry.get("pattern")
        cell = entry.get("cell")
        if not isinstance(pattern, str) or not pattern.strip():
            raise ValueError(f"policy[{i}] is malformed: missing/empty pattern")
        if not isinstance(cell, str) or not cell.strip():
            raise ValueError(f"policy[{i}] is malformed: missing/empty cell")
        rules.append(PolicyCellRule(pattern=pattern, cell=cell))

    return PolicyCellRegistry(default_cell=default_cell, rules=rules)


def _validate_rule(index: int, rule: PolicyCellRule) -> PolicyCellRule:
    pattern = rule.pattern.strip()
    if not pattern:
        raise ValueError(f"policy[{index}] is malformed: missing/empty pattern")
    return PolicyCellRule(
        pattern=pattern,
        cell=_validate_cell(rule.cell, f"policy[{index}].cell"),
    )


def _validate_cell(raw: str, location: str) -> str:
    cell = raw.strip()
    if cell not in VALID_CELLS:
        allowed = ", ".join(sorted(VALID_CELLS))
        raise ValueError(f"{location} has unknown cell {cell!r}; expected one of: {allowed}")
    return cell


def _has_glob(pattern: str) -> bool:
    return any(char in pattern for char in "*?[")
```

- [ ] **Step 4: Run the registry tests to verify they pass**

Run: `uv run pytest tests/policy/test_cells.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/legis/policy/cells.py tests/policy/test_cells.py
git commit -m "feat(policy): add policy cell registry loader (WP-M2)"
```

---

### Task 2: Repository Default `policy/cells.toml`

**Files:**
- Create: `policy/cells.toml`
- Modify: `tests/policy/test_cells.py`

- [ ] **Step 1: Add a failing test for the repository default config**

Append this test to `tests/policy/test_cells.py`:

```python
from pathlib import Path


def test_repository_default_policy_cells_file_loads():
    repo_root = Path(__file__).resolve().parents[2]
    registry = load_policy_cells(repo_root / "policy" / "cells.toml")

    assert registry.cell_for("import-allowlist") == "coached"
    assert registry.cell_for("protected.source-integrity") == "protected"
    assert registry.cell_for("human.release-signoff") == "structured"
    assert registry.cell_for("ordinary.policy") == "chill"
```

- [ ] **Step 2: Run the config test to verify it fails**

Run: `uv run pytest tests/policy/test_cells.py::test_repository_default_policy_cells_file_loads -v`

Expected: FAIL with `FileNotFoundError` for `policy/cells.toml`.

- [ ] **Step 3: Create the repository default config**

```toml
# policy/cells.toml
# Default policy-to-cell routing for local Legis startup.
# Exact policy names beat globs; unlisted policies use default_cell.

default_cell = "chill"

[[policy]]
pattern = "import-allowlist"
cell = "coached"

[[policy]]
pattern = "protected.*"
cell = "protected"

[[policy]]
pattern = "human.*"
cell = "structured"
```

- [ ] **Step 4: Run the config test to verify it passes**

Run: `uv run pytest tests/policy/test_cells.py::test_repository_default_policy_cells_file_loads -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add policy/cells.toml tests/policy/test_cells.py
git commit -m "chore(policy): add default policy cell routing config (WP-M2)"
```

---

### Task 3: Service-Level `legis_explain` Contract

**Files:**
- Create: `src/legis/service/explain.py`
- Create: `tests/service/test_explain.py`
- Modify: `src/legis/enforcement/engine.py:37-48`
- Modify: `src/legis/service/__init__.py:16-43`

- [ ] **Step 1: Write the failing service tests**

```python
# tests/service/test_explain.py
from legis.clock import SystemClock
from legis.enforcement.engine import EnforcementEngine
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.policy.cells import PolicyCellRegistry, PolicyCellRule
from legis.service.explain import explain_policy
from legis.store.audit_store import AuditStore


class _AcceptingJudge:
    def evaluate(self, record):
        return JudgeOpinion(
            verdict=Verdict.ACCEPTED,
            model="stub-judge",
            rationale="accepted for explain wiring test",
        )


def _engine(tmp_path, *, judge=None):
    return EnforcementEngine(
        AuditStore(f"sqlite:///{tmp_path / 'gov.db'}"),
        SystemClock(),
        judge=judge,
    )


def test_explain_chill_policy_reports_enabled_self_clearable_cell(tmp_path):
    registry = PolicyCellRegistry(default_cell="chill")

    result = explain_policy(
        registry,
        policy="ordinary.policy",
        entity="src/x.py:f",
        engine=_engine(tmp_path),
        protected_gate=None,
        signoff_gate=None,
    )

    assert result.to_payload() == {
        "cell": "chill",
        "judge_inline": False,
        "self_clearable": True,
        "human_in_loop": False,
        "enabled": True,
        "available_moves": ["legis_submit_override"],
        "required_inputs": [],
    }


def test_explain_coached_policy_reports_disabled_without_judge_and_enabled_with_judge(tmp_path):
    registry = PolicyCellRegistry(
        default_cell="chill",
        rules=(PolicyCellRule(pattern="review.*", cell="coached"),),
    )

    disabled = explain_policy(
        registry,
        policy="review.rationale",
        entity="src/x.py:f",
        engine=_engine(tmp_path),
        protected_gate=None,
        signoff_gate=None,
    )

    assert disabled.to_payload() == {
        "cell": "coached",
        "judge_inline": True,
        "self_clearable": False,
        "human_in_loop": False,
        "enabled": False,
        "available_moves": [],
        "required_inputs": [],
    }

    enabled = explain_policy(
        registry,
        policy="review.rationale",
        entity="src/x.py:f",
        engine=_engine(tmp_path, judge=_AcceptingJudge()),
        protected_gate=None,
        signoff_gate=None,
    )

    assert enabled.enabled is True
    assert enabled.available_moves == ("legis_submit_override",)


def test_explain_protected_policy_reports_required_inputs_even_when_gate_disabled(tmp_path):
    registry = PolicyCellRegistry(
        default_cell="chill",
        rules=(PolicyCellRule(pattern="protected.*", cell="protected"),),
    )

    result = explain_policy(
        registry,
        policy="protected.source-integrity",
        entity="src/x.py:f",
        engine=_engine(tmp_path),
        protected_gate=None,
        signoff_gate=None,
    )

    assert result.to_payload() == {
        "cell": "protected",
        "judge_inline": True,
        "self_clearable": False,
        "human_in_loop": False,
        "enabled": False,
        "available_moves": [],
        "required_inputs": [
            {
                "field": "file_fingerprint",
                "how": "sha256 of the target file contents",
            },
            {
                "field": "ast_path",
                "how": "dotted path to the AST node",
            },
        ],
    }


def test_explain_structured_policy_reports_human_loop_when_signoff_gate_wired(tmp_path):
    registry = PolicyCellRegistry(
        default_cell="chill",
        rules=(PolicyCellRule(pattern="human.*", cell="structured"),),
    )

    result = explain_policy(
        registry,
        policy="human.release-signoff",
        entity="src/x.py:f",
        engine=_engine(tmp_path),
        protected_gate=None,
        signoff_gate=object(),
    )

    assert result.to_payload() == {
        "cell": "structured",
        "judge_inline": False,
        "self_clearable": False,
        "human_in_loop": True,
        "enabled": True,
        "available_moves": ["legis_submit_override", "legis_signoff_status"],
        "required_inputs": [],
    }
```

- [ ] **Step 2: Run the service tests to verify they fail**

Run: `uv run pytest tests/service/test_explain.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'legis.service.explain'`.

- [ ] **Step 3: Add the judge-wiring property to `EnforcementEngine`**

Insert this property in `src/legis/enforcement/engine.py` immediately after `__init__`:

```python
    @property
    def has_judge(self) -> bool:
        return self._judge is not None
```

- [ ] **Step 4: Implement `src/legis/service/explain.py`**

```python
# src/legis/service/explain.py
"""Service-level discovery contract for the MCP ``legis_explain`` tool."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from legis.enforcement.engine import EnforcementEngine
from legis.policy.cells import PolicyCellRegistry


@dataclass(frozen=True)
class RequiredInput:
    field: str
    how: str

    def to_payload(self) -> dict[str, str]:
        return {"field": self.field, "how": self.how}


@dataclass(frozen=True)
class PolicyExplanation:
    cell: str
    judge_inline: bool
    self_clearable: bool
    human_in_loop: bool
    enabled: bool
    available_moves: tuple[str, ...]
    required_inputs: tuple[RequiredInput, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "cell": self.cell,
            "judge_inline": self.judge_inline,
            "self_clearable": self.self_clearable,
            "human_in_loop": self.human_in_loop,
            "enabled": self.enabled,
            "available_moves": list(self.available_moves),
            "required_inputs": [item.to_payload() for item in self.required_inputs],
        }


_PROTECTED_INPUTS = (
    RequiredInput(
        field="file_fingerprint",
        how="sha256 of the target file contents",
    ),
    RequiredInput(
        field="ast_path",
        how="dotted path to the AST node",
    ),
)


def explain_policy(
    registry: PolicyCellRegistry,
    *,
    policy: str,
    entity: str,
    engine: EnforcementEngine | None,
    protected_gate: object | None,
    signoff_gate: object | None,
) -> PolicyExplanation:
    """Explain the governing cell and legal moves for a policy/entity pair.

    ``entity`` is accepted now because it is part of the ratified tool contract.
    The v1 registry routes by policy only, so the value is not used for routing.
    """
    del entity
    cell = registry.cell_for(policy)
    if cell == "chill":
        enabled = engine is not None and not engine.has_judge
        return PolicyExplanation(
            cell="chill",
            judge_inline=False,
            self_clearable=True,
            human_in_loop=False,
            enabled=enabled,
            available_moves=("legis_submit_override",) if enabled else (),
            required_inputs=(),
        )
    if cell == "coached":
        enabled = engine is not None and engine.has_judge
        return PolicyExplanation(
            cell="coached",
            judge_inline=True,
            self_clearable=False,
            human_in_loop=False,
            enabled=enabled,
            available_moves=("legis_submit_override",) if enabled else (),
            required_inputs=(),
        )
    if cell == "structured":
        enabled = signoff_gate is not None
        return PolicyExplanation(
            cell="structured",
            judge_inline=False,
            self_clearable=False,
            human_in_loop=True,
            enabled=enabled,
            available_moves=("legis_submit_override", "legis_signoff_status")
            if enabled
            else (),
            required_inputs=(),
        )
    if cell == "protected":
        enabled = protected_gate is not None
        return PolicyExplanation(
            cell="protected",
            judge_inline=True,
            self_clearable=False,
            human_in_loop=False,
            enabled=enabled,
            available_moves=("legis_submit_override",) if enabled else (),
            required_inputs=_PROTECTED_INPUTS,
        )
    raise AssertionError(f"unknown policy cell {cell!r}")
```

- [ ] **Step 5: Re-export the explain API from `legis.service`**

Update `src/legis/service/__init__.py` to include the new imports and `__all__` entries:

```python
from legis.service.explain import PolicyExplanation, RequiredInput, explain_policy
```

Add these names to `__all__`:

```python
    "PolicyExplanation",
    "RequiredInput",
    "explain_policy",
```

- [ ] **Step 6: Run the service tests to verify they pass**

Run: `uv run pytest tests/service/test_explain.py -v`

Expected: PASS.

- [ ] **Step 7: Run existing service tests to catch regressions**

Run: `uv run pytest tests/service tests/enforcement/test_engine_chill.py tests/enforcement/test_engine_coached.py -v`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/legis/service/explain.py src/legis/enforcement/engine.py src/legis/service/__init__.py tests/service/test_explain.py
git commit -m "feat(service): add policy explain contract (WP-M2)"
```

---

### Task 4: Startup-Loaded Registry in MCP Runtime

**Files:**
- Modify: `src/legis/mcp.py:18-117`
- Modify: `tests/mcp/test_server.py`

- [ ] **Step 1: Add a failing MCP runtime loading test**

Append this test to `tests/mcp/test_server.py`:

```python
def test_build_runtime_loads_policy_cells_from_configured_path(tmp_path, monkeypatch):
    cells = tmp_path / "cells.toml"
    cells.write_text(
        """
default_cell = "chill"

[[policy]]
pattern = "secure.*"
cell = "protected"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("LEGIS_POLICY_CELLS", str(cells))
    monkeypatch.setenv("LEGIS_GOVERNANCE_DB", f"sqlite:///{tmp_path / 'gov.db'}")

    from legis.mcp import build_runtime

    runtime = build_runtime("agent-1")

    assert runtime.cell_registry is not None
    assert runtime.cell_registry.cell_for("secure.source") == "protected"
    assert runtime.cell_registry.cell_for("ordinary.policy") == "chill"
```

- [ ] **Step 2: Run the MCP test to verify it fails**

Run: `uv run pytest tests/mcp/test_server.py::test_build_runtime_loads_policy_cells_from_configured_path -v`

Expected: FAIL with `AttributeError: 'McpRuntime' object has no attribute 'cell_registry'`.

- [ ] **Step 3: Add registry imports and the runtime field**

In `src/legis/mcp.py`, add this import near the existing policy import:

```python
from legis.policy.cells import PolicyCellRegistry, default_policy_cells, load_policy_cells
```

Add this field to `McpRuntime`:

```python
    cell_registry: PolicyCellRegistry | None = None
```

- [ ] **Step 4: Add the startup loader helper**

Insert this helper above `build_runtime` in `src/legis/mcp.py`:

```python
def _load_policy_cell_registry() -> PolicyCellRegistry:
    configured = os.environ.get("LEGIS_POLICY_CELLS")
    if configured:
        return load_policy_cells(configured)

    root = Path(os.environ.get("LEGIS_SOURCE_ROOT") or os.getcwd())
    default_path = root / "policy" / "cells.toml"
    if default_path.exists():
        return load_policy_cells(default_path)

    return default_policy_cells()
```

- [ ] **Step 5: Wire the registry into `build_runtime`**

In the `return McpRuntime(...)` call in `src/legis/mcp.py`, add:

```python
        cell_registry=_load_policy_cell_registry(),
```

- [ ] **Step 6: Run the MCP loading test to verify it passes**

Run: `uv run pytest tests/mcp/test_server.py::test_build_runtime_loads_policy_cells_from_configured_path -v`

Expected: PASS.

- [ ] **Step 7: Run the existing MCP tests to catch regressions**

Run: `uv run pytest tests/mcp/test_server.py -v`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/legis/mcp.py tests/mcp/test_server.py
git commit -m "feat(mcp): load policy cell registry at startup (WP-M2)"
```

---

### Task 5: WP-M2 Verification Gate

**Files:**
- No source edits unless verification exposes a regression.

- [ ] **Step 1: Run the focused WP-M2 test set**

Run:

```bash
uv run pytest tests/policy/test_cells.py tests/service/test_explain.py tests/mcp/test_server.py -v
```

Expected: PASS.

- [ ] **Step 2: Run the full suite**

Run:

```bash
uv run pytest -q
```

Expected: PASS.

- [ ] **Step 3: Commit any regression fixes from verification**

If Step 1 or Step 2 required a source or test fix, commit only those files:

```bash
git add <files changed by the verification fix>
git commit -m "fix: keep WP-M2 registry explain tests green"
```

If Step 1 and Step 2 passed without edits, do not create an empty commit.

---

## Self-Review

**Spec coverage:** This plan implements the line-85 `policy/cells.toml` registry, policy-name and glob mapping, default for unlisted policies, stdlib `tomllib` loading, fail-closed malformed config handling, service-level `legis_explain`, and enabled/disabled deployment wiring for mapped cells. `legis_submit_override` routing is intentionally left for WP-M3/WP-M4 because the spec sequences submit after the registry and explain brain exists.

**Placeholder scan:** The plan has no forbidden marker text, undefined helper names, or generic test-writing instructions. Every code-writing step names the exact file and includes concrete code.

**Type consistency:** The registry type is consistently `PolicyCellRegistry`; rules are `PolicyCellRule`; explanation results are `PolicyExplanation`; required-input entries are `RequiredInput`; the service function is `explain_policy`; the startup field is `McpRuntime.cell_registry`.
