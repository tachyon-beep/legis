# Catalog F — Suite Integrations & Git/CI Domain

Cluster F covers the suite-seam integrations (Legis ↔ Loomweave / Wardline /
Filigree) plus the git and CI/PR domain surfaces. Read 100% of all 21 source
files in the six packages. Dependency edges grepped exhaustively across `src/`.

---

## Identity (SEI)

**Location:** `src/legis/identity/`

**Responsibility:** Resolve a code locator to an SEI-keyed (or honestly-degraded, locator-keyed) opaque `EntityKey` by consuming Loomweave's SEI HTTP surfaces, never parsing the SEI and never guessing.

**Key Components:**
- `entity_key.py` (40 lines) — `EntityKey` frozen dataclass: `value` (opaque locator or SEI) + `identity_stable` (False for locator, True for SEI). Factories `from_locator`/`from_sei`; `to_dict`/`from_dict`. `from_dict` (lines 34-40) validates `value` is a non-empty str and `identity_stable` is a `bool`, raising `ValueError` otherwise.
- `resolver.py` (96 lines) — `IdentityResolver.resolve(locator)` → `IdentityResolution` (entity_key, alive, content_hash, lineage_snapshot, two status strings). Probes capability once per instance (line 33, 40-48); on capability absent / no client / not-alive locator / non-dict response / transport exception, returns a locator-keyed degraded resolution. On a stable alive SEI, captures the REQ-L-01 lineage snapshot `{length, hash}` (lines 50-55).
- `loomweave_client.py` (219 lines) — HTTP transport seam. `LoomweaveIdentity` Protocol (capability/resolve_locator/resolve_batch/resolve_sei/lineage); `HttpLoomweaveIdentity` over stdlib `urllib` with injectable `fetch`. HMAC request signing (`sign_loomweave_request`, lines 67-87) emits `X-Weft-Component: loomweave:<hmac>` + `X-Weft-Timestamp` + `X-Weft-Nonce` on protected (signed) routes; capability probe is unsigned (line 185). Base-URL validation requires HTTPS unless loopback (lines 143-150); 1 MB response cap; JSON-content-type enforcement.

**Dependencies:**
- Inbound (heavily consumed foundation — 14 edges):
  - `api/app.py:41` (`entity_key.EntityKey`), `:42` (`resolver.IdentityResolver`), `:299-300` (lazy `HttpLoomweaveIdentity`+`loomweave_hmac_key_from_env`, `IdentityResolver`)
  - `cli.py:10` (`HttpLoomweaveIdentity`, `loomweave_hmac_key_from_env`)
  - `mcp.py:122-123` (lazy `HttpLoomweaveIdentity`+key, `IdentityResolver`)
  - `enforcement/engine.py:23`, `enforcement/lifecycle.py:17`, `enforcement/protected.py:21`, `enforcement/signoff.py:18` (all `entity_key.EntityKey`)
  - `governance/binding_ledger.py:20` (`EntityKey`), `governance/gaps.py:18` (`LoomweaveIdentity`), `governance/sei_backfill.py:16-17` (`LoomweaveIdentity`, `EntityKey`), `governance/signoff_binding.py:23` (`EntityKey`)
  - `records/override_record.py:14` (`EntityKey`)
  - `service/governance.py:19-20` (`EntityKey`, `IdentityResolver`), `service/wardline.py:11-12` (`EntityKey`, `IdentityResolver`)
  - `wardline/governor.py:35` (`EntityKey` type only)
- Outbound: `identity/resolver.py:15 → legis.canonical.content_hash` (lineage snapshot hashing). No other non-cluster outbound. `loomweave_client.py` and `entity_key.py` import only stdlib.

**Patterns Observed:**
- SEI opacity discipline — `value` never parsed by legis; locator→SEI is a value change with no schema change (entity_key.py docstring).
- Honest degradation — every non-stable path returns `identity_stable=False` with an explicit status string; `alive` distinguishes `False` (known not-alive) from `None` (no capability/decision).
- Capability probed once per resolver instance, but a probe exception transiently degrades without caching (resolver.py:44-48), permitting retry on next resolve.
- Transport seam injectable (`fetch`) for offline tests; stdlib-only, no added dependency.

**Concerns:**
- **M5 not reproduced (prior audit claim does not match current source).** `EntityKey.from_dict` (entity_key.py:38-39) rejects a non-`bool` `identity_stable` with `ValueError` rather than coercing malformed stability to `True`. Grep for any constructor bypassing the factories/`from_dict` (`EntityKey(` minus `from_*`) returns nothing — no path reconstructs an `EntityKey` while skipping validation. The malformed-stability-coerces-true defect is closed in the current tree.
- Capability cache is per-instance and never invalidated once `True` is latched (resolver.py:42-48): a Loomweave that loses the `sei` capability mid-life keeps being treated as capable by a long-lived resolver until a later call raises. Low severity (capability rarely revoked), but worth noting for long-lived service resolvers.
- `content_hash` field on a stable resolution is taken verbatim from the Loomweave response (`res.get("content_hash")`, resolver.py:92) with no type check (unlike `sei`).

**Confidence:** High — read all 4 files (entity_key, resolver, loomweave_client, `__init__`) at 100%; cross-verified the 14 inbound edges by grep with file:line; ran the M5 bypass grep (clean). HMAC/degradation paths traced line-by-line.

---

## Wardline Integration

**Location:** `src/legis/wardline/`

**Responsibility:** Ingest an agent-supplied Wardline MCP scan response, validate its shape, select the active-defect gate population, and route each finding into a configured 2×2 governance cell (surface+override / block+escalate / surface+only) — Wardline analyses, legis governs.

**Key Components:**
- `ingest.py` (226 lines) — payload validation. `WardlineSeverity` (CRITICAL…NONE, ranked). `WardlineFinding.from_wire` validates required fields, severity enum, non-empty strings, optional `qualname`; carries `properties` **verbatim** (write-only evidence, tier-conformance deliberately NOT enforced — comment lines 142-145). `active_defects` selects `kind == "defect"` + `suppressed == "active"`; agent-suppressed states (`waived`/`suppressed`) require suppression proof (top-level or nested in `properties`), non-agent states (`baselined`/`judged`) are silently excluded, any other state rejected. `MAX_FINDINGS = 500` batch cap. `verify_wardline_artifact` optionally HMAC-verifies scanner/rule-set/commit/tree provenance when an `artifact_key` is configured; without a key it records supplied metadata as `artifact_status: "unverified"`.
- `governor.py` (142 lines) — `route_findings`. Requires exactly one of `policy` (whole-scan single cell) or `cell_map` (per-severity, every present severity must be mapped). Pre-write validation guard (lines 59-89) confirms engine/signoff presence and **rejects** any batch whose cells span block_escalate AND a surface_* cell (lines 86-89). Each finding resolves its entity via injected `resolve(qualname)` callable, builds a `wardline` extension (fingerprint, properties verbatim, severity, batch_provenance) merged with the loomweave lineage ext, and dispatches to `signoff.request` / `engine.submit_override` / `engine.record_event`.
- `policy.py` (17 lines) — `resolve_cell`: severity ≥ `fail_on` → `gate_cell`, else `SURFACE_ONLY`.

**Dependencies:**
- Inbound:
  - `api/app.py:55-56` (`WardlineCellPolicy`; `WardlinePayloadError`, `WardlineSeverity`)
  - `mcp.py:55-56` (same)
  - `service/wardline.py:14-15,21` (`WardlineCellPolicy`, `route_findings`; ingest symbols; `policy.resolve_cell`) — the orchestrator that wires the `resolve` callable from `IdentityResolver`
- Outbound:
  - `wardline/ingest.py:14 → legis.enforcement.signing.verify` (artifact signature)
  - `wardline/governor.py:33 → legis.enforcement.engine.EnforcementEngine`, `:34 → legis.enforcement.signoff.SignoffGate`, `:35 → legis.identity.entity_key.EntityKey` (type only)
  - `wardline/policy.py` and `wardline/governor.py` import sibling `wardline.ingest`/`wardline.governor`
  - Note: governor's identity coupling is the `EntityKey` *type* import only. Resolution arrives via the injected `resolve` callable (wired in `service/wardline.py`), NOT a static `IdentityResolver` import — there is no governor→resolver static edge.

**Patterns Observed:**
- Single-judge governance: Wardline produces, legis decides the cell; trust tiers carried verbatim as the one suite vocabulary, never re-derived.
- Properties-as-write-only-evidence: tiers + diagnostics ride untyped into the record; nothing reads the values back.
- Validate-all-dependencies-before-any-write guard, plus an explicit cross-store-split rejection to keep a routed batch single-store.
- Optional artifact authentication: provenance verified only when a key is configured; otherwise honestly labelled unverified.

**Concerns:**
- **M3 — refined (across-store version largely closed; intra-store non-atomicity remains).** The guard at governor.py:86-89 rejects any batch whose cells span block_escalate (signoff store) and surface_* (engine store), so a *routed* batch is structurally single-store — the across-stores M3 is closed by that guard. What remains (and is admitted in the comment at governor.py:60-65) is **intra-store** non-atomicity: a multi-finding same-cell batch performs N sequential appends to one append-only store, and a mid-loop runtime failure leaves the earlier findings permanently persisted. There is no transaction wrapping the loop.
- **Ingest validator relaxation (commit bbed0ba, 2026-06-05) — current state.** Three conscious, backward-compatible relaxations are live: (1) `properties` carried verbatim with tier-conformance dropped (ingest.py:139-145); (2) `baselined`/`judged` accepted as non-active without proof (lines 173, 221-222); (3) suppression proof read top-level OR in `properties` (lines 176-193). Structural validation (required fields, defect/active semantics, batch cap, signature-when-keyed) is unchanged. Net: the validator now accepts strictly more shapes; the only governance-relevant control retained is "agent-suppressed defects must carry proof."
- Artifact provenance is optional by default — when no `artifact_key` is configured, scanner/commit/tree provenance is accepted unverified (ingest.py:86-87). The verified path exists but is opt-in.

**Confidence:** High — read all 4 files at 100%; traced `from_wire`, `active_defects`, and `route_findings` end-to-end; cross-checked commit bbed0ba's stated relaxations against the current source lines; verified the cross-store guard and the entity_key-type-only coupling by reading governor imports and `service/wardline.py` edges.

---

## Filigree Integration

**Location:** `src/legis/filigree/`

**Responsibility:** Bind a cleared, SEI-keyed governance sign-off to a Filigree issue as an opaque entity-association (`entity_id` = SEI), so the code↔governance binding survives rename/move — without mutating Filigree issue lifecycle.

**Key Components:**
- `client.py` (123 lines) — `FiligreeClient` Protocol (`attach`, `associations_for_entity`) and `HttpFiligreeClient` over stdlib `urllib` with injectable `fetch`. `attach` POSTs `{entity_id, content_hash, actor, signoff_seq?, signature?}` to `/api/issue/{id}/entity-associations`; `associations_for_entity` GETs `/api/entity-associations?entity_id=…`. Same base-URL HTTPS-unless-loopback validation, 1 MB cap, and JSON-content-type enforcement as the Loomweave client.
- (The binding orchestration lives outside this package, in `governance/signoff_binding.py:bind_signoff_to_issue` — read for the M4 trace below.)

**Dependencies:**
- Inbound:
  - `api/app.py:38` (`FiligreeClient`), `:308` (lazy `HttpFiligreeClient`)
  - `governance/signoff_binding.py:21` (`FiligreeClient`) — the caller of `attach`
- Outbound: none to other `legis.*` modules. `client.py` imports only stdlib.

**Patterns Observed:**
- Same transport posture as the Loomweave client (stdlib urllib, injectable fetch, no added dependency).
- Opaque-pointer binding: SEI handed as `entity_id`; Filigree never parses it; drift comparison stays legis's job (docstring).
- Authority separation: legis attaches an attestation but never mutates Filigree issue status (locked decision 5).

**Concerns:**
- **M4 confirmed — deliberate rejection with a coupling consequence.** `bind_signoff_to_issue` (governance/signoff_binding.py:38-42) raises `ValueError` on any `identity_stable=False` (locator) key. This is intentional (docstring: an unstable binding would orphan on rename). The cataloguable consequence: when Loomweave is degraded or the locator has no alive SEI, the resolver returns a locator key, and the sign-off — though it can be *recorded* — **cannot be bound to Filigree at all**. Filigree binding availability is therefore coupled to Loomweave SEI capability; a degraded suite seam silently removes the binding surface for those sign-offs. The signoff_binding docstring acknowledges the rejection but not this availability coupling.
- **Transport is unsigned (asymmetry vs Loomweave).** `HttpFiligreeClient` carries no Weft-component HMAC — unlike `loomweave_client.py`, which signs protected routes with `X-Weft-Component`/timestamp/nonce. The `signature` passed to `attach` is an *application-level binding attestation* (produced by `enforcement.signing.sign` in `signoff_binding.py:44-53`), not transport authentication. The Filigree HTTP channel itself is unauthenticated.
- `attach`/`record` ordering in the caller is validate→attach→record with no compensating delete (signoff_binding.py:64-73): if the ledger `record` raises after a successful `attach`, Filigree holds a pointer with no local ledger entry (accepted trade-off — surfaced by the ledger's `verify()`).

**Confidence:** High — read `client.py` and `__init__` at 100%, plus `governance/signoff_binding.py` (the M4 site) at 100%; cross-verified both inbound edges and the unsigned-transport asymmetry against the Loomweave client.

---

## Git Domain

**Location:** `src/legis/git/`

**Responsibility:** Answer "what changed?" over a real repository by shelling out to `git` (stateless, repo-as-source-of-truth), and produce a structured rename/history feed for Loomweave's SEI identity matcher; also define the injectable forge-PR seam shape.

**Key Components:**
- `surface.py` (207 lines) — `GitSurface` over `subprocess` `git -C`, 10 s timeout. `branches()` (ahead/behind via `rev-list --left-right`), `commit()`/`commits()` (numstat, US-delimited `--format`), `merge_base()` (honest `None` on no ancestor), `renames(rev_range)` (committed, `-M --diff-filter=R`, captures old/new blob SHAs), `working_tree_renames(base)` (uncommitted, hash-object for new blob). Every ref/SHA argument is regex-validated and rejects leading `-` (arg-injection guard, e.g. surface.py:80, 118, 137, 177).
- `rename_feed.py` (48 lines) — `build_rename_feed`: superset of `GET /git/renames`. Bundles base/head + committed renames, optionally working-tree renames. `status` reflects what was *found*; separate `worktree_checked` flag reflects what was *checked* (clean-vs-unchecked disambiguation). Contract-locked provider for Loomweave (committed-only consumer ignores worktree fields).
- `pull_request.py` (27 lines) — `PullRequestContext` dataclass + `PullRequestSource` Protocol: an injectable forge seam (no baked-in GitHub HTTP).
- `models.py` (45 lines) — passive `BranchInfo`, `CommitInfo`, `RenameEvidence` (path-level rename evidence; docstring explicitly disclaims symbol-level detection — that is Loomweave's).

**Dependencies:**
- Inbound:
  - `api/app.py:34` (`PullRequestSource`), `:35` (`build_rename_feed`), `:36` (`GitError`, `GitSurface`)
  - `mcp.py:28` (`GitError`, `GitSurface`), `:957` (lazy `build_rename_feed`)
- Outbound: none to other `legis.*` modules. Internal only: `git/surface.py:13 → git.models`; `git/rename_feed.py:23 → git.surface`. Depends on stdlib `subprocess`/`re`/`pathlib`.

**Patterns Observed:**
- Stateless reader; git is the source of truth, no added dependency.
- Defensive arg validation — regex + leading-dash rejection on every ref/range argument before it reaches `git`.
- Honest tri-state reporting (`status` found vs `worktree_checked` checked) so consumers never infer "clean" from "unchecked".
- Contract-locked additive provider: `rename_feed` is a superset of the committed-only endpoint; existing consumers unaffected.

**Concerns:**
- **M2 (writer-facts-without-provenance) — does not apply to the git surface.** `GitSurface` reads facts directly from the repo, so there is no untrusted writer; the M2 concern is a checks/pulls property (see those blocks), not a git-domain one.
- `commit()` re-imports `re` inside each method (surface.py:79, 117, 124, 136, 176) rather than at module scope — minor style nit, no correctness impact.
- `working_tree_renames` shells `hash-object` per renamed file with no batch (surface.py:190); fine at PR scale, unbounded with a very large working-tree rename set.

**Confidence:** High — read all 5 files (surface, rename_feed, pull_request, models, `__init__`) at 100%; traced rename committed + worktree paths and the arg-injection guards; both inbound edges grepped with file:line; confirmed git has no non-cluster outbound legis edge.

---

## Checks

**Location:** `src/legis/checks/`

**Responsibility:** Record and serve CI check-run facts (named check ran against a code state → outcome), in an indexed relational table queryable by commit / branch / PR — deliberately NOT the hash-chained governance audit log.

**Key Components:**
- `surface.py` (122 lines) — `CheckSurface` over its own SQLAlchemy `create_engine` (NullPool). `check_runs` table (indexed on check_name/commit_sha/branch/pr); idempotent additive migration adds `recorded_by` (lines 52-59). `record`, `for_commit`/`for_branch`/`for_pr`, `latest_state` (last write per check_name wins).
- `models.py` (34 lines) — `CheckOutcome` enum (pass/fail/skipped/timeout); frozen `CheckRun` (check_name, run_id, commit_sha, outcome, optional branch/pr/ran_against/rule_set/policy_version/timestamps/recorded_by).

**Dependencies:**
- Inbound: `api/app.py:29-30` (`CheckOutcome`,`CheckRun`; `CheckSurface`), `mcp.py:20-21` (`CheckRun`; `CheckSurface`).
- Outbound: none to `legis.*`. External: SQLAlchemy; instantiates its **own** engine per surface (not the shared audit store).

**Patterns Observed:**
- Operational facts vs governance trail: indexed queryable table, explicitly separated from the Sprint-0 append-only hash-chained audit log (docstring).
- Idempotent schema-evolution via `PRAGMA table_info` + conditional `ALTER TABLE`.
- Immutable fact records (frozen dataclass), but rows are mutable in practice (last-write-wins via `latest_state`).

**Concerns:**
- **M2 confirmed (the checks half).** `CheckRun` is constructed from the API client's `model_dump()` with only `recorded_by=actor` attached (`api/app.py:466`). The check *outcome/commit_sha/run_id facts themselves are accepted on the writer's word* — no signature, no provenance verification, unlike the signed Wardline artifact path or the hash-chained audit log. `recorded_by` records *who submitted*, not that the fact is true. Architecturally this is by design (operational table, own engine, not the tamper-evident trail), but a consumer treating check outcomes as authoritative governance input would be trusting an unauthenticated writer.

**Confidence:** High — read both files (surface, models) and `__init__` at 100%; confirmed the M2 write path at `api/app.py:466`; verified own-engine instantiation and the deliberate separation from the audit store.

---

## Pulls

**Location:** `src/legis/pulls/`

**Responsibility:** Record and serve forge-reported pull-request metadata (number/title/base/head/state) in its own relational table — facts legis records, not local git.

**Key Components:**
- `surface.py` (68 lines) — `PullSurface` over its own SQLAlchemy engine (NullPool). `pull_requests` table keyed on `number` (indexed base/head/state); idempotent `recorded_by` migration. `record` is delete-then-insert (upsert by number); `get`.
- `models.py` (23 lines) — `PullRequestState` enum (open/closed/merged); frozen `PullRequest` (number, title, base, head, state, optional url/recorded_by).
- `__init__.py` — re-exports `PullRequest`, `PullRequestState`, `PullSurface`.

**Dependencies:**
- Inbound: `api/app.py:53-54` (`PullRequest`,`PullRequestState`; `PullSurface`), `mcp.py:36` (`PullSurface`).
- Outbound: none to `legis.*`. External: SQLAlchemy; own engine per surface.

**Patterns Observed:**
- Same operational-table posture as checks; own engine, separate from the audit trail.
- Upsert-by-number via delete-then-insert in one transaction.

**Concerns:**
- **M2 confirmed (the pulls half).** `PullRequest` is built from the client's `model_dump()` with only `recorded_by=actor` (`api/app.py:448`); PR state/base/head are accepted unauthenticated, same posture as checks. By design (recorded forge facts, not governance trail), but the writer's word is the only provenance.

**Confidence:** High — read all 3 files at 100%; confirmed the M2 write path at `api/app.py:448`; verified own-engine instantiation.

---

## Cross-Block Confidence / Risk / Gaps / Caveats

**Confidence Assessment:** High across all six blocks. All 21 source files read at 100% (none exceed 226 lines). Every dependency edge grepped with file:line. The four prior-audit concerns (M2/M3/M4/M5) were each discriminated against current source: M5 not reproduced (with a confirming bypass-grep), M3 refined to intra-store, M4 confirmed with a coupling consequence, M2 confirmed at two precise write sites.

**Risk Assessment:** Low risk in the read itself. The synthesis-relevant risks in the code: (1) intra-store non-atomic Wardline batches (governor.py:60-65); (2) Filigree binding availability coupled to Loomweave SEI capability (signoff_binding.py:38-42); (3) checks/pulls accept unauthenticated writer facts (api/app.py:448,466); (4) unsigned Filigree transport vs signed Loomweave transport.

**Information Gaps:** Did not read the `service/wardline.py` orchestrator, `api/app.py`, or `mcp.py` bodies in full — only the specific edge/write lines (448, 466, 299-308, governor wiring). The exact shape of the injected `resolve` callable that `route_findings` receives was inferred from the governor signature + the service edge, not read end-to-end in the service layer. Loomweave/Wardline/Filigree wire contracts are taken from docstrings, not from the sibling repos.

**Caveats:** "M5 not reproduced" and "M3 refined" reflect the tree at commit 2e69141 (current HEAD); the prior audit may have run against an earlier tree where the defects were live. The git-domain blocks disclaim symbol-level rename detection (that is Loomweave's matcher); `RenameEvidence` is path-level only.
