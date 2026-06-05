# 02 — Subsystem Catalog

Consolidated from six parallel codebase-explorer passes (clusters A–F), each reading its
files at 100% and grepping every dependency edge with `file:line`. Subsystems are ordered
bottom-up by dependency layer. Per-subsystem confidence is **High** unless noted; the basis
is "all files read, edges grepped" in every case.

> **Edge convention:** `X -> Y` means module X imports/depends on module Y.

---

## Foundations — `canonical.py`, `clock.py`

**Responsibility:** Leaf deterministic primitives — canonical JSON + content hashing (the basis of every hash/HMAC in the suite) and an injectable time source for deterministic timestamps.

**Key Components:**
- `canonical.py` (22 LOC) — `canonical_json` (`sort_keys=True`, tight separators, `ensure_ascii=False`, **`allow_nan=False`**) and `content_hash` (sha256 of canonical JSON). RFC-8785 convergence explicitly deferred (ADR-0001).
- `clock.py` (30 LOC) — `Clock` Protocol, `SystemClock` (UTC ISO), `FixedClock` (deterministic test double). Production never calls `datetime.now()` directly.

**Dependencies:** Outbound: none (leaf, stdlib only). Inbound (canonical, 9 edges): `store/audit_store`, `enforcement/signing`, `governance/sei_backfill`, `governance/gaps`, `service/wardline`, `identity/resolver`, `mcp`, `policy/decorator`, `policy/boundary_scan`. Inbound (clock): `enforcement/{engine,protected,signoff}`, `governance/{binding_ledger,sei_backfill}`, `mcp`, `cli`, `api`.

**Patterns:** Leaf-module discipline (bottom of the DAG); single canonicalization choke point (RFC-8785 upgrade = one-file change); DI clock with deterministic double.

**Concerns:** **M13 partially closed** — `allow_nan=False` present; full RFC-8785 hardening still deferred. `ensure_ascii=False` makes byte output encoding-dependent (consistent today; latent footgun if any caller hashes the `str` differently).

---

## Identity (SEI) — `src/legis/identity/`

**Responsibility:** Resolve a code locator to an SEI-keyed (or honestly-degraded, locator-keyed) opaque `EntityKey` by consuming Loomweave's SEI HTTP surfaces — never parsing the SEI, never guessing.

**Key Components:**
- `entity_key.py` (40) — `EntityKey` frozen dataclass (`value` + `identity_stable`); factories `from_locator`/`from_sei`; `from_dict` validates `value` is non-empty `str` and `identity_stable` is a `bool` (raises `ValueError` otherwise).
- `resolver.py` (96) — `IdentityResolver.resolve` → `IdentityResolution` (entity_key, alive, content_hash, lineage_snapshot, status). Degrades to locator-keyed on capability-absent / no-client / not-alive / non-dict / transport-exception. Captures REQ-L-01 lineage snapshot `{length, hash}` on stable alive SEI.
- `loomweave_client.py` (219) — `LoomweaveIdentity` Protocol + `HttpLoomweaveIdentity` over stdlib `urllib`. HMAC request signing on protected routes (`X-Weft-Component`/timestamp/nonce); HTTPS-unless-loopback; 1 MB cap; JSON content-type enforcement.

**Dependencies:** Outbound: `resolver -> canonical.content_hash` (only non-cluster edge; entity_key/client are stdlib-only). Inbound (heavily consumed — 14 edges): `api`, `cli`, `mcp`, `enforcement/{engine,lifecycle,protected,signoff}`, `governance/{binding_ledger,gaps,sei_backfill,signoff_binding}`, `records/override_record`, `service/{governance,wardline}`, `wardline/governor` (type only).

**Patterns:** SEI opacity (`value` never parsed); honest degradation (`alive` `False` vs `None`); injectable transport seam.

**Concerns:** **M5 NOT reproduced** — `from_dict` rejects non-`bool` stability; defect closed in current tree. Capability cache is per-instance, never invalidated once `True` (long-lived resolver keeps treating a since-degraded Loomweave as capable). `content_hash` taken verbatim from Loomweave response with no type check.

---

## Records — `src/legis/records/`

**Responsibility:** The shared core `OverrideRecord` schema (the chill-cell recordable override) that serializes to a flat dict for the record-agnostic audit store; judge/HMAC fields attach via `extensions`.

**Key Components:** `override_record.py` (39) — frozen `OverrideRecord` (policy, entity_key, rationale, agent_id, recorded_at, extensions); `identity_stable` delegates to `EntityKey`; `to_payload()` emits the canonical flat dict.

**Dependencies:** Outbound: `-> identity.entity_key`. Inbound (all enforcement): `protected`, `judge_factory`, `lifecycle`, `engine`, `judge`, `signoff`.

**Patterns:** Stable-core / extensible-edge; explicit `to_payload()` serialization boundary; identity delegation.

**Concerns:** None observed. (`to_payload` does no field-type validation — acceptable for an internal frozen dataclass.)

---

## Store (persistence) — `src/legis/store/`

**Responsibility:** Record-agnostic, append-only, hash-chained SQLAlchemy audit log with DB-level mutation rejection and a structural integrity verifier; plus the `AppendOnlyStore` protocol consumers depend on.

**Key Components:**
- `audit_store.py` (186) — `AuditStore` over SQLAlchemy + `NullPool`; SQLite WAL/NORMAL/busy_timeout PRAGMAs; append-only enforced by `BEFORE UPDATE`/`BEFORE DELETE` triggers (`RAISE(ABORT)`); `append` chains `chain_hash = sha256(prev + content_hash)` under `BEGIN IMMEDIATE`; `verify_integrity` re-walks the chain.
- `protocol.py` (30) — `AuditRecordLike` / `AppendOnlyStore` Protocols (the abstraction enforcement types against).

**Dependencies:** Outbound: `-> canonical`. Inbound — protocol `AppendOnlyStore`: `enforcement/{engine,protected,signoff}`; concrete `AuditStore`: `governance/{sei_backfill,binding_ledger,gaps}`, `api`, `cli`, `mcp` (composition roots).

**Patterns:** Two integrity layers (DB triggers reject in-band mutation + hash chain detects out-of-band tampering); record-agnostic opaque payloads; protocol-first consumption seam.

**Concerns:** **M6 PARTIALLY closed** — `verify_integrity` guards decode of `read_all()` but the loop body `content_hash(rec.payload)` (L168) is unguarded; `json.loads` accepts `Infinity`/`NaN`, so a directly-tampered payload makes `canonical_json(allow_nan=False)` **raise `ValueError` out of `verify_integrity`** — the exact tamper case it defends against (empirically reproduced). **HMAC framing:** the store is hash-chain *only*; HMAC lives in `enforcement/signing.py`. PRAGMA failures are silently swallowed (no observability).

---

## Policy Grammar — `src/legis/policy/`

**Responsibility:** The agent-programmable policy-boundary grammar — boundary types evaluating to CLEAR/VIOLATION/UNKNOWN (fail-closed), policy→cell routing, one-off exemptions, and an AST honesty gate verifying a `@policy_boundary` decoration is backed by a real, pinned test that actually exercises the boundary.

**Key Components:**
- `grammar.py` (123) — `PolicyResult`, `PolicyEvaluation` (carries `provenance_gap`), `BoundaryType` Protocol, append-only `PolicyGrammar` registry (raises `PolicyConflictError` on shadowing); `evaluate()` fails closed (UNKNOWN+gap on unregistered; `except Exception` around boundary calls).
- `cells.py` (99) — `PolicyCellRegistry.cell_for` resolves policy → {chill, coached, structured, protected} (exact rules, then `fnmatch` globs, else `default_cell`). In-code default is `chill`.
- `decorator.py` (212) — `@policy_boundary` decorator + `check_policy_boundary()` runtime honesty gate (metadata-transplant, qualname scope, citation shape, fingerprint drift, then delegates semantics to `evaluate_test_evidence`).
- `evidence.py` (152) — single shared judgement (gate + scanner) enforcing shadowing / exercise / policy-co-occurrence checks.
- `exemptions.py` (128) — `ExemptionRegistry` + YAML/TOML loaders (fail closed on malformed).
- `boundary_scan.py` (357) — static `@policy_boundary` scanner (`scan_policy_boundaries`) with strict `tests/*.py` path sandboxing; reuses `evaluate_test_evidence`. Drives CLI `policy-boundary-check`.
- `policy/cells.toml` (repo-root) — runtime routing, `default_cell="structured"`; loaded by `mcp.py`, overriding the in-code `chill`.

**Dependencies:** Outbound: `-> canonical.content_hash` (only intra-legis edge) + intra-package + `yaml`. Inbound: `mcp` (cells, grammar), `service/governance` (grammar), `service/explain` (cells), `api` (grammar), `cli` (boundary_scan).

**Patterns:** Provider-seam / open instance set (agents add boundaries, no human config); fail-closed everywhere; single-source-of-truth evidence judgement (gate + scanner can't drift); anti-vibe provenance (decoration-time TypeErrors + pinned test fingerprint).

**Concerns:** **H6 confirmed** — in-code default cell is self-clearing `chill` (`cells.py:44`); only mitigated when `cells.toml` (`structured`) loads — if config absent, `mcp.py:111` falls back to `chill`. **M7 confirmed** — honesty gate's policy-co-occurrence is a `\b`-substring match in an assert, not a check that the boundary *result* is the assertion subject. **L4 confirmed (narrow)** — runtime gate (`inspect.getsource`+dedent) vs scanner (`get_source_segment`+dedent) can diverge for class-method/decorated test_refs. Grammar-layer exemptions silently flip VIOLATION→CLEAR with `provenance_gap=False` and only fire when `target['value']` is a `str`.

---

## Enforcement Engine — `src/legis/enforcement/` (12 files)

**Responsibility:** Grade a policy firing through the governance 2×2 (simple/complex × judge off/on), writing exactly one append-only hash-chained audit record per submission and — in the protected cell — binding each verdict to its inspected source with an HMAC signature plus lifecycle gates (decay re-judge + override-rate).

**Key Components:**
- `engine.py` (115) — `EnforcementEngine.submit_override`: chill (`judge=None`) / coached (judge evaluates *before* write). `record_event` for raw governance events.
- `verdict.py` (28) — `Verdict` (ACCEPTED/BLOCKED/OVERRIDDEN_BY_OPERATOR), `SignoffState`, `JudgeOpinion`.
- `judge.py` (111) — `Judge`/`LLMClient` Protocols; `LLMJudge` (structured-JSON-first, fail-closed; BLOCKED wins on ambiguity; untrusted input framed as data).
- `judge_factory.py` (31) — env-wired `OpenRouterLLMClient`, else `FailClosedJudge` (always BLOCKED).
- `llm_client.py` (168) — `OpenRouterLLMClient`; SSRF/transport hardening (HTTPS-or-loopback, no-redirect, 1 MB cap, strict shape validation).
- `protected.py` (288) — `ProtectedGate.submit`/`operator_override`; every record HMAC-signed via `signing_fields()` (binds entity+policy+source fingerprint+ast_path+lineage); `TrailVerifier.verify` (protected-policy set from config/ADR-0002, not the record → no flag-flip downgrade).
- `signoff.py` (151) — `SignoffGate` (structured/protected block+escalate, no LLM); `request` records PENDING (does not clear); `sign_off` records SIGNED_OFF referencing `request_seq` + `request_payload_hash`.
- `lifecycle.py` (122) — `decay_sweep` (re-judges judge-ACCEPTED suppressions), `evaluate_override_rate` (rolling-window; PASS/FAIL/PASS_WITH_NOTICE).
- `signing.py` (47) — keyed HMAC-SHA256 over `canonical_json`; versioned (`v2` default, `v1` legacy); `compare_digest`.

**Dependencies:** Outbound: `-> clock`, `-> identity.entity_key`, `-> records.override_record`, `-> store.protocol` (protocol, not concrete), `-> canonical`. **No edge to `governance` or `policy`** (one-directional, clean). Inbound: `service/{governance,wardline,explain}`, `mcp`, `api`, `cli`, `wardline/{governor,ingest}` (signing), `governance/{signoff_binding,binding_ledger}` (signing).

**Patterns:** Ports-and-adapters DI (store/clock/judge/LLM all injected Protocols; chill↔coached is one nullable `judge` arg); single-source-of-signed-fields (signer + verifier can't drift); fail-closed everywhere; append-only single trail; config-driven trust boundary (anti-downgrade); security-hardened LLM egress.

**Concerns:** `TrailVerifier._requires_verification` ORs config protected-set with in-record markers — correct only if the config set is always complete/current. Dual signing-field functions (v1/v2) widen the accept set during the legacy window. `decay_sweep` has no per-record try/except — one malformed `entity_key` row aborts the whole sweep. `record_event` bypasses the judge/verdict path (relies on callers not misusing it for protected policies). HMAC key rotation out of scope.

---

## Governance — `src/legis/governance/`

**Responsibility:** Tamper-bound binding of sign-offs to Filigree issues, append-only SEI re-keying/backfill of pre-SEI records, lineage-spine gap/divergence detection, and pure closure-gate decisions — layered on the record-agnostic audit store.

**Key Components:**
- `binding_ledger.py` (93) — `BindingLedger` records signed `issue_binding`s to a dedicated `AuditStore`; `verify()` now checks `store.verify_integrity()` (hash chain) **then** per-record HMAC; `get`/`get_by_issue_id` fail-closed.
- `signoff_binding.py` (74) — `bind_signoff_to_issue`: validate (rejects locator keys) → `filigree.attach` → optional `ledger.record` (non-atomic, documented).
- `sei_backfill.py` (259) — `run_pre_sei_backfill`: appends `SEI_BACKFILL`/`SEI_BACKFILL_UNRESOLVED` events referencing `original_seq` (never rewrites); idempotent; fails closed on integrity failure.
- `gaps.py` (115) — `find_orphan_gaps` (Loomweave `alive:false`); `find_lineage_integrity` (REQ-L-01 prefix-custody: stored snapshot must be a prefix of current lineage).
- `filigree_gate.py` (32) — `evaluate_issue_closure` (pure decision; closable only with a verified binding).
- `params.py` (11) — ADR-0002 reviewed constants (`OVERRIDE_RATE_THRESHOLD`, window, min-sample).

**Dependencies:** Outbound: `-> store.audit_store` (concrete), `-> canonical`, `-> clock`, `-> enforcement.signing`, `-> identity.{entity_key,loomweave_client}`, `-> filigree.client`. Inbound: `cli`, `mcp`, `service/governance` (params), `api`.

**Patterns:** Fail-closed throughout; append-only migration (never rewrites history); prefix-monotonic custody; pure decision functions separated from I/O; dedicated isolated ledger store.

**Concerns:** **H5 RESOLVED** — `verify()` now invokes `store.verify_integrity()`. **M12 residual relocated** — enforcement now uses the `AppendOnlyStore` protocol, but `binding_ledger`/`sei_backfill`/`gaps` type against concrete `AuditStore` (can't be unit-tested against a protocol fake). **M6 propagation** — these callers branch on `verify_integrity()` which can *raise* (see Store), turning a tamper signal into an uncaught crash. **gaps.py null-deref** — `_stable_seis`/`find_lineage_integrity` do `payload.get("entity_key", {}).get(...)`; an explicit `"entity_key": null` raises `AttributeError` (inconsistent with `sei_backfill._entity_key` which guards). Non-atomic attach→record window.

---

## Wardline Integration — `src/legis/wardline/`

**Responsibility:** Ingest an agent-supplied Wardline scan, validate its shape, select the active-defect population, and route each finding into a configured 2×2 cell — Wardline analyses, legis governs.

**Key Components:**
- `ingest.py` (226) — `WardlineSeverity`, `WardlineFinding.from_wire` (carries `properties` **verbatim**, tier-conformance deliberately not enforced); `active_defects` (defect + active; agent-suppressed states require proof); `MAX_FINDINGS=500`; `verify_wardline_artifact` (optional HMAC provenance when `artifact_key` set).
- `governor.py` (142) — `route_findings`: requires exactly one of `policy`/`cell_map`; pre-write validation guard **rejects** batches whose cells span block_escalate AND surface_*; resolves each entity via injected `resolve(qualname)`; dispatches to `signoff.request` / `engine.submit_override` / `engine.record_event`.
- `policy.py` (17) — `resolve_cell` (severity ≥ `fail_on` → gate cell, else SURFACE_ONLY).

**Dependencies:** Outbound: `ingest -> enforcement.signing.verify`; `governor -> enforcement.{engine,signoff}`, `-> identity.entity_key` (type only — resolution injected via callable, no static resolver edge). Inbound: `api`, `mcp`, `service/wardline` (the orchestrator wiring `resolve`).

**Patterns:** Single-judge governance (tiers verbatim, never re-derived); properties as write-only evidence; validate-all-before-any-write + cross-store-split rejection; optional artifact authentication.

**Concerns:** **M3 refined** — across-store version closed by the cross-store-split guard; **intra-store** non-atomicity remains (N sequential appends, no transaction; mid-loop failure persists earlier findings). **Ingest relaxation (bbed0ba)** live — three backward-compatible relaxations; only retained governance control is "agent-suppressed defects must carry proof." Artifact provenance optional by default.

---

## Filigree Integration — `src/legis/filigree/`

**Responsibility:** Bind a cleared, SEI-keyed sign-off to a Filigree issue as an opaque entity-association (`entity_id` = SEI) so the binding survives rename/move — without mutating Filigree issue lifecycle.

**Key Components:** `client.py` (123) — `FiligreeClient` Protocol + `HttpFiligreeClient` over stdlib `urllib`; `attach` POSTs `{entity_id, content_hash, actor, signoff_seq?, signature?}`; `associations_for_entity` GETs. (Binding orchestration lives in `governance/signoff_binding.py`.)

**Dependencies:** Outbound: none to `legis.*` (stdlib only). Inbound: `api`, `governance/signoff_binding` (the `attach` caller).

**Patterns:** Same transport posture as Loomweave client; opaque-pointer binding; authority separation (attests, never mutates issue status).

**Concerns:** **M4 confirmed** — `bind_signoff_to_issue` rejects locator keys (intentional, avoids rename-orphan), but the consequence is **Filigree binding availability is coupled to Loomweave SEI capability**: a degraded seam silently removes the binding surface for those sign-offs. **Unsigned transport** — `HttpFiligreeClient` carries no Weft-component HMAC (unlike the signed Loomweave client); the `attach` `signature` is an app-level attestation, not transport auth.

---

## Git Domain — `src/legis/git/`

**Responsibility:** Answer "what changed?" over a real repo by shelling out to `git` (stateless), and produce a structured rename/history feed for Loomweave's SEI matcher; define the injectable forge-PR seam shape.

**Key Components:**
- `surface.py` (207) — `GitSurface` over `subprocess git -C` (10 s timeout): `branches`, `commit(s)`, `merge_base` (honest `None`), `renames` (committed `-M`), `working_tree_renames` (uncommitted). Every ref/SHA regex-validated + leading-`-` rejected (arg-injection guard).
- `rename_feed.py` (48) — `build_rename_feed`: superset of `GET /git/renames`; `status` (found) vs `worktree_checked` (checked) disambiguation. Contract-locked Loomweave provider.
- `pull_request.py` (27) — `PullRequestSource` Protocol (injectable forge seam).
- `models.py` (45) — passive `BranchInfo`/`CommitInfo`/`RenameEvidence` (path-level only; disclaims symbol-level — that's Loomweave's).

**Dependencies:** Outbound: none to `legis.*` (internal `surface→models`, `rename_feed→surface`; stdlib subprocess). Inbound: `api`, `mcp`.

**Patterns:** Stateless reader (git is truth); defensive arg validation; honest tri-state reporting; contract-locked additive provider.

**Concerns:** M2 does **not** apply (reads facts from repo, no untrusted writer). `re` re-imported per method (style nit). `working_tree_renames` shells `hash-object` per file (unbounded for very large rename sets).

---

## Checks — `src/legis/checks/`

**Responsibility:** Record/serve CI check-run facts in an indexed relational table queryable by commit/branch/PR — deliberately NOT the hash-chained governance audit log.

**Key Components:** `surface.py` (122) — `CheckSurface` over its **own** SQLAlchemy engine; `check_runs` table; idempotent `recorded_by` migration; `record`/`for_commit`/`for_branch`/`for_pr`/`latest_state`. `models.py` (34) — `CheckOutcome` enum, frozen `CheckRun`.

**Dependencies:** Outbound: none to `legis.*` (own engine, SQLAlchemy). Inbound: `api`, `mcp`.

**Patterns:** Operational facts vs governance trail (separate engine); idempotent schema-evolution; last-write-wins.

**Concerns:** **M2 confirmed (checks half)** — `CheckRun` built from client `model_dump()` with only `recorded_by=actor`; outcome/commit_sha facts accepted on the writer's word, no signature/provenance. By design (operational table), but a consumer treating check outcomes as authoritative governance input trusts an unauthenticated writer.

---

## Pulls — `src/legis/pulls/`

**Responsibility:** Record/serve forge-reported PR metadata (number/title/base/head/state) in its own relational table.

**Key Components:** `surface.py` (68) — `PullSurface` over its own engine; `pull_requests` table; idempotent `recorded_by` migration; `record` (delete-then-insert upsert by number)/`get`. `models.py` (23) — `PullRequestState` enum, frozen `PullRequest`.

**Dependencies:** Outbound: none to `legis.*`. Inbound: `api`, `mcp`.

**Patterns:** Same operational-table posture as checks; upsert-by-number.

**Concerns:** **M2 confirmed (pulls half)** — `PullRequest` built from client `model_dump()` with only `recorded_by=actor`; PR state/base/head accepted unauthenticated.

---

## Service Layer — `src/legis/service/`

**Responsibility:** Transport-agnostic governance business logic — the shared decision/enforcement primitives the HTTP, MCP, and CLI frontends route through, raising `ServiceError` subclasses (never `HTTPException`/JSON-RPC) so each adapter owns its error translation.

**Key Components:**
- `__init__.py` (47) — public re-export contract (`evaluate_policy`, `compute_override_rate`, `submit_override`/`submit_protected_override`/`submit_operator_override`, `request_signoff`, `resolve_for_record`, `verified_records`, `explain_policy`, `route_wardline_scan`, errors).
- `errors.py` (28) — `ServiceError` + `AuditIntegrityError`/`NotEnabledError`/`NotFoundError`/`InvalidArgumentError` (adapters switch on type, never message text).
- `governance.py` (248) — `resolve_for_record` (single resolve-then-key boundary); `verified_records` (fail-closed verified-trail read); `compute_override_rate` (binds ADR-0002 params, not caller input); `submit_override`/`submit_protected_override`/`submit_operator_override` (each protected path gated by source-binding); `request_signoff`; `evaluate_policy`.
- `source_binding.py` (89) — `verify_current_source_binding` (re-hashes on-disk file under `source_root`); `require_verified_source_binding` (fails closed only for `.py`-shaped entities).
- `explain.py` (122) — `explain_policy` (policy→cell explanation; drives MCP `policy_explain`; not consumed by HTTP).

**Dependencies:** Outbound: `-> enforcement.{engine,lifecycle,protected,signoff}`, `-> governance.params`, `-> identity.{entity_key,resolver}`, `-> policy.{grammar,cells}`, `-> canonical`, `-> wardline.{governor,ingest,policy}`. **No `-> store` edge** (store-agnostic via duck-typed gate/verifier). Inbound: `api`, `mcp`. (`cli` does NOT import service.)

**Patterns:** Explicit DI (no globals); keyword-only args after the positional gate (transposition-proof); fail-closed verification; policy constants from `params` not caller; duck-typing at the enforcement seam.

**Concerns:** **M1 refined** — `require_verified_source_binding` only enforces for `.py`-shaped entities; a non-`.py`/opaque-SEI protected entity yields `status:unverified` and still produces an HMAC-signed protected record. **M2** — `evaluate_policy` flags `provenance_gap` only on UNKNOWN; writer-supplied `target` facts otherwise trusted. `explain.py` `del entity` — accepted-but-ignored parameter. `NotFoundError` defined/exported but never raised in `service/`.

---

## HTTP API — `src/legis/api/`

**Responsibility:** FastAPI `create_app` factory exposing git/check read surfaces plus mutating governance surfaces, enforcing bearer auth (writer/operator scopes) and translating `ServiceError` subclasses to HTTP status codes.

**Key Components:** `app.py` (830) — single `create_app(...)` factory (~16 DI params) with lazy env-driven fallback wiring (builds `AuditStore`/`TrailVerifier`/`ProtectedGate`/`SignoffGate`/`BindingLedger` when `LEGIS_HMAC_KEY` set). Auth: `_token_actor_from_mapping`, `_verify_secret`, `verify_writer`/`verify_operator`. **26 routes** (full table in cluster-D partial), e.g.: read surfaces (`GET /git/*`, `/checks/*`, `/overrides`, `/governance/*`) unscoped; `POST /overrides|/checks|/git/pulls|/policy/evaluate|/wardline/scan-results|/signoff/request` = **writer**; `POST /protected/operator-override`, `POST /signoff/{seq}/sign` = **operator**.

**Dependencies:** Outbound: `-> service.*` (primary seam), `-> enforcement.{engine,protected,signoff}` (**direct reach-through** for sign-off + trail verify), `-> checks/pulls/git`, `-> governance.{gaps,binding_ledger,signoff_binding,filigree_gate}`, `-> filigree`, `-> identity`, `-> policy.grammar`, `-> wardline`, `-> store/clock/judge_factory` (lazy). Inbound: `cli` (launcher via factory string), `mcp` (imports `DEFAULT_GOVERNANCE_DB`/`DEFAULT_CHECK_DB` constants — sibling-frontend coupling).

**Patterns:** Application factory with exhaustive DI + lazy fallback; adapter error-translation (404/422/500/409); ACCEPTED/BLOCKED → 201/409; server-owned authority (rate constants, wardline cell, recorded actor).

**Concerns:** **C2/H1 — HTTP is the reference; now has parity with MCP** (server routing wins + forbids caller fields → 403; caller routing behind `LEGIS_UNSAFE_WARDLINE_REQUEST_ROUTING=1`; artifact HMAC via `LEGIS_WARDLINE_ARTIFACT_KEY`). **H7 mitigated** — unscoped `TOKEN_ACTORS` entries rejected unless `LEGIS_ALLOW_UNSCOPED_API_TOKENS=1`. **NEW — H7-adjacent (single-secret mode):** `_verify_secret` (`:108-116`) returns the actor on a `LEGIS_API_SECRET` match **without consulting `required_scope`** — so writer and operator routes are satisfied by the same token; the writer/operator split is a real control ONLY in TOKEN_ACTORS mode. **M1/M2 surface here**. **Drift signal** — sign-off routes call `SignoffGate` directly, bypassing the exported `service.request_signoff`, and re-implement the `verified_records` tamper-check inline. Unauthenticated governance read surfaces.

---

## CLI — `src/legis/cli.py`, `__init__.py`

**Responsibility:** The `legis` console script — an argparse dispatcher (`serve`, `mcp`, `check-override-rate`, `governance-gate`, `sei-backfill`, `policy-boundary-check`) wiring flags into `LEGIS_*` env and deferring to frontends/gates.

**Key Components:** `build_parser` (6 subcommands); `_check_override_rate` (the override-rate CI gate — **reads the audit store directly**, inlines its own protected-record detection, builds its own `TrailVerifier`, then `evaluate_override_rate`); `_apply_judge_env`. `__init__.py` — `__version__ = "1.0.0rc2"`.

**Dependencies:** Outbound: `-> api.app:create_app` (launcher), `-> mcp.main` (launcher), `-> store.audit_store`, `-> enforcement.{lifecycle,protected}`, `-> governance.{sei_backfill,params}`, `-> identity.loomweave_client`, `-> policy.boundary_scan`, `-> clock`. **`-> service.*` = NONE.** Inbound: console-script entry point only.

**Patterns:** Env-var seam (flags → `LEGIS_*` → frontend re-reads); lazy local imports in dispatch branches; fail-closed CI posture (missing DB / integrity failure / unverifiable protected records → exit 1, guarded by `CI=true`/`LEGIS_ALLOW_MISSING_GOVERNANCE_DB`).

**Concerns:** **Service-layer bypass (adapter drift, CLI side)** — `_check_override_rate` routes through no `service.*` function; it hand-rolls parallel copies of `verified_records` + `compute_override_rate`. This duplication already forced a divergent fix (`07cf54e`). MCP's `override_rate_get` *does* go through the service. `print`-only, no structured observability around gate outcomes.

---

## MCP Server — `src/legis/mcp.py`

**Responsibility:** A stdlib-only, hand-rolled MCP-over-stdio JSON-RPC server (protocols `2024-11-05`/`2025-03-26`) exposing governance + git/CI tools to agents under a launch-bound `agent_id`, mapping governance *decisions* onto `service/` and *reads* onto their owning surfaces.

**Key Components:** `McpRuntime` (per-launch state); `build_runtime` (wires gates + `TrailVerifier` together under `LEGIS_HMAC_KEY` — no "gate without verifier" hole); `tool_definitions` (schemas, all `additionalProperties:false`); `call_tool` (dispatch, begins with `_validate_argument_keys`); `handle_request`/`run_jsonrpc`/`main`. **Tool routing:** the 5 governance-decision tools (`policy_explain`, `override_submit`, `policy_evaluate`, `scan_route`, `override_rate_get`) route through `service/`; read/poll surfaces (`signoff_status_get`, `filigree_closure_gate_get`, `git_*`, `pull_request_get`, `check_list`) reach owning surfaces directly (consistent with HTTP).

**Dependencies:** Outbound: `-> api.app` (**sibling-frontend coupling** — `DEFAULT_GOVERNANCE_DB`/`DEFAULT_CHECK_DB`), `-> service.{governance,wardline,explain,errors}`, `-> enforcement.*`, `-> governance.{binding_ledger,filigree_gate}`, `-> policy.{cells,grammar}`, `-> wardline.{governor,ingest}`, `-> git/checks/pulls`, `-> store/identity/canonical`. Inbound: `cli` only.

**Patterns:** Service-for-decisions, direct-surface-for-reads; launch-bound identity (schemas never accept actor identity); lazy resource construction; discriminated outcome envelopes + recovery hints; idempotency-replay machinery.

**Concerns — adapter-drift audit verdicts (all RESOLVED in current source):**
- **C2 RESOLVED** — `scan_route` rejects caller routing under server routing (`INVALID_CELL_SPEC`), mirroring HTTP; caller routing only behind `LEGIS_UNSAFE_WARDLINE_REQUEST_ROUTING=1`. *Caveat: closed in `call_tool`, not the schema (schema still advertises the keys).*
- **C3 RESOLVED** — `_verified_records` → `service.verified_records` → `trail_verifier.verify` raising `AuditIntegrityError`; gate + verifier always co-constructed.
- **H1 RESOLVED** — passes `artifact_key` → `verify_wardline_artifact` requires signed provenance when key set.
- **M9 RESOLVED** — `_validate_argument_keys` rejects unknown keys (`InvalidArgumentError`).
- **M10 RESOLVED** — `poll_handle`/`seq` both integer; `_require_int` tolerant.
- **M11 RESOLVED** (commit `b4285dc`) — request-hash idempotency binding + recorded-outcome replay; rejects key reuse with a different request; replay reads the verified trail.

**Non-drift concerns:** sibling-frontend coupling to `api.app` (cleanest single coupling to break); hand-rolled JSON-RPC framing with no stdin line-size bound; 464-stmt `call_tool` single if/elif (table-driven candidate as tools grow).
