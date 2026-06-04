# SEI Batch Resolve And Pre-SEI Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consume Clarion `POST /api/v1/identity/resolve:batch` and provide an operator-invoked append-only migration path for legacy locator-keyed governance records.

**Architecture:** Add batch resolution to `HttpClarionIdentity`, then add a governance backfill service that reads `AuditStore` records, resolves locator keys in one batch, and appends explicit backfill events. The audit log stays append-only; no historical row is updated or deleted. A CLI command drives dry-run and execute modes.

**Tech Stack:** Python 3.12, stdlib urllib Clarion client, SQLAlchemy-backed `AuditStore`, pytest, mypy.

---

### Task 1: Clarion Batch Resolve Client

**Files:**
- Modify: `src/legis/identity/clarion_client.py`
- Modify: `tests/identity/test_clarion_client.py`

- [x] **Step 1: Write the failing client test**

Add a test asserting `HttpClarionIdentity.resolve_batch(["python:function:m.f", "python:function:gone"])` POSTs to `/api/v1/identity/resolve:batch` with `{"locators": [...]}` and returns the documented `resolved` / `invalid` / `not_found` object unchanged.

- [x] **Step 2: Run RED**

Run: `uv run pytest tests/identity/test_clarion_client.py::test_resolve_batch_posts_locators_to_clarion_batch_endpoint -q`
Expected: fail with `AttributeError: 'HttpClarionIdentity' object has no attribute 'resolve_batch'`.

- [x] **Step 3: Implement batch client**

Add `resolve_batch(self, locators: list[str]) -> dict[str, Any]` to the `ClarionIdentity` protocol and `HttpClarionIdentity`, posting to `/api/v1/identity/resolve:batch`.

### Task 2: Append-Only Governance Backfill Service

**Files:**
- Create: `src/legis/governance/sei_backfill.py`
- Create: `tests/governance/test_sei_backfill.py`

- [x] **Step 1: Write failing service tests**

Cover:
- alive locator creates a `SEI_BACKFILL` event with `entity_key.identity_stable:true`;
- not-found locator creates a `SEI_BACKFILL_UNRESOLVED` event with `identity_stable:false`;
- stable SEI-keyed records are skipped;
- rerunning after an executed pass appends no duplicates.

- [x] **Step 2: Run RED**

Run: `uv run pytest tests/governance/test_sei_backfill.py -q`
Expected: fail because `legis.governance.sei_backfill` does not exist.

- [x] **Step 3: Implement service**

Implement `run_pre_sei_backfill(store, client, clock, dry_run=True, actor="legis-sei-backfill") -> SeiBackfillReport`. It verifies audit integrity, collects eligible `identity_stable:false` locator records with no previous backfill event, calls `client.resolve_batch`, and appends events only when `dry_run=False`.

### Task 3: CLI Entrypoint

**Files:**
- Modify: `src/legis/cli.py`
- Modify: `tests/test_cli.py`

- [x] **Step 1: Write failing CLI tests**

Assert `legis sei-backfill --db sqlite:///gov.db --clarion-url http://localhost --execute` parses and dispatches the service, and dry-run is the default.

- [x] **Step 2: Implement CLI**

Add `sei-backfill` with `--db`, `--clarion-url`, `--execute`, and `--actor`. It constructs `AuditStore`, `HttpClarionIdentity`, and `SystemClock`, then prints the report as sorted JSON.

### Task 4: Verification

**Files:**
- Verify: `src/legis/identity/clarion_client.py`
- Verify: `src/legis/governance/sei_backfill.py`
- Verify: `src/legis/cli.py`

- [x] **Step 1: Focused tests**

Run: `uv run pytest tests/identity/test_clarion_client.py tests/governance/test_sei_backfill.py tests/test_cli.py -q`
Observed: targeted RED tests failed before implementation; affected suite later passed with `58 passed`.

- [x] **Step 2: Release checks**

Run: `uv run pytest -q`
Observed: `363 passed`.

Run: `uv run mypy`
Observed: `Success: no issues found in 56 source files`.
