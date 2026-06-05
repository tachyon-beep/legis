# Cluster D — Service Layer + HTTP API

## Service Layer
**Location:** `src/legis/service/`
**Responsibility:** Transport-agnostic governance business logic — the shared decision/enforcement primitives that the HTTP, MCP, and CLI frontends all route through, raising `ServiceError` subclasses (never `HTTPException`/JSON-RPC) so each adapter owns its own error translation.

**Key Components:**
- `__init__.py` (47 LOC) — Public re-export surface; defines the contract both adapters import (`evaluate_policy`, `compute_override_rate`, `submit_override`/`submit_protected_override`/`submit_operator_override`, `request_signoff`, `resolve_for_record`, `verified_records`, `explain_policy`, `route_wardline_scan`, error types).
- `errors.py` (28 LOC) — Domain exception taxonomy: `ServiceError` base + `AuditIntegrityError` (HTTP 500 / MCP `AUDIT_INTEGRITY_FAILURE`), `NotEnabledError` (gate not wired → 404), `NotFoundError`, `InvalidArgumentError` (→ 422). Adapters switch on type, never message text (`errors.py:8-28`).
- `governance.py` (248 LOC) — Core enforcement wrappers. `resolve_for_record` (`:29`) is the single resolve-then-key boundary (SEI-keyed via Loomweave `IdentityResolver`, locator-keyed standalone, emits `loomweave` extension with alive/content_hash/lineage). `verified_records` (`:63`) is the fail-closed verified-trail read (protected gate owns trail when wired, else simple-tier engine; `verify_integrity()` + `TrailVerifier.verify()` → `AuditIntegrityError` on tamper). `compute_override_rate` (`:95`) binds threshold/window/floor to ADR-0002 `params` constants — NOT caller input. `submit_override` (`:109`) wraps `EnforcementEngine.submit_override` (simple-tier chill/coached). `submit_protected_override` (`:140`) + `submit_operator_override` (`:174`) wrap `ProtectedGate.submit`/`.operator_override`, each gated by `verify_current_source_binding` + `require_verified_source_binding`. `request_signoff` (`:207`) wraps `SignoffGate.request`. `evaluate_policy` (`:230`) wraps `PolicyGrammar.evaluate` and records an `UNKNOWN_POLICY` provenance-gap event when result is UNKNOWN.
- `source_binding.py` (89 LOC) — Current-source fingerprint verification for protected submissions. `verify_current_source_binding` (`:31`) re-hashes the on-disk file under `source_root`, rejecting stale fingerprints (`InvalidArgumentError`) and path escapes (`:24-28`); returns `{status: verified|unverified}`. `require_verified_source_binding` (`:82`) fails closed only for source-shaped (`.py` locator) entities.
- `explain.py` (122 LOC) — `explain_policy` (`:57`) maps a policy→cell (chill/coached/structured/protected) into a `PolicyExplanation` (judge_inline, self_clearable, human_in_loop, enabled, available_moves, required_inputs). Pure discovery; drives the MCP `policy_explain` tool. Not consumed by the HTTP API.

**Dependencies:**
- Inbound:
  - `src/legis/api/app.py:43-51` — HTTP adapter imports `compute_override_rate`, `evaluate_policy`, `resolve_for_record`, `submit_override`, `submit_protected_override`, `submit_operator_override`, `verified_records`, `route_wardline_scan`, and the three error types.
  - `src/legis/mcp.py:37-53` — MCP adapter imports the error types, `explain_policy`, the governance helpers (`:45`), and `route_wardline_scan` (`:53`). Note: MCP additionally imports `DEFAULT_GOVERNANCE_DB`/`DEFAULT_CHECK_DB` constants *from* `legis.api.app` (`mcp.py:115,496,505`) — an api→service-peer coupling worth flagging.
  - `cli.py` does NOT import `legis.service` directly; it launches the HTTP app (`cli.py:270` `legis.api.app:create_app`). CLI reaches the service layer transitively through HTTP, not in-process.
- Outbound (all file:line in `service/`):
  - `service -> legis.enforcement.engine` (`governance.py:14` EnforcementEngine/EnforcementResult; `explain.py:8`)
  - `service -> legis.enforcement.lifecycle` (`governance.py:15` evaluate_override_rate)
  - `service -> legis.enforcement.protected` (`governance.py:16` ProtectedGate/ProtectedResult/TamperError)
  - `service -> legis.enforcement.signoff` (`governance.py:17`, `wardline.py:10` SignoffGate)
  - `service -> legis.governance.params` (`governance.py:18` ADR-0002 rate constants)
  - `service -> legis.identity.entity_key` (`governance.py:19`, `wardline.py:11` EntityKey)
  - `service -> legis.identity.resolver` (`governance.py:20`, `wardline.py:12` IdentityResolver)
  - `service -> legis.policy.grammar` (`governance.py:21` PolicyGrammar/PolicyEvaluation/PolicyResult)
  - `service -> legis.policy.cells` (`explain.py:9` PolicyCellRegistry)
  - `service -> legis.canonical` (`wardline.py:8` content_hash)
  - `service -> legis.wardline.governor` (`wardline.py:14` WardlineCellPolicy/route_findings)
  - `service -> legis.wardline.ingest` (`wardline.py:15` verify_wardline_artifact/active_defects/wardline_artifact_fields/WardlineSeverity)
  - `service -> legis.wardline.policy` (`wardline.py:21` resolve_cell)
  - Internal: `governance.py:22-26` imports `service.errors` + `service.source_binding`; `wardline.py:13` imports `service.governance.resolve_for_record`.
  - No outbound dependency on `legis.store` (the engine/gate own their stores); service stays store-agnostic via duck-typed `protected_gate`/`trail_verifier` in `verified_records`.

**Patterns Observed:**
- Explicit-dependency injection: every helper takes its gates/engine/identity as parameters (no globals, no closures) — `governance.py:1-6` docstring states this as a rule.
- Keyword-only args after the positional gate (`submit_override(engine, *, ...)`) to prevent same-typed field transposition at the call site (`governance.py:126-128`).
- Fail-closed verification: `verified_records` and `require_verified_source_binding` raise rather than degrade.
- Policy constants sourced from `governance.params`, not caller input — gate-tuning resistance (`governance.py:98-106`).
- Duck-typing at the enforcement seam to avoid coupling to concrete gate types (`governance.py:77-80`).

**Concerns:**
- **M1 (source binding can be `unverified` yet still sign a protected record)** — REFINED. `require_verified_source_binding` (`source_binding.py:82-89`) only enforces verification when `_source_path_from_entity` returns non-None, i.e. the locator's pre-`:` segment ends in `.py`. A protected entity whose locator is NOT a `.py` source path (e.g. an opaque SEI or non-`.py` locator) yields `status: unverified` and passes the guard, then `submit_protected_override` (`governance.py:163`) still produces an HMAC-signed protected record carrying `source_binding={status: unverified, reason: "entity is not a Python source locator"}`. Provenance is recorded honestly, but the "current-source must match before signing" invariant only binds `.py`-shaped entities. Confirmed.
- **M2 (provenance gaps)** — `evaluate_policy` records an `UNKNOWN_POLICY` event with `provenance_gap: True` only when grammar returns UNKNOWN (`governance.py:239-247`); writer-supplied `target` facts are otherwise trusted without provenance. The gap-flagging is grammar-driven, not provenance-of-input-driven.
- `explain.py:71` `del entity` — the ratified tool contract accepts `entity` but v1 registry routes by policy only; a no-op parameter that could mislead callers into thinking entity affects routing (documented at `:67-70`).
- Error-type completeness: `NotFoundError` is exported and defined but not raised anywhere in `service/` (only `NotEnabledError`/`InvalidArgumentError`/`AuditIntegrityError` are). Reserved for adapter use.

**Confidence:** High — read 100% of all 6 service files; cross-validated inbound importers via grep across `src/` (`api/app.py:43-51`, `mcp.py:37-53`, `cli.py:270`) and outbound imports line-by-line. M1/M2 confirmed against `source_binding.py:82-89` and `governance.py:230-248`.

---

## HTTP API
**Location:** `src/legis/api/`
**Responsibility:** The FastAPI application factory (`create_app`) exposing the git/check operating-picture read surfaces plus the mutating governance surfaces (overrides, protected/operator overrides, sign-off, wardline scan routing, binding, closure-gate), enforcing bearer auth with writer/operator scopes and translating `ServiceError` subclasses into HTTP status codes.

**Key Components:**
- `__init__.py` (1 LOC) — package marker.
- `app.py` (830 LOC) — Single `create_app(...)` factory (`:277`); ~16 keyword DI params (repo_path, check/pull surfaces, enforcement engine, protected/signoff gates, trail_verifier, grammar, identity, filigree, binding_ledger, binding_key, pull sources). Lazy env-driven fallback wiring (`:296-347`): builds `IdentityResolver`, `FiligreeClient`, and — when `LEGIS_HMAC_KEY` is set — `AuditStore`, `TrailVerifier`, `ProtectedGate`, `SignoffGate`, `BindingLedger`. Auth helpers `_token_actor_from_mapping` (`:61`), `_verify_secret` (`:100`), `verify_writer`/`verify_operator` (`:138-143`). Pydantic request models `:150-225`.

**Routes table** (METHOD PATH | scope | delegates-to):

| METHOD PATH | scope | delegates-to |
|---|---|---|
| GET /health | none | inline (`:389`) |
| GET /git/branches | none | `GitSurface.branches` (`:395`) |
| GET /git/commits/{sha} | none | `GitSurface.commit` (`:402`) |
| GET /git/renames | none | `GitSurface.renames` (`:409`) |
| GET /git/rename-feed | none | `git.rename_feed.build_rename_feed` (`:416`) |
| GET /git/pull-requests/{number} | none | `PullRequestSource.get` + `checks().for_pr` (`:432`) |
| POST /git/pulls | **writer** | `PullSurface.record` (`:444`) |
| GET /git/pulls/{number} | none | `PullSurface.get` + `checks().for_pr` (`:452`) |
| POST /checks | **writer** | `CheckSurface.record` (`:464`) |
| GET /checks/commit/{sha} | none | `CheckSurface.for_commit` (`:470`) |
| GET /checks/branch/{name} | none | `CheckSurface.for_branch` (`:474`) |
| GET /checks/pr/{pr} | none | `CheckSurface.for_pr` (`:478`) |
| POST /overrides | **writer** | `service.submit_override` (`:484`) |
| GET /overrides | none | `service.verified_records` (`:522`) |
| POST /protected/overrides | **writer** | `service.submit_protected_override` (`:528`) |
| POST /protected/operator-override | **operator** | `service.submit_operator_override` (`:558`) |
| POST /signoff/request | **writer** | `SignoffGate.request` directly (NOT via `service.request_signoff`) (`:583`) |
| POST /signoff/{request_seq}/bind-issue | **writer** | `governance.bind_signoff_to_issue` (`:597`) |
| GET /signoff/{request_seq}/binding | none | `BindingLedger.get` (`:650`) |
| GET /filigree/issues/{issue_id}/closure-gate | none | `governance.filigree_gate.evaluate_issue_closure` (`:662`) |
| POST /signoff/{request_seq}/sign | **operator** | `SignoffGate.sign_off` directly (`:676`) |
| GET /governance/override-rate | none | `service.compute_override_rate` + `verified_records` (`:687`) |
| GET /governance/identity-gaps | none | `governance.gaps.find_orphan_gaps` + `verified_records` (`:704`) |
| GET /governance/lineage-integrity | none | `governance.gaps.find_lineage_integrity` (`:711`) |
| POST /policy/evaluate | **writer** | `service.evaluate_policy` (`:733`) |
| POST /wardline/scan-results | **writer** | `service.route_wardline_scan` (`:750`) |

**Dependencies:**
- Inbound:
  - `src/legis/cli.py:270` — `legis serve` launches `legis.api.app:create_app` via uvicorn (factory=True). CLI is the only in-process caller; it is a *launcher*, not a consumer.
  - `src/legis/mcp.py:115,496,505` — imports the `DEFAULT_GOVERNANCE_DB`/`DEFAULT_CHECK_DB` constants from `api.app` (constant reuse, not a runtime call). Flag: a sibling adapter depending on the HTTP adapter's module for shared defaults.
- Outbound (file:line in `app.py`):
  - `api -> legis.service.*` — `:43` errors, `:44-50` governance helpers, `:51` `route_wardline_scan` (primary business-logic seam).
  - `api -> legis.enforcement.engine` (`:31`), `legis.enforcement.protected` (`:32` ProtectedGate/TamperError/TrailVerifier), `legis.enforcement.signoff` (`:33` SignoffGate) — **direct reach-through**: the API constructs and calls these gates directly for sign-off (`:588`,`:680`) and trail verification (`:605-618`).
  - `api -> legis.checks.{models,surface}` (`:29-30`), `legis.pulls.{models,surface}` (`:53-54`), `legis.git.{pull_request,rename_feed,surface}` (`:34-36`).
  - `api -> legis.governance.*` — `gaps` (`:37`), `binding_ledger` (`:39`), `signoff_binding` (`:40` bind_signoff_to_issue), `filigree_gate` (lazy `:664`).
  - `api -> legis.filigree.client` (`:38`), `legis.identity.{entity_key,resolver}` (`:41-42`), `legis.policy.grammar` (`:52`), `legis.wardline.{governor,ingest}` (`:55-56`).
  - `api -> legis.store.audit_store` (lazy `:318,373`), `legis.clock.SystemClock` (lazy `:317,372`), `legis.enforcement.judge_factory` (lazy `:333`).

**Patterns Observed:**
- Application factory with exhaustive DI and lazy env-fallback construction; a no-arg app creates no state until a route needing a store is hit (`:358-384` lazy `checks()`/`pulls()`/`engine()`/`grammar_()`).
- Adapter error-translation: `NotEnabledError → 404`, `InvalidArgumentError → 422`, `AuditIntegrityError → 500`, `WardlinePayloadError → 422`, gate `ValueError → 409` (`:544-547`, `:824-827`, `:519-520`).
- ACCEPTED/BLOCKED → 201/409 status mapping so agents get the judge rationale either way (`:502-512`).
- Server-owned authority: override-rate constants, wardline routing cell, and the recorded actor are server-decided, not caller-supplied.
- Scope-gated dependencies via FastAPI `Depends(verify_writer|verify_operator)` — but the writer/operator split is enforced only in `LEGIS_API_TOKEN_ACTORS` mode; single-secret mode collapses both to one credential (see Concerns H7-adjacent).

**Concerns:**
- **C2/H1 (server-owned wardline routing + artifact HMAC) — HTTP is the reference and now has PARITY with MCP.** HTTP enforces: server routing wins and forbids caller routing fields (`:757-760` → 403); when no server routing, caller routing requires the unsafe escape hatch `LEGIS_UNSAFE_WARDLINE_REQUEST_ROUTING=1` (`:761-766` → 403); artifact HMAC via `LEGIS_WARDLINE_ARTIFACT_KEY` (`:818-822`, verified in `wardline.py:36` `verify_wardline_artifact`). CROSS-CHECK (HTTP-authoritative; MCP is another cluster's read): verification itself lives in the shared `route_wardline_scan` (`wardline.py:36`), so any caller of the seam gets artifact HMAC. A grep of `mcp.py:863-928` SUGGESTS MCP now mirrors all three (server_cell/server_routing gate, same `LEGIS_UNSAFE_WARDLINE_REQUEST_ROUTING` escape hatch, same artifact_key plumbing) — but this is a grep, not a full read of that cluster. Synthesis owns confirming the prior MCP-skips-this gap is actually closed; do not treat it as closed on my word.
- **H7 (unscoped API token entries grant operator authority) — REFINED/MITIGATED.** `_token_actor_from_mapping` (`:80-91`): a `LEGIS_API_TOKEN_ACTORS` entry with NO `:scope` segment is now REJECTED with 403 (`:82-86`) UNLESS `LEGIS_ALLOW_UNSCOPED_API_TOKENS=1` is set. With that flag, an unscoped entry returns the actor for ANY `required_scope` (the `if scope_sep and required_scope not in scopes` check at `:87` is skipped when `scope_sep` is falsy) — so an unscoped token still grants operator authority, but only behind an explicit opt-in flag. Residual risk gated by env opt-in. Confirmed.
- **H7-adjacent (single-secret mode has NO scope split — same vulnerability class, more common deployment).** The `LEGIS_API_SECRET` branch of `_verify_secret` (`:108-116`) returns `LEGIS_API_ACTOR`/default actor on a `compare_digest` match WITHOUT ever consulting `required_scope`. So when a deployment uses a single shared secret (no `LEGIS_API_TOKEN_ACTORS` mapping), `verify_operator` (required_scope=`operator`, `:142`) and `verify_writer` (required_scope=`writer`, `:138`) are satisfied by the *same* token — the operator-only routes (`POST /protected/operator-override`, `POST /signoff/{seq}/sign`) are reachable by any holder of the writer secret. The writer/operator scope split is therefore a real control ONLY in TOKEN_ACTORS mode; in single-secret mode it is vacuous and the secret grants operator authority. Confirmed against `:104-116`.
- **M1 surfaces here** — `POST /protected/overrides` (`:528`) and `POST /protected/operator-override` (`:558`) pass `source_root` to the service, but non-`.py` entities still produce signed records with `source_binding: unverified` (see Service-layer M1). The HTTP layer adds no extra guard beyond the service helper.
- **M2 surfaces here** — `POST /checks` (`:464`), `POST /git/pulls` (`:444`), and `POST /policy/evaluate` (`:733`) accept writer-supplied facts (CheckRun outcome, PR state, policy target) with `recorded_by=actor` provenance but no fact-provenance attestation; a writer can record arbitrary check/PR outcomes.
- **Drift signal — sign-off bypasses the service seam.** `POST /signoff/request` (`:588`) and `POST /signoff/{seq}/sign` (`:680`) call `SignoffGate.request`/`.sign_off` directly rather than `service.request_signoff` (which exists and is exported, `__init__.py:42`). The bind-issue trail-verification block (`:605-618`) also re-implements the `verified_records` tamper-check pattern inline instead of reusing the service helper. This is the same class of HTTP↔service divergence the audit watches for — here the HTTP adapter reaches past its own service layer.
- Unauthenticated read surfaces (`GET /overrides`, `/governance/*`, `/signoff/{seq}/binding`) expose governance trail/binding data with no scope; acceptable for an operating-picture read API but worth noting governance records are readable by any client.
- `LEGIS_UNSAFE_DEV_AUTH=1` (`:130-131`,`:117`) bypasses auth entirely when no secret/token is configured — fail-open dev path; the default with nothing configured is 401 (`:119-123`), so this is opt-in.

**Confidence:** High — read 100% of `app.py` (830 LOC) and enumerated every `@app.<verb>` decorator with its `Depends`/scope and delegate. Auth logic (`:61-143`) and wardline routing (`:750-828`) read in full. H7/C2/H1 cross-validated against `mcp.py:863-928` and `wardline.py:36`. Inbound importers confirmed via grep.
