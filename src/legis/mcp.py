"""Minimal MCP-over-stdio adapter for Legis.

The adapter is deliberately stdlib-only: one JSON-RPC object per line on stdin,
one response per line on stdout. Tool calls are thin transport mappings over the
service layer and the launch-bound ``agent_id``; tool schemas never accept actor
identity from call arguments.
"""

from __future__ import annotations

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


_WP_M3_TOOLS = frozenset(
    {"legis_explain", "legis_submit_override", "legis_checks_for"}
)


@dataclass
class McpRuntime:
    agent_id: str
    engine: EnforcementEngine | None = None
    identity: Any | None = None
    protected_gate: ProtectedGate | None = None
    signoff_gate: Any | None = None
    cell_registry: PolicyCellRegistry | None = None
    check_surface: CheckSurface | None = None


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
        from legis.identity.clarion_client import HttpClarionIdentity
        from legis.identity.resolver import IdentityResolver

        identity = IdentityResolver(HttpClarionIdentity(clarion_url))

    protected_gate = None
    signoff_gate = None
    hmac_key = os.environ.get("LEGIS_HMAC_KEY")
    if hmac_key:
        key = hmac_key.encode("utf-8")

        class FailClosedJudge:
            def evaluate(self, record: OverrideRecord) -> JudgeOpinion:
                return JudgeOpinion(
                    verdict=Verdict.BLOCKED,
                    model="fail-closed-fallback",
                    rationale="No LLM judge client is configured on this MCP server.",
                )

        from legis.enforcement.signoff import SignoffGate

        protected_gate = ProtectedGate(store, clock, FailClosedJudge(), key)
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
    return [
        {
            "name": "legis_explain",
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
            "name": "legis_submit_override",
            "description": (
                "Submit an override as the launch-bound agent. In WP-M3 this "
                "records one new chill-cell override attempt; repeated calls "
                "create repeated audit records."
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
            "name": "legis_checks_for",
            "description": (
                "Read recorded CI/check outcomes for a commit, branch, or pull "
                "request target."
            ),
            "inputSchema": _schema(
                ["target_type", "target"],
                {"target_type": string, "target": string},
            ),
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


def _wp_m3_explanation_payload(explanation) -> dict[str, Any]:
    payload = explanation.to_payload()
    payload["available_moves"] = [
        move for move in payload["available_moves"] if move in _WP_M3_TOOLS
    ]
    return payload


def call_tool(runtime: McpRuntime, name: str, args: dict[str, Any]) -> dict[str, Any]:
    try:
        if name == "legis_explain":
            explanation = explain_policy(
                _registry(runtime),
                policy=_require(args, "policy"),
                entity=_require(args, "entity"),
                engine=runtime.engine,
                protected_gate=runtime.protected_gate,
                signoff_gate=runtime.signoff_gate,
            )
            return _tool_result(_wp_m3_explanation_payload(explanation))

        if name == "legis_submit_override":
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

        if name == "legis_checks_for":
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
