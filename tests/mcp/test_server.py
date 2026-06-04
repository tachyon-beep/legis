import io
import json
import hashlib

from legis.cli import build_parser
from legis.clock import FixedClock
from legis.enforcement.engine import EnforcementEngine
from legis.enforcement.protected import ProtectedGate, TrailVerifier
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.store.audit_store import AuditStore


class ScriptedJudge:
    def __init__(self, opinion):
        self.opinion = opinion

    def evaluate(self, record):
        return self.opinion


def _messages(*items):
    return "\n".join(json.dumps(item) for item in items) + "\n"


def _run(messages, runtime):
    from legis.mcp import run_jsonrpc

    inp = io.StringIO(messages)
    out = io.StringIO()
    run_jsonrpc(inp, out, runtime)
    return [json.loads(line) for line in out.getvalue().splitlines()]


def _runtime(tmp_path, *, agent_id="agent-launch"):
    from legis.mcp import McpRuntime

    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    engine = EnforcementEngine(store, FixedClock("2026-06-02T12:00:00+00:00"))
    return McpRuntime(agent_id=agent_id, engine=engine), store


def _fingerprint(path):
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _protected_runtime(tmp_path, *, agent_id="agent-launch", source_root=None):
    from legis.mcp import McpRuntime

    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    clock = FixedClock("2026-06-02T12:00:00+00:00")
    protected_gate = ProtectedGate(
        store,
        clock,
        judge=ScriptedJudge(JudgeOpinion(Verdict.ACCEPTED, "judge@1", "ok")),
        key=b"k",
    )
    return (
        McpRuntime(
            agent_id=agent_id,
            protected_gate=protected_gate,
            trail_verifier=TrailVerifier(b"k", frozenset({"no-eval"})),
            source_root=source_root,
        ),
        store,
    )


def test_cli_has_mcp_subcommand_with_launch_bound_agent_id():
    args = build_parser().parse_args(["mcp", "--agent-id", "agent-1"])
    assert args.command == "mcp"
    assert args.agent_id == "agent-1"


def test_initialize_and_tools_list_hide_actor_identity_arguments(tmp_path):
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
    assert {
        "submit_override",
        "protected_override",
        "policy_evaluate",
        "wardline_scan_results",
        "list_overrides",
    } <= set(by_name)
    assert "protected_operator_override" not in by_name
    for tool in tools:
        props = tool["inputSchema"].get("properties", {})
        assert "agent_id" not in props
        assert "operator_id" not in props
    wardline_props = by_name["wardline_scan_results"]["inputSchema"]["properties"]
    assert "cell" not in wardline_props
    assert "cell_by_severity" not in wardline_props


def test_submit_override_tool_records_launch_agent_not_tool_arguments(tmp_path):
    runtime, store = _runtime(tmp_path, agent_id="agent-launch")
    responses = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "submit_override",
                    "arguments": {
                        "policy": "no-eval",
                        "entity": "src/x.py:f",
                        "rationale": "reviewed",
                        "agent_id": "spoofed-agent",
                    },
                },
            }
        ),
        runtime,
    )
    assert responses[0]["result"]["structuredContent"]["accepted"] is True
    assert store.read_all()[0].payload["agent_id"] == "agent-launch"


def test_disabled_protected_cell_maps_to_mcp_tool_error(tmp_path):
    runtime, _store = _runtime(tmp_path)
    responses = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "protected_override",
                    "arguments": {
                        "policy": "no-eval",
                        "entity": "src/x.py:f",
                        "rationale": "reviewed",
                        "file_fingerprint": "sha256:abc",
                        "ast_path": "Module/FunctionDef[f]",
                    },
                },
            }
        ),
        runtime,
    )
    result = responses[0]["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == "NOT_ENABLED"


def test_protected_tool_rejects_stale_source_fingerprint_before_signing(tmp_path):
    source = tmp_path / "src" / "x.py"
    source.parent.mkdir()
    source.write_text("def f():\n    return 1\n")
    runtime, store = _protected_runtime(tmp_path, source_root=tmp_path)
    responses = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "protected_override",
                    "arguments": {
                        "policy": "no-eval",
                        "entity": "src/x.py:f",
                        "rationale": "reviewed",
                        "file_fingerprint": "sha256:" + "0" * 64,
                        "ast_path": "Module/FunctionDef[f]",
                    },
                },
            }
        ),
        runtime,
    )

    result = responses[0]["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == "INVALID_ARGUMENT"
    assert "fingerprint does not match current source" in result["structuredContent"]["message"]
    assert store.read_all() == []


def test_protected_tool_records_verified_source_binding(tmp_path):
    source = tmp_path / "src" / "x.py"
    source.parent.mkdir()
    source.write_text("def f():\n    return 1\n")
    runtime, store = _protected_runtime(tmp_path, source_root=tmp_path)
    responses = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "protected_override",
                    "arguments": {
                        "policy": "no-eval",
                        "entity": "src/x.py:f",
                        "rationale": "reviewed",
                        "file_fingerprint": _fingerprint(source),
                        "ast_path": "Module/FunctionDef[f]",
                    },
                },
            }
        ),
        runtime,
    )

    result = responses[0]["result"]["structuredContent"]
    assert result["accepted"] is True
    assert store.read_all()[0].payload["extensions"]["source_binding"]["status"] == "verified"


def test_wardline_tool_uses_server_owned_routing_and_launch_agent(tmp_path):
    runtime, store = _runtime(tmp_path, agent_id="agent-launch")
    runtime.wardline_cell = "surface_only"
    responses = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "wardline_scan_results",
                    "arguments": {
                        "scan": {
                            "findings": [
                                {
                                    "rule_id": "PY-WL-101",
                                    "message": "m",
                                    "severity": "INFO",
                                    "kind": "defect",
                                    "fingerprint": "fp1",
                                    "qualname": "m.f",
                                    "properties": {},
                                    "suppressed": "active",
                                }
                            ]
                        },
                        "cell": "surface_override",
                        "agent_id": "spoofed-agent",
                    },
                },
            }
        ),
        runtime,
    )
    result = responses[0]["result"]["structuredContent"]
    assert result["routed"][0]["mode"] == "surface_only"
    payload = store.read_all()[0].payload
    assert payload["agent_id"] == "agent-launch"
    assert payload["extensions"]["wardline"]["scan_digest"].startswith("sha256:")


def test_wardline_tool_invalid_server_cell_maps_to_mcp_error(tmp_path):
    runtime, _store = _runtime(tmp_path)
    runtime.wardline_cell = "not-a-cell"
    responses = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "wardline_scan_results",
                    "arguments": {"scan": {"findings": []}},
                },
            }
        ),
        runtime,
    )
    result = responses[0]["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == "INVALID_ARGUMENT"


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
