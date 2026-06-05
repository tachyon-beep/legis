# Legis Deferred Follow-ups — Remediation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development`
> or `superpowers:executing-plans` for **Section A only** (the legis-side build tasks).
> Sections B–D are a coordination/tracking ledger, not a build queue — do not "implement"
> them inside legis.

**Status:** Tracking ledger + small legis-side build set. Created 2026-06-05.

**Goal:** Collect every deferred, sibling-gated, out-of-scope, and named-future item still
scattered across the `docs/superpowers/specs/` design records into one place, so the
outstanding work is visible and traceable rather than buried. The legis-side `A`-track and
the `B`-track *legis halves* all shipped (Sprints 0–6, not-yets Tracks 1–6, MCP WP-M1…M6,
home closeout); what remains is (A) two small legis-side hardening tasks tied to a deferred
sibling change, (B) joint steps that legis cannot complete alone, (C) YAGNI-deferred surface,
and (D) informational pre-lock asks to siblings.

**Provenance — where each item came from:**

| Source spec | Items pulled |
| --- | --- |
| `2026-06-02-not-yets-completion-design.md` | Track B: B1, B2, B3, B4 (legis halves done; joint steps open) |
| `2026-06-05-legis-home-closeout-design.md` | Out-of-scope follow-ons; review findings M5/M6 |
| `2026-06-03-legis-mcp-surface-design.md` | §1 named-and-deferred v1 exclusions; §4.2 signed launch token |
| `2026-06-01-legis-roadmap-to-first-class.md` | Appendix A.4 (lineage custody), A.5 (push surface) |
| `2026-06-02-roadmap-conformance-findings.md` | "Gated — legis-side ready, sibling pending" rows; doc-drift (now closed) |

---

## Section A — Legis-side, actionable now (TDD) — ✅ done 2026-06-05

Both tasks harden the additive `/git/rename-feed` surface *before* Clarion re-points to it
(Section B, item B3). They are the two open review findings from
`2026-06-05-legis-home-closeout.review.json` (M5, M6) that were accepted as follow-ups.

> **✅ done 2026-06-05.** A1: `tests/contract/test_git_rename_feed_contract.py` pins the feed's
> object shape + committed-entry field set + Clarion-parse re-point safety. A2: `build_rename_feed`
> now returns an additive `worktree_checked: bool` (= `include_worktree`) disambiguating
> "checked-and-clean" from "not-checked"; `status` semantics unchanged so A1's lock and the
> `/git/renames` byte-compatibility hold. Suite `483 passed, 2 skipped`; mypy clean;
> `policy-boundary-check: PASS`.

### Task A1: Contract-lock test for `/git/rename-feed` (review finding M6)

**Why:** `/git/rename-feed` returns an **object** (`{status, base, head, committed[], working_tree[]}`)
whereas the existing `/git/renames` returns an **array** — the shape Clarion's
`parse_legis_rename_json` currently expects. When the deferred re-point (B3) lands, a drifted
shape would break Clarion silently. Pin it now.

**Files:**
- Created: `tests/contract/test_git_rename_feed_contract.py`

- [x] **Step 1:** Contract-lock test asserts the required top-level keys (superset-tolerant so A2's
  additive field is allowed), `status` ∈ known set, echoed `base`/`head`, and that each `committed`
  entry carries *exactly* the `RenameEvidence` field set; a second test parses `committed[]` the way
  Clarion's `parse_legis_rename_json` does and asserts the rename survives (re-point safety).
- [x] **Step 2:** Green: `2 passed`.

### Task A2: Disambiguate "checked-and-clean" from "not-checked" in the rename feed (review finding M5)

**Why:** `build_rename_feed(include_worktree=True)` with zero working-tree renames currently
yields `status="committed_only"`, conflating "I checked the working tree and it was clean"
with "I did not check the working tree." A consumer cannot tell the difference.

**Files:**
- Modify: `src/legis/git/rename_feed.py`
- Modify: `tests/git/test_rename_feed.py`

- [x] **Step 1:** Failing test `test_worktree_checked_distinguishes_clean_from_unchecked`
  (RED: `KeyError: 'worktree_checked'`). Both calls report `status=="committed_only"`, so only the
  new flag distinguishes them. `/git/renames` untouched.
- [x] **Step 2:** Added `worktree_checked: bool` (= `include_worktree`) to the feed dict; `status`
  semantics and the committed-rename shape A1 locks are unchanged (additive only). Docstring updated.
- [x] **Step 3:** Green — focused `17 passed`, full `483 passed, 2 skipped`, mypy clean.

**Section A exit:** `uv run pytest -q` and `uv run mypy` green; the `/git/rename-feed` shape is
contract-locked and worktree-checked state is observable.

---

## Section B — Sibling-gated joint steps (coordination ledger; legis half already done)

These cannot be completed inside legis. Each row records the legis-side state (built), the
joint step owned by the sibling, and the operative exit criterion. Surface to the relevant
sibling repo; do not "implement" in legis.

| ID | Item | Legis-side state | Joint step (owner) | Operative exit |
| --- | --- | --- | --- | --- |
| **B1** ✅ | Filigree binding signature column (R-2.3-01c) | `attach` sends opaque HMAC `signature` + `signoff_seq` over `{issue_id, entity_id, content_hash, signoff_seq}` | **Filigree** adds opaque `signature`/`signoff_seq` to `entity_associations`, stores verbatim, returns on read | **DONE 2026-06-05** — Filigree landed v25 (`db_entity_associations.py`; NULL when no key; refreshed on re-attach). Field names + signed tuple match legis's attach payload exactly. No legis change needed. |
| **B2** | Live-Clarion oracle + HMAC auth | Env-gated oracle test exists (skips without `CLARION_URL`); `X-Loom-Component` auth header wired + unit-tested vs. fake transport | **Ops/Clarion**: a running reference Clarion; provision `CLARION_URL` (+ `CLARION_LIVE_ORACLE_LOCATOR` for full round-trip) | Env-gated oracle runs green against live Clarion; SEI resolves to a live `clarion:eid:` |
| **B3** ✅ | Operative git-rename feed | `/git/renames` (committed) + additive `/git/rename-feed` (committed + working-tree) both served; shape pinned by A1 | **Clarion** re-points to `/git/rename-feed`'s `.committed` leg; updates `contracts.md` disclosure | **Clarion landed 2026-06-05** (working tree, commit pending): `legis_rename_feed_url` + `parse_legis_rename_feed_json` read `.committed` (committed-only; guards untouched; legacy flat-array → empty, clean switch); contracts disclosure reworded; `cargo test`/`clippy`/`fmt` green. **legis-verified:** A1 contract-lock re-implements Clarion's parser against `.committed` and passes → committed↔parse byte-safe. Optional working-tree enablement (consume `.working_tree` + relax `!base.is_empty()`) NOT taken — separate future commit. |
| **B4a** | Wardline→legis **signed hop** (what legis needs) | legis verifies an optional signed scan body (`artifact_signature`, HMAC-SHA256 `hmac-sha256:v2:` over canonical-JSON of `scan` minus the sig) on `POST /wardline/scan-results`; unsigned retains documented trust-the-agent posture. **legis ingest relaxed 2026-06-05** (it had only ever been tested against one clean fixture): properties carried verbatim (tiers + diagnostics — no tier-conformance), `baselined`/`judged` accepted as non-active (no proof), agent-`waived`/`suppressed` still need proof read **top-level or in properties**; record field `tiers`→`properties` for honesty. | **Wardline** emits the 4 provenance fields + signs the scan; projection now **shrinks** to structural normalization only | Wardline signs by default → legis flips deployment to require it (the intended breaking change); unsigned then rejected |
| **B4b** | `@trust_boundary` decorator grammar (R-2.2-04) | **NOT a legis build.** legis only consumes the trust-tier vocab (`TRUST_TIERS`) as finding `properties`, already in sync | **Wardline** milestone, on Wardline's own schedule | Decoupled from B4a — **not** a precondition for the legis handshake. Constraint: keep the 8 tier names identical |
| **B5** ✅ | Filigree closure-gate **consumption** | legis serves `GET /filigree/issues/{id}/closure-gate` → 200 `{allowed:true}` / 409 `{allowed:false}` / 404 disabled / 500 tamper; all four pinned by tests (`test_combinations_api.py:563-647`) | **Filigree** `close_issue` consults the gate before closing | **DONE 2026-06-05** — Filigree landed `governance.py` + `legis_client.py` (governed = issue has a non-null B1 signature; fail-closed on 404/500/unreachable; off when `LEGIS_URL` unset). Its documented "Wire contract consumed (Legis side)" matches legis's served contract exactly. No legis change needed. |
| **B6** | Live cross-repo handshake tests | legis unit/contract tests green against fakes for every seam above | **Joint** (legis + Clarion + Filigree + Wardline): an integration harness exercising the real handshakes | A cross-repo CI/integration job exercises bind→close, resolve, rename-feed, and signed-scan end-to-end |

**Source:** B1–B4 = `not-yets-completion-design.md` §Track B; B5–B6 = `home-closeout-design.md`
§Out of scope; B3/B2 also appear as Gated rows in `roadmap-conformance-findings.md`.

---

## Section C — Deferred by YAGNI (do NOT build absent a demonstrated user need)

The MCP surface design explicitly named these as *named-and-deferred, no design owed*. They are
recorded so they are not silently forgotten — **not** queued for build. Build only when a real
agent-customer need appears, and design then.

- [ ] **C1 — Idempotency keys** on write tools (`override_submit`). *(mcp-surface §1)*
- [ ] **C2 — Batch policy evaluation.** *(mcp-surface §1)*
- [ ] **C3 — `trail_id` correlation handles** on outcomes. *(mcp-surface §1)*
- [ ] **C4 — `identity_stable` warnings on `policy_explain`.** Deferred, *not killed* — both
  agent-customers flagged it as a future nice-to-have, not a v1 blocker. *(mcp-surface §1)*
- [ ] **C5 — `agent_id` signed launch token (cross-host non-repudiation).** Today `agent_id` is
  bound at subprocess launch, which defeats **in-session spoofing** but not a **lying host**.
  Cross-host non-repudiation needs a signed launch token (the protected cell's HMAC machinery
  already exists). Named future step, explicitly NOT v1. *(mcp-surface §4.2)*

---

## Section D — Informational / pre-lock asks to siblings (not legis builds)

- [ ] **D1 — Clarion lineage-custody option (pre-lock, roadmap A.4).** Legis accepts **Option 3**
  for v1 (store a lineage snapshot hash at each governance decision and detect divergence on
  re-read — already implemented as REQ-L-01 divergence detection). The ask is *explicitness*:
  get Clarion to record which custody option it implements so legis's boundary code stays aligned.
- [ ] **D2 — Lineage push/event surface (informational, roadmap A.5).** Legis is intentionally
  **pull-only** on `lineage(sei)` for v1 (a push/registry/event-bus is exactly the apparatus the
  SEI standard's minimal posture avoids). Flagged as a possible **SEI vN** consideration, *not* a
  lock-blocking demand. No action unless SEI vN reopens it.

---

## Closed (recorded for completeness — do not action)

- **Doc-drift** ("design-ready, not implemented" in roadmap line 58 / README; "none of
  milestones 1–3 built" in roadmap §3). **CLOSED** by `ffbda95` — roadmap now reads
  "implemented through Sprint 6"; README status corrected.
- **All A-track WPs (A1–A12)** and **B-track legis halves** — done (see the design specs'
  `✅ done` markers and the now-removed dated plans, recoverable from git history).

---

## Verification (Section A only) — ✅ 2026-06-05

- [x] `uv run pytest -q` green — `483 passed, 2 skipped` (+3 vs. the 480 baseline; the 2 skips
  are B2's env-gated live oracle).
- [x] `uv run mypy` clean — `no issues found in 63 source files`.
- [x] `/git/rename-feed` response shape is contract-locked and worktree-checked state is
  observable (`worktree_checked` field).
- [x] `uv run legis policy-boundary-check --root src --repo-root .` → `PASS`.

Sections B–D have no legis-side verification — their exit criteria live in the sibling repos
and are listed inline above.
