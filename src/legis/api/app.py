"""FastAPI application factory.

The read API mirrors Clarion's consumer model: consumers are HTTP clients.
Sprint 1 mounts the two operating-picture surfaces:

* ``/git/*``    — the stateless git/change surface (WP-1.1)
* ``/checks/*`` — the recorded CI/check surface (WP-1.2)

Dependencies are injected for testability: ``repo_path`` selects the git repo
(default: the process CWD, so a standalone server describes its own repo), and
``check_surface`` supplies the check store (lazily defaulted to a file DB so a
no-arg app never creates state until a check route is used).
"""

from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import BaseModel

from legis import __version__
from legis.checks.models import CheckOutcome, CheckRun
from legis.checks.surface import CheckSurface
from legis.enforcement.engine import EnforcementEngine
from legis.enforcement.lifecycle import evaluate_override_rate
from legis.enforcement.protected import ProtectedGate, TamperError, TrailVerifier
from legis.enforcement.signoff import SignoffGate
from legis.git.pull_request import PullRequestSource
from legis.git.surface import GitError, GitSurface
from legis.governance import params
from legis.governance.gaps import find_lineage_divergence, find_orphan_gaps
from legis.filigree.client import FiligreeClient
from legis.governance.binding_ledger import BindingError, BindingLedger
from legis.governance.signoff_binding import bind_signoff_to_issue
from legis.identity.entity_key import EntityKey
from legis.identity.resolver import IdentityResolver
from legis.policy.grammar import PolicyGrammar, PolicyResult, default_grammar
from legis.wardline.governor import WardlineCellPolicy, route_findings
from legis.wardline.ingest import WardlineSeverity, active_defects

DEFAULT_CHECK_DB = "sqlite:///legis-checks.db"
DEFAULT_GOVERNANCE_DB = "sqlite:///legis-governance.db"


class OverrideIn(BaseModel):
    policy: str
    entity: str  # a locator today (pre-SEI); identity_stable=False
    rationale: str
    agent_id: str


class ProtectedIn(BaseModel):
    policy: str
    entity: str
    rationale: str
    agent_id: str
    file_fingerprint: str
    ast_path: str


class OperatorOverrideIn(BaseModel):
    policy: str
    entity: str
    rationale: str
    operator_id: str
    file_fingerprint: str
    ast_path: str


class SignoffRequestIn(BaseModel):
    policy: str
    entity: str
    rationale: str
    agent_id: str


class SignoffSignIn(BaseModel):
    operator_id: str
    rationale: str = ""


class PolicyEvalIn(BaseModel):
    policy: str
    target: dict = {}


class ScanResultsIn(BaseModel):
    agent_id: str
    scan: dict
    cell: str | None = None
    cell_by_severity: dict[str, str] | None = None


class BindIssueIn(BaseModel):
    issue_id: str


class CheckRunIn(BaseModel):
    check_name: str
    run_id: str
    commit_sha: str
    outcome: CheckOutcome
    branch: str | None = None
    pr: int | None = None
    ran_against: str | None = None
    rule_set: str | None = None
    policy_version: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


def _check_to_dict(run: CheckRun) -> dict:
    d = asdict(run)
    d["outcome"] = run.outcome.value
    return d


def create_app(
    repo_path: str | Path | None = None,
    check_surface: CheckSurface | None = None,
    enforcement: EnforcementEngine | None = None,
    protected_gate: ProtectedGate | None = None,
    signoff_gate: SignoffGate | None = None,
    trail_verifier: TrailVerifier | None = None,
    grammar: PolicyGrammar | None = None,
    identity: IdentityResolver | None = None,
    filigree: FiligreeClient | None = None,
    binding_ledger: BindingLedger | None = None,
    pull_requests: PullRequestSource | None = None,
) -> FastAPI:
    app = FastAPI(title="legis", version=__version__)
    state: dict[str, object | None] = {
        "checks": check_surface,
        "enforcement": enforcement,
        "grammar": grammar,
    }

    def git() -> GitSurface:
        return GitSurface(repo_path or os.getcwd())

    def checks() -> CheckSurface:
        if state["checks"] is None:
            state["checks"] = CheckSurface(DEFAULT_CHECK_DB)
        return state["checks"]

    def engine() -> EnforcementEngine:
        if state["enforcement"] is None:
            from legis.clock import SystemClock
            from legis.store.audit_store import AuditStore

            state["enforcement"] = EnforcementEngine(
                AuditStore(DEFAULT_GOVERNANCE_DB), SystemClock()
            )
        return state["enforcement"]

    def grammar_() -> PolicyGrammar:
        if state["grammar"] is None:
            state["grammar"] = default_grammar()
        return state["grammar"]

    def resolve_for_record(locator: str) -> tuple[EntityKey, dict]:
        # The one resolve-then-key boundary: every governance write path keys on
        # the SEI when Clarion proves a stable identity, on the locator otherwise.
        # When no resolver is wired legis runs standalone (locator-keyed). The
        # `clarion` extension carries the two distinct axes (identity: alive,
        # content: content_hash) plus the REQ-L-01 lineage snapshot, never
        # collapsed — present only when a resolution decision was actually made.
        if identity is None:
            return EntityKey.from_locator(locator), {}
        res = identity.resolve(locator)
        ext: dict = {}
        if res.alive is not None:
            ext["clarion"] = {
                "alive": res.alive,
                "content_hash": res.content_hash,
                "lineage_snapshot": res.lineage_snapshot,
            }
        return res.entity_key, ext

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "legis", "version": __version__}

    # --- git/change surface (WP-1.1) ---

    @app.get("/git/branches")
    def git_branches() -> list[dict]:
        return [asdict(b) for b in git().branches()]

    @app.get("/git/commits/{sha}")
    def git_commit(sha: str) -> dict:
        try:
            return asdict(git().commit(sha))
        except GitError:
            raise HTTPException(status_code=404, detail=f"unknown commit: {sha}")

    @app.get("/git/renames")
    def git_renames(rev_range: str = Query(...)) -> list[dict]:
        return [asdict(r) for r in git().renames(rev_range)]

    @app.get("/git/pull-requests/{number}")
    def get_pull_request(number: int) -> dict:
        if pull_requests is None:
            raise HTTPException(status_code=404, detail="pull-request source not enabled")
        pr = pull_requests.get(number)
        if pr is None:
            raise HTTPException(status_code=404, detail=f"no pull request {number}")
        # PR metadata AND the check outcomes associated with it (roadmap §1.1):
        # join the recorded CI check runs for this PR onto the forge context.
        return {**asdict(pr),
                "checks": [_check_to_dict(r) for r in checks().for_pr(number)]}

    # --- CI/check surface (WP-1.2) ---

    @app.post("/checks", status_code=201)
    def post_check(run: CheckRunIn) -> dict:
        cr = CheckRun(**run.model_dump())
        checks().record(cr)
        return _check_to_dict(cr)

    @app.get("/checks/commit/{sha}")
    def checks_for_commit(sha: str) -> list[dict]:
        return [_check_to_dict(r) for r in checks().for_commit(sha)]

    @app.get("/checks/branch/{name}")
    def checks_for_branch(name: str) -> list[dict]:
        return [_check_to_dict(r) for r in checks().for_branch(name)]

    @app.get("/checks/pr/{pr}")
    def checks_for_pr(pr: int) -> list[dict]:
        return [_check_to_dict(r) for r in checks().for_pr(pr)]

    # --- simple-tier enforcement surface (WP-2.1 chill / WP-2.2 coached) ---

    @app.post("/overrides")
    def post_override(body: OverrideIn, response: Response) -> dict:
        entity_key, ext = resolve_for_record(body.entity)
        result = engine().submit_override(
            policy=body.policy,
            entity_key=entity_key,
            rationale=body.rationale,
            agent_id=body.agent_id,
            extensions=ext,
        )
        # ACCEPTED → 201 (the override took effect); BLOCKED → 409 (it did not,
        # the agent must correct or convince). Full body either way so the agent
        # gets the judge's reasoning to revise.
        response.status_code = 201 if result.accepted else 409
        return {
            "accepted": result.accepted,
            "seq": result.seq,
            "verdict": result.verdict.value if result.verdict else None,
            "judge_model": result.judge_model,
            "judge_rationale": result.judge_rationale,
        }

    def verified_governance_records():
        # The protected gate (when wired) owns the governance trail; otherwise
        # the simple-tier engine does. Never mix the two stores. Verification is
        # fail-closed and applies to EVERY consumer of the protected trail — the
        # human read path AND the enforcement gates — so a tampered record is an
        # honest integrity error, never silently read or scored.
        if protected_gate is not None:
            records = protected_gate.records()
            if trail_verifier is not None:
                try:
                    trail_verifier.verify(records)
                except TamperError as exc:
                    raise HTTPException(
                        status_code=500, detail=f"audit integrity failure: {exc}"
                    )
            return records
        return engine().records()

    @app.get("/overrides")
    def get_overrides() -> list[dict]:
        return [r.payload for r in verified_governance_records()]

    # --- complex-tier enforcement surface (WP-3.1 structured / WP-3.2 protected) ---

    @app.post("/protected/overrides")
    def post_protected_override(body: ProtectedIn, response: Response) -> dict:
        if protected_gate is None:
            raise HTTPException(status_code=404, detail="protected cell not enabled")
        entity_key, ext = resolve_for_record(body.entity)
        result = protected_gate.submit(
            policy=body.policy,
            entity_key=entity_key,
            rationale=body.rationale,
            agent_id=body.agent_id,
            file_fingerprint=body.file_fingerprint,
            ast_path=body.ast_path,
            extensions=ext,
        )
        response.status_code = 201 if result.accepted else 409
        return {
            "accepted": result.accepted,
            "seq": result.seq,
            "verdict": result.verdict.value,
            "judge_model": result.judge_model,
            "judge_rationale": result.judge_rationale,
            "signature": result.signature,
        }

    @app.post("/protected/operator-override", status_code=201)
    def post_operator_override(body: OperatorOverrideIn) -> dict:
        if protected_gate is None:
            raise HTTPException(status_code=404, detail="protected cell not enabled")
        entity_key, ext = resolve_for_record(body.entity)
        result = protected_gate.operator_override(
            policy=body.policy,
            entity_key=entity_key,
            rationale=body.rationale,
            operator_id=body.operator_id,
            file_fingerprint=body.file_fingerprint,
            ast_path=body.ast_path,
            extensions=ext,
        )
        return {
            "accepted": result.accepted,
            "seq": result.seq,
            "verdict": result.verdict.value,
            "signature": result.signature,
        }

    @app.post("/signoff/request", status_code=202)
    def post_signoff_request(body: SignoffRequestIn) -> dict:
        if signoff_gate is None:
            raise HTTPException(status_code=404, detail="structured cell not enabled")
        entity_key, ext = resolve_for_record(body.entity)
        result = signoff_gate.request(
            policy=body.policy,
            entity_key=entity_key,
            rationale=body.rationale,
            agent_id=body.agent_id,
            extensions=ext,
        )
        return {"seq": result.seq, "cleared": result.cleared}

    @app.post("/signoff/{request_seq}/bind-issue", status_code=201)
    def bind_issue(request_seq: int, body: BindIssueIn) -> dict:
        if filigree is None:
            raise HTTPException(status_code=404, detail="filigree binding not enabled")
        if signoff_gate is None:
            raise HTTPException(status_code=404, detail="structured cell not enabled")
        req = signoff_gate.request_record(request_seq)
        if req is None:
            raise HTTPException(
                status_code=404, detail="no sign-off request at seq"
            )
        if not signoff_gate.is_cleared(request_seq):
            raise HTTPException(status_code=409, detail="sign-off not cleared")
        # The SEI and content_hash come from the recorded request, never the
        # caller — binding only what was actually signed off.
        entity_key = EntityKey.from_dict(req["entity_key"])
        content_hash = req.get("extensions", {}).get("clarion", {}).get(
            "content_hash"
        ) or ""
        try:
            return bind_signoff_to_issue(
                filigree,
                issue_id=body.issue_id,
                entity_key=entity_key,
                content_hash=content_hash,
                signoff_seq=request_seq,
                ledger=binding_ledger,
            )
        except ValueError as exc:
            # A locator-keyed (non-SEI) sign-off can't be rename-stably bound.
            raise HTTPException(status_code=409, detail=str(exc))

    @app.get("/signoff/{request_seq}/binding")
    def get_binding(request_seq: int) -> dict:
        if binding_ledger is None:
            raise HTTPException(status_code=404, detail="binding ledger not enabled")
        try:
            binding = binding_ledger.get(request_seq)
        except BindingError as exc:
            raise HTTPException(status_code=500, detail=f"binding integrity failure: {exc}")
        if binding is None:
            raise HTTPException(status_code=404, detail="no binding at seq")
        return binding

    @app.post("/signoff/{request_seq}/sign")
    def post_signoff_sign(request_seq: int, body: SignoffSignIn) -> dict:
        if signoff_gate is None:
            raise HTTPException(status_code=404, detail="structured cell not enabled")
        result = signoff_gate.sign_off(
            request_seq=request_seq,
            operator_id=body.operator_id,
            rationale=body.rationale,
        )
        return {"seq": result.seq, "cleared": result.cleared}

    @app.get("/governance/override-rate")
    def override_rate() -> dict:
        # Threshold/window/floor come from ADR-0002 policy constants — NOT query
        # params — so the gate an agent is measured against cannot be tuned by it.
        res = evaluate_override_rate(
            verified_governance_records(),
            threshold=params.OVERRIDE_RATE_THRESHOLD,
            window=params.OVERRIDE_RATE_WINDOW,
            min_sample=params.OVERRIDE_RATE_MIN_SAMPLE,
        )
        return {
            "status": res.status.value,
            "rate": res.rate,
            "sample_size": res.sample_size,
        }

    # --- SEI lineage-spine read surfaces (WP-5.2) ---
    # Pull-only, on-demand: each held SEI is re-resolved against Clarion when the
    # surface is hit. Detection consumes verified_governance_records() — the
    # protected store (HMAC-verified, fail-closed) when a protected gate is wired,
    # the engine store otherwise — so PROTECTED attestations are orphan-detectable.
    # A tampered protected trail raises HTTP 500 before any scan is attempted.
    # When no client is wired there is nothing stable to probe.

    @app.get("/governance/identity-gaps")
    def identity_gaps() -> list[dict]:
        if identity is None or identity.client is None:
            return []
        gaps = find_orphan_gaps(verified_governance_records(), identity.client)
        return [{"sei": g.sei, "reason": g.reason, "lineage": g.lineage} for g in gaps]

    @app.get("/governance/lineage-integrity")
    def lineage_integrity() -> dict:
        if identity is None or identity.client is None:
            return {"divergences": []}
        divs = find_lineage_divergence(verified_governance_records(), identity.client)
        return {"divergences": [
            {"sei": d.sei, "recorded_length": d.recorded_length,
             "current_length": d.current_length} for d in divs]}

    # --- agent-programmable policy grammar (WP-4.1) ---

    @app.post("/policy/evaluate")
    def policy_evaluate(body: PolicyEvalIn) -> dict:
        ev = grammar_().evaluate(body.policy, body.target)
        if ev.result is PolicyResult.UNKNOWN:
            # Honest event + provenance gap — never a silent false-green.
            engine().record_event(
                {
                    "event": "UNKNOWN_POLICY",
                    "policy": ev.policy,
                    "detail": ev.detail,
                    "provenance_gap": True,
                }
            )
        return {
            "policy": ev.policy,
            "result": ev.result.value,
            "detail": ev.detail,
            "provenance_gap": ev.provenance_gap,
        }

    # --- wardline suite-combination surface (WP-6.1) ---

    @app.post("/wardline/scan-results")
    def wardline_scan_results(body: ScanResultsIn) -> dict:
        if (body.cell is None) == (body.cell_by_severity is None):
            raise HTTPException(status_code=422,
                                detail="provide exactly one of cell or cell_by_severity")
        if body.cell_by_severity is not None and not body.cell_by_severity:
            raise HTTPException(status_code=422, detail="cell_by_severity must not be empty")

        def resolve(qualname: str | None) -> tuple[EntityKey, dict]:
            # Use the one resolve-then-key boundary so a wardline-routed override
            # captures the clarion lineage snapshot like every other write path.
            if qualname:
                return resolve_for_record(qualname)
            return EntityKey.from_locator("unknown"), {}

        try:
            if body.cell_by_severity is not None:
                cell_map = {WardlineSeverity[sev]: WardlineCellPolicy(cell)
                            for sev, cell in body.cell_by_severity.items()}
                # SURFACE_OVERRIDE is always reachable via the unmapped-severity fallback.
                cells = set(cell_map.values()) | {WardlineCellPolicy.SURFACE_OVERRIDE}
            else:
                policy = WardlineCellPolicy(body.cell)
                cells = {policy}
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"unknown cell/severity: {exc}")

        # Only provision the governance store when a surface cell can actually run:
        # engine() lazily creates legis-governance.db, so a pure block_escalate scan
        # must not touch it. signoff_gate is an injected param (no side effect).
        needs_engine = bool(cells & {WardlineCellPolicy.SURFACE_OVERRIDE,
                                     WardlineCellPolicy.SURFACE_ONLY})
        kwargs: dict = {"agent_id": body.agent_id, "resolve": resolve,
                        "engine": engine() if needs_engine else None,
                        "signoff": signoff_gate}
        if body.cell_by_severity is not None:
            kwargs["cell_map"] = cell_map
        else:
            kwargs["policy"] = policy

        try:
            routed = route_findings(active_defects(body.scan), **kwargs)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"routed": routed}

    return app
