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
from legis.canonical import content_hash
from legis.checks.models import CheckRun
from legis.checks.surface import CheckSurface
from legis.clock import SystemClock
from legis.enforcement.engine import EnforcementEngine
from legis.enforcement.judge_factory import build_judge_from_env
from legis.enforcement.protected import ProtectedGate, TrailVerifier, TamperError
from legis.enforcement.signoff import SignoffGate
from legis.enforcement.verdict import SignoffState, Verdict
from legis.git.surface import GitError, GitSurface
from legis.governance.binding_ledger import BindingError
from legis.policy.cells import (
    PolicyCellRegistry,
    default_policy_cells,
    fail_closed_policy_cells,
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
    verified_records as service_verified_records,
)
from legis.service.wardline import route_wardline_scan
from legis.store.audit_store import AuditStore
from legis.wardline.governor import WardlineCellPolicy
from legis.wardline.ingest import WardlinePayloadError, WardlineSeverity


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
        "git_rename_feed_get",
        "pull_request_get",
        "check_list",
        "override_rate_get",
        "filigree_closure_gate_get",
    }
)
_OVERRIDE_RATE_NOTE = "measures operator force-pasts; not movable by agent retries"
_SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26")
_DEFAULT_PROTOCOL_VERSION = _SUPPORTED_PROTOCOL_VERSIONS[-1]


@dataclass
class McpRuntime:
    agent_id: str
    initialized: bool = False
    protocol_version: str | None = None
    engine: EnforcementEngine | None = None
    identity: Any | None = None
    protected_gate: ProtectedGate | None = None
    trail_verifier: TrailVerifier | None = None
    signoff_gate: Any | None = None
    cell_registry: PolicyCellRegistry | None = None
    check_surface: CheckSurface | None = None
    git_surface: GitSurface | None = None
    pull_surface: PullSurface | None = None
    grammar: PolicyGrammar | None = None
    source_root: str | Path | None = None
    wardline_artifact_key: bytes | None = None
    binding_ledger: Any | None = None


def _load_policy_cell_registry() -> PolicyCellRegistry:
    configured = os.environ.get("LEGIS_POLICY_CELLS")
    if configured:
        return load_policy_cells(configured)

    root = Path(os.environ.get("LEGIS_SOURCE_ROOT") or os.getcwd())
    default_path = root / "policy" / "cells.toml"
    if default_path.exists():
        return load_policy_cells(default_path)

    # No configuration found. Fail closed — an unmatched policy escalates to a
    # human operator (structured) — unless a deployment explicitly opts into the
    # chill dev posture. Otherwise an incomplete deployment would silently
    # downgrade governance to self-clear (Q-M7 / audit H6).
    if os.environ.get("LEGIS_DEV_DEFAULT_CELLS") == "1":
        return default_policy_cells()
    return fail_closed_policy_cells()


def build_runtime(agent_id: str) -> McpRuntime:
    from legis.config import DEFAULT_GOVERNANCE_DB

    clock = SystemClock()
    engine = None
    identity = None
    loomweave_url = os.environ.get("LOOMWEAVE_API_URL")
    if loomweave_url:
        from legis.identity.loomweave_client import HttpLoomweaveIdentity, loomweave_hmac_key_from_env
        from legis.identity.resolver import IdentityResolver

        identity = IdentityResolver(
            HttpLoomweaveIdentity(loomweave_url, hmac_key=loomweave_hmac_key_from_env())
        )

    protected_gate = None
    trail_verifier = None
    signoff_gate = None
    binding_ledger = None
    hmac_key = os.environ.get("LEGIS_HMAC_KEY")
    if hmac_key:
        key = hmac_key.encode("utf-8")
        store = AuditStore(os.environ.get("LEGIS_GOVERNANCE_DB", DEFAULT_GOVERNANCE_DB))
        protected_policies_str = os.environ.get("LEGIS_PROTECTED_POLICIES", "")
        protected_policies = frozenset(
            p.strip() for p in protected_policies_str.split(",") if p.strip()
        )
        trail_verifier = TrailVerifier(key, protected_policies)

        protected_gate = ProtectedGate(store, clock, build_judge_from_env("MCP"), key)
        signoff_gate = SignoffGate(store, clock, signer=True, key=key)

        from legis.governance.binding_ledger import BindingLedger

        binding_ledger = BindingLedger(
            AuditStore(os.environ.get("LEGIS_BINDING_DB", "sqlite:///legis-binding.db")),
            clock,
            key,
        )

    return McpRuntime(
        agent_id=agent_id,
        engine=engine,
        identity=identity,
        protected_gate=protected_gate,
        trail_verifier=trail_verifier,
        signoff_gate=signoff_gate,
        cell_registry=_load_policy_cell_registry(),
        check_surface=None,
        git_surface=GitSurface(os.environ.get("LEGIS_SOURCE_ROOT") or os.getcwd()),
        pull_surface=None,
        grammar=default_grammar(),
        source_root=os.environ.get("LEGIS_SOURCE_ROOT") or os.getcwd(),
        wardline_artifact_key=(
            os.environ["LEGIS_WARDLINE_ARTIFACT_KEY"].encode("utf-8")
            if os.environ.get("LEGIS_WARDLINE_ARTIFACT_KEY")
            else None
        ),
        binding_ledger=binding_ledger,
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
    integer = {"type": "integer", "minimum": 1}
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
                    "idempotency_key": string,
                },
            ),
        },
        {
            "name": "signoff_status_get",
            "description": "Poll whether a structured sign-off request has been cleared.",
            "inputSchema": _schema(["seq"], {"seq": integer}),
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
            "name": "git_rename_feed_get",
            "description": (
                "Loomweave-ready rename feed: committed renames over base..head plus "
                "optional uncommitted working-tree renames."
            ),
            "inputSchema": _schema(
                ["base"],
                {
                    "base": string,
                    "head": string,
                    "include_worktree": {"type": "boolean"},
                },
            ),
        },
        {
            "name": "filigree_closure_gate_get",
            "description": "Read whether legis holds verified binding evidence for closing a Filigree issue.",
            "inputSchema": _schema(["issue_id"], {"issue_id": string}),
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


def _recovery_for(code: str) -> dict[str, Any]:
    recoverable = code not in {"AUDIT_INTEGRITY_FAILURE", "INTERNAL_ERROR"}
    next_actions = {
        "INVALID_ARGUMENT": "Correct the tool arguments and retry.",
        "INVALID_CELL_SPEC": "Use server-owned routing or a valid cell configuration.",
        "CELL_NOT_ENABLED": "Ask the operator to enable the required governance cell.",
        "NO_SUCH_REQUEST": "Poll a known sign-off sequence returned by override_submit.",
        "NOT_FOUND": "Refresh the target identifier and retry.",
        "UNKNOWN_TOOL": "Call tools/list and use one of the advertised tool names.",
        "AUDIT_INTEGRITY_FAILURE": "Stop and ask an operator to inspect the governance trail.",
        "GIT_ERROR": "Check the git ref or revision range and retry.",
    }
    return {
        "recoverable": recoverable,
        "next_action": next_actions.get(code, "Inspect the error message before retrying."),
    }


def _tool_error(code: str, message: str) -> dict[str, Any]:
    recovery = _recovery_for(code)
    return {
        "isError": True,
        "content": [{"type": "text", "text": f"{code}: {message}"}],
        "structuredContent": {
            "error_code": code,
            "message": message,
            **recovery,
        },
    }


def _service_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, AuditIntegrityError):
        return _tool_error("AUDIT_INTEGRITY_FAILURE", str(exc))
    if isinstance(exc, BindingError):
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


def _allowed_tool_arguments(name: str) -> set[str] | None:
    for tool in tool_definitions():
        if tool["name"] == name:
            return set(tool["inputSchema"].get("properties", {}))
    return None


def _validate_argument_keys(name: str, args: dict[str, Any]) -> None:
    allowed = _allowed_tool_arguments(name)
    if allowed is None:
        return
    unexpected = sorted(set(args) - allowed)
    if unexpected:
        joined = ", ".join(unexpected)
        raise InvalidArgumentError(f"unexpected argument(s) for {name}: {joined}")


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
    raw = args.get(key)
    if isinstance(raw, int) and not isinstance(raw, bool):
        value = raw
    elif isinstance(raw, str) and raw:
        try:
            value = int(raw)
        except ValueError as exc:
            raise InvalidArgumentError(f"argument {key!r} must be an integer") from exc
    else:
        raise InvalidArgumentError(f"argument {key!r} must be an integer")
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
    # Defensive fallback if a runtime was built without a registry: fail closed
    # rather than self-clear (Q-M7 / audit H6).
    return runtime.cell_registry or fail_closed_policy_cells()


def _parse_wardline_cell_map(raw: str) -> dict[WardlineSeverity, WardlineCellPolicy]:
    mapping: dict[WardlineSeverity, WardlineCellPolicy] = {}
    for part in raw.split(","):
        if not part.strip():
            continue
        severity_raw, sep, cell_raw = part.partition("=")
        if not sep:
            raise ValueError("cell map entries must be SEVERITY=cell")
        mapping[WardlineSeverity[severity_raw.strip()]] = WardlineCellPolicy(
            cell_raw.strip()
        )
    if not mapping:
        raise ValueError("cell map must not be empty")
    return mapping


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


def _engine(runtime: McpRuntime) -> EnforcementEngine:
    if runtime.engine is None:
        from legis.config import DEFAULT_GOVERNANCE_DB

        store = AuditStore(os.environ.get("LEGIS_GOVERNANCE_DB", DEFAULT_GOVERNANCE_DB))
        runtime.engine = EnforcementEngine(store, SystemClock())
    return runtime.engine


def _checks(runtime: McpRuntime) -> CheckSurface:
    if runtime.check_surface is None:
        from legis.config import DEFAULT_CHECK_DB

        runtime.check_surface = CheckSurface(
            os.environ.get("LEGIS_CHECK_DB", DEFAULT_CHECK_DB)
        )
    return runtime.check_surface


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


def _override_idempotency_request_hash(
    *,
    agent_id: str,
    policy: str,
    entity: str,
    rationale: str,
    cell: str,
    file_fingerprint: str | None,
    ast_path: str | None,
) -> str:
    return content_hash(
        {
            "version": 1,
            "agent_id": agent_id,
            "policy": policy,
            "entity": entity,
            "rationale": rationale,
            "cell": cell,
            "file_fingerprint": file_fingerprint,
            "ast_path": ast_path,
        }
    )


def _existing_idempotent_record(
    runtime: McpRuntime, key: str, request_hash: str
) -> Any | None:
    for rec in _verified_records(runtime):
        ext = rec.payload.get("extensions", {})
        if ext.get("mcp_idempotency_key") != key:
            continue
        if ext.get("mcp_idempotency_request_hash") == request_hash:
            return rec
        raise InvalidArgumentError(
            "idempotency key already references a different override request"
        )
    return None


def _idempotent_override_response(payload: dict[str, Any], seq: int) -> dict[str, Any]:
    ext = payload.get("extensions", {})
    cell = ext.get("mcp_cell")
    if cell == "chill":
        return {
            "outcome": "ACCEPTED_SELF",
            "cell": "chill",
            "seq": seq,
            "note": "self-cleared; human reviews asynchronously",
        }
    if cell == "structured":
        return {
            "outcome": "ESCALATED_PENDING",
            "cell": "structured",
            "seq": seq,
            "cleared": False,
            "human_required": True,
            "operator_instruction": f"Human sign-off required for seq {seq}.",
            "poll_tool": "signoff_status_get",
            "poll_handle": seq,
        }
    if cell in ("coached", "protected"):
        verdict = ext.get("judge_verdict")
        return _judged_result_payload(
            cell=cell,
            seq=seq,
            accepted=verdict == Verdict.ACCEPTED.value,
            judge_model=ext.get("judge_model"),
            judge_rationale=ext.get("judge_rationale"),
        )
    raise InvalidArgumentError("idempotency key references an unsupported record")


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
    if runtime.protected_gate is not None:
        return service_verified_records(
            runtime.protected_gate,
            runtime.trail_verifier,
            lambda: [],
        )
    if runtime.signoff_gate is not None and runtime.engine is None:
        if (
            hasattr(runtime.signoff_gate, "verify_integrity")
            and not runtime.signoff_gate.verify_integrity()
        ):
            raise AuditIntegrityError(
                "audit integrity failure: database hash chain verification failed"
            )
        records = runtime.signoff_gate.records()
        if runtime.trail_verifier is not None:
            try:
                runtime.trail_verifier.verify(records)
            except TamperError as exc:
                raise AuditIntegrityError(f"audit integrity failure: {exc}") from exc
        return records
    if runtime.engine is None:
        return []
    return runtime.engine.records()


def call_tool(runtime: McpRuntime, name: str, args: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate_argument_keys(name, args)
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
            idempotency_key = _optional_string(args, "idempotency_key")
            simple_engine = (
                _engine(runtime)
                if _registry(runtime).cell_for(policy) in ("chill", "coached")
                else runtime.engine
            )
            explanation = explain_policy(
                _registry(runtime),
                policy=policy,
                entity=entity,
                engine=simple_engine,
                protected_gate=runtime.protected_gate,
                signoff_gate=runtime.signoff_gate,
            )
            if not explanation.enabled:
                raise NotEnabledError(
                    f"cell {explanation.cell!r} is not enabled for override submission"
                )
            idempotency_request_hash = (
                _override_idempotency_request_hash(
                    agent_id=runtime.agent_id,
                    policy=policy,
                    entity=entity,
                    rationale=rationale,
                    cell=explanation.cell,
                    file_fingerprint=_optional_string(args, "file_fingerprint"),
                    ast_path=_optional_string(args, "ast_path"),
                )
                if idempotency_key is not None
                else None
            )
            extra_extensions = (
                {
                    "mcp_idempotency_key": idempotency_key,
                    "mcp_idempotency_request_hash": idempotency_request_hash,
                    "mcp_cell": explanation.cell,
                }
                if idempotency_key is not None
                else {"mcp_cell": explanation.cell}
            )
            if idempotency_key is not None and idempotency_request_hash is not None:
                existing = _existing_idempotent_record(
                    runtime, idempotency_key, idempotency_request_hash
                )
                if existing is not None:
                    return _tool_result(
                        _idempotent_override_response(existing.payload, existing.seq)
                    )
            if explanation.cell in ("chill", "coached"):
                override_result = submit_override(
                    _engine(runtime),
                    identity=runtime.identity,
                    policy=policy,
                    entity=entity,
                    rationale=rationale,
                    agent_id=runtime.agent_id,
                    extra_extensions=extra_extensions,
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
                    extra_extensions=extra_extensions,
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
                    extra_extensions=extra_extensions,
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
                engine=_engine(runtime),
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
            server_cell = os.environ.get("LEGIS_WARDLINE_CELL")
            server_cell_by_severity = os.environ.get("LEGIS_WARDLINE_CELL_BY_SEVERITY")
            if server_cell and server_cell_by_severity:
                return _tool_error(
                    "INVALID_CELL_SPEC", "server Wardline routing is misconfigured"
                )
            has_cell = "cell" in args
            has_map = "severity_map" in args
            has_fail_on = "fail_on" in args
            server_routing = server_cell is not None or server_cell_by_severity is not None
            if server_routing and (has_cell or has_map or has_fail_on):
                return _tool_error(
                    "INVALID_CELL_SPEC", "Wardline routing is server-owned"
                )
            if not server_routing:
                if os.environ.get("LEGIS_UNSAFE_WARDLINE_REQUEST_ROUTING") != "1":
                    return _tool_error(
                        "INVALID_CELL_SPEC",
                        "Wardline routing is server-owned; configure "
                        "LEGIS_WARDLINE_CELL or LEGIS_WARDLINE_CELL_BY_SEVERITY",
                    )
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
                if server_cell_by_severity is not None:
                    scan_cell_map = _parse_wardline_cell_map(server_cell_by_severity)
                elif server_cell is not None:
                    scan_policy = WardlineCellPolicy(server_cell)
                elif has_cell:
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
                engine=_engine(runtime),
                signoff=runtime.signoff_gate,
                policy=scan_policy,
                cell_map=scan_cell_map,
                fail_on=scan_fail_on,
                artifact_key=(
                    runtime.wardline_artifact_key
                    or (
                        os.environ["LEGIS_WARDLINE_ARTIFACT_KEY"].encode("utf-8")
                        if os.environ.get("LEGIS_WARDLINE_ARTIFACT_KEY")
                        else None
                    )
                ),
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

        if name == "git_rename_feed_get":
            from legis.git.rename_feed import build_rename_feed

            return _tool_result(
                build_rename_feed(
                    runtime.source_root or os.getcwd(),
                    base=_require(args, "base"),
                    head=args.get("head", "HEAD"),
                    include_worktree=bool(args.get("include_worktree", False)),
                )
            )

        if name == "filigree_closure_gate_get":
            from legis.governance.filigree_gate import evaluate_issue_closure

            if runtime.binding_ledger is None:
                raise NotEnabledError("binding ledger not enabled")
            return _tool_result(
                evaluate_issue_closure(runtime.binding_ledger, issue_id=_require(args, "issue_id"))
            )

        if name == "pull_request_get":
            number = _require_int(args, "number")
            pull = _pulls(runtime).get(number)
            if pull is None:
                return _tool_error("NOT_FOUND", f"unknown PR: {number}")
            pull_payload = asdict(pull)
            pull_payload["state"] = pull.state.value
            pull_checks = (
                _checks(runtime).for_pr(number)
                if runtime.check_surface is not None
                else []
            )
            pull_payload["checks"] = [_check_to_dict(run) for run in pull_checks]
            return _tool_result(pull_payload)

        if name == "check_list":
            check_surface = _checks(runtime)
            target_type = _require(args, "target_type")
            target = _require(args, "target")
            if target_type == "commit":
                checks = check_surface.for_commit(target)
                response_target: str | int = target
            elif target_type == "branch":
                checks = check_surface.for_branch(target)
                response_target = target
            elif target_type == "pr":
                try:
                    pr_number = int(target)
                except ValueError as exc:
                    raise InvalidArgumentError(
                        "target_type 'pr' requires an integer target"
                    ) from exc
                checks = check_surface.for_pr(pr_number)
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
        if method == "notifications/initialized":
            runtime.initialized = True
        return None
    result: dict[str, Any]
    if method == "initialize":
        params = request.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": "initialize params must be an object"},
            }
        requested = params.get("protocolVersion")
        if requested is not None and requested not in _SUPPORTED_PROTOCOL_VERSIONS:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32602,
                    "message": f"unsupported protocolVersion: {requested}",
                    "data": {"supported": list(_SUPPORTED_PROTOCOL_VERSIONS)},
                },
            }
        runtime.protocol_version = requested or _DEFAULT_PROTOCOL_VERSION
        runtime.initialized = True
        result = {
            "protocolVersion": runtime.protocol_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "legis", "version": __version__},
        }
    elif not runtime.initialized:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32002,
                "message": "MCP server is not initialized; call initialize first",
            },
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
