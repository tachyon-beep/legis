# Cluster E — Agent/CLI Frontends

Two of the three Legis frontends. The HTTP API (`api/app.py`) is the third,
covered by another explorer. All three are *supposed* to route governance
decisions through the transport-agnostic `service/` layer.

---

## CLI Frontend

**Location:** `src/legis/cli.py` (~161 stmts), `src/legis/__init__.py`

**Responsibility:** Provides the `legis` console script — an argparse dispatcher that runs the HTTP server, launches the MCP stdio server, executes governance CI gates (override-rate, policy-boundary), and runs the SEI backfill — wiring CLI flags into the environment variables the frontends read.

**Key Components:**
- `cli.py:build_parser` (32–143) — declares six subcommands: `serve`, `mcp`, `check-override-rate`, `governance-gate`, `sei-backfill`, `policy-boundary-check`.
  - `serve` (36–63, dispatch 254–271) — sets `LEGIS_*`/`LOOMWEAVE_API_URL`/`FILIGREE_API_URL` env from flags, then `uvicorn.run("legis.api.app:create_app", factory=True)`.
  - `mcp` (65–87, dispatch 287–303) — requires `--agent-id`, sets env, then calls `legis.mcp.main(agent_id)`. This is the launch-bound identity boundary for the MCP server.
  - `check-override-rate` / `governance-gate` (91–106, dispatch 273–274) — both route to `_check_override_rate`; exit 1 on FAIL for CI.
  - `sei-backfill` (107–130, dispatch 276–285) — resolves legacy locator-keyed records through Loomweave batch resolve (dry-run unless `--execute`).
  - `policy-boundary-check` (132–141, dispatch 305–314) — fails when `@policy_boundary` metadata lacks current behavioural evidence; text or json output.
- `cli.py:_check_override_rate` (170–244) — the override-rate CI gate. **Reads the audit store directly** (`AuditStore(db_url).read_all()`, 194/199), inlines its own protected-record detection (`_requires_protected_verification`, 206–215), builds its own `TrailVerifier` and calls `verify()` (228–231), then `evaluate_override_rate` (236). Fail-closed on missing DB under CI (177–192) and on protected records without `LEGIS_HMAC_KEY` (220–226).
- `cli.py:_apply_judge_env` (159–167) — maps `--judge-*` flags onto `LEGIS_JUDGE_*` env for both `serve` and `mcp`.
- `__init__.py` (3) — `__version__ = "1.0.0rc2"`; consumed by `mcp.py` serverInfo.

**Dependencies:**
- Inbound: console-script entry point (`legis = legis.cli:main`); top-level operator/CI invocation. No in-tree importers.
- Outbound (module-level + dispatch-time):
  - `cli -> uvicorn` (`cli.py:6`, run target at 270)
  - `cli -> legis.api.app:create_app` (`cli.py:270`, sibling frontend, by factory string)
  - `cli -> legis.mcp.main` (`cli.py:301`, sibling frontend — CLI launches the MCP server)
  - `cli -> legis.clock.SystemClock` (`cli.py:8`)
  - `cli -> legis.governance.sei_backfill.run_pre_sei_backfill` (`cli.py:9`)
  - `cli -> legis.identity.loomweave_client` (`cli.py:10`)
  - `cli -> legis.policy.boundary_scan.scan_policy_boundaries` (`cli.py:11`)
  - `cli -> legis.store.audit_store.AuditStore` (`cli.py:12`, also 194)
  - `cli -> legis.enforcement.lifecycle` (GateStatus, evaluate_override_rate) (`cli.py:172`)
  - `cli -> legis.governance.params` (`cli.py:173`)
  - `cli -> legis.enforcement.protected` (TrailVerifier, TamperError) (`cli.py:228`)
  - `cli -> legis.service.*` — **NONE** (verified: `grep legis.service src/legis/cli.py` → 0 hits).

**Patterns Observed:**
- Env-var seam: every subcommand translates flags into `LEGIS_*` env vars, then defers to a frontend/service that re-reads env. Flags never pass through function arguments to the server, so server and CLI share one configuration surface.
- Lazy local imports inside dispatch branches (`enforcement.lifecycle`, `enforcement.protected`, `legis.mcp`) keep import cost and store side-effects off the cold path.
- Fail-closed CI posture: missing DB, integrity-chain failure, and unverifiable protected records all return exit 1 (guarded by `CI=true` / `LEGIS_ALLOW_MISSING_GOVERNANCE_DB`).

**Concerns:**
- **Service-layer bypass (adapter drift, CLI side).** `_check_override_rate` (170–244) routes through *no* `service.*` function. It hand-rolls a parallel copy of `service.verified_records` (store read + `TrailVerifier.verify`, 199/228–231) and of `service.compute_override_rate` (inline `evaluate_override_rate` with the `params.*` constants, 236–241). MCP's `override_rate_get` (mcp.py:1023) *does* go through `service.compute_override_rate(_verified_records(...))`. So the CLI and MCP read the same gate two different ways. This duplication already forced a divergent fix: commit `07cf54e "fix(cli): fail closed on protected override-rate trails"` patched the CLI's inline protected-verification path alone. Recommend collapsing `_check_override_rate` onto `service.verified_records` + `service.compute_override_rate`.
- `import os` appears inside three dispatch branches (255, 288) and helpers (89, 160, 171) rather than at module top — harmless but inconsistent.
- No structured logging/observability around gate outcomes; results are `print`-only.

**Confidence:** High — Read cli.py in full (318 lines) and `__init__.py` in full. Verified the service-bypass claim with `grep legis.service src/legis/cli.py` (0 hits) and cross-checked the MCP counterpart at mcp.py:1023. Every dependency edge is a literal import statement cited by line. Cross-referenced commit `07cf54e` to confirm the duplication already drove a CLI-only fix.

---

## MCP Server Frontend

**Location:** `src/legis/mcp.py` (~464 stmts — the largest module in the cluster)

**Responsibility:** A stdlib-only, hand-rolled MCP-over-stdio JSON-RPC server (protocols `2024-11-05` / `2025-03-26`) that exposes Legis governance + git/CI read tools to agents under a launch-bound `agent_id`, mapping each tool call onto the transport-agnostic `service/` layer (or, for read surfaces, directly onto the owning surface).

**Key Components:**
- `McpRuntime` dataclass (81–98) — per-launch state: `agent_id`, lazily-built engine/gates/surfaces, `trail_verifier`, `wardline_artifact_key`, `binding_ledger`.
- `build_runtime` (114–173) — wires gates only when `LEGIS_HMAC_KEY` is present: `TrailVerifier`, `ProtectedGate`, `SignoffGate`, and `BindingLedger` are all constructed together under the same key (133–152), so there is no "gate without verifier" hole.
- `tool_definitions` (185–307) — JSON schemas; every schema is built via `_schema` (176–182) with `additionalProperties: False`.
- `call_tool` (676–1036) — the dispatch table. Begins with `_validate_argument_keys` (678).
- `handle_request` / `run_jsonrpc` / `main` (1039–1123) — JSON-RPC framing, `initialize` gating, protocol negotiation.

**MCP tools and their routing (Task #1):**

| Tool | Routes through `service/`? | Target |
|------|---------------------------|--------|
| `policy_explain` | service | `service.explain.explain_policy` (680) |
| `override_submit` | service | `service.governance.submit_override` / `submit_protected_override` / `request_signoff` (743/771/808) |
| `policy_evaluate` | service | `service.governance.evaluate_policy` (848) |
| `scan_route` | service | `service.wardline.route_wardline_scan` (916) |
| `override_rate_get` | service | `service.governance.compute_override_rate` over `_verified_records` (1023–1024) |
| `signoff_status_get` | **direct** | `runtime.signoff_gate` (`enforcement.signoff`) — `request_record`/`is_cleared` (831–845) |
| `filigree_closure_gate_get` | **direct** | `governance.filigree_gate.evaluate_issue_closure` over `binding_ledger` (968–975) |
| `git_branch_list` / `git_commit_get` / `git_rename_list` | **direct** | `git.surface.GitSurface` (936–954) |
| `git_rename_feed_get` | **direct** | `git.rename_feed.build_rename_feed` (956–966) |
| `pull_request_get` | **direct** | `pulls.surface.PullSurface` (+ `checks.surface`) (977–990) |
| `check_list` | **direct** | `checks.surface.CheckSurface` (992–1021) |

The five governance-decision tools all route through `service/`. The read/poll surfaces (`signoff_status_get`, `filigree_closure_gate_get`, `git_*`, `pull_request_get`, `check_list`) reach their owning surface directly — consistent with the HTTP adapter, which does the same for read surfaces.

**Dependencies:**
- Inbound: `legis.cli` only (`cli.py:301 from legis.mcp import main`). The MCP server is launched exclusively by the CLI's `mcp` subcommand.
- Outbound (module-level unless noted):
  - `mcp -> legis.api.app` — **sibling-frontend coupling.** Imports `DEFAULT_GOVERNANCE_DB` (`mcp.py:115`, `mcp.py:496`) and `DEFAULT_CHECK_DB` (`mcp.py:505`) from the *HTTP adapter* module for default DB URLs. (See Concerns.)
  - `mcp -> legis.service.governance` (compute_override_rate, evaluate_policy, submit_override, submit_protected_override, request_signoff, verified_records) (`mcp.py:45`)
  - `mcp -> legis.service.wardline.route_wardline_scan` (`mcp.py:53`)
  - `mcp -> legis.service.explain.explain_policy` (`mcp.py:44`)
  - `mcp -> legis.service.errors` (`mcp.py:37`)
  - `mcp -> legis.enforcement.engine.EnforcementEngine` (`mcp.py:23`, 499)
  - `mcp -> legis.enforcement.protected` (ProtectedGate, TrailVerifier, TamperError) (`mcp.py:25`)
  - `mcp -> legis.enforcement.signoff.SignoffGate` (`mcp.py:26`)
  - `mcp -> legis.enforcement.judge_factory.build_judge_from_env` (`mcp.py:24`)
  - `mcp -> legis.enforcement.verdict` (SignoffState, Verdict) (`mcp.py:27`)
  - `mcp -> legis.governance.binding_ledger` (BindingError; BindingLedger lazy at 146) (`mcp.py:29`)
  - `mcp -> legis.governance.filigree_gate.evaluate_issue_closure` (lazy, `mcp.py:969`)
  - `mcp -> legis.policy.cells` / `legis.policy.grammar` (`mcp.py:30–35`)
  - `mcp -> legis.wardline.governor` / `legis.wardline.ingest` (`mcp.py:55–56`)
  - `mcp -> legis.git.surface.GitSurface`, `legis.git.rename_feed.build_rename_feed` (`mcp.py:28`, lazy 957)
  - `mcp -> legis.pulls.surface.PullSurface`, `legis.checks.surface.CheckSurface`, `legis.checks.models.CheckRun` (`mcp.py:36/20/21`)
  - `mcp -> legis.store.audit_store.AuditStore` (`mcp.py:54`)
  - `mcp -> legis.identity.*` (lazy in build_runtime, `mcp.py:122`)
  - `mcp -> legis.canonical.content_hash` (`mcp.py:19`)

**Patterns Observed:**
- Service-routing for decisions, direct-surface for reads (table above). Governance writes always cross the `service/` seam; cheap reads do not.
- Launch-bound identity: `agent_id` is supplied once at process start; tool schemas never accept actor identity (module docstring 1–7, enforced because every `submit_*` call passes `agent_id=runtime.agent_id`).
- Lazy resource construction (`_engine`/`_checks`/`_pulls`/`_git`, 486–518) so a protected-only deployment never initialises the simple-tier store.
- Discriminated outcome envelopes + structured recovery hints (`_tool_error` / `_recovery_for`, 317–345); per-cell payload shapers (`_judged_result_payload`, 532–559).
- Idempotency-replay machinery: request-hash binding + recorded-outcome replay (`_override_idempotency_request_hash` 562–583, `_existing_idempotent_record` 586–598, `_idempotent_override_response` 601–631).

**Concerns:**

*Adapter-drift audit verdicts (against current source — most important output):*

- **C2 — RESOLVED.** MCP `scan_route` no longer blindly honors caller-chosen `cell`/`severity_map`/`fail_on`. The handler reads server routing from `LEGIS_WARDLINE_CELL` / `LEGIS_WARDLINE_CELL_BY_SEVERITY` (863–864) and, when server routing is configured, rejects any caller-supplied `cell`/`severity_map`/`fail_on` with `INVALID_CELL_SPEC` (872–876). Caller-chosen routing is only reachable behind the `LEGIS_UNSAFE_WARDLINE_REQUEST_ROUTING=1` escape hatch (878–894). This mirrors the HTTP handler `app.py:752–777` line-for-line. *Caveat:* the bypass is closed **behaviorally in `call_tool`**, not at the schema — the `scan_route` inputSchema still advertises `cell`/`severity_map`/`fail_on` as accepted properties (241–249), and the M9 key-validator therefore lets them through to the runtime guard. The guard, not the schema, is what enforces server-owned routing.

- **C3 — RESOLVED.** Protected-trail reads now go through the HMAC `TrailVerifier`. `_verified_records` (649–673), when `protected_gate` is wired, delegates to `service.governance.verified_records(protected_gate, trail_verifier, lambda: [])` (651), which calls `trail_verifier.verify(records)` and raises `AuditIntegrityError` on `TamperError` (service/governance.py:86–90). `build_runtime` always constructs `trail_verifier` together with `protected_gate` under the same key (141–143), so there is no "gate set, verifier None" gap. The unkeyed-hash-chain-only read path is gone.

- **H1 — RESOLVED.** MCP now passes the configured Wardline artifact key into routing. `scan_route` supplies `artifact_key=runtime.wardline_artifact_key or os.environ["LEGIS_WARDLINE_ARTIFACT_KEY"]` (925–932); `route_wardline_scan` calls `verify_wardline_artifact(scan, artifact_key)` (service/wardline.py:36), which, when a key is present, *requires* signed scanner/rule-set/commit/tree provenance and a verifying `artifact_signature`, raising `WardlinePayloadError` otherwise (ingest.py:86–107). Matches the HTTP path (app.py:818–822).

- **M9 — RESOLVED.** Schemas claim `additionalProperties:false` (`_schema`, 179) *and* dispatch enforces it. `call_tool` calls `_validate_argument_keys(name, args)` as its first action (678); that helper diffs supplied keys against the schema's declared properties and raises `InvalidArgumentError("unexpected argument(s) …")` for any extra (375–382). Unknown keys are now rejected rather than silently ignored.

- **M10 — RESOLVED.** The handle/seq type contract is now internally consistent. `override_submit` returns `poll_handle: signoff.seq` (791) where `SignoffResult.seq: int` (enforcement/signoff.py:25), and `signoff_status_get` declares `seq` as `{"type":"integer"}` (224 via the shared `integer` schema, 187). The reader `_require_int` (413–426) additionally tolerates an integer-valued *string*, so a caller round-tripping the int handle (or a stringified copy) both validate. No int-vs-string mismatch remains.

- **M11 — RESOLVED.** `override_submit` now has idempotency protection (commit `b4285dc "fix: scope MCP idempotency replays"`, mcp.py +57 lines). When an `idempotency_key` is supplied, the handler computes a request hash binding agent/policy/entity/rationale/cell/fingerprint/ast_path (562–583), looks for a prior record with the same key (734–741), replays the recorded outcome on match (`_idempotent_override_response`, 601–631), and raises `InvalidArgumentError` if the same key is reused for a *different* request (595–597). Replay lookups read the verified trail (`_verified_records`, 589), so the protection is fail-closed against tampering.

*Non-drift concerns:*
- **Sibling-frontend coupling.** MCP imports DB-default constants (`DEFAULT_GOVERNANCE_DB`, `DEFAULT_CHECK_DB`) from `legis.api.app` (115/496/505) — the HTTP adapter. Two peer frontends should not depend on each other for shared configuration; these constants belong in a shared config/store module. Architecturally the cleanest single coupling to break in this cluster.
- Hand-rolled JSON-RPC framing (`run_jsonrpc`, 1101–1118) with no message-size bound on a stdin line; acceptable for launch-bound local stdio but worth noting.
- The 464-stmt `call_tool` is a single long if/elif dispatch (676–1034); readable but a candidate for table-driven dispatch as the tool count grows.

**Confidence:** High — Read mcp.py in full (1123 lines). Each adapter-drift verdict was cross-validated against the actual enforcement target: C2 against the HTTP handler (app.py:752–777); C3 against `service/governance.py:81–91`; H1 against `service/wardline.py:36` + `wardline/ingest.py:67–107`; M10 against `enforcement/signoff.py:25`; M11 against commit `b4285dc` (`git show --stat`). Tool-routing table built by reading every dispatch branch. The `api.app` coupling confirmed with `grep "from legis.api" src/legis/mcp.py`.
