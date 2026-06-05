# Legis Home Closeout — Design

**Date:** 2026-06-05
**Status:** Approved (brainstorming), pending implementation plan
**Supersedes/extends:** `docs/superpowers/plans/2026-06-04-legis-p0-agent-utility-remediation.md` (Workstreams B/C/D)

## Goal

Bring Legis home — it is the only piece of the Legis/Loomweave/Filigree governance
ecosystem still lagging. Close the three remaining **legis-side** deliverables so
the cross-repo governance handshakes become real rather than latent:

1. A policy-boundary CI gate that is **not trickable** — the static scanner must
   apply the *same* evidence rules as the runtime gate.
2. The Loomweave-ready git **rename feed** endpoint (committed + optional
   working-tree renames).
3. The Filigree **closure-gate** endpoint that reports whether Legis holds
   verified binding evidence for closing an issue.

Filigree's *consumption* of the closure gate (and any Loomweave re-pointing) is
explicitly a **follow-on spec**, not this one.

## Context discovered during brainstorming

- **The rename half is already partially wired.** Legis already serves
  `GET /git/renames` (`src/legis/api/app.py:407`, MCP `src/legis/mcp.py:864`).
  Loomweave already has a complete consumer — `LegisGitRenameSource` in
  `loomweave/crates/loomweave-cli/src/sei_git.rs` reads
  `GET /git/renames?rev_range=<base>..HEAD` with a `/health` probe, timeout, and
  graceful degradation. Loomweave consumes **committed** renames only and documents
  that working-tree renames "never reach legis."
- **Decision:** build the richer `/git/rename-feed` (working-tree support) anyway
  as future-proofing, but keep it **additive** — `/git/renames` stays untouched so
  Loomweave keeps working unchanged. Re-pointing Loomweave is out of scope.
- **The closure-gate half is genuinely missing on both sides.** Filigree
  (`src/filigree/`, Python) has `close_issue` (`db_issues.py:1117`) and
  `api_close_issue` (`dashboard_routes/issues.py:506`) but **no reference to Legis
  or any governance gate**. Legis does not serve `/filigree/issues/{id}/closure-gate`
  yet.
- **The static scanner is trickable** (code-review finding, verified). It checks
  "test calls subject" (`_test_calls_subject`) and "test mentions policy"
  (`_test_mentions_policy`) **independently**. The runtime gate
  (`check_policy_boundary` in `src/legis/policy/decorator.py`) requires boundary
  evidence and a policy reference to **co-occur in the same `assert`**, plus it
  detects name **shadowing** and tracks **call-result** variables. The two gates
  that should agree, don't — and the CI-facing one is the weaker.

## Architecture

Four workstreams, each independently testable. TDD per workstream.

### Workstream 1 — Policy-boundary honesty gate (convergence + CLI + CI)

The defect is that the static scanner and runtime gate implement the same
intent with divergent logic. Fix it by **extracting one shared evaluator** that
both call, so they agree by construction and cannot drift again.

- **New module `src/legis/policy/evidence.py`:**
  `evaluate_test_evidence(parsed_test, boundary_names, suppresses) -> GateFinding`.
  Move the helpers currently nested inside `check_policy_boundary`
  (`_name_targets`, `_is_boundary_call`, `_contains_boundary_call`,
  `_contains_policy_reference`) to module scope, parameterized by
  `boundary_names` / `suppresses` instead of closing over `meta`. The evaluator
  encapsulates: shadowing detection, call-result tracking, and the same-assert
  co-occurrence requirement.
- **`src/legis/policy/decorator.py`:** `check_policy_boundary` keeps all its
  metadata-integrity checks (qualname, source citation, invariant, `test_ref`,
  fingerprint) and delegates the *test-evidence* portion to
  `evaluate_test_evidence`. **Behavior-preserving** — guarded by the existing
  passing runtime tests.
- **`src/legis/policy/boundary_scan.py`:** delete `_test_calls_subject` and
  `_test_mentions_policy`; call `evaluate_test_evidence` instead. A non-ok verdict
  maps to a `POLICY_BOUNDARY_TEST_WEAK` finding carrying the evaluator's reason.
  Existing rule_ids (missing `test_ref`, drifted fingerprint, parse errors)
  are preserved.
- **CLI:** `legis policy-boundary-check --root src --repo-root . --format {text,json}`,
  exit code 1 when findings exist, 0 otherwise, in `src/legis/cli.py`.
- **CI:** a step in `.github/workflows/ci.yml` after mypy:
  `uv run legis policy-boundary-check --root src --repo-root .`.

### Workstream 2 — Git rename feed (Loomweave-ready, additive)

- **`src/legis/git/surface.py`:** add `GitSurface.working_tree_renames(base)`
  returning `list[RenameEvidence]` for `git diff -M --name-status <base>` rename
  rows (`commit_sha="WORKTREE"`), with the same ref-validation guard the existing
  methods use.
- **New module `src/legis/git/rename_feed.py`:**
  `build_rename_feed(repo_path, *, base, head="HEAD", include_worktree=False) -> dict`
  returning `{status, base, head, committed: [...], working_tree: [...]}` where
  `status` is `committed_only` or `committed_and_worktree`.
- **API:** `GET /git/rename-feed` in `src/legis/api/app.py` (query: `base`,
  `head`, `include_worktree`). Additive — `/git/renames` is unchanged.
- **MCP:** read-only `git_rename_feed_get` tool in `src/legis/mcp.py`, added to
  `_AGENT_TOOLS`.

### Workstream 3 — Filigree closure gate (legis side)

- **`src/legis/governance/binding_ledger.py`:** add
  `get_by_issue_id(issue_id) -> dict | None`, a verified lookup (today's `get` is
  keyed by `signoff_seq`).
- **New module `src/legis/governance/filigree_gate.py`:**
  `evaluate_issue_closure(ledger, *, issue_id) -> dict`. Pure decision function:
  calls `ledger.verify()` (a `BindingError` propagates to the caller), then
  returns `{allowed, issue_id, reason, evidence}`. `allowed=True` iff a verified
  binding record exists for the issue.
- **API:** `GET /filigree/issues/{issue_id}/closure-gate` in
  `src/legis/api/app.py`: 404 when the binding ledger is not enabled, 500 on
  `BindingError`, **409** when `allowed` is false, 200 when allowed.
- **MCP:** read-only `filigree_closure_gate_get` tool in `src/legis/mcp.py` plus a
  `binding_ledger` field on `McpRuntime`, initialized when `LEGIS_HMAC_KEY` is set
  (alongside `protected_gate` / `signoff_gate`).

### Workstream 4 — Verification + docs

- Full `uv run pytest -q`, `uv run mypy src/legis`, and the new
  `policy-boundary-check` gate all green.
- Dated implementation notes appended to the relevant P0 sections of:
  `docs/superpowers/specs/2026-06-02-not-yets-completion-design.md`,
  `docs/superpowers/specs/2026-06-01-legis-roadmap-to-first-class.md`,
  `docs/superpowers/specs/2026-06-03-legis-mcp-surface-design.md`.

## Testing strategy

TDD per workstream. Two tests are the keystones of Workstream 1:

- **Trickability regression:** a fixture that the *pre-refactor* scanner accepts
  but the *post-refactor* scanner rejects — a test that calls the subject once,
  mentions the policy only in a throwaway string, and asserts something that does
  *not* reference the policy. The new scanner must emit `POLICY_BOUNDARY_TEST_WEAK`.
- **Parity:** over a shared corpus of test functions, assert the static scanner
  and `check_policy_boundary` return the *same* allow/block verdict, so the two
  gates provably agree.

Other coverage: CLI exit-code/json tests; real-git committed + working-tree
rename-feed tests; closure-gate decision tests (no HTTP); API contract tests for
both new endpoints; MCP tool-exposure and call tests for both new tools.

## Error handling / fail-closed posture

- Scanner exits non-zero on any finding (CI-blocking).
- Closure gate is fail-closed: missing binding ⇒ blocked (409); hash-chain
  integrity failure ⇒ `BindingError` ⇒ 500; ledger disabled ⇒ 404.
- Rename feed is additive and read-only; the existing `/git/renames` contract is
  preserved verbatim.

## Acceptance criteria

- `legis policy-boundary-check --root src --repo-root .` exists, exits non-zero on
  stale/weak/drifted boundary evidence, and runs in CI.
- The static scanner and runtime gate return identical verdicts on a shared
  corpus (parity test green); the trickability regression is closed.
- `GET /git/rename-feed` and MCP `git_rename_feed_get` return committed and
  optional working-tree rename evidence; `/git/renames` is unchanged.
- `GET /filigree/issues/{issue_id}/closure-gate` and MCP
  `filigree_closure_gate_get` return a verified binding decision and block missing
  evidence (409).
- Full suite, mypy, and the new gate pass.

## Out of scope (follow-on specs)

- Filigree's `close_issue` / `api_close_issue` actually calling the closure gate.
- Loomweave re-pointing from `/git/renames` to `/git/rename-feed`.
- Live cross-repo handshake integration tests.
