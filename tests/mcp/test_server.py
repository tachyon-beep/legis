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
