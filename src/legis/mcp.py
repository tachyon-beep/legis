"""Minimal MCP-over-stdio adapter for Legis.

The adapter is deliberately stdlib-only: one JSON-RPC object per line on stdin,
one response per line on stdout. Tool calls are thin transport mappings over the
service layer and the launch-bound ``agent_id``; tool schemas never accept actor
identity from call arguments.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
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
from legis.enforcement.judge_factory import build_judge_from_env
from legis.enforcement.protected import ProtectedGate
from legis.enforcement.signoff import SignoffGate
from legis.enforcement.verdict import SignoffState
from legis.git.surface import GitError, GitSurface
from legis.policy.cells import (
    PolicyCellRegistry,
    default_policy_cells,
    load_policy_cells,
)
from legis.policy.grammar import PolicyGrammar, default_grammar
from legis.pulls.surface import PullSurface
from legis.service.errors import (
    AuditIntegrityError,
    InvalidArgumentError,
    NotEnabledError,
    NotFoundError,
    ServiceError,
)
from legis.service.explain import explain_policy
from legis.service.governance import (
    compute_override_rate,
    evaluate_policy,
    submit_override,
    submit_protected_override,
    request_signoff,
)
from legis.service.wardline import route_wardline_scan
from legis.store.audit_store import AuditStore
from legis.wardline.governor import WardlineCellPolicy
from legis.wardline.ingest import WardlineSeverity


_AGENT_TOOLS = frozenset(
    {
        "policy_explain",
        "override_submit",
        "signoff_status_get",
        "policy_evaluate",
        "scan_route",
        "git_branch_list",
        "git_commit_get",
        "git_rename_list",
        "pull_request_get",
        "check_list",
        "override_rate_get",
    }
)
_OVERRIDE_RATE_NOTE = "measures operator force-pasts; not movable by agent retries"


@dataclass
class McpRuntime:
    agent_id: str
    engine: EnforcementEngine | None = None
    identity: Any | None = None
    protected_gate: ProtectedGate | None = None
    signoff_gate: Any | None = None
    cell_registry: PolicyCellRegistry | None = None
    check_surface: CheckSurface | None = None
    git_surface: GitSurface | None = None
    pull_surface: PullSurface | None = None
    grammar: PolicyGrammar | None = None
    source_root: str | Path | None = None


def _load_policy_cell_registry() -> PolicyCellRegistry:
    configured = os.environ.get("LEGIS_POLICY_CELLS")
    if configured:
        return load_policy_cells(configured)

    root = Path(os.environ.get("LEGIS_SOURCE_ROOT") or os.getcwd())
    default_path = root / "policy" / "cells.toml"
    if default_path.exists():
        return load_policy_cells(default_path)

    return default_policy_cells()


def build_runtime(agent_id: str) -> McpRuntime:
    from legis.api.app import DEFAULT_CHECK_DB, DEFAULT_GOVERNANCE_DB

    clock = SystemClock()
    store = AuditStore(os.environ.get("LEGIS_GOVERNANCE_DB", DEFAULT_GOVERNANCE_DB))
    engine = EnforcementEngine(store, clock)
    identity = None
    clarion_url = os.environ.get("CLARION_API_URL")
    if clarion_url:
        from legis.identity.clarion_client import HttpClarionIdentity, clarion_hmac_key_from_env
        from legis.identity.resolver import IdentityResolver

        identity = IdentityResolver(
            HttpClarionIdentity(clarion_url, hmac_key=clarion_hmac_key_from_env())
        )

    protected_gate = None
    signoff_gate = None
    hmac_key = os.environ.get("LEGIS_HMAC_KEY")
    if hmac_key:
        key = hmac_key.encode("utf-8")

        protected_gate = ProtectedGate(store, clock, build_judge_from_env("MCP"), key)
        signoff_gate = SignoffGate(store, clock, signer=True, key=key)

    return McpRuntime(
        agent_id=agent_id,
        engine=engine,
        identity=identity,
        protected_gate=protected_gate,
        signoff_gate=signoff_gate,
        cell_registry=_load_policy_cell_registry(),
        check_surface=CheckSurface(
            os.environ.get("LEGIS_CHECK_DB", DEFAULT_CHECK_DB)
        ),
        git_surface=GitSurface(os.environ.get("LEGIS_SOURCE_ROOT") or os.getcwd()),
        pull_surface=PullSurface(
            os.environ.get("LEGIS_PULL_DB", "sqlite:///legis-pulls.db")
        ),
        grammar=default_grammar(),
        source_root=os.environ.get("LEGIS_SOURCE_ROOT") or os.getcwd(),
    )


def _schema(required: list[str], properties: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def tool_definitions() -> list[dict[str, Any]]:
    string = {"type": "string"}
    object_schema = {"type": "object"}
    return [
        {
            "name": "policy_explain",
            "description": (
                "Explain which governance cell controls a policy/entity pair, "
                "whether that cell is enabled on this server, and which move the "
                "agent may make next."
            ),
            "inputSchema": _schema(
                ["policy", "entity"],
                {"policy": string, "entity": string},
            ),
        },
        {
            "name": "override_submit",
            "description": (
                "Submit an override as the launch-bound agent. The server "
                "routes to the governing cell and returns a discriminated "
                "outcome envelope."
            ),
            "inputSchema": _schema(
                ["policy", "entity", "rationale"],
                {
                    "policy": string,
                    "entity": string,
                    "rationale": string,
                    "file_fingerprint": string,
                    "ast_path": string,
                },
            ),
        },
        {
            "name": "signoff_status_get",
            "description": "Poll whether a structured sign-off request has been cleared.",
            "inputSchema": _schema(["seq"], {"seq": string}),
        },
        {
            "name": "policy_evaluate",
            "description": (
                "Evaluate a policy against a target without recording an override."
            ),
            "inputSchema": _schema(
                ["policy", "target"], {"policy": string, "target": object_schema}
            ),
        },
        {
            "name": "scan_route",
            "description": (
                "Route Wardline scan findings through one cell, a severity_map "
                "policy, or a cell plus fail_on threshold."
            ),
            "inputSchema": _schema(
                ["scan"],
                {
                    "scan": object_schema,
                    "cell": string,
                    "severity_map": object_schema,
                    "fail_on": string,
                },
            ),
        },
        {
            "name": "git_branch_list",
            "description": "List local git branches and upstream divergence facts.",
            "inputSchema": _schema([], {}),
        },
        {
            "name": "git_commit_get",
            "description": "Read one git commit by SHA or safe ref.",
            "inputSchema": _schema(["sha"], {"sha": string}),
        },
        {
            "name": "git_rename_list",
            "description": "List git rename evidence for a revision range.",
            "inputSchema": _schema(["rev_range"], {"rev_range": string}),
        },
        {
            "name": "pull_request_get",
            "description": "Read recorded pull-request metadata with joined check outcomes.",
            "inputSchema": _schema(["number"], {"number": string}),
        },
        {
            "name": "check_list",
            "description": (
                "Read recorded CI/check outcomes for a commit, branch, or pull "
                "request target."
            ),
            "inputSchema": _schema(
                ["target_type", "target"],
                {"target_type": string, "target": string},
            ),
        },
        {
            "name": "override_rate_get",
            "description": "Read the fixed operator force-past override-rate gate.",
            "inputSchema": _schema([], {}),
        },
    ]


def _tool_result(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(value, sort_keys=True)}],
        "structuredContent": value,
    }


def _tool_error(code: str, message: str) -> dict[str, Any]:
    return {
        "isError": True,
        "content": [{"type": "text", "text": f"{code}: {message}"}],
        "structuredContent": {"error_code": code, "message": message},
    }


def _service_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, AuditIntegrityError):
        return _tool_error("AUDIT_INTEGRITY_FAILURE", str(exc))
    if isinstance(exc, NotEnabledError):
        return _tool_error("CELL_NOT_ENABLED", str(exc))
    if isinstance(exc, NotFoundError):
        return _tool_error("NOT_FOUND", str(exc))
    if isinstance(exc, InvalidArgumentError):
        return _tool_error("INVALID_ARGUMENT", str(exc))
    if isinstance(exc, GitError):
        return _tool_error("GIT_ERROR", str(exc))
    if isinstance(exc, ServiceError):
        return _tool_error("SERVICE_ERROR", str(exc))
    if isinstance(exc, ValueError):
        return _tool_error("INVALID_ARGUMENT", str(exc))
    return _tool_error("INTERNAL_ERROR", str(exc))


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


def _require(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"argument {key!r} must be a non-empty string")
    return value


def _optional_string(args: dict[str, Any], key: str) -> str | None:
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"argument {key!r} must be a non-empty string when provided")
    return value


def _require_int(args: dict[str, Any], key: str) -> int:
    raw = _require(args, key)
    try:
        value = int(raw)
    except ValueError as exc:
        raise InvalidArgumentError(f"argument {key!r} must be an integer") from exc
    if value < 1:
        raise InvalidArgumentError(f"argument {key!r} must be a positive integer")
    return value


def _require_object(args: dict[str, Any], key: str) -> dict[str, Any]:
    value = args.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"argument {key!r} must be an object")
    return value


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


def _explanation_payload(explanation) -> dict[str, Any]:
    payload = explanation.to_payload()
    payload["available_moves"] = [
        move for move in payload["available_moves"] if move in _AGENT_TOOLS
    ]
    return payload


def _grammar(runtime: McpRuntime) -> PolicyGrammar:
    if runtime.grammar is None:
        runtime.grammar = default_grammar()
    return runtime.grammar


def _git(runtime: McpRuntime) -> GitSurface:
    if runtime.git_surface is None:
        runtime.git_surface = GitSurface(
            os.environ.get("LEGIS_SOURCE_ROOT") or os.getcwd()
        )
    return runtime.git_surface


def _pulls(runtime: McpRuntime) -> PullSurface:
    if runtime.pull_surface is None:
        runtime.pull_surface = PullSurface(
            os.environ.get("LEGIS_PULL_DB", "sqlite:///legis-pulls.db")
        )
    return runtime.pull_surface


def _blocked_reason_code(judge_rationale: str | None) -> str:
    text = (judge_rationale or "").lower()
    if "rationale" in text or "justification" in text or "insufficient" in text:
        return "RATIONALE_INSUFFICIENT"
    if "code" in text or "violation" in text or "eval" in text or "untrusted" in text:
        return "CODE_VIOLATION"
    if "hard block" in text or "forbidden" in text or "never allow" in text:
        return "POLICY_HARD_BLOCK"
    return "UNCLASSIFIED"


def _judged_result_payload(
    *,
    cell: str,
    seq: int,
    accepted: bool,
    judge_model: str | None,
    judge_rationale: str | None,
) -> dict[str, Any]:
    if accepted:
        return {
            "outcome": "ACCEPTED_BY_JUDGE",
            "cell": cell,
            "seq": seq,
            "judge_model": judge_model,
            "judge_rationale": judge_rationale,
            "note": "may be re-judged later",
        }
    return {
        "outcome": "BLOCKED",
        "cell": cell,
        "seq": seq,
        "judge_model": judge_model,
        "judge_rationale": judge_rationale,
        "blocked_reason_code": _blocked_reason_code(judge_rationale),
        "self_clearable": False,
        "next_actions": ["REVISE_CODE", "REVISE_RATIONALE"],
        "note": "this attempt does not count toward your override-rate",
    }


def _signoff_signed_record(
    runtime: McpRuntime, request_seq: int
) -> dict[str, Any] | None:
    gate = runtime.signoff_gate
    records = gate.records() if gate is not None and hasattr(gate, "records") else []
    for rec in records:
        ext = rec.payload.get("extensions", {})
        if (
            ext.get("signoff_state") == SignoffState.SIGNED_OFF.value
            and ext.get("request_seq") == request_seq
        ):
            return rec.payload
    return None


def _verified_records(runtime: McpRuntime) -> list[Any]:
    if runtime.protected_gate is not None and hasattr(
        runtime.protected_gate, "verify_integrity"
    ):
        if not runtime.protected_gate.verify_integrity():
            raise AuditIntegrityError(
                "audit integrity failure: database hash chain verification failed"
            )
        return runtime.protected_gate.records()
    if runtime.signoff_gate is not None and runtime.engine is None:
        if (
            hasattr(runtime.signoff_gate, "verify_integrity")
            and not runtime.signoff_gate.verify_integrity()
        ):
            raise AuditIntegrityError(
                "audit integrity failure: database hash chain verification failed"
            )
        return runtime.signoff_gate.records()
    if runtime.engine is None:
        return []
    return runtime.engine.records()


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
            return _tool_result(_explanation_payload(explanation))

        if name == "override_submit":
            policy = _require(args, "policy")
            entity = _require(args, "entity")
            rationale = _require(args, "rationale")
            explanation = explain_policy(
                _registry(runtime),
                policy=policy,
                entity=entity,
                engine=runtime.engine,
                protected_gate=runtime.protected_gate,
                signoff_gate=runtime.signoff_gate,
            )
            if not explanation.enabled:
                raise NotEnabledError(
                    f"cell {explanation.cell!r} is not enabled for override submission"
                )
            if explanation.cell in ("chill", "coached"):
                if runtime.engine is None:
                    raise NotEnabledError(f"cell {explanation.cell!r} is not enabled")
                override_result = submit_override(
                    runtime.engine,
                    identity=runtime.identity,
                    policy=policy,
                    entity=entity,
                    rationale=rationale,
                    agent_id=runtime.agent_id,
                )
                if explanation.cell == "chill":
                    return _tool_result(
                        {
                            "outcome": "ACCEPTED_SELF",
                            "cell": "chill",
                            "seq": override_result.seq,
                            "note": "self-cleared; human reviews asynchronously",
                        }
                    )
                return _tool_result(
                    _judged_result_payload(
                        cell="coached",
                        seq=override_result.seq,
                        accepted=override_result.accepted,
                        judge_model=override_result.judge_model,
                        judge_rationale=override_result.judge_rationale,
                    )
                )
            if explanation.cell == "structured":
                signoff = request_signoff(
                    runtime.signoff_gate,
                    identity=runtime.identity,
                    policy=policy,
                    entity=entity,
                    rationale=rationale,
                    agent_id=runtime.agent_id,
                )
                return _tool_result(
                    {
                        "outcome": "ESCALATED_PENDING",
                        "cell": "structured",
                        "seq": signoff.seq,
                        "cleared": signoff.cleared,
                        "human_required": True,
                        "operator_instruction": (
                            f"Human sign-off required for seq {signoff.seq}."
                        ),
                        "poll_tool": "signoff_status_get",
                        "poll_handle": signoff.seq,
                    }
                )
            if explanation.cell == "protected":
                missing = [
                    item.to_payload()
                    for item in explanation.required_inputs
                    if not _optional_string(args, item.field)
                ]
                if missing:
                    return _tool_result(
                        {
                            "outcome": "NEED_INPUTS",
                            "cell": "protected",
                            "required_inputs": missing,
                        }
                    )
                protected = submit_protected_override(
                    runtime.protected_gate,
                    identity=runtime.identity,
                    policy=policy,
                    entity=entity,
                    rationale=rationale,
                    agent_id=runtime.agent_id,
                    file_fingerprint=_require(args, "file_fingerprint"),
                    ast_path=_require(args, "ast_path"),
                    source_root=runtime.source_root,
                )
                return _tool_result(
                    _judged_result_payload(
                        cell="protected",
                        seq=protected.seq,
                        accepted=protected.accepted,
                        judge_model=protected.judge_model,
                        judge_rationale=protected.judge_rationale,
                    )
                )
            raise NotEnabledError(f"unsupported policy cell {explanation.cell!r}")

        if name == "signoff_status_get":
            seq = _require_int(args, "seq")
            if runtime.signoff_gate is None:
                raise NotEnabledError("structured cell not enabled")
            request = runtime.signoff_gate.request_record(seq)
            if request is None:
                return _tool_error("NO_SUCH_REQUEST", f"no sign-off request at seq {seq}")
            if not runtime.signoff_gate.is_cleared(seq):
                return _tool_result({"cleared": False, "seq": seq})
            signed = _signoff_signed_record(runtime, seq)
            payload: dict[str, Any] = {"cleared": True, "seq": seq}
            if signed is not None:
                payload["signed_by"] = signed.get("agent_id")
                payload["signed_at"] = signed.get("recorded_at")
            return _tool_result(payload)

        if name == "policy_evaluate":
            ev = evaluate_policy(
                _grammar(runtime),
                engine=runtime.engine,
                policy=_require(args, "policy"),
                target=_require_object(args, "target"),
            )
            return _tool_result(
                {
                    "outcome": ev.result.value,
                    "detail": ev.detail,
                    "provenance_gap": ev.provenance_gap,
                }
            )

        if name == "scan_route":
            has_cell = "cell" in args
            has_map = "severity_map" in args
            has_fail_on = "fail_on" in args
            if has_fail_on:
                if not has_cell or has_map:
                    return _tool_error(
                        "INVALID_CELL_SPEC",
                        "fail_on routing requires cell and forbids severity_map",
                    )
            elif has_cell == has_map:
                return _tool_error(
                    "INVALID_CELL_SPEC",
                    "provide exactly one of cell or severity_map",
                )
            scan = _require_object(args, "scan")
            scan_policy: WardlineCellPolicy | None = None
            scan_cell_map: dict[WardlineSeverity, WardlineCellPolicy] | None = None
            scan_fail_on: WardlineSeverity | None = None
            try:
                if has_cell:
                    scan_policy = WardlineCellPolicy(_require(args, "cell"))
                    if has_fail_on:
                        scan_fail_on = WardlineSeverity[_require(args, "fail_on")]
                else:
                    raw_map = _require_object(args, "severity_map")
                    scan_cell_map = {
                        WardlineSeverity[severity]: WardlineCellPolicy(cell)
                        for severity, cell in raw_map.items()
                    }
            except (KeyError, ValueError) as exc:
                return _tool_error("INVALID_CELL_SPEC", str(exc))
            routed = route_wardline_scan(
                scan,
                agent_id=runtime.agent_id,
                identity=runtime.identity,
                engine=runtime.engine,
                signoff=runtime.signoff_gate,
                policy=scan_policy,
                cell_map=scan_cell_map,
                fail_on=scan_fail_on,
            )
            return _tool_result({"outcome": "ROUTED", "routed": routed})

        if name == "git_branch_list":
            return _tool_result(
                {"branches": [asdict(branch) for branch in _git(runtime).branches()]}
            )

        if name == "git_commit_get":
            return _tool_result(
                {"commit": asdict(_git(runtime).commit(_require(args, "sha")))}
            )

        if name == "git_rename_list":
            return _tool_result(
                {
                    "renames": [
                        asdict(rename)
                        for rename in _git(runtime).renames(_require(args, "rev_range"))
                    ]
                }
            )

        if name == "pull_request_get":
            number = _require_int(args, "number")
            pull = _pulls(runtime).get(number)
            if pull is None:
                return _tool_error("NOT_FOUND", f"unknown PR: {number}")
            pull_payload = asdict(pull)
            pull_payload["state"] = pull.state.value
            pull_checks = (
                runtime.check_surface.for_pr(number)
                if runtime.check_surface is not None
                else []
            )
            pull_payload["checks"] = [_check_to_dict(run) for run in pull_checks]
            return _tool_result(pull_payload)

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
                    pr_number = int(target)
                except ValueError as exc:
                    raise InvalidArgumentError(
                        "target_type 'pr' requires an integer target"
                    ) from exc
                checks = runtime.check_surface.for_pr(pr_number)
                response_target = pr_number
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

        if name == "override_rate_get":
            rate = compute_override_rate(_verified_records(runtime))
            return _tool_result(
                {
                    "status": rate.status.value,
                    "rate": rate.rate,
                    "sample_size": rate.sample_size,
                    "note": _OVERRIDE_RATE_NOTE,
                }
            )

        return _tool_error("UNKNOWN_TOOL", f"unknown tool: {name}")
    except Exception as exc:
        return _service_error(exc)


def handle_request(request: dict[str, Any], runtime: McpRuntime) -> dict[str, Any] | None:
    request_id = request.get("id")
    method = request.get("method")
    if request_id is None:
        return None
    result: dict[str, Any]
    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "legis", "version": __version__},
        }
    elif method == "tools/list":
        result = {"tools": tool_definitions()}
    elif method == "tools/call":
        try:
            name, args = _arguments(request.get("params", {}))
            result = call_tool(runtime, name, args)
        except Exception as exc:
            result = _service_error(exc)
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def run_jsonrpc(input_stream: TextIO, output_stream: TextIO, runtime: McpRuntime) -> None:
    for line in input_stream:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("JSON-RPC request must be an object")
            response = handle_request(request, runtime)
        except Exception as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": str(exc)},
            }
        if response is not None:
            output_stream.write(json.dumps(response, separators=(",", ":")) + "\n")
            output_stream.flush()


def main(agent_id: str) -> int:
    run_jsonrpc(sys.stdin, sys.stdout, build_runtime(agent_id))
    return 0
