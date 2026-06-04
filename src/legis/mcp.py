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
from legis.clock import SystemClock
from legis.enforcement.engine import EnforcementEngine
from legis.enforcement.protected import ProtectedGate, TrailVerifier
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.policy.cells import (
    PolicyCellRegistry,
    default_policy_cells,
    load_policy_cells,
)
from legis.policy.grammar import default_grammar
from legis.records.override_record import OverrideRecord
from legis.service.errors import (
    AuditIntegrityError,
    InvalidArgumentError,
    NotEnabledError,
    NotFoundError,
    ServiceError,
)
from legis.service.governance import (
    compute_override_rate,
    evaluate_policy,
    request_signoff,
    submit_override,
    submit_protected_override,
    verified_records,
)
from legis.service.wardline import route_wardline_scan
from legis.store.audit_store import AuditStore
from legis.wardline.governor import WardlineCellPolicy
from legis.wardline.ingest import WardlineSeverity


@dataclass
class McpRuntime:
    agent_id: str
    engine: EnforcementEngine | None = None
    identity: Any | None = None
    protected_gate: ProtectedGate | None = None
    signoff_gate: Any | None = None
    trail_verifier: TrailVerifier | None = None
    grammar: Any | None = None
    cell_registry: PolicyCellRegistry | None = None
    source_root: str | Path | None = None
    wardline_artifact_key: bytes | None = None
    wardline_cell: str | None = None
    wardline_cell_by_severity: str | None = None


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
    from legis.api.app import DEFAULT_GOVERNANCE_DB

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
    trail_verifier = None
    hmac_key = os.environ.get("LEGIS_HMAC_KEY")
    if hmac_key:
        key = hmac_key.encode("utf-8")
        protected_policies = frozenset(
            p.strip()
            for p in os.environ.get("LEGIS_PROTECTED_POLICIES", "").split(",")
            if p.strip()
        )

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
        trail_verifier = TrailVerifier(key, protected_policies)

    return McpRuntime(
        agent_id=agent_id,
        engine=engine,
        identity=identity,
        protected_gate=protected_gate,
        signoff_gate=signoff_gate,
        trail_verifier=trail_verifier,
        grammar=default_grammar(),
        cell_registry=_load_policy_cell_registry(),
        source_root=os.environ.get("LEGIS_SOURCE_ROOT") or os.getcwd(),
        wardline_artifact_key=(
            os.environ["LEGIS_WARDLINE_ARTIFACT_KEY"].encode("utf-8")
            if os.environ.get("LEGIS_WARDLINE_ARTIFACT_KEY")
            else None
        ),
        wardline_cell=os.environ.get("LEGIS_WARDLINE_CELL"),
        wardline_cell_by_severity=os.environ.get("LEGIS_WARDLINE_CELL_BY_SEVERITY"),
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
            "name": "submit_override",
            "description": "Submit a simple-tier governance override as the launch-bound agent.",
            "inputSchema": _schema(
                ["policy", "entity", "rationale"],
                {"policy": string, "entity": string, "rationale": string},
            ),
        },
        {
            "name": "protected_override",
            "description": "Submit a protected-cell override as the launch-bound agent.",
            "inputSchema": _schema(
                ["policy", "entity", "rationale", "file_fingerprint", "ast_path"],
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
            "name": "signoff_request",
            "description": "Open a structured sign-off request as the launch-bound agent.",
            "inputSchema": _schema(
                ["policy", "entity", "rationale"],
                {"policy": string, "entity": string, "rationale": string},
            ),
        },
        {
            "name": "policy_evaluate",
            "description": "Evaluate a policy grammar boundary.",
            "inputSchema": _schema(
                ["policy", "target"],
                {"policy": string, "target": object_schema},
            ),
        },
        {
            "name": "wardline_scan_results",
            "description": "Route a Wardline scan using server-owned routing policy.",
            "inputSchema": _schema(["scan"], {"scan": object_schema}),
        },
        {
            "name": "list_overrides",
            "description": "Read the verified governance trail.",
            "inputSchema": _schema([], {}),
        },
        {
            "name": "override_rate",
            "description": "Evaluate the configured override-rate gate.",
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
        return _tool_error("NOT_ENABLED", str(exc))
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
    name = params.get("name")
    arguments = params.get("arguments", {})
    if not isinstance(name, str):
        raise ValueError("tools/call requires a string tool name")
    if not isinstance(arguments, dict):
        raise ValueError("tools/call arguments must be an object")
    return name, arguments


def _parse_wardline_cell_map(raw: str) -> dict[WardlineSeverity, WardlineCellPolicy]:
    mapping: dict[WardlineSeverity, WardlineCellPolicy] = {}
    for part in raw.split(","):
        if not part.strip():
            continue
        severity_raw, sep, cell_raw = part.partition("=")
        if not sep:
            raise ValueError("Wardline cell map entries must be SEVERITY=cell")
        mapping[WardlineSeverity[severity_raw.strip()]] = WardlineCellPolicy(
            cell_raw.strip()
        )
    if not mapping:
        raise ValueError("Wardline cell map must not be empty")
    return mapping


def _require(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"argument {key!r} must be a non-empty string")
    return value


def call_tool(runtime: McpRuntime, name: str, args: dict[str, Any]) -> dict[str, Any]:
    try:
        if name == "submit_override":
            if runtime.engine is None:
                raise NotEnabledError("governance engine not enabled")
            override_result = submit_override(
                runtime.engine,
                identity=runtime.identity,
                policy=_require(args, "policy"),
                entity=_require(args, "entity"),
                rationale=_require(args, "rationale"),
                agent_id=runtime.agent_id,
            )
            return _tool_result(
                {
                    "accepted": override_result.accepted,
                    "seq": override_result.seq,
                    "verdict": override_result.verdict.value if override_result.verdict else None,
                    "judge_model": override_result.judge_model,
                    "judge_rationale": override_result.judge_rationale,
                }
            )
        if name == "protected_override":
            protected_result = submit_protected_override(
                runtime.protected_gate,
                identity=runtime.identity,
                policy=_require(args, "policy"),
                entity=_require(args, "entity"),
                rationale=_require(args, "rationale"),
                agent_id=runtime.agent_id,
                file_fingerprint=_require(args, "file_fingerprint"),
                ast_path=_require(args, "ast_path"),
                source_root=runtime.source_root,
            )
            return _tool_result(
                {
                    "accepted": protected_result.accepted,
                    "seq": protected_result.seq,
                    "verdict": protected_result.verdict.value,
                    "judge_model": protected_result.judge_model,
                    "judge_rationale": protected_result.judge_rationale,
                    "signature": protected_result.signature,
                }
            )
        if name == "signoff_request":
            signoff_result = request_signoff(
                runtime.signoff_gate,
                identity=runtime.identity,
                policy=_require(args, "policy"),
                entity=_require(args, "entity"),
                rationale=_require(args, "rationale"),
                agent_id=runtime.agent_id,
            )
            return _tool_result({"seq": signoff_result.seq, "cleared": signoff_result.cleared})
        if name == "policy_evaluate":
            grammar = runtime.grammar or default_grammar()
            target = args.get("target")
            if not isinstance(target, dict):
                raise ValueError("argument 'target' must be an object")
            policy_result = evaluate_policy(
                grammar,
                engine=runtime.engine,
                policy=_require(args, "policy"),
                target=target,
            )
            return _tool_result(
                {
                    "policy": policy_result.policy,
                    "result": policy_result.result.value,
                    "detail": policy_result.detail,
                    "provenance_gap": policy_result.provenance_gap,
                }
            )
        if name == "wardline_scan_results":
            scan = args.get("scan")
            if not isinstance(scan, dict):
                raise ValueError("argument 'scan' must be an object")
            if runtime.wardline_cell and runtime.wardline_cell_by_severity:
                raise ValueError("server Wardline routing is misconfigured")
            if runtime.wardline_cell_by_severity:
                routed = route_wardline_scan(
                    scan,
                    agent_id=runtime.agent_id,
                    identity=runtime.identity,
                    engine=runtime.engine,
                    signoff=runtime.signoff_gate,
                    cell_map=_parse_wardline_cell_map(runtime.wardline_cell_by_severity),
                    artifact_key=runtime.wardline_artifact_key,
                )
            elif runtime.wardline_cell:
                routed = route_wardline_scan(
                    scan,
                    agent_id=runtime.agent_id,
                    identity=runtime.identity,
                    engine=runtime.engine,
                    signoff=runtime.signoff_gate,
                    policy=WardlineCellPolicy(runtime.wardline_cell),
                    artifact_key=runtime.wardline_artifact_key,
                )
            else:
                raise NotEnabledError("Wardline MCP routing is not configured")
            return _tool_result({"routed": routed})
        if name == "list_overrides":
            records = verified_records(
                runtime.protected_gate,
                runtime.trail_verifier,
                lambda: runtime.engine.records() if runtime.engine is not None else [],
            )
            return _tool_result({"records": [record.payload for record in records]})
        if name == "override_rate":
            records = verified_records(
                runtime.protected_gate,
                runtime.trail_verifier,
                lambda: runtime.engine.records() if runtime.engine is not None else [],
            )
            rate_result = compute_override_rate(records)
            return _tool_result(
                {
                    "status": rate_result.status.value,
                    "rate": rate_result.rate,
                    "sample_size": rate_result.sample_size,
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
