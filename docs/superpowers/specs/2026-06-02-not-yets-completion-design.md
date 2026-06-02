# Legis "Not-Yets" Completion — Design Spec

**Date:** 2026-06-02
**Status:** Design-ready — decomposition approved, awaiting per-WP implementation plans
**Source of work-items:** `2026-06-02-roadmap-conformance-findings.md` (gap IDs `R-*`) plus the
Known-Limitations blocks of the Sprint 5 (`2026-06-02-legis-sprint-5-sei-conformance.md`) and
Sprint 6 (`2026-06-02-legis-sprint-6-suite-combinations.md`) plans.
**Baseline:** `src/legis` + `tests`, 147/147 green at HEAD `a77bce9`.

## Purpose

Sprints 5 and 6 shipped their stated exit criteria, but each disclosed deferred work, and the
roadmap-conformance audit found additional gaps in the §1 surfaces. This spec collects **every**
disclosed not-yet and audit gap into one decomposed backlog of work-packages (WPs), marks which
are buildable on legis alone versus jointly gated on a sibling repo, sequences them by dependency
and leverage, and gives each a concrete exit criterion. Each WP becomes its own dated TDD
implementation plan (the Sprint 5/6 format) after this spec is approved.

This is a **decomposition spec**, not an implementation plan. It defines *what* and *in what order*;
the per-WP plans define *how*, task by task.

## Scope

In scope: all three categories from the review —
- **A. legis-side buildable now** (no sibling dependency)
- **B. jointly gated on a sibling** (legis side buildable; operative completion needs the other repo)
- **C. documentation drift** (status/wording corrections)

Out of scope: any new capability not traceable to a disclosed limitation or an audit gap ID. No
speculative features. YAGNI applies per-WP.

## Design principles (carried from Sprints 5–6, do not reopen)

1. **SEI stays opaque.** New code that touches identity keys stores/forwards them verbatim; never parses.
2. **Degrade honestly.** Any new resolution/verification path that cannot establish a fact records
   the honest negative (`identity_stable=False`, `alive=False`, divergence flagged) — never guesses.
3. **No new runtime dependency.** New client seams use stdlib `urllib` with an injectable `fetch`,
   mirroring `identity/clarion_client.py` and `filigree/client.py`.
4. **Filigree owns issue lifecycle; Clarion owns identity.** legis supplies signal and attestations;
   it never mutates a sibling's authoritative state.
5. **Tests are evidence.** Every WP exit criterion is proven by a passing test that exercises the
   asserted behaviour (not the happy path only); adversarial/forged-input tests where integrity is claimed.

---

## Work-packages

Each WP lists: the gap/limitation it closes, its category, dependencies, and an exit criterion.

### Track 1 — Protected-tier identity integrity (Sprint 5 follow-through)

Today only the simple-tier `/overrides` record carries the `clarion` two-axis + lineage-snapshot
block, and gap/integrity detection scans only the simple-tier engine trail. Protected and sign-off
records key on the SEI but are not yet orphan-detectable.

**WP-A1 — Carry the `clarion` block onto protected & sign-off records — ✅ done 2026-06-02**
- Closes: Sprint 5 Known Limitation ("extension carried on the simple-tier `/overrides` record only").
- Category: A.
- Dependencies: none.
- Exit: a protected override and a sign-off request, when keyed on an alive SEI, persist
  `extensions["clarion"] = {alive, content_hash, lineage_snapshot}` identical in shape to the
  simple-tier record; a test reads each back and asserts the block. For protected (signed) records,
  the spec must decide and document whether the `clarion` block is inside `signing_fields` — default
  recommendation: identity binding (`entity_key`) stays signed (already true); the two-axis/snapshot
  metadata rides as unsigned extension unless the per-WP plan justifies signing it.

**WP-A2 — Point gap + lineage-integrity detection at the protected store — ✅ done 2026-06-02**
- Closes: Sprint 5 Known Limitation ("gap detection reads the simple-tier engine trail only").
- Category: A.
- Dependencies: **A1** (needs the metadata A1 adds on protected records).
- Exit: `find_orphan_gaps` / `find_lineage_divergence` consume a unified record set
  (`verified_governance_records()` or equivalent spanning simple + protected trails); a test seeds a
  protected attestation on an SEI that Clarion reports `alive:false` and asserts the orphan gap
  surfaces. Protected-trail reads remain HMAC-verified at load (no weakening of the existing guard).

### Track 2 — Sign-off binding hardening (Sprint 6 §2.3)

The binding sent to Filigree is an unsigned `{entity_id, content_hash, actor}` pointer; the §6.2
self-review overstates it as having "the same tamper-binding as a governance verdict," and
`signoff_seq` is returned but never persisted/transmitted (R-2.3-02).

**WP-A3 — legis-side signed `BindingRecord` + persist `signoff_seq` + doc correction — ✅ done 2026-06-02**
- Closes: R-2.3-01c (legis half), R-2.3-02; the §6.2 overstatement.
- Category: A.
- Dependencies: none.
- Design: on a bind, legis appends a `BindingRecord{signoff_seq, issue_id, sei, content_hash, hmac}`
  to its own audit trail, signed with the same HMAC scheme as protected verdicts and verified at
  load (forged record rejected). The Filigree row stays an opaque pointer for now (B1 extends it).
  `bind_signoff_to_issue` persists the binding record and the `signoff_seq` durably rather than only
  adorning the return dict.
- Exit: a test forges a `BindingRecord` and asserts load-time HMAC rejection; a test asserts
  `signoff_seq` survives readback from the legis trail; the §6.2 self-review + Known-Limitation text
  is corrected to describe the Filigree row as a pointer backed by a tamper-bound legis record.

### Track 3 — Wardline routing breadth (Sprint 6 §2.2)

WP-6.1 routes the whole active-defect set through one configured cell, and only two of the four 2×2
outcomes are reachable from the Wardline seam.

**WP-A4 — 4th routing outcome: plain surface, no hard gate — ✅ done 2026-06-02**
- Closes: R-2.2-08.
- Category: A.
- Dependencies: none.
- Exit: `WardlineCellPolicy` gains a `SURFACE_ONLY` (no-gate) member; `route_findings` records the
  finding as surfaced without opening an override or sign-off; a test asserts no governance gate is
  created yet the finding is logged. (Confirm the 4 reachable outcomes map to the README 2×2.)

**WP-A5 — Severity-driven cell selection — ✅ done 2026-06-02**
- Closes: R-2.2-05 + the coarse-routing Known Limitation.
- Category: A.
- Dependencies: A4 (uses the fuller cell set).
- Exit: a per-severity routing map (e.g. CRITICAL→block_escalate, WARN→surface_override,
  INFO→surface_only) selectable per scan; `--fail-on`/exit-class is an *input* to that map, not
  ignored; a test routes a mixed-severity scan and asserts each finding lands in the right cell.
  One-cell-per-scan remains available as a degenerate map for back-compat.

**WP-A6 — Coached Wardline path coverage — ✅ done 2026-06-02**
- Closes: R-2.2-07.
- Category: A.
- Dependencies: none (test-only unless a defect surfaces).
- Exit: a test drives a Wardline finding through `surface_override` with the judge enabled and
  asserts the coached verdict is recorded; if the path is not actually reachable per-scan, the
  defect is fixed in the governor, not papered over.

### Track 4 — Policy grammar completeness (§1.4)

Three of five decorator fields are enforced by the honesty gate; `source` and `invariant` are
carried but never read. The YAML one-off-exemption companion does not exist.

**WP-A7 — Enforce decorator `source` + `invariant` — ✅ done 2026-06-02**
- Closes: R-1.4-06, R-1.4-08.
- Category: A.
- Approved semantics: when a decorator suppresses a policy, the honesty gate **requires** both fields
  non-empty and well-formed — `source` shape-checked as a resolvable citation (in-repo path, URL, or
  commit ref) and `invariant` a non-empty statement surfaced on the governance record. Absent or
  malformed → the gate flags it exactly as it flags a missing `test_ref` today. (Advisory-only and
  test_ref cross-check were considered and rejected: the roadmap insists these fields are
  "behavioural evidence, not vibe.")
- Exit: a decorator missing/empty `source` or `invariant` is rejected by the honesty gate with a
  test asserting the rejection; a well-formed pair passes and `invariant` appears on the record.

**WP-A8 — YAML allowlist companion for one-off exemptions — ✅ done 2026-06-02**
- Closes: R-1.4-11.
- Category: A.
- Dependencies: none.
- Exit: a YAML-backed exemption surface (the decorator's documented companion for one-off
  exemptions) parsed into the existing `AllowlistBoundary` path; a test loads a YAML exemption file
  and asserts a listed entity is exempted, an unlisted one is not, and a malformed file fails closed.
- Note: the roadmap names this a "YAML allowlist"; it was implemented as a TOML file via stdlib
  `tomllib` to hold legis's no-new-dependency posture (substance-equivalent).

### Track 5 — Git/change & CI surface gaps (§1.1/§1.2/§1.3c)

**WP-A9 — Branch upstream status, PR context, pre/post-rename content**
- Closes: R-1.1-04, R-1.1-10, R-1.1-14.
- Category: A.
- Dependencies: none.
- Exit:
  - `BranchInfo` (or a sibling model) reports ahead/behind/tracking against `@{u}`; test on a repo
    with a configured upstream.
  - A PR-context surface (model + `/git/...` endpoint) exposing title/base/head/state, distinct from
    the `CheckRun.pr` FK; test asserts the shape. (If PR metadata has no local git source, the per-WP
    plan defines where it comes from — explicit, not invented.)
  - Rename evidence captures pre/post content (or blob refs); test asserts both states on a
    fabricated rename. This must not break the `/git/renames` Clarion contract (A9 is additive).

**WP-A10 — `rule_set` / `policy_version` round-trip tests**
- Closes: R-1.2-04, R-1.2-05.
- Category: A. Cheap — no production change expected.
- Exit: tests assert both fields survive write→readback through the check-run store; a write-path bug
  nulling either now fails a test.

**WP-A11 — Override-rate gate CI wiring**
- Closes: R-1.3c-17.
- Category: A.
- Dependencies: none.
- Exit: a CI entrypoint (Makefile target / nox / GitHub workflow — chosen in the per-WP plan)
  consumes `GET /governance/override-rate` (or the underlying function) and **fails the build** on
  `status: FAIL` or on a tampered-trail 500; a test or CI dry-run demonstrates the non-zero exit.
  Observable-but-not-build-failing is the current state; this makes it build-failing.

### Track 6 — SEI consumer depth (Sprint 5)

**WP-A12 — Batch-resolve consumption + pre-SEI locator backfill**
- Closes: Sprint 5 Known Limitation ("batch resolve not consumed; bulk backfill deferred").
- Category: A (legis consumes Clarion's existing `resolve:batch`; no sibling change).
- Dependencies: none.
- Exit: a migration path re-keys pre-SEI locator-keyed records via `POST /api/v1/identity/resolve:batch`,
  resolving alive locators to SEIs and leaving dead ones honestly `identity_stable=False`; a test runs
  the backfill against a fake batch client and asserts the re-keying + honest degrade.

### Track B — Jointly gated on a sibling (legis side buildable; operative needs the other repo)

These WPs build and test legis's half offline (against a fake), and explicitly surface the joint step
the sibling must take. Each is "ready, inert until the sibling acts" — a correct not-yet, recorded.

**WP-B1 — Filigree signature column (extends A3)**
- Closes: R-2.3-01c (the Filigree half — the row at Filigree becomes tamper-evident).
- Category: B (Filigree schema/endpoint change). Dependencies: **A3**.
- legis half: send the HMAC computed in A3 as an opaque `signature` on attach.
- Joint step: Filigree adds an opaque `signature` column (or properties field) to `entity_associations`,
  stores it verbatim, returns it on read. Filigree's table is currently fixed
  `(issue_id, clarion_entity_id, content_hash_at_attach, attached_at, attached_by)` with no such field —
  confirmed in `filigree/src/filigree/db_entity_associations.py`.
- Exit (legis side): legis's `attach` carries `signature`; a test asserts it is sent; offline against a
  fake that echoes it, legis verifies the round-trip. Operative completion gated on the Filigree change.

**WP-B2 — Live-Clarion oracle integration + Clarion HMAC auth header**
- Closes: Sprint 5 Known Limitations (oracle runs against a fake, not live Clarion; `HttpClarionIdentity`
  auth out of scope).
- Category: B. Dependencies: none on legis side.
- legis half: an environment-gated integration test target that points the real `HttpClarionIdentity`
  at a live reference Clarion; provision the `X-Loom-Component` HMAC header (decided alongside the
  WP-3.2 HMAC-key provisioning).
- Exit (legis side): the env-gated test exists and is skipped without a `CLARION_URL`; the auth header
  is wired and unit-tested against a fake. Operative completion gated on a running reference Clarion.

**WP-B3 — Operative git-rename feed (WP-6.3 enablement)**
- Closes: Sprint 6 WP-6.3 Known Limitation (inert until Clarion drives a committed rev-range).
- Category: B. Dependencies: none on legis side (provider half is contract-locked).
- Joint step (one of): Clarion tracks a prior-run commit and drives `<base>..HEAD`; **or** legis adds a
  working-tree rename window AND Clarion relaxes its `!base.is_empty()` selector guard.
- Exit (legis side): if the working-tree-window option is chosen, legis implements it behind the existing
  endpoint with a test; the disclosure in `clarion/docs/federation/contracts.md` is updated. Operative
  completion gated on the joint decision.

**WP-B4 — Wardline→legis hop signature + `@trust_boundary` grammar convergence**
- Closes: Sprint 6 Known Limitation (no signature on the Wardline→legis hop; legis trusts the scan body)
  and R-2.2-04 (the `@trust_boundary` decorator grammar is Wardline's milestone).
- Category: B (Wardline-side). Dependencies: none on legis side.
- legis half: accept and verify an optional signature on `POST /wardline/scan-results` if present.
- Exit (legis side): legis verifies a signed scan body against a fake signer; unsigned bodies retain the
  current trust-the-agent posture (documented). Grammar convergence tracked as a Wardline milestone, not
  a legis build.

### Track C — Documentation drift

**WP-C1 — Reconcile stale "not implemented" status lines**
- Closes: the two framing-level contradictions in the findings doc.
- Category: C.
- Exit: `README.md:7`, roadmap line 58, and roadmap §3 lines 335–339 are updated to reflect that
  Sprints 1–6 are implemented (147/147 green) and consistent with the README combination matrix's
  "Live" rows; the §6.2 wording fix from A3 is cross-referenced.

---

## Dependency & sequencing summary

```
A1 ──▶ A2                         (protected-tier integrity; do first, highest leverage)
A3 ──▶ B1                         (signed binding, then the joint Filigree column)
A4 ──▶ A5     A6                  (Wardline breadth; A6 test-only, parallel)
A7     A8                         (policy grammar; parallel)
A9     A10    A11                 (git/CI surface; parallel, all independent)
A12                               (SEI backfill; independent)
B2  B3  B4                        (sibling-gated; legis half anytime, operative deferred)
C1                                (doc; anytime, cross-ref A3)
```

**Recommended order:** A1→A2, then A3 (unblocks B1), then Tracks 3–5 in parallel as capacity allows,
then A12, then the B-track legis halves, with C1 folded in opportunistically. Tracks are otherwise
independent (no shared state) and may be planned/executed concurrently.

## Per-WP plan handoff

After this spec is approved, each WP gets a dated implementation plan under
`docs/superpowers/plans/` in the Sprint 5/6 TDD format (failing test → minimal impl → green → commit),
with a self-review WP-coverage table mapping exit criteria to tests. Legis-only WPs (Track A) are
plannable immediately; Track B plans cover the legis half and explicitly record the joint step as a
gated not-yet; Track C is a single doc-edit plan.

## Risks & non-goals

- **Non-goal:** redefining any sibling's authoritative state (Filigree issue lifecycle, Clarion identity
  minting). All sibling-touching WPs stay on legis's side of the seam.
- **Risk — A1 signing decision:** carrying the `clarion` block into a *signed* protected payload changes
  the signed field-set; the A1 plan must decide signed-vs-unsigned explicitly and prove the existing
  signature still verifies. Default: unsigned extension, signed identity binding unchanged.
- **Risk — A9 PR metadata source:** PR title/base/head/state may have no purely-local git source; the A9
  plan must name the source (e.g. a forge API or an injected context) rather than invent one, consistent
  with the honest-degrade principle.
- **Risk — A5 back-compat:** severity-driven routing must preserve the existing one-cell-per-scan callers
  via a degenerate map; the A5 plan keeps the current API working.
