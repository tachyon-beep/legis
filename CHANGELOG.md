# Changelog

All notable changes to Legis are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
versions per [PEP 440](https://peps.python.org/pep-0440/) /
[SemVer](https://semver.org/) (pre-release: `1.0.0rc1`).

## [Unreleased]

Dogfood-#2 governance honesty (convention C-10) — branch-local; merge/release
gated on the filigree-first propagation. Capability confinement (proposed C-8) is
preserved: operator signing keys stay out of agent reach, no key is auto-provisioned
or relocated, and no MCP tool enables a cell or self-grants authority (pinned by
`test_c8_no_agent_reachable_enablement_or_signing_surface`).

### Changed
- **Adopt Wardline's `suppression_state` key (W3, weft-ef79348eb2).** Wardline
  renamed the per-finding output key `suppressed` → `suppression_state` across all
  surfaces, including the **signed** legis scan artifact — which changed the
  canonical signed bytes and broke the Wardline→legis hop (`legis_e2e` red). legis
  ingest (`WardlineFinding.from_wire` + `active_defects`) now reads the new key; the
  values (active/waived/suppressed/baselined/judged) are unchanged. Clean break: a
  finding carrying only the legacy `suppressed` key reads as `active` and **over**-gates
  (fail-safe — never silently drops a defect). No signing/canonical change was needed
  (legis's signer already reproduces Wardline's rekeyed golden byte-for-byte). Added the
  **legis-side cross-impl golden mirror** legis was missing — `sign(_GOLDEN_FIELDS,
  _GOLDEN_KEY) == hmac-sha256:v2:2b2cf09…` over `suppression_state` — so the signed hop
  is self-verifying on both ends, not only in Wardline's opt-in oracle.
- **Honest, actionable unconfigured-governance errors (N3, weft-df8d2ef454 — C-10(c)).**
  legis no longer "ships dark and quiet": the two inert axes now name their concrete
  enablement path. `INVALID_CELL_SPEC` (scan_route, server-owned routing unset) names
  `LEGIS_WARDLINE_CELL` / `LEGIS_WARDLINE_CELL_BY_SEVERITY`; `CELL_NOT_ENABLED` is split
  into the keyless simple tier (map the policy via `policy/cells.toml` /
  `LEGIS_POLICY_CELLS`, `LEGIS_DEV_DEFAULT_CELLS=1` for the chill dev default) and the
  complex tier (`LEGIS_HMAC_KEY`, operator-held, out-of-band + relaunch). Subsumes Le1.
  Fail-closed is preserved — the errors become honest, nothing auto-opens.
- **Honest `SKIPPED_DIRTY_TREE` skip payload (N4, weft-a7a92a40dd — C-10(d)).** The
  dirty-tree skip is no longer a prose-only blob: `WardlineDirtyTreeError.to_payload()`
  is the single source both transports (MCP `structuredContent` + HTTP body) serialize,
  carrying machine-switchable `reason` / `posture` / `cause` / `remediation` (commit for
  a signed artifact, or the `LEGIS_WARDLINE_ALLOW_DIRTY=1` operator opt-in) while still
  governing nothing. The dirty-snapshot opt-in stays an env-only operator switch — no
  `scan_route` call argument was added. (Compounds with sibling finding C1: loomweave's
  tracked runtime DB perpetually dirties the tree; that fix is loomweave-side.)

### Added
- **Two report-only `legis doctor` checks (N3).** `runtime.policy_cells` and
  `runtime.wardline_routing` report whether the governance surface is wired and, when
  not, name the exact enablement keys (warn, never auto-fixed; presence-only — they
  write nothing and never render a key value).

### Docs
- **Charter: self-asserted write actor (C3, weft-f506e5f845).** `legis-charter.md`'s
  known-gaps note now also covers legis's *own* audit records — `agent_id` / `operator_id`
  are self-asserted (launch-bound + HMAC-tamper-evident, but not authenticated); the
  narrative `verified_author: null` maps to these stored fields. The governed subject's
  SEI is still resolved; only the actor is unauthenticated.

## [1.0.0rc4] — 2026-06-08

### Added
- **`legis --version`** — top-level version flag (LG-1, weft-9da517a67e); reports
  the installed package version and exits. Closes the dogfood gap where the only
  way to identify the running build was an indirect probe.
- **`legis doctor [--root] [--repair] [--format text|json]`** — operator health
  view and safe repair for the install + config layer (instruction blocks, skills,
  SessionStart hook, `.gitignore`, `.mcp.json` registration, store dir, audit
  hash-chain integrity, key/sibling wiring). Report-only on `weft.toml` (C-9(b))
  and on hash chains; key values are never rendered.
- **`legis install --mcp`** — register the legis MCP server in `.mcp.json`
  (also part of `legis install` with no flags).
- **Self-install (`legis install`)** — legis now stands itself up like its
  siblings: it injects a lean, versioned agent-orientation block into CLAUDE.md /
  AGENTS.md, installs the `legis-workflow` skill pack (Claude + Codex), registers
  a `SessionStart` hook, and extends `.gitignore` with the local config surface
  (`.legis/`, `legis.yaml`). The block carries a content-hashed, version-pinned
  marker (`<!-- legis:instructions:v{version}:{hash} -->`); a drift check
  re-injects it when either the bundled content or the package version changes.
  Two triggers keep it fresh — the SessionStart hook (`legis session-context`)
  and a best-effort refresh on `legis mcp` boot, the latter closing the
  Codex-only-repo gap a hook-only approach leaves open. Mirrors filigree's
  inject/replace/append install mechanism (atomic write, symlink rejection,
  idempotent hook registration), right-sized for legis; the lean block +
  skill-pack split keeps the injected context small while the skill carries the
  full CLI + MCP-tool reference. Design spec:
  `docs/superpowers/specs/2026-06-06-legis-instruction-injection-design.md`.
  (legis-0127b66; hardening — skill swap, hook upgrade, gitignore, nested-corrupt
  settings — in legis-b245710.)
- **Dirty-tree dev path** — `verify_wardline_artifact` now recognises the
  unsigned `dirty: true` dev artifact emitted by `wardline scan --format legis
  --allow-dirty`. In the keyless posture it governs but records the marker
  honestly (`artifact_status: "dirty"`). In the CI posture (artifact key
  configured) a dirty dev artifact is a typed amber **`SKIPPED_DIRTY_TREE`**
  outcome on `scan_route` / `/wardline/scan-results` — distinguishable from the
  generic red, never governed — unless `LEGIS_WARDLINE_ALLOW_DIRTY=1` opts into
  governing it unsigned (recorded as `"dirty"`). The relaxation is scoped to
  exactly `dirty is True AND no signature`: a signed payload still verifies
  (a forged signature stays red) and a clean unsigned payload still requires a
  signature, so the clean-tree signing guarantee is intact. (legis-d731c760c5,
  legis-7e85e8e7ba; upstream wardline `--allow-dirty`.)

### Changed
- **Typed outcome/status axes (str Enums)** — five stringly-typed axes are now
  `str, Enum` following the existing `WardlineSeverity` model: `ScanOutcome`
  (`ROUTED` / `SKIPPED_DIRTY_TREE`), `ArtifactStatus`
  (`verified` / `dirty` / `unverified`), `IdentityResolutionStatus`,
  `LineageSnapshotStatus`, and `Suppressed`. A `str, Enum` serializes identically
  to the bare string, so wire payloads and HMAC artifact signatures are
  byte-identical (the signature path signs the raw scan, not legis's
  enum-bearing provenance). `IdentityResolution` gains a `__post_init__`
  bijection (`alive` `None`↔`UNAVAILABLE`, `False`↔`NOT_ALIVE`,
  `True`↔`RESOLVED`) so a self-contradictory frozen record is no longer
  representable; the dead `getattr` fallbacks in `service/governance.py` are
  dropped. The guard now covers the record's *other* half too — the lineage axis
  (`lineage_snapshot` present iff `lineage_snapshot_status` is `VERIFIED`) — and
  rejects a non-bool `alive` with its own `ValueError` rather than a `KeyError`. The `suppressed` field stays `str` on the wire-facing dataclass
  (validation timing and error type unchanged); the enum is the vocabulary
  source of truth. Behavior-preserving. (legis-bba4f22949; deferred from the
  rc4 code review.)
- **Table-driven MCP dispatch (Q-L8)** — `call_tool` now routes through a tool
  table instead of an if/elif ladder, and the stdio server bounds each stdin
  line so a malformed client cannot stream unbounded input. Behavior-preserving.
- **`CELL_NOT_ENABLED` recovery hint names the enablement path (Le1,
  weft-f506e5f845)** — the MCP error's `next_action` now tells the agent *how* to
  enable a governance cell (set `LEGIS_HMAC_KEY`; configure policy cells via
  `LEGIS_POLICY_CELLS` / `policy/cells.toml` / `LEGIS_DEV_DEFAULT_CELLS=1`) instead
  of a generic "ask the operator". The per-cell message still names which cell is
  unenabled.
- **Charter documents the self-asserted-write-actor gap (C3, weft-f506e5f845)** —
  `docs/design/legis-charter.md` now records `verified_author: null` (federation
  write attribution is self-asserted, not cryptographically verified) as a known
  governance gap, acceptable for trust-local use and deferred for multi-principal.
- **Release CI gates** — the coverage floor is raised to 88% with a `ruff` lint
  gate added (Q-L7), and the Filigree client's transport / error branches are
  covered. (The live Loomweave conformance step is opt-in via the per-PR oracle
  in `ci.yml`, skipped when `LOOMWEAVE_URL` is unset; it does **not** hard-gate
  PyPI publish — the fail-closed release gate was removed because no
  CI-reachable Loomweave oracle is provisioned, which would otherwise make every
  release fail before publish.)

### Fixed
- **Fingerprint reconciliation + RFC-8785 deferral (Q-L5 / Q-L4)** — the policy
  gate and the static boundary scanner now extract the same fingerprint (they had
  diverged); the RFC-8785 canonical-JSON upgrade is explicitly deferred (its
  trigger is a *non-Python* verifier, and the one cross-tool verifier — Wardline —
  is a byte-for-byte Python replica pinned by a golden vector).
- **AuditStore batch read-free invariant (Q-M5)** — the batch append path is
  guarded against issuing a read mid-batch, with a regression test pinning the
  three-layer append-only enforcement.
- **Capability-latch TTL revalidation (Q-L6)** — the SEI capability latch is
  TTL-revalidated rather than cached indefinitely, and `content_hash` is
  type-checked at its call sites.
- **Lint** — cleared the remaining `ruff` findings in the test suite (unused
  imports, mid-file imports hoisted to module top, and `# noqa: F821` on the
  honesty-gate fixture functions whose free `handler` name is fingerprinted by
  source, not executed). `ruff check src tests` is now clean.
- **`pull_request_get` reports recorded checks unconditionally** — the tool no
  longer short-circuits to an empty `checks` list on a fresh runtime whose check
  surface has not yet been lazily initialised. A PR's CI outcomes are now
  call-order-independent, so a governance agent can never be told a PR is clean
  when failing checks exist.
- **Injector anchors on its own top-level fence, not a quoted marker** — the
  instruction injector previously located its block with a bare substring search
  for `<!-- legis:instructions`, so a legis marker *quoted inside* a co-resident
  sibling block (a worked example, documentation) could anchor the rewrite there
  and gut the sibling. `inject_instructions` now walks fences span-aware and
  anchors only on legis's own *top-level* open fence (one not enclosed by an
  unclosed foreign block); a file with no real legis block of its own appends
  (deleting nothing). Completes the foreign-block-deletion fix (peer of
  filigree-bcbd4d66fd) — the "never delete a co-resident sibling block" property
  is now total. (legis-068e359d28.)
- **Drift-refresh failures are no longer dropped silently** — `refresh_instructions`
  (the SessionStart / MCP-boot drift path) discarded the result of a re-injection
  or skill-reinstall and only reported success. Because `inject_instructions` /
  `install_skills` return `(False, reason)` (rather than raising) on a recoverable
  refusal such as a symlinked target, the upstream `except` never saw it and agents
  could run on drifted instructions with zero signal. Both paths now log a
  `WARNING` with the reason on failure (peer of the boot-log path closed earlier).
- **Unexpected MCP tool errors are logged server-side** — the `INTERNAL_ERROR`
  fall-through in `_service_error` reached the agent caller but left no
  server/Sentry record; an unexpected exception now logs at `ERROR` with the
  exception attached. The typed, expected errors (`NOT_FOUND`, `INVALID_ARGUMENT`,
  …) stay quiet.
- **Corrupt `settings.json` recovery is surfaced** — `install_claude_code_hooks`
  already backed a malformed or wrong-typed `settings.json` up to `.json.bak`
  before resetting it, but reported ordinary success; it now logs a `WARNING` and
  names the backup in its return message so the user knows to reconcile.
- **Injector handles an empty target file cleanly** — injecting into an existing
  zero-byte / whitespace-only `CLAUDE.md` / `AGENTS.md` now writes just the block
  (like the create path) instead of leaving leading blank-line artifacts.

## [1.0.0rc3] — 2026-06-06

Audit remediation: the `Q-*` series hardening the governance, transport, and
read paths surfaced by the rc2 architecture analysis.

### Changed
- **Service layer is the one path to governance decisions (Q-H2)** — the FastAPI
  and MCP adapters both drive `legis.service`; no decision logic lives in a route
  closure.
- **Weft-component HMAC on the Filigree transport (Q-M4)** — the Filigree binding
  hop is authenticated, and the wire carries the canonical signed bytes (signing
  and transport agree byte-for-byte).
- **Recorded check/PR facts labelled unauthenticated (Q-M2 / Q-M4)** — `Check`
  and `PullRequest` records carry an explicit unauthenticated provenance label;
  legis never presents an unsigned upstream fact as verified.
- **Core modules typed against the `AppendOnlyStore` protocol (Q-L3)** — the
  governance modules depend on the append-only contract, not a concrete store.

### Fixed
- **Single-secret mode is writer-scoped (Q-H1)** — a single shared secret grants
  writer scope only; operator force-past stays an explicit opt-in, never implied.
- **LLM judge is advisory-only on protected policies (Q-H3)** — on a protected
  cell the judge cannot clear a verdict; the protected gate decides.
- **`verify_integrity` fails on non-finite-float tamper (Q-M3)** — a record
  carrying a NaN/Inf that survives decode now fails integrity verification rather
  than passing silently.
- **Fail closed when policy-cell config is absent (Q-M7)** — a missing cell
  configuration is a block, not a default-allow.
- **Honesty gate requires the boundary result as the assertion subject (Q-M8)** —
  the static policy-boundary gate cannot be satisfied by an unrelated assertion.
- **Same-cell batch routing is atomic (Q-M5)** — a batch routed into one cell
  commits or fails as a unit.
- **Read paths hardened against malformed `entity_key` (Q-L1 / Q-L2)** — the
  governance read surfaces reject a malformed locator instead of raising.
- **Source-binding contract clarified and signed status proven (Q-M1 / Q-M6)** —
  the Filigree binding-availability contract is decided and documented
  (ADR-0003).
- Declared the `pydantic` runtime dependency explicitly.

## [1.0.0rc2] — 2026-06-06

The agent-facing MCP surface, the deployable LLM judge, and the sibling
integration surfaces (Filigree closure-gate, Loomweave git-rename feed) that rc1
listed as not-yet-built.

### Added
- **MCP stdio surface (WP-M2 / WP-M3)** — the ratified agent tool catalog is
  loaded and callable over an MCP stdio server: the policy-cell registry and
  `policy_explain` contract (WP-M2), the callable tool catalog with store/registry
  flags (WP-M3), plus the `git_rename_feed_get` and `filigree_closure_gate_get`
  tools.
- **Deployable LLM judge** — an OpenRouter judge client behind the `LLMClient`
  seam, wired into both the API and MCP runtimes via deployable judge
  configuration flags.
- **Filigree closure-gate** — a governance decision function exposed over
  `GET /filigree/closure-gate` and the `filigree_closure_gate_get` MCP tool, with
  a verified `get_by_issue_id` on the `BindingLedger`.
- **Git rename feed** — a Clarion/Loomweave-ready rename-feed builder with
  working-tree rename detection on `GitSurface`, exposed over `GET /git/rename-feed`
  and the `git_rename_feed_get` MCP tool; the feed contract is locked.
- **Static policy-boundary honesty gate** — a static scanner plus the
  `legis policy-boundary-check` CLI command, enforced in CI; the static scanner
  is converged onto the same runtime evidence gate.
- **PyPI Trusted Publishing** — a release workflow and package metadata for
  publishing to PyPI.

### Changed
- **Rebrand Clarion→Loomweave and Loom→Weft** across legis (identifiers, docs,
  and config references). The protected-cell signing field set follows the
  rename (`clarion_content_hash` → `loomweave_content_hash`, `ext["clarion"]` →
  `ext["loomweave"]`). This is a deliberate **clean break**, not a migration:
  legis is unreleased, so no signed governance records predate the rename. The
  now-impossible legacy fallback (`legacy_signing_fields` and the
  `hmac-sha256:v1` acceptance path in `TrailVerifier` / `signing`) is removed
  accordingly; the version-tag mechanism (`v2`) is retained so a future
  field-set change can still be introduced as a new tag without ambiguity.
- **MCP idempotency replays scoped** so a replayed call resolves against its own
  prior result, not a sibling's.
- **Store-URL resolution centralised in `config.py`** — the `LEGIS_*_DB` env
  override precedence the module documents is now implemented inside the
  `*_db_url()` resolvers themselves (via `_resolve_db_url`), instead of being
  re-wrapped as `os.environ.get("LEGIS_*_DB", *_db_url())` at ~11 call sites
  across `api/app.py`, `mcp.py`, and `cli.py`. Consumers call the resolver
  directly; precedence/alias changes are a one-line edit in one place, and a
  direct resolver call can no longer silently ignore its override. No change to
  the resolved URLs for existing deployments.
- **Weft-component transport-HMAC seam extracted to `weft_signing`** — the
  Loomweave SEI client and the Filigree association client signed their requests
  with byte-for-byte copies of the same `X-Weft-Component` scheme
  (`_json_body_bytes` / `_path_and_query` / `sign_*_request` /
  `*_hmac_key_from_env`). The wire format now has a single definition; both
  clients delegate to it (component name and channel env var parameterised), so
  a future canonicalization or header change can no longer touch one channel and
  silently diverge the other. The shared serializer deliberately stays off
  `canonical.canonical_json` (whose `ensure_ascii=False` would change the signed
  bytes). Behavior-preserving — existing per-channel golden vectors unchanged,
  plus a new cross-channel anti-drift test. No change to signatures on the wire.
- **Wardline scan-routing validation centralised in the service layer** — "is
  request-side routing allowed, and is the cell-spec well-formed?" is a
  governance decision that was hand-copied into both the HTTP
  (`/wardline/scan-results`) and MCP (`scan_route`) adapters, along with the
  cell-spec parse and a `_parse_wardline_cell_map` helper. The copies had already
  drifted: the HTTP adapter rejected an empty `cell_by_severity` (422) while MCP
  silently accepted an empty `severity_map` and routed nothing. The decision now
  lives in `service.resolve_scan_routing`, raising a `WardlineRoutingError` whose
  `kind` each adapter maps to its own taxonomy (HTTP 500/403/422 by kind; MCP
  collapses to `INVALID_CELL_SPEC`) — so a new routing rule is added once and
  cannot reach one transport but not the other. Behavior-preserving for every
  pinned case; the one intended change closes the drift (an empty per-severity
  map is now rejected up front on both transports — no silent governance skip).
- **Instruction-marker reader colocated with its writer** — the SessionStart /
  MCP-boot freshness check in `hooks.py` re-encoded the marker format
  (`<!-- legis:instructions:v{version}:{hash} -->`) with its own regex,
  independently of `install.py`, which builds the marker and owns
  `INSTRUCTIONS_MARKER`. A change to the marker spacing or token shape in the
  writer would silently desync the reader, and the drift check — the hook's whole
  job — would stop matching. The token-extraction helper (`_extract_marker_token`)
  now lives next to the writer in `install.py`; its regex is `re.escape`d from the
  `INSTRUCTIONS_MARKER` constant and captures the token opaquely (`\S+`), so it
  cannot desync from the prefix and needs no edit if the token shape changes. A
  round-trip test (`_extract_marker_token(_build_instructions_block())` ==
  `_marker_token()`) pins reader-to-writer, failing loudly on any future format
  change.

### Fixed
- **Ingest accepts realistic scans** — the over-strict Wardline ingest validator
  was relaxed to accept the diagnostics a real scan carries while keeping the
  trust-grammar projection.
- **CLI fails closed on protected override-rate trails** — a missing or
  unverifiable protected trail exits non-zero rather than reporting a clean rate.
- **Override-rate gate no longer over-detects protected records** — the
  keyless-branch protected-detector dropped its soft `file_fingerprint` /
  `ast_path` extension sniffs, which a chill/coached record could carry via an
  arbitrary `extra_extensions` dict and thereby fail-close a non-protected
  deployment's `legis governance-gate`. It now keys off the policy set plus the
  `protected_cell` / signature markers the simple-tier engine never writes;
  `TrailVerifier`'s (deliberately over-inclusive) verify-path heuristic is
  unchanged.
- Hardened the governance audit boundaries with regression coverage.

## [1.0.0rc1] — 2026-06-03

First release candidate for 1.0. Everything built through Sprint 6 plus the
WP-M1 service-layer extraction, consolidated behind a stable version.

### Added
- **git/CI surface** — stateless `GitSurface` (branches, commits, renames with
  `-M`, merge-base) and a recorded CI `CheckSurface`, exposed over `/git/*` and
  `/checks/*`; injectable `PullRequestSource` seam with `/git/pull-requests/{n}`.
- **Graded 2×2 enforcement engine** — chill / coached / structured / protected
  cells; LLM judge behind an injected `LLMClient` seam (fail-closed verdict
  parsing); HMAC-signed protected verdicts; decay sweep and the override-rate
  gate (`legis check-override-rate`, exits 1 on FAIL).
- **Agent-programmable policy grammar** — `/policy/evaluate` returning
  CLEAR / VIOLATION / UNKNOWN, with honest `provenance_gap` events (no silent
  false-green); TOML-backed one-off exemptions.
- **SEI-keyed attestations** — `identity/loomweave_client.py` + resolver
  (resolve-then-key, honest degrade, lineage snapshot); all governance write
  paths key on Stable Entity Identity when alive; `/governance/identity-gaps`
  and `/governance/lineage-integrity` read surfaces.
- **Suite combinations** — Wardline findings route into the 2×2 via
  `/wardline/scan-results`; governed SEI-keyed sign-off binding to Filigree
  issues via a tamper-bound `BindingLedger`.
- **Console scripts** — `legis serve` (uvicorn factory) and
  `legis check-override-rate`.
- **Transport-agnostic service layer (WP-M1)** — `legis.service` extracts the
  cross-cutting governance logic (`resolve_for_record`, `verified_records`,
  `compute_override_rate`, the `submit_override` seam) out of the FastAPI route
  closures and raises domain errors (`ServiceError` subclasses) rather than
  `HTTPException`, so both HTTP and the forthcoming MCP adapter drive one code
  path. Behavior-preserving; FastAPI handlers are now thin adapters.

### Known limitations
- The agent-facing **MCP surface** is designed and decomposed
  (`docs/superpowers/specs/2026-06-03-legis-mcp-surface-design.md`) with WP-M1
  landed; WP-M2..M6 (registry + `legis_explain`, the MCP stdio server, the
  write/governance tools, safety hardening, judge reason-classification) are not
  yet built.
- The git-rename provider to Loomweave is contract-locked but operatively gated on
  Loomweave driving a committed rev-range.
- `HttpLoomweave` runs loopback-unauthenticated; sibling-gated work packages
  (Filigree signature column, live-Loomweave oracle + HMAC auth, operative
  git-rename feed) remain.

[1.0.0rc4]: https://github.com/foundryside-dev/legis/compare/v1.0.0rc3...HEAD
[1.0.0rc3]: https://github.com/foundryside-dev/legis/compare/v1.0.0rc2...v1.0.0rc3
[1.0.0rc2]: https://github.com/foundryside-dev/legis/releases/tag/v1.0.0rc2
[1.0.0rc1]: https://github.com/foundryside-dev/legis/releases/tag/v1.0.0rc1
