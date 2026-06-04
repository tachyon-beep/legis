# Full Agent-Facing MCP Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the v1 Legis MCP surface from the ratified MCP design spec, with unified override submission, sign-off polling, policy evaluation, Wardline routing, read tools, and guarded agent identity.

**Architecture:** Keep `src/legis/mcp.py` as the stdlib JSON-RPC adapter and route behavior through existing service/domain objects. The adapter owns MCP schemas, argument validation, tool result envelopes, and launch-bound `agent_id`; governance semantics remain in the enforcement, policy, Wardline, git, checks, and pull surfaces.

**Tech Stack:** Python 3.12, stdlib JSON-RPC-over-stdio adapter, pytest, mypy.

---

### Task 1: Contract Tests For The Full Tool Catalog

**Files:**
- Modify: `tests/mcp/test_server.py`
- Modify: `src/legis/mcp.py`

- [x] **Step 1: Write failing tests**

Add tests that assert `tools/list` exposes only the agent-facing v1 tools:
`policy_explain`, `override_submit`, `signoff_status_get`, `policy_evaluate`,
`scan_route`, `git_branch_list`, `git_commit_get`, `git_rename_list`,
`pull_request_get`, `check_list`, and `override_rate_get`.

- [x] **Step 2: Verify RED**

Run: `uv run pytest tests/mcp/test_server.py -q`
Expected before implementation: failure because only the WP-M3 catalog exists.

- [x] **Step 3: Implement catalog**

Update `tool_definitions()` in `src/legis/mcp.py` with the full tool set and
assert no `agent_id` or `operator_id` inputs are accepted.

### Task 2: Unified Override Submission

**Files:**
- Modify: `tests/mcp/test_server.py`
- Modify: `src/legis/mcp.py`

- [x] **Step 1: Write failing tests**

Cover chill `ACCEPTED_SELF`, coached `ACCEPTED_BY_JUDGE` and `BLOCKED`,
structured `ESCALATED_PENDING`, protected `NEED_INPUTS`, protected `BLOCKED`,
launch-bound attribution, and `blocked_reason_code`.

- [x] **Step 2: Verify RED**

Run: `uv run pytest tests/mcp/test_server.py -q`
Expected before implementation: failures on non-chill cells and unknown polling/read tools.

- [x] **Step 3: Implement routing**

Route `override_submit` through `explain_policy()`, `submit_override()`,
`request_signoff()`, and `submit_protected_override()` and return the
discriminated MCP outcome envelope.

### Task 3: Discovery, Polling, Evaluation, Wardline, And Reads

**Files:**
- Modify: `tests/mcp/test_server.py`
- Modify: `src/legis/mcp.py`

- [x] **Step 1: Write failing tests**

Cover `policy_explain`, `signoff_status_get`, `policy_evaluate`, `scan_route`,
`git_branch_list`, `git_commit_get`, `git_rename_list`, `pull_request_get`,
`check_list`, and `override_rate_get`.

- [x] **Step 2: Implement adapters**

Add runtime dependencies for git, pulls, grammar, checks, source root, protected
gate, sign-off gate, and cell registry. Keep all operator-only tools absent.

### Task 4: Verification

**Files:**
- Verify: `src/legis/mcp.py`
- Verify: `tests/mcp/test_server.py`

- [x] **Step 1: Run focused MCP suite**

Run: `uv run pytest tests/mcp/test_server.py -q`
Observed: `16 passed`.

- [x] **Step 2: Run adjacent dependency suite**

Run: `uv run pytest tests/mcp/test_server.py tests/service/test_governance.py tests/service/test_explain.py tests/enforcement/test_protected_submit.py tests/enforcement/test_signoff.py tests/git/test_git_surface.py tests/pulls/test_pull_surface.py tests/checks/test_check_surface.py tests/wardline/test_coached_routing.py tests/policy/test_cells.py -q`
Observed: `74 passed`.

- [x] **Step 3: Run release checks**

Run: `uv run pytest -q`
Observed: `357 passed`.

Run: `uv run mypy`
Observed: `Success: no issues found in 55 source files`.
