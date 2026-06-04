# WP-M3: MCP Vertical Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the pre-spec MCP tool surface with the first ratified agent-facing vertical slice: `policy_explain`, chill-only `override_submit` returning `ACCEPTED_SELF`, `check_list`, launch-bound `agent_id`, and the operator-surface-absent invariant.

**Architecture:** Keep the dependency-free JSON-RPC-over-stdio server in `src/legis/mcp.py`, but make its discovered and callable surface match the approved `<entity>_<verb>` WP-M3 contract in one atomic slice, with no `legis_` prefix because MCP hosts already expose server identity as `mcp__legis__<tool>`. `policy_explain` calls the WP-M2 service explanation contract, `override_submit` routes through the registry and only executes enabled chill-cell writes, and `check_list` reads the check store. Legacy pre-spec tool names must not remain callable.

**Tech Stack:** Python 3.12+, stdlib JSON/stdio MCP framing, pytest, existing `CheckSurface`, existing `EnforcementEngine`, existing `PolicyCellRegistry`, no new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-06-03-legis-mcp-surface-design.md:92-149` and WP-M3 at `docs/superpowers/specs/2026-06-03-legis-mcp-surface-design.md:183-188`.

**Baseline:** WP-M2 is complete at `6105b61`; full suite was green with `331 passed`.

---

## File Structure

- **Modify** `src/legis/mcp.py` — expose only the WP-M3 tool catalog, make each listed tool callable, remove legacy pre-spec tool dispatch branches, add check-store runtime support, map disabled cells to `CELL_NOT_ENABLED`, and keep JSON-RPC framing unchanged.
- **Modify** `tests/mcp/test_server.py` — rewrite MCP tests around the WP-M3 tool names, outcome envelope, read tool, launch-bound agent identity, legacy-name removal, and structural absence of operator authority.
- **Modify** `src/legis/cli.py` — add `legis mcp --check-db` and `--policy-cells` pass-throughs so hosts can launch the in-process MCP server against explicit check stores and registry files.
- **Modify** `tests/test_cli.py` — cover the new `legis mcp` flags.

This WP does **not** implement coached, structured, protected, sign-off polling, policy evaluation, Wardline routing, remaining reads, override-rate, or tamper-to-`AUDIT_INTEGRITY_FAILURE`; those are WP-M4/M5.

---

### Task 1: Coherent WP-M3 MCP Tool Surface

**Files:**
- Modify: `src/legis/mcp.py`
- Modify: `tests/mcp/test_server.py`

- [ ] **Step 1: Replace MCP tests with the WP-M3 surface tests**

Replace `tests/mcp/test_server.py` with this WP-M3-focused test module:

```python
import io
import json

from legis.checks.models import CheckOutcome, CheckRun
from legis.checks.surface import CheckSurface
from legis.cli import build_parser
from legis.clock import FixedClock
from legis.enforcement.engine import EnforcementEngine
from legis.policy.cells import PolicyCellRegistry, PolicyCellRule
from legis.store.audit_store import AuditStore


def _messages(*items):
    return "\n".join(json.dumps(item) for item in items) + "\n"


def _run(messages, runtime):
    from legis.mcp import run_jsonrpc

    inp = io.StringIO(messages)
    out = io.StringIO()
    run_jsonrpc(inp, out, runtime)
    return [json.loads(line) for line in out.getvalue().splitlines()]


def _runtime(tmp_path, *, agent_id="agent-launch", check_surface=None):
    from legis.mcp import McpRuntime

    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    engine = EnforcementEngine(store, FixedClock("2026-06-02T12:00:00+00:00"))
    return McpRuntime(
        agent_id=agent_id,
        engine=engine,
        check_surface=check_surface,
    ), store


def test_cli_has_mcp_subcommand_with_launch_bound_agent_id():
    args = build_parser().parse_args(["mcp", "--agent-id", "agent-1"])
    assert args.command == "mcp"
    assert args.agent_id == "agent-1"


def test_initialize_and_tools_list_exposes_only_wp_m3_agent_tools(tmp_path):
    runtime, _store = _runtime(tmp_path)
    responses = _run(
        _messages(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ),
        runtime,
    )

    assert responses[0]["result"]["serverInfo"]["name"] == "legis"
    tools = responses[1]["result"]["tools"]
    by_name = {tool["name"]: tool for tool in tools}

    assert set(by_name) == {
        "policy_explain",
        "override_submit",
        "check_list",
    }
    assert "signoff_sign" not in by_name
    assert "protected_operator_override" not in by_name
    assert "operator_override" not in by_name

    for tool in tools:
        assert not tool["name"].startswith("legis_")
        props = tool["inputSchema"].get("properties", {})
        assert "agent_id" not in props
        assert "operator_id" not in props

    submit_description = by_name["override_submit"]["description"]
    assert "records one new chill-cell override attempt" in submit_description


def test_policy_explain_returns_service_explanation_payload(tmp_path):
    runtime, _store = _runtime(tmp_path)
    runtime.cell_registry = PolicyCellRegistry(
        default_cell="chill",
        rules=(PolicyCellRule(pattern="human.*", cell="structured"),),
    )
    runtime.signoff_gate = object()

    responses = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "policy_explain",
                    "arguments": {
                        "policy": "human.release-signoff",
                        "entity": "src/x.py:f",
                    },
                },
            }
        ),
        runtime,
    )

    result = responses[0]["result"]
    assert "isError" not in result
    assert result["structuredContent"] == {
        "cell": "structured",
        "judge_inline": False,
        "self_clearable": False,
        "human_in_loop": True,
        "enabled": True,
        "available_moves": ["override_submit"],
        "required_inputs": [],
    }


def test_override_submit_chill_records_launch_agent_and_returns_accepted_self(tmp_path):
    runtime, store = _runtime(tmp_path, agent_id="agent-launch")
    runtime.cell_registry = PolicyCellRegistry(default_cell="chill")

    responses = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "override_submit",
                    "arguments": {
                        "policy": "ordinary.policy",
                        "entity": "src/x.py:f",
                        "rationale": "generated file; lint is not applicable",
                        "agent_id": "spoofed-agent",
                    },
                },
            }
        ),
        runtime,
    )

    result = responses[0]["result"]
    assert "isError" not in result
    assert result["structuredContent"] == {
        "outcome": "ACCEPTED_SELF",
        "cell": "chill",
        "seq": 1,
        "note": "self-cleared; human reviews asynchronously",
    }
    assert store.read_all()[0].payload["agent_id"] == "agent-launch"


def test_override_submit_non_chill_cell_returns_cell_not_enabled_without_write(tmp_path):
    runtime, store = _runtime(tmp_path)
    runtime.cell_registry = PolicyCellRegistry(
        default_cell="chill",
        rules=(PolicyCellRule(pattern="human.*", cell="structured"),),
    )

    responses = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "override_submit",
                    "arguments": {
                        "policy": "human.release-signoff",
                        "entity": "src/x.py:f",
                        "rationale": "needs release signoff",
                    },
                },
            }
        ),
        runtime,
    )

    result = responses[0]["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == "CELL_NOT_ENABLED"
    assert "structured" in result["structuredContent"]["message"]
    assert store.read_all() == []


def test_check_list_reads_recorded_checks_by_commit_and_pr(tmp_path):
    checks = CheckSurface(f"sqlite:///{tmp_path / 'checks.db'}")
    checks.record(
        CheckRun(
            check_name="unit",
            run_id="run-1",
            commit_sha="abc123",
            outcome=CheckOutcome.PASS,
            branch="main",
            pr=7,
            ran_against="abc123",
        )
    )
    runtime, _store = _runtime(tmp_path, check_surface=checks)

    responses = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "check_list",
                    "arguments": {"target_type": "commit", "target": "abc123"},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "check_list",
                    "arguments": {"target_type": "pr", "target": "7"},
                },
            },
        ),
        runtime,
    )

    for response in responses:
        result = response["result"]
        assert "isError" not in result
        assert result["structuredContent"]["checks"] == [
            {
                "check_name": "unit",
                "run_id": "run-1",
                "commit_sha": "abc123",
                "outcome": "pass",
                "branch": "main",
                "pr": 7,
                "ran_against": "abc123",
                "rule_set": None,
                "policy_version": None,
                "started_at": None,
                "finished_at": None,
            }
        ]


def test_check_list_invalid_target_type_is_tool_error(tmp_path):
    checks = CheckSurface(f"sqlite:///{tmp_path / 'checks.db'}")
    runtime, _store = _runtime(tmp_path, check_surface=checks)

    responses = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "check_list",
                    "arguments": {"target_type": "tag", "target": "v1"},
                },
            }
        ),
        runtime,
    )

    result = responses[0]["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == "INVALID_ARGUMENT"
    assert "target_type" in result["structuredContent"]["message"]


def test_tools_call_with_non_object_params_returns_invalid_argument(tmp_path):
    runtime, _store = _runtime(tmp_path)
    responses = _run(
        _messages(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": "bad"}
        ),
        runtime,
    )

    result = responses[0]["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == "INVALID_ARGUMENT"
    assert "params" in result["structuredContent"]["message"]


def test_non_wp_m3_tool_names_are_not_callable(tmp_path):
    runtime, store = _runtime(tmp_path)

    for non_m3_name in (
        "submit_override",
        "protected_override",
        "signoff_request",
        "policy_evaluate",
        "wardline_scan_results",
        "list_overrides",
        "override_rate",
    ):
        responses = _run(
            _messages(
                {
                    "jsonrpc": "2.0",
                    "id": non_m3_name,
                    "method": "tools/call",
                    "params": {"name": non_m3_name, "arguments": {}},
                }
            ),
            runtime,
        )
        result = responses[0]["result"]
        assert result["isError"] is True
        assert result["structuredContent"]["error_code"] == "UNKNOWN_TOOL"

    assert store.read_all() == []


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
    monkeypatch.setenv("LEGIS_CHECK_DB", f"sqlite:///{tmp_path / 'checks.db'}")

    from legis.mcp import build_runtime

    runtime = build_runtime("agent-1")

    assert runtime.cell_registry is not None
    assert runtime.cell_registry.cell_for("secure.source") == "protected"
    assert runtime.cell_registry.cell_for("ordinary.policy") == "chill"
```

- [ ] **Step 2: Run the MCP test file to verify the replacement tests fail**

Run:

```bash
uv run pytest tests/mcp/test_server.py -v
```

Expected: FAIL before implementation because `McpRuntime` has no `check_surface`, ratified tools have no handlers, and legacy names are still callable.

- [ ] **Step 3: Update imports and runtime fields in `src/legis/mcp.py`**

At the top of `src/legis/mcp.py`, use these imports for WP-M3:

```python
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from typing import Any, TextIO

from legis import __version__
from legis.checks.models import CheckRun
from legis.checks.surface import CheckSurface
from legis.clock import SystemClock
from legis.enforcement.engine import EnforcementEngine
from legis.enforcement.protected import ProtectedGate
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.policy.cells import (
    PolicyCellRegistry,
    default_policy_cells,
    load_policy_cells,
)
from legis.records.override_record import OverrideRecord
from legis.service.errors import (
    AuditIntegrityError,
    InvalidArgumentError,
    NotEnabledError,
    NotFoundError,
    ServiceError,
)
from legis.service.explain import explain_policy
from legis.service.governance import submit_override
from legis.store.audit_store import AuditStore
```

Make `McpRuntime`:

```python
@dataclass
class McpRuntime:
    agent_id: str
    engine: EnforcementEngine | None = None
    identity: Any | None = None
    protected_gate: ProtectedGate | None = None
    signoff_gate: Any | None = None
    cell_registry: PolicyCellRegistry | None = None
    check_surface: CheckSurface | None = None
```

In `build_runtime`, import both defaults:

```python
    from legis.api.app import DEFAULT_CHECK_DB, DEFAULT_GOVERNANCE_DB
```

Return `McpRuntime` with:

```python
        check_surface=CheckSurface(
            os.environ.get("LEGIS_CHECK_DB", DEFAULT_CHECK_DB)
        ),
```

- [ ] **Step 4: Replace helpers and `call_tool()` with the WP-M3 implementation**

Keep `_schema`, `tool_definitions`, `_tool_result`, `_tool_error`, `_service_error`, `_arguments`, and `_require`; update/add these exact pieces:

```python
def _service_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, AuditIntegrityError):
        return _tool_error("AUDIT_INTEGRITY_FAILURE", str(exc))
    if isinstance(exc, NotEnabledError):
        return _tool_error("CELL_NOT_ENABLED", str(exc))
    if isinstance(exc, NotFoundError):
        return _tool_error("NOT_FOUND", str(exc))
    if isinstance(exc, InvalidArgumentError):
        return _tool_error("INVALID_ARGUMENT", str(exc))
    if isinstance(exc, ServiceError):
        return _tool_error("SERVICE_ERROR", str(exc))
    if isinstance(exc, ValueError):
        return _tool_error("INVALID_ARGUMENT", str(exc))
    return _tool_error("INTERNAL_ERROR", str(exc))
```

Update `_arguments` so malformed JSON-RPC `tools/call` params are a recoverable tool error:

```python
def _arguments(params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if not isinstance(params, dict):
        raise ValueError("tools/call params must be an object")
    name = params.get("name")
    arguments = params.get("arguments", {})
    if not isinstance(name, str):
        raise ValueError("tools/call requires a string tool name")
    if not isinstance(arguments, dict):
        raise ValueError("tools/call arguments must be an object")
    return name, arguments
```

Add:

```python
def _check_to_dict(run: CheckRun) -> dict[str, Any]:
    return {
        "check_name": run.check_name,
        "run_id": run.run_id,
        "commit_sha": run.commit_sha,
        "outcome": run.outcome.value,
        "branch": run.branch,
        "pr": run.pr,
        "ran_against": run.ran_against,
        "rule_set": run.rule_set,
        "policy_version": run.policy_version,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


def _registry(runtime: McpRuntime) -> PolicyCellRegistry:
    return runtime.cell_registry or default_policy_cells()


_WP_M3_TOOLS = frozenset({"policy_explain", "override_submit", "check_list"})


def _wp_m3_explanation_payload(explanation) -> dict[str, Any]:
    payload = explanation.to_payload()
    payload["available_moves"] = [
        move for move in payload["available_moves"] if move in _WP_M3_TOOLS
    ]
    return payload
```

Replace `call_tool()` with:

```python
def call_tool(runtime: McpRuntime, name: str, args: dict[str, Any]) -> dict[str, Any]:
    try:
        if name == "policy_explain":
            explanation = explain_policy(
                _registry(runtime),
                policy=_require(args, "policy"),
                entity=_require(args, "entity"),
                engine=runtime.engine,
                protected_gate=runtime.protected_gate,
                signoff_gate=runtime.signoff_gate,
            )
            return _tool_result(_wp_m3_explanation_payload(explanation))

        if name == "override_submit":
            policy = _require(args, "policy")
            entity = _require(args, "entity")
            explanation = explain_policy(
                _registry(runtime),
                policy=policy,
                entity=entity,
                engine=runtime.engine,
                protected_gate=runtime.protected_gate,
                signoff_gate=runtime.signoff_gate,
            )
            if explanation.cell != "chill" or not explanation.enabled:
                raise NotEnabledError(
                    f"cell {explanation.cell!r} is not enabled for WP-M3 submit"
                )
            if runtime.engine is None:
                raise NotEnabledError("cell 'chill' is not enabled for WP-M3 submit")
            override_result = submit_override(
                runtime.engine,
                identity=runtime.identity,
                policy=policy,
                entity=entity,
                rationale=_require(args, "rationale"),
                agent_id=runtime.agent_id,
            )
            return _tool_result(
                {
                    "outcome": "ACCEPTED_SELF",
                    "cell": "chill",
                    "seq": override_result.seq,
                    "note": "self-cleared; human reviews asynchronously",
                }
            )

        if name == "check_list":
            if runtime.check_surface is None:
                raise NotEnabledError("check surface is not enabled")
            target_type = _require(args, "target_type")
            target = _require(args, "target")
            if target_type == "commit":
                checks = runtime.check_surface.for_commit(target)
                response_target: str | int = target
            elif target_type == "branch":
                checks = runtime.check_surface.for_branch(target)
                response_target = target
            elif target_type == "pr":
                try:
                    pr = int(target)
                except ValueError as exc:
                    raise InvalidArgumentError(
                        "target_type 'pr' requires an integer target"
                    ) from exc
                checks = runtime.check_surface.for_pr(pr)
                response_target = pr
            else:
                raise InvalidArgumentError(
                    "target_type must be one of: commit, branch, pr"
                )
            return _tool_result(
                {
                    "target_type": target_type,
                    "target": response_target,
                    "checks": [_check_to_dict(run) for run in checks],
                }
            )

        return _tool_error("UNKNOWN_TOOL", f"unknown tool: {name}")
    except Exception as exc:
        return _service_error(exc)
```

Remove old helper/import/field code that was used only by removed pre-spec tools: `TrailVerifier`, `default_grammar`, `request_signoff`, `submit_protected_override`, `verified_records`, `compute_override_rate`, `evaluate_policy`, `route_wardline_scan`, `WardlineCellPolicy`, `WardlineSeverity`, `_parse_wardline_cell_map`, `trail_verifier`, `grammar`, `source_root`, `wardline_*`.

- [ ] **Step 5: Run MCP tests to verify they pass**

Run:

```bash
uv run pytest tests/mcp/test_server.py -v
```

Expected: PASS.

- [ ] **Step 6: Run companion registry/explain tests**

Run:

```bash
uv run pytest tests/service/test_explain.py tests/policy/test_cells.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/legis/mcp.py tests/mcp/test_server.py
git commit -m "feat(mcp): add coherent WP-M3 callable surface"
```

If this task is repairing a prior catalog-only commit in the same branch, use this commit message instead:

```bash
git add src/legis/mcp.py tests/mcp/test_server.py
git commit -m "fix(mcp): make WP-M3 tool catalog callable"
```

---

### Task 2: MCP CLI Store/Registry Flags

**Files:**
- Modify: `src/legis/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Add failing CLI test for MCP config flags**

Append this test to `tests/test_cli.py`:

```python
def test_mcp_command_accepts_store_and_policy_cell_flags():
    from legis.cli import build_parser

    args = build_parser().parse_args(
        [
            "mcp",
            "--agent-id",
            "agent-1",
            "--governance-db",
            "sqlite:///gov.db",
            "--check-db",
            "sqlite:///checks.db",
            "--policy-cells",
            "policy/cells.toml",
        ]
    )

    assert args.command == "mcp"
    assert args.agent_id == "agent-1"
    assert args.governance_db == "sqlite:///gov.db"
    assert args.check_db == "sqlite:///checks.db"
    assert args.policy_cells == "policy/cells.toml"
```

- [ ] **Step 2: Run the CLI test to verify it fails**

Run:

```bash
uv run pytest tests/test_cli.py::test_mcp_command_accepts_store_and_policy_cell_flags -v
```

Expected: FAIL because `--check-db` and `--policy-cells` are not recognized on `legis mcp`.

- [ ] **Step 3: Add MCP parser flags**

In `src/legis/cli.py`, in the `mcp = subparsers.add_parser(...)` section, add:

```python
    mcp.add_argument(
        "--check-db",
        help="Check store URL (falls back to LEGIS_CHECK_DB env var)",
    )
    mcp.add_argument(
        "--policy-cells",
        help="Policy cell registry TOML path (falls back to LEGIS_POLICY_CELLS env var)",
    )
```

- [ ] **Step 4: Wire CLI flags to environment before launching MCP**

In the `if args.command == "mcp":` block, after governance/protected/clarion handling, add:

```python
        if args.check_db:
            os.environ["LEGIS_CHECK_DB"] = args.check_db
        if args.policy_cells:
            os.environ["LEGIS_POLICY_CELLS"] = args.policy_cells
```

- [ ] **Step 5: Run the CLI test to verify it passes**

Run:

```bash
uv run pytest tests/test_cli.py::test_mcp_command_accepts_store_and_policy_cell_flags -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/legis/cli.py tests/test_cli.py
git commit -m "feat(cli): add MCP store and registry flags"
```

---

### Task 3: WP-M3 Verification And Final Review

**Files:**
- No source edits unless verification exposes a regression.

- [ ] **Step 1: Run the MCP test file**

Run:

```bash
uv run pytest tests/mcp/test_server.py -v
```

Expected: PASS.

- [ ] **Step 2: Run CLI tests**

Run:

```bash
uv run pytest tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 3: Run the focused WP-M3 set**

Run:

```bash
uv run pytest tests/mcp/test_server.py tests/test_cli.py tests/service/test_explain.py tests/policy/test_cells.py -v
```

Expected: PASS.

- [ ] **Step 4: Run the full suite**

Run:

```bash
uv run pytest -q
```

Expected: PASS.

- [ ] **Step 5: Commit any verification fixes**

If verification required source edits, commit only those files:

```bash
git add <files changed by verification fix>
git commit -m "fix(mcp): keep WP-M3 vertical slice green"
```

If verification passed without edits, do not create an empty commit.

- [ ] **Step 6: Run final code review**

Dispatch a read-only reviewer over the full WP-M3 diff from `6105b61..HEAD`. The reviewer must check:
- listed tools are callable;
- listed tools follow Filigree ADR-016-style `<entity>_<verb>` names with no `legis_` project prefix;
- legacy pre-spec tools are not callable;
- operator authority is absent structurally;
- launch-bound `agent_id` is used for writes;
- expected governance outcomes are not `isError`;
- no WP-M4/M5 surface leaked in.

Expected: Approved, or concrete findings fixed and re-reviewed.

---

## Self-Review

**Spec coverage:** This plan implements WP-M3: ratified MCP stdio surface over the existing hand-rolled JSON-RPC server, `policy_explain`, chill-only `override_submit` with `ACCEPTED_SELF`, `check_list`, launch-bound agent identity, and the structural absence test for `operator_id`, operator sign-off, and operator override tools. It deliberately leaves WP-M4/M5 tools and cells out.

**MCP engineering gate:** The side-effecting tool description states its retry/idempotency behavior: repeated `override_submit` calls create repeated audit records because idempotency keys are explicitly out of v1 scope. Error envelopes are structured through `structuredContent.error_code`; expected governance outcomes do not set `isError`. Return shapes are bounded for this WP: one explain object, one submit object, and check rows for one selected target.

**Placeholder scan:** The plan has no forbidden marker text, undefined helper names, or generic test-writing instructions. Every code-writing step names exact files and includes concrete code.

**Type consistency:** The runtime field is consistently `check_surface`; the registry helper is `_registry`; the three tool names are consistently `policy_explain`, `override_submit`, and `check_list`; the submit outcome is consistently `ACCEPTED_SELF`; disabled non-chill submit maps to `CELL_NOT_ENABLED`.
