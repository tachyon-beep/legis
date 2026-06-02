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
from legis.git.surface import GitError, GitSurface
from legis.identity.entity_key import EntityKey

DEFAULT_CHECK_DB = "sqlite:///legis-checks.db"
DEFAULT_GOVERNANCE_DB = "sqlite:///legis-governance.db"


class OverrideIn(BaseModel):
    policy: str
    entity: str  # a locator today (pre-SEI); identity_stable=False
    rationale: str
    agent_id: str


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
) -> FastAPI:
    app = FastAPI(title="legis", version=__version__)
    state: dict[str, object | None] = {
        "checks": check_surface,
        "enforcement": enforcement,
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
        result = engine().submit_override(
            policy=body.policy,
            entity_key=EntityKey.from_locator(body.entity),
            rationale=body.rationale,
            agent_id=body.agent_id,
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

    @app.get("/overrides")
    def get_overrides() -> list[dict]:
        return engine().trail()

    return app
