import io
import json
import logging
import sqlite3

from legis.canonical import canonical_json, content_hash
from legis.checks.models import CheckOutcome, CheckRun
from legis.checks.surface import CheckSurface
from legis.cli import build_parser
from legis.clock import FixedClock
from legis.enforcement.engine import EnforcementEngine
from legis.enforcement.protected import ProtectedGate, TrailVerifier
from legis.enforcement.signoff import SignoffGate
from legis.enforcement.signing import sign
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.git.surface import GitSurface
from legis.identity.entity_key import EntityKey
from legis.policy.grammar import AllowlistBoundary, PolicyGrammar
from legis.policy.cells import PolicyCellRegistry, PolicyCellRule
from legis.pulls.models import PullRequest, PullRequestState
from legis.pulls.surface import PullSurface
from legis.store.audit_store import GENESIS, _chain
from legis.store.audit_store import AuditStore
from legis.wardline.ingest import wardline_artifact_fields


def _messages(*items):
    return "\n".join(json.dumps(item) for item in items) + "\n"


def _run(messages, runtime):
    from legis.mcp import run_jsonrpc

    inp = io.StringIO(messages)
    out = io.StringIO()
    run_jsonrpc(inp, out, runtime)
    return [json.loads(line) for line in out.getvalue().splitlines()]


class _ScriptedJudge:
    def __init__(self, *opinions):
        self._opinions = list(opinions)

    def evaluate(self, record):
        if self._opinions:
            return self._opinions.pop(0)
        return JudgeOpinion(Verdict.ACCEPTED, "judge@1", "ok")


KEY = b"protected-key-1"


def _runtime(
    tmp_path,
    *,
    agent_id="agent-launch",
    check_surface=None,
    judge=None,
):
    from legis.mcp import McpRuntime

    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    engine = EnforcementEngine(
        store, FixedClock("2026-06-02T12:00:00+00:00"), judge=judge
    )
    return McpRuntime(
        agent_id=agent_id,
        initialized=True,
        engine=engine,
        check_surface=check_surface,
    ), store


def _active_scan():
    return {
        "findings": [
            {
                "rule_id": "PY-WL-101",
                "message": "untrusted reaches trusted",
                "severity": "ERROR",
                "kind": "defect",
                "fingerprint": "fp1",
                "qualname": "m.f",
                "properties": {"actual_return": "UNKNOWN_RAW"},
                "suppressed": "active",
            }
        ]
    }


def _signed_wardline_scan(scan, key=b"wardline-key"):
    return {**scan, "artifact_signature": sign(wardline_artifact_fields(scan), key)}


def _tamper_first_record_and_rechain(db, mutate):
    con = sqlite3.connect(db)
    con.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
    con.execute("DROP TRIGGER IF EXISTS audit_log_no_delete")
    seq, payload = con.execute(
        "SELECT seq, payload FROM audit_log ORDER BY seq ASC LIMIT 1"
    ).fetchone()
    p = json.loads(payload)
    mutate(p)
    con.execute("UPDATE audit_log SET payload=? WHERE seq=?", (canonical_json(p), seq))
    prev = GENESIS
    for s, pl in con.execute(
        "SELECT seq, payload FROM audit_log ORDER BY seq ASC"
    ).fetchall():
        ch = content_hash(json.loads(pl))
        con.execute(
            "UPDATE audit_log SET content_hash=?, prev_hash=?, chain_hash=? WHERE seq=?",
            (ch, prev, _chain(prev, ch), s),
        )
        prev = _chain(prev, ch)
    con.commit()
    con.close()


def test_cli_has_mcp_subcommand_with_launch_bound_agent_id():
    args = build_parser().parse_args(["mcp", "--agent-id", "agent-1"])
    assert args.command == "mcp"
    assert args.agent_id == "agent-1"


def test_build_runtime_wires_env_configured_openrouter_judge(tmp_path, monkeypatch):
    from legis.enforcement.llm_client import OpenRouterLLMClient
    from legis.mcp import build_runtime

    def fake_init(self, config, *, fetch=None):
        self.model_id = "openrouter:test-model"

    monkeypatch.setenv("LEGIS_HMAC_KEY", "secret")
    monkeypatch.setenv("LEGIS_JUDGE_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-key")
    monkeypatch.setenv("LEGIS_GOVERNANCE_DB", f"sqlite:///{tmp_path / 'gov-env.db'}")
    monkeypatch.setattr(OpenRouterLLMClient, "__init__", fake_init)
    monkeypatch.setattr(OpenRouterLLMClient, "complete", lambda self, prompt: "ACCEPTED\nok")

    runtime = build_runtime("agent-launch")

    assert runtime.protected_gate is not None
    result = runtime.protected_gate.submit(
        policy="no-eval",
        entity_key=EntityKey.from_locator("src/x.py:f"),
        rationale="specific rationale",
        agent_id="agent-launch",
        file_fingerprint="fp",
        ast_path="ap",
    )
    assert result.judge_model == "openrouter:test-model"


def test_initialize_and_tools_list_exposes_full_agent_surface(tmp_path):
    runtime, _store = _runtime(tmp_path)
    runtime.initialized = False
    responses = _run(
        _messages(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ),
        runtime,
    )

    assert responses[0]["result"]["serverInfo"]["name"] == "legis"
    assert responses[0]["result"]["protocolVersion"] == "2025-03-26"
    tools = responses[1]["result"]["tools"]
    by_name = {tool["name"]: tool for tool in tools}

    assert set(by_name) == {
        "policy_explain",
        "override_submit",
        "signoff_status_get",
        "policy_evaluate",
        "scan_route",
        "git_branch_list",
        "git_commit_get",
        "git_rename_list",
        "git_rename_feed_get",
        "pull_request_get",
        "check_list",
        "override_rate_get",
        "filigree_closure_gate_get",
    }
    assert "signoff_sign" not in by_name
    assert "protected_operator_override" not in by_name
    assert "operator_override" not in by_name
    assert "submit_override" not in by_name
    assert "protected_override" not in by_name
    assert "signoff_request" not in by_name
    for tool in tools:
        assert not tool["name"].startswith("legis_")
        props = tool["inputSchema"].get("properties", {})
        assert "agent_id" not in props
        assert "operator_id" not in props

    submit_description = by_name["override_submit"]["description"]
    assert "routes to the governing cell" in submit_description


def test_tools_reject_before_initialize(tmp_path):
    runtime, _store = _runtime(tmp_path)
    runtime.initialized = False

    responses = _run(
        _messages({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}),
        runtime,
    )

    assert responses[0]["error"]["code"] == -32002


def test_initialize_negotiates_unsupported_protocol_version(tmp_path):
    # MCP spec: an unsupported (or newer) requested version must not hard-error;
    # the server replies with a version it does support and lets the client
    # decide. This is what lets newer clients (e.g. 2025-06-18) connect.
    runtime, _store = _runtime(tmp_path)
    runtime.initialized = False

    responses = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            }
        ),
        runtime,
    )

    assert "error" not in responses[0]
    assert responses[0]["result"]["protocolVersion"] == "2025-03-26"
    assert responses[0]["result"]["serverInfo"]["name"] == "legis"


def test_build_runtime_initialize_does_not_create_local_state(tmp_path, monkeypatch):
    from legis.mcp import build_runtime

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LEGIS_HMAC_KEY", raising=False)
    monkeypatch.delenv("LEGIS_GOVERNANCE_DB", raising=False)
    monkeypatch.delenv("LEGIS_CHECK_DB", raising=False)
    monkeypatch.delenv("LEGIS_PULL_DB", raising=False)
    runtime = build_runtime("agent-1")

    responses = _run(
        _messages({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        runtime,
    )

    assert responses[0]["result"]["serverInfo"]["name"] == "legis"
    # The federated store subtree must not be created on the initialize path —
    # stores are opened lazily, so neither the .weft/legis dir nor any DB appears.
    assert not (tmp_path / ".weft").exists()
    assert not (tmp_path / ".weft" / "legis" / "legis-governance.db").exists()
    assert not (tmp_path / ".weft" / "legis" / "legis-checks.db").exists()
    assert not (tmp_path / ".weft" / "legis" / "legis-pulls.db").exists()


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
        "available_moves": ["override_submit", "signoff_status_get"],
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


def test_override_submit_idempotency_key_prevents_duplicate_records(tmp_path):
    runtime, store = _runtime(tmp_path, agent_id="agent-launch")
    runtime.cell_registry = PolicyCellRegistry(default_cell="chill")
    call = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "override_submit",
            "arguments": {
                "policy": "ordinary.policy",
                "entity": "src/x.py:f",
                "rationale": "generated file; lint is not applicable",
                "idempotency_key": "retry-1",
            },
        },
    }

    responses = _run(
        _messages({**call, "id": 1}, {**call, "id": 2}),
        runtime,
    )

    assert responses[0]["result"]["structuredContent"]["seq"] == 1
    assert responses[1]["result"]["structuredContent"]["seq"] == 1
    assert len(store.read_all()) == 1
    assert store.read_all()[0].payload["extensions"]["mcp_idempotency_key"] == "retry-1"


def test_override_submit_idempotency_key_is_scoped_to_exact_request(tmp_path):
    runtime, store = _runtime(tmp_path, agent_id="agent-launch")
    runtime.cell_registry = PolicyCellRegistry(
        default_cell="chill",
        rules=(
            PolicyCellRule("protected.policy", "protected"),
            PolicyCellRule("release.signoff", "structured"),
        ),
    )
    runtime.protected_gate = ProtectedGate(
        store,
        FixedClock("2026-06-02T12:00:00+00:00"),
        _ScriptedJudge(JudgeOpinion(Verdict.ACCEPTED, "judge@protected", "ok")),
        b"secret",
    )
    runtime.signoff_gate = SignoffGate(
        store, FixedClock("2026-06-02T12:00:00+00:00")
    )

    first = _run(
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
                        "idempotency_key": "retry-1",
                    },
                },
            }
        ),
        runtime,
    )[0]["result"]["structuredContent"]

    assert first["outcome"] == "ACCEPTED_SELF"
    recorded_ext = store.read_all()[0].payload["extensions"]
    assert recorded_ext["mcp_idempotency_key"] == "retry-1"
    assert "mcp_idempotency_request_hash" in recorded_ext

    replay = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "override_submit",
                    "arguments": {
                        "policy": "ordinary.policy",
                        "entity": "src/x.py:f",
                        "rationale": "generated file; lint is not applicable",
                        "idempotency_key": "retry-1",
                    },
                },
            }
        ),
        runtime,
    )[0]["result"]["structuredContent"]

    assert replay["seq"] == first["seq"]
    assert len(store.read_all()) == 1

    protected_reuse = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "override_submit",
                    "arguments": {
                        "policy": "protected.policy",
                        "entity": "src/secret.py:danger",
                        "rationale": "needs exception",
                        "idempotency_key": "retry-1",
                    },
                },
            }
        ),
        runtime,
    )[0]["result"]

    assert protected_reuse["isError"] is True
    assert protected_reuse["structuredContent"]["error_code"] == "INVALID_ARGUMENT"
    assert "different override request" in protected_reuse["structuredContent"]["message"]

    structured_reuse = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "override_submit",
                    "arguments": {
                        "policy": "release.signoff",
                        "entity": "svc/api",
                        "rationale": "production deploy",
                        "idempotency_key": "retry-1",
                    },
                },
            }
        ),
        runtime,
    )[0]["result"]

    assert structured_reuse["isError"] is True
    assert structured_reuse["structuredContent"]["error_code"] == "INVALID_ARGUMENT"
    assert len(store.read_all()) == 1


def test_tools_call_rejects_unexpected_arguments(tmp_path):
    runtime, store = _runtime(tmp_path, agent_id="agent-launch")
    runtime.cell_registry = PolicyCellRegistry(default_cell="chill")

    result = _run(
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
    )[0]["result"]

    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == "INVALID_ARGUMENT"
    assert "unexpected" in result["structuredContent"]["message"]
    assert store.read_all() == []


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


def test_override_submit_coached_accepts_and_blocks_with_reason_code(tmp_path):
    runtime, store = _runtime(
        tmp_path,
        judge=_ScriptedJudge(
            JudgeOpinion(Verdict.ACCEPTED, "judge@1", "ship it"),
            JudgeOpinion(
                Verdict.BLOCKED,
                "judge@1",
                "rationale insufficient for this exception",
            ),
        ),
    )
    runtime.cell_registry = PolicyCellRegistry(default_cell="coached")

    responses = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "override_submit",
                    "arguments": {
                        "policy": "reviewed.policy",
                        "entity": "src/x.py:f",
                        "rationale": "first accepted path",
                    },
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "override_submit",
                    "arguments": {
                        "policy": "reviewed.policy",
                        "entity": "src/x.py:g",
                        "rationale": "trust me",
                    },
                },
            },
        ),
        runtime,
    )

    accepted = responses[0]["result"]["structuredContent"]
    assert accepted == {
        "outcome": "ACCEPTED_BY_JUDGE",
        "cell": "coached",
        "seq": 1,
        "judge_model": "judge@1",
        "judge_rationale": "ship it",
        "note": "may be re-judged later",
    }

    blocked = responses[1]["result"]["structuredContent"]
    assert blocked == {
        "outcome": "BLOCKED",
        "cell": "coached",
        "seq": 2,
        "judge_model": "judge@1",
        "judge_rationale": "rationale insufficient for this exception",
        "blocked_reason_code": "RATIONALE_INSUFFICIENT",
        "self_clearable": False,
        "next_actions": ["REVISE_CODE", "REVISE_RATIONALE"],
        "note": "this attempt does not count toward your override-rate",
    }
    assert len(store.read_all()) == 2


def test_override_submit_structured_escalates_and_status_poll_reflects_signoff(tmp_path):
    runtime, store = _runtime(tmp_path, agent_id="agent-structured")
    runtime.cell_registry = PolicyCellRegistry(default_cell="structured")
    runtime.signoff_gate = SignoffGate(
        store, FixedClock("2026-06-02T12:00:00+00:00")
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
                        "policy": "release.signoff",
                        "entity": "svc/api",
                        "rationale": "production deploy",
                    },
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "signoff_status_get",
                    "arguments": {"seq": "1"},
                },
            },
        ),
        runtime,
    )

    assert responses[0]["result"]["structuredContent"] == {
        "outcome": "ESCALATED_PENDING",
        "cell": "structured",
        "seq": 1,
        "cleared": False,
        "human_required": True,
        "operator_instruction": "Human sign-off required for seq 1.",
        "poll_tool": "signoff_status_get",
        "poll_handle": 1,
    }
    assert responses[1]["result"]["structuredContent"] == {"cleared": False, "seq": 1}
    assert store.read_all()[0].payload["agent_id"] == "agent-structured"

    poll_handle = responses[0]["result"]["structuredContent"]["poll_handle"]
    poll_with_handle = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 20,
                "method": "tools/call",
                "params": {
                    "name": "signoff_status_get",
                    "arguments": {"seq": poll_handle},
                },
            }
        ),
        runtime,
    )[0]["result"]["structuredContent"]
    assert poll_with_handle == {"cleared": False, "seq": 1}

    runtime.signoff_gate.sign_off(
        request_seq=1, operator_id="op-release", rationale="approved"
    )
    signed = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "signoff_status_get",
                    "arguments": {"seq": "1"},
                },
            }
        ),
        runtime,
    )[0]["result"]["structuredContent"]
    assert signed == {
        "cleared": True,
        "seq": 1,
        "signed_by": "op-release",
        "signed_at": "2026-06-02T12:00:00+00:00",
    }


def test_override_submit_protected_needs_inputs_without_write_then_blocks(tmp_path):
    runtime, store = _runtime(tmp_path)
    runtime.cell_registry = PolicyCellRegistry(default_cell="protected")
    runtime.protected_gate = ProtectedGate(
        store,
        FixedClock("2026-06-02T12:00:00+00:00"),
        _ScriptedJudge(
            JudgeOpinion(Verdict.BLOCKED, "judge@protected", "code violation: eval")
        ),
        b"secret",
    )
    source_file = tmp_path / "src" / "x.py"
    source_file.parent.mkdir()
    source_file.write_text("print('hello')\n", encoding="utf-8")
    runtime.source_root = tmp_path

    missing_inputs = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "override_submit",
                    "arguments": {
                        "policy": "protected.policy",
                        "entity": "src/x.py:f",
                        "rationale": "needs exception",
                    },
                },
            }
        ),
        runtime,
    )[0]["result"]["structuredContent"]

    assert missing_inputs == {
        "outcome": "NEED_INPUTS",
        "cell": "protected",
        "required_inputs": [
            {
                "field": "file_fingerprint",
                "how": "sha256 of the target file contents",
            },
            {"field": "ast_path", "how": "dotted path to the AST node"},
        ],
    }
    assert store.read_all() == []

    blocked = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "override_submit",
                    "arguments": {
                        "policy": "protected.policy",
                        "entity": "src/x.py:f",
                        "rationale": "needs exception",
                        "file_fingerprint": "sha256:03e693d9f2f687e0f40e36a8df7fcb4d1c22974012b7c2a55c000eb30f305824",
                        "ast_path": "Module/Expr",
                    },
                },
            }
        ),
        runtime,
    )[0]["result"]["structuredContent"]
    assert blocked["outcome"] == "BLOCKED"
    assert blocked["cell"] == "protected"
    assert blocked["seq"] == 1
    assert blocked["judge_model"] == "judge@protected"
    assert blocked["blocked_reason_code"] == "CODE_VIOLATION"
    assert len(store.read_all()) == 1


def test_policy_evaluate_returns_unknown_distinct_from_clear(tmp_path):
    runtime, _store = _runtime(tmp_path)
    grammar = PolicyGrammar()
    grammar.register(AllowlistBoundary("imports", frozenset({"json"})))
    runtime.grammar = grammar

    responses = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "policy_evaluate",
                    "arguments": {
                        "policy": "imports",
                        "target": {"value": "socket"},
                    },
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "policy_evaluate",
                    "arguments": {
                        "policy": "missing",
                        "target": {},
                    },
                },
            },
        ),
        runtime,
    )

    assert responses[0]["result"]["structuredContent"]["outcome"] == "VIOLATION"
    assert responses[0]["result"]["structuredContent"]["provenance_gap"] is False
    assert responses[1]["result"]["structuredContent"]["outcome"] == "UNKNOWN"
    assert responses[1]["result"]["structuredContent"]["provenance_gap"] is True


def test_scan_route_requires_exactly_one_cell_spec_and_routes_findings(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGIS_UNSAFE_WARDLINE_REQUEST_ROUTING", "1")
    runtime, store = _runtime(tmp_path)
    scan = _active_scan()

    invalid = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "scan_route",
                    "arguments": {
                        "scan": scan,
                        "cell": "surface_override",
                        "severity_map": {"ERROR": "surface_override"},
                    },
                },
            }
        ),
        runtime,
    )[0]["result"]
    assert invalid["isError"] is True
    assert invalid["structuredContent"]["error_code"] == "INVALID_CELL_SPEC"
    assert store.read_all() == []

    routed = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "scan_route",
                    "arguments": {"scan": scan, "cell": "surface_override"},
                },
            }
        ),
        runtime,
    )[0]["result"]["structuredContent"]
    assert routed == {
        "outcome": "ROUTED",
        "routed": [
            {
                "mode": "surface_override",
                "fingerprint": "fp1",
                "seq": 1,
                "accepted": True,
            }
        ],
    }


def test_scan_route_rejects_empty_severity_map(tmp_path, monkeypatch):
    # Drift fix: the HTTP adapter already rejected an empty cell_by_severity, but
    # MCP silently accepted an empty severity_map (routed nothing). Both transports
    # now reject it up front via the shared resolver — no silent governance skip.
    monkeypatch.setenv("LEGIS_UNSAFE_WARDLINE_REQUEST_ROUTING", "1")
    runtime, store = _runtime(tmp_path)
    result = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "scan_route",
                    "arguments": {"scan": _active_scan(), "severity_map": {}},
                },
            }
        ),
        runtime,
    )[0]["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == "INVALID_CELL_SPEC"
    assert store.read_all() == []


def test_scan_route_rejects_request_routing_when_server_owned(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGIS_WARDLINE_CELL", "surface_only")
    runtime, store = _runtime(tmp_path)
    scan = _active_scan()

    result = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "scan_route",
                    "arguments": {"scan": scan, "cell": "surface_override"},
                },
            }
        ),
        runtime,
    )[0]["result"]

    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == "INVALID_CELL_SPEC"
    assert "server-owned" in result["structuredContent"]["message"]
    assert store.read_all() == []


def test_scan_route_defaults_to_server_owned_routing(tmp_path, monkeypatch):
    monkeypatch.delenv("LEGIS_UNSAFE_WARDLINE_REQUEST_ROUTING", raising=False)
    runtime, store = _runtime(tmp_path)
    scan = _active_scan()

    result = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "scan_route",
                    "arguments": {"scan": scan, "cell": "surface_only"},
                },
            }
        ),
        runtime,
    )[0]["result"]

    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == "INVALID_CELL_SPEC"
    assert "server-owned" in result["structuredContent"]["message"]
    assert store.read_all() == []


def test_scan_route_uses_server_owned_cell(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGIS_WARDLINE_CELL", "surface_only")
    runtime, store = _runtime(tmp_path)

    routed = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "scan_route", "arguments": {"scan": _active_scan()}},
            }
        ),
        runtime,
    )[0]["result"]["structuredContent"]

    assert routed["routed"][0]["mode"] == "surface_only"
    assert store.read_all()[0].payload["kind"] == "wardline_surfaced"


def test_scan_route_requires_signed_artifact_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGIS_WARDLINE_ARTIFACT_KEY", "wardline-key")
    monkeypatch.setenv("LEGIS_WARDLINE_CELL", "surface_only")
    runtime, store = _runtime(tmp_path)
    scan = {
        "scanner_identity": "wardline@1",
        "rule_set_version": "rules@abc123",
        "commit_sha": "a" * 40,
        "tree_sha": "b" * 40,
        **_active_scan(),
    }

    result = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "scan_route", "arguments": {"scan": scan}},
            }
        ),
        runtime,
    )[0]["result"]

    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == "INVALID_ARGUMENT"
    assert "artifact signature" in result["structuredContent"]["message"]
    assert store.read_all() == []


def test_scan_route_records_verified_artifact_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGIS_WARDLINE_ARTIFACT_KEY", "wardline-key")
    monkeypatch.setenv("LEGIS_WARDLINE_CELL", "surface_only")
    runtime, store = _runtime(tmp_path)
    scan = _signed_wardline_scan(
        {
            "scanner_identity": "wardline@1",
            "rule_set_version": "rules@abc123",
            "commit_sha": "a" * 40,
            "tree_sha": "b" * 40,
            **_active_scan(),
        }
    )

    result = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "scan_route", "arguments": {"scan": scan}},
            }
        ),
        runtime,
    )[0]["result"]["structuredContent"]

    assert result["routed"][0]["mode"] == "surface_only"
    wardline = store.read_all()[0].payload["extensions"]["wardline"]
    assert wardline["artifact_status"] == "verified"
    assert wardline["scanner_identity"] == "wardline@1"
    assert wardline["artifact_signature"].startswith("hmac-sha256:v2:")


def _dirty_scan():
    return {
        "scanner_identity": "wardline@1.0.0rc1",
        "rule_set_version": "rules@abc123",
        "commit_sha": "a" * 40,
        "tree_sha": "b" * 40,
        "dirty": True,
        **_active_scan(),
    }


def test_scan_route_dirty_tree_is_amber_skip_not_red(tmp_path, monkeypatch):
    # P1: a dirty dev artifact in the CI posture (key configured) is a typed
    # amber SKIPPED_DIRTY_TREE outcome, NOT the generic INVALID_ARGUMENT red,
    # and nothing is governed.
    monkeypatch.setenv("LEGIS_WARDLINE_ARTIFACT_KEY", "wardline-key")
    monkeypatch.setenv("LEGIS_WARDLINE_CELL", "surface_only")
    monkeypatch.delenv("LEGIS_WARDLINE_ALLOW_DIRTY", raising=False)
    runtime, store = _runtime(tmp_path)

    result = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "scan_route", "arguments": {"scan": _dirty_scan()}},
            }
        ),
        runtime,
    )[0]["result"]

    assert result.get("isError") is not True
    structured = result["structuredContent"]
    assert structured["outcome"] == "SKIPPED_DIRTY_TREE"
    assert structured["routed"] == []
    assert store.read_all() == []


def test_scan_route_dirty_tree_governs_under_devmode_optin(tmp_path, monkeypatch):
    # P0: the explicit server-side dev-mode opt-in governs the unsigned dirty
    # artifact, recorded honestly as artifact_status="dirty".
    monkeypatch.setenv("LEGIS_WARDLINE_ARTIFACT_KEY", "wardline-key")
    monkeypatch.setenv("LEGIS_WARDLINE_CELL", "surface_only")
    monkeypatch.setenv("LEGIS_WARDLINE_ALLOW_DIRTY", "1")
    runtime, store = _runtime(tmp_path)

    result = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "scan_route", "arguments": {"scan": _dirty_scan()}},
            }
        ),
        runtime,
    )[0]["result"]["structuredContent"]

    assert result["outcome"] == "ROUTED"
    assert result["routed"][0]["mode"] == "surface_only"
    wardline = store.read_all()[0].payload["extensions"]["wardline"]
    assert wardline["artifact_status"] == "dirty"
    assert "artifact_signature" not in wardline


def test_scan_route_malformed_finding_is_invalid_argument_red(tmp_path, monkeypatch):
    # The other half of the dirty-vs-malformed contract (cf. the amber test
    # above): a malformed finding — here an unknown severity — is a generic red
    # INVALID_ARGUMENT, NOT the amber SKIPPED_DIRTY_TREE. WardlinePayloadError is
    # deliberately not a WardlineDirtyTreeError, so the boundary keeps "broken or
    # tampered scan" distinct from "commit first". Nothing is governed.
    monkeypatch.setenv("LEGIS_WARDLINE_CELL", "surface_only")
    runtime, store = _runtime(tmp_path)
    malformed = {
        "findings": [
            {
                "rule_id": "PY-WL-101",
                "message": "untrusted reaches trusted",
                "severity": "NOT_A_SEVERITY",
                "kind": "defect",
                "fingerprint": "fp1",
            }
        ]
    }

    result = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "scan_route", "arguments": {"scan": malformed}},
            }
        ),
        runtime,
    )[0]["result"]

    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == "INVALID_ARGUMENT"
    assert store.read_all() == []


def test_scan_route_fail_on_threshold_routes_each_finding(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGIS_UNSAFE_WARDLINE_REQUEST_ROUTING", "1")
    runtime, _store = _runtime(tmp_path)
    scan = {
        "findings": [
            {
                "rule_id": "PY-WL-E",
                "message": "error finding",
                "severity": "ERROR",
                "kind": "defect",
                "fingerprint": "fp-error",
                "qualname": "m.error",
                "properties": {},
                "suppressed": "active",
            },
            {
                "rule_id": "PY-WL-W",
                "message": "warn finding",
                "severity": "WARN",
                "kind": "defect",
                "fingerprint": "fp-warn",
                "qualname": "m.warn",
                "properties": {},
                "suppressed": "active",
            },
        ]
    }

    routed = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "scan_route",
                        "arguments": {
                            "scan": scan,
                            "cell": "surface_override",
                            "fail_on": "ERROR",
                        },
                },
            }
        ),
        runtime,
    )[0]["result"]["structuredContent"]["routed"]

    assert {item["fingerprint"]: item["mode"] for item in routed} == {
        "fp-error": "surface_override",
        "fp-warn": "surface_only",
    }


def test_override_rate_get_fails_closed_on_rechained_protected_tamper(tmp_path):
    db = tmp_path / "gov.db"
    store = AuditStore(f"sqlite:///{db}")
    gate = ProtectedGate(
        store,
        FixedClock("2026-06-02T12:00:00+00:00"),
        judge=_ScriptedJudge(JudgeOpinion(Verdict.ACCEPTED, "judge@1", "ok")),
        key=KEY,
    )
    gate.submit(
        policy="no-eval",
        entity_key=EntityKey.from_locator("src/x.py:f"),
        rationale="original",
        agent_id="agent-launch",
        file_fingerprint="fp",
        ast_path="ap",
    )
    _tamper_first_record_and_rechain(db, lambda p: p.update({"rationale": "FORGED"}))
    assert store.verify_integrity() is True

    runtime, _unused = _runtime(tmp_path)
    runtime.engine = None
    runtime.protected_gate = gate
    runtime.trail_verifier = TrailVerifier(KEY, frozenset({"no-eval"}))

    result = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "override_rate_get", "arguments": {}},
            }
        ),
        runtime,
    )[0]["result"]

    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == "AUDIT_INTEGRITY_FAILURE"


def test_read_tools_return_git_pull_checks_and_override_rate(tmp_path, git_repo):
    checks = CheckSurface(f"sqlite:///{tmp_path / 'checks.db'}")
    checks.record(
        CheckRun(
            check_name="unit",
            run_id="run-1",
            commit_sha="abc123",
            outcome=CheckOutcome.PASS,
            pr=7,
            ran_against="abc123",
        )
    )
    pulls = PullSurface(f"sqlite:///{tmp_path / 'pulls.db'}")
    pulls.record(
        PullRequest(
            number=7,
            title="Feature",
            base="main",
            head="feature",
            state=PullRequestState.OPEN,
            url="https://example.test/pr/7",
        )
    )
    runtime, _store = _runtime(tmp_path, check_surface=checks)
    runtime.git_surface = GitSurface(git_repo)
    runtime.pull_surface = pulls
    runtime.engine.submit_override(
        policy="ordinary.policy",
        entity_key=EntityKey.from_locator("x"),
        rationale="r",
        agent_id="agent-launch",
    )

    head = GitSurface(git_repo).commits(limit=1)[0].sha
    responses = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "git_branch_list", "arguments": {}},
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "git_commit_get",
                    "arguments": {"sha": head},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "git_rename_list",
                    "arguments": {"rev_range": "HEAD~1..HEAD"},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "pull_request_get",
                    "arguments": {"number": "7"},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "override_rate_get", "arguments": {}},
            },
        ),
        runtime,
    )

    assert {b["name"] for b in responses[0]["result"]["structuredContent"]["branches"]} == {
        "main",
        "feature",
    }
    assert responses[1]["result"]["structuredContent"]["commit"]["sha"] == head
    assert responses[2]["result"]["structuredContent"]["renames"][0]["old_path"] == "a.txt"
    pr = responses[3]["result"]["structuredContent"]
    assert pr["number"] == 7
    assert pr["checks"][0]["check_name"] == "unit"
    rate = responses[4]["result"]["structuredContent"]
    assert rate["sample_size"] == 0
    assert rate["note"] == "measures operator force-pasts; not movable by agent retries"


def test_pull_request_get_returns_checks_on_a_fresh_runtime(tmp_path, monkeypatch):
    # Regression: build_runtime yields check_surface=None, and the first tool
    # call an agent makes may be pull_request_get (no prior check_list to lazily
    # initialise the surface). The result must NOT be call-order-dependent — a PR
    # with recorded checks must report them, or a governance agent is told a PR is
    # clean when checks exist and may be failing.
    checks = CheckSurface(f"sqlite:///{tmp_path / 'checks.db'}")
    checks.record(
        CheckRun(
            check_name="unit",
            run_id="run-1",
            commit_sha="abc123",
            outcome=CheckOutcome.FAIL,
            pr=7,
            ran_against="abc123",
        )
    )
    # The lazy _checks() builder resolves the DB from LEGIS_CHECK_DB, exactly as a
    # deployed server does — so the surface is uninitialised but reachable.
    monkeypatch.setenv("LEGIS_CHECK_DB", f"sqlite:///{tmp_path / 'checks.db'}")
    pulls = PullSurface(f"sqlite:///{tmp_path / 'pulls.db'}")
    pulls.record(
        PullRequest(
            number=7,
            title="Feature",
            base="main",
            head="feature",
            state=PullRequestState.OPEN,
            url="https://example.test/pr/7",
        )
    )
    # Fresh runtime: check_surface left at its build_runtime default (None).
    runtime, _store = _runtime(tmp_path, check_surface=None)
    runtime.pull_surface = pulls

    responses = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "pull_request_get",
                    "arguments": {"number": "7"},
                },
            },
        ),
        runtime,
    )

    pr = responses[0]["result"]["structuredContent"]
    assert pr["number"] == 7
    assert pr["checks"][0]["check_name"] == "unit"
    assert pr["checks"][0]["outcome"] == "fail"


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
    assert result["structuredContent"]["recoverable"] is True
    assert "retry" in result["structuredContent"]["next_action"].lower()


def test_non_wp_m3_tool_names_are_not_callable(tmp_path):
    runtime, store = _runtime(tmp_path)

    for non_m3_name in (
        "submit_override",
        "protected_override",
        "signoff_request",
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


def test_tool_registries_are_in_sync():
    # mcp.py hand-maintains three parallel name registries: the public schema
    # (tool_definitions), the dispatch table (_TOOL_HANDLERS), and the agent-
    # exposed set (_AGENT_TOOLS). They MUST agree. A handler without a schema
    # entry is reachable-but-unvalidated (it accepts arbitrary arg keys); a
    # schema entry without a handler advertises a tool that errors UNKNOWN_TOOL.
    # The table-driven dispatch makes exactly this drift easy to introduce, so
    # pin it directly rather than inferring it from per-tool listing tests.
    from legis.mcp import _AGENT_TOOLS, _TOOL_HANDLERS, tool_definitions

    defined = {t["name"] for t in tool_definitions()}
    assert defined == set(_TOOL_HANDLERS) == set(_AGENT_TOOLS)


def test_git_rename_feed_get_is_listed():
    from legis.mcp import tool_definitions

    names = {t["name"] for t in tool_definitions()}
    assert "git_rename_feed_get" in names


def test_git_rename_feed_get_returns_committed_renames(git_repo, monkeypatch):
    from legis.mcp import build_runtime, call_tool

    monkeypatch.setenv("LEGIS_SOURCE_ROOT", str(git_repo))
    runtime = build_runtime("agent-1")

    result = call_tool(runtime, "git_rename_feed_get", {"base": "HEAD~1", "head": "HEAD"})

    assert result["structuredContent"]["committed"][0]["new_path"] == "renamed.txt"
    assert result["structuredContent"]["status"] == "committed_only"


def test_filigree_closure_gate_get_is_listed():
    from legis.mcp import tool_definitions

    names = {t["name"] for t in tool_definitions()}
    assert "filigree_closure_gate_get" in names


def test_filigree_closure_gate_get_not_enabled_without_ledger(monkeypatch):
    from legis.mcp import build_runtime, call_tool

    monkeypatch.delenv("LEGIS_HMAC_KEY", raising=False)
    runtime = build_runtime("agent-1")

    result = call_tool(runtime, "filigree_closure_gate_get", {"issue_id": "ISSUE-7"})

    # NotEnabledError is mapped to an error envelope, not raised.
    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == "CELL_NOT_ENABLED"
    # Le1 (weft-f506e5f845): the recovery hint must name the concrete
    # enablement path, not a vague "ask the operator". Every governance cell
    # is wired behind LEGIS_HMAC_KEY in build_runtime.
    next_action = result["structuredContent"]["next_action"]
    assert "LEGIS_HMAC_KEY" in next_action


def test_filigree_closure_gate_get_surfaces_integrity_failure(monkeypatch, tmp_path):
    # A tampered binding ledger must surface AUDIT_INTEGRITY_FAILURE via MCP,
    # mirroring the HTTP 500 path — not a generic INTERNAL_ERROR.
    from legis.governance.binding_ledger import BindingError
    from legis.mcp import McpRuntime, call_tool

    class _TamperedLedger:
        def get_by_issue_id(self, issue_id):
            raise BindingError("hash chain integrity check failed")

    runtime = McpRuntime(agent_id="agent-1", binding_ledger=_TamperedLedger())
    result = call_tool(runtime, "filigree_closure_gate_get", {"issue_id": "ISSUE-7"})

    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == "AUDIT_INTEGRITY_FAILURE"


# --- roadmap 14: stdin JSON-RPC line-size bound ---

def test_run_jsonrpc_rejects_oversized_line_and_stays_framed(tmp_path, monkeypatch):
    # A single line over the bound is rejected with -32700 and does not consume
    # the following request — framing realigns at the next newline.
    monkeypatch.setenv("LEGIS_MCP_MAX_REQUEST_BYTES", "400")
    runtime, _store = _runtime(tmp_path)
    runtime.initialized = False
    oversized = {
        "jsonrpc": "2.0", "id": 99, "method": "tools/list",
        "params": {"pad": "A" * 2000},
    }
    responses = _run(
        _messages(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-03-26"}},
            oversized,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ),
        runtime,
    )

    assert responses[0]["id"] == 1 and "result" in responses[0]
    assert responses[1]["id"] is None
    assert responses[1]["error"]["code"] == -32700
    assert "maximum size" in responses[1]["error"]["message"]
    # The request AFTER the oversized line is still parsed and answered.
    assert responses[2]["id"] == 2 and "result" in responses[2]


def test_max_request_bytes_env_override_and_fallback(monkeypatch, caplog):
    from legis.mcp import _DEFAULT_MAX_REQUEST_BYTES, _max_request_bytes

    monkeypatch.delenv("LEGIS_MCP_MAX_REQUEST_BYTES", raising=False)
    assert _max_request_bytes() == _DEFAULT_MAX_REQUEST_BYTES
    monkeypatch.setenv("LEGIS_MCP_MAX_REQUEST_BYTES", "4096")
    assert _max_request_bytes() == 4096
    # Both the unparseable and the non-positive fat-finger fall back, but neither
    # may do so silently — an operator lowering the bound must see why it was
    # ignored.
    for bad in ("not-an-int", "0", "-5"):
        caplog.clear()
        monkeypatch.setenv("LEGIS_MCP_MAX_REQUEST_BYTES", bad)
        with caplog.at_level(logging.WARNING, logger="legis.mcp"):
            assert _max_request_bytes() == _DEFAULT_MAX_REQUEST_BYTES
        assert "LEGIS_MCP_MAX_REQUEST_BYTES" in caplog.text


def test_read_bounded_line_enforces_bytes_not_chars():
    # The bound is named in BYTES; readline() counts characters. A record that
    # fits the char count but whose UTF-8 encoding exceeds the cap (multibyte
    # content) must still overflow — otherwise the byte limit could be exceeded
    # ~4×. The record AFTER it must stay framed.
    from legis.mcp import _read_bounded_line

    multibyte = "中" * 200  # 200 chars, 600 UTF-8 bytes — under 400 chars, over 400 bytes
    stream = io.StringIO(f"{multibyte}\n" + '{"next":true}\n')

    line, overflow = _read_bounded_line(stream, 400)
    assert overflow is True
    assert line.startswith("中")

    nxt, nxt_overflow = _read_bounded_line(stream, 400)
    assert nxt_overflow is False
    assert nxt == '{"next":true}\n'


def test_read_bounded_line_at_byte_boundary():
    # The bound counts the trailing newline (fail-safe off-by-one): a 399-byte
    # data record + "\n" == 400 bytes passes; one more byte overflows.
    from legis.mcp import _read_bounded_line

    ok_line, ok_overflow = _read_bounded_line(io.StringIO("x" * 399 + "\n"), 400)
    assert ok_overflow is False
    assert ok_line == "x" * 399 + "\n"

    _, over_overflow = _read_bounded_line(io.StringIO("x" * 400 + "\n"), 400)
    assert over_overflow is True


def test_read_bounded_line_drains_oversized_multibyte_record():
    # A record longer than the *character* cap forces the drain loop (first
    # branch) — exercise it with multibyte content and assert the next record
    # stays framed (the existing multibyte test stays under the char cap and
    # hits the second branch instead).
    from legis.mcp import _read_bounded_line

    stream = io.StringIO("中" * 20 + "\n" + "{}\n")  # 20 chars > 10-char cap
    line, overflow = _read_bounded_line(stream, 10)
    assert overflow is True
    assert line.startswith("中")

    nxt, nxt_overflow = _read_bounded_line(stream, 10)
    assert nxt == "{}\n"
    assert nxt_overflow is False


def test_service_error_logs_unexpected_internal_error(caplog):
    # An unexpected exception is surfaced to the caller as INTERNAL_ERROR; it must
    # also be logged server-side (with the exception) so the operator/Sentry sees
    # what the agent caller's payload alone would hide.
    from legis.mcp import _service_error

    with caplog.at_level(logging.ERROR, logger="legis.mcp"):
        result = _service_error(RuntimeError("kaboom"))

    assert result["structuredContent"]["error_code"] == "INTERNAL_ERROR"
    assert any(r.levelno == logging.ERROR and r.exc_info for r in caplog.records)


def test_service_error_does_not_log_expected_typed_errors(caplog):
    # Expected, typed service errors map to typed codes and must NOT spam the
    # server log — only the unexpected INTERNAL_ERROR fall-through logs.
    from legis.mcp import _service_error
    from legis.service.errors import NotFoundError

    with caplog.at_level(logging.ERROR, logger="legis.mcp"):
        result = _service_error(NotFoundError("nope"))

    assert result["structuredContent"]["error_code"] == "NOT_FOUND"
    assert not caplog.records
