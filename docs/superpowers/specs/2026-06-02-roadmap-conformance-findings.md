# Roadmap conformance audit — findings

**Date:** 2026-06-02
**Method:** `2026-06-02-roadmap-conformance-audit-method.md`
**Target:** `2026-06-01-legis-roadmap-to-first-class.md` vs. `src/legis` + `tests` (147/147 green)
**Coverage:** §1.1, §1.2, §1.3 (4 cells), §1.4, §2.1+Appendix A, §2.2, §2.3, §2.4+§3 — one reviewer
per subsection, each with an adversarial refutation pass on its Implemented verdicts.

## Verdict tally

| Verdict | Count | Where |
|---|---|---|
| Implemented | ~55 | the bulk of §1.1–§2.4 |
| Partial | 12 | see below |
| Missing | 4 | see below |
| Contradicted | 0 (per-claim) | — but two doc-drift contradictions at the framing level |
| Gated (legis-side ready) | §2.4 operative end | correct not-yet, **not gaps** |
| N/A (principle/ask) | Appendix A asks, §4/§5 | not legis code |

**Headline:** the load-bearing engine (§1.3 simple + complex, §1.4 soundness, §2.1 SEI)
is genuinely built and adversarially survives — HMAC is *verified at load time* (not just
signed), the decay sweep *re-invokes the judge* (not a stub), SEI is *opaque* everywhere,
and `identity_stable:false` is set across all four degrade paths. The gaps cluster in
(a) the git/CI surface's "relationships" bullets, (b) two inert decorator fields + the
missing YAML companion, (c) the Wardline routing breadth, and (d) the Filigree binding
being weaker than "same structure as a verdict."

---

## Missing (4) — promised, no code does it

| ID | Section | Claim | Evidence of absence |
|---|---|---|---|
| **R-1.1-04** | §1.1 | Branch **status relative to upstream** (ahead/behind/tracking) | `BranchInfo` (`git/models.py:9-12`) carries only name/head_sha/is_current; nothing in `git/` computes `@{u}`/ahead/behind. |
| **R-1.1-10** | §1.1 | **Pull-request context / metadata** as a surface | No PR model or git endpoint exists. The only `pr` is an int FK on `CheckRun` (`api/app.py:104`) — a §1.2 concern, not PR title/base/head/state. |
| **R-1.4-11** | §1.4 | **YAML allowlist** reserved for one-off exemptions (the decorator's companion) | No YAML-backed exemption surface in `src/legis`. `AllowlistBoundary` is an in-memory `frozenset` builtin, not the one-off-exemption file the roadmap pairs with the decorator. |
| **R-2.2-08** | §2.2 | 4th routing outcome: **plain surface to the agent, no hard gate** | `WardlineCellPolicy` has only `SURFACE_OVERRIDE` and `BLOCK_ESCALATE`. Only 2 of the 4 listed 2×2 outcomes are routable from the Wardline seam. |

## Partial (12) — present but incomplete, inert, or untested

| ID | Section | Claim | Why Partial |
|---|---|---|---|
| R-1.1-14 | §1.1 | Pre/post-rename **state** captured | Only `old_path`/`new_path`/`similarity`; no content/blob pre/post state. |
| R-1.2-04 | §1.2 | Records which **rule_set** | Persisted+returned in code, but **no test asserts it survives readback** — appears only in input fixtures. A write-path bug nulling it passes all 7 tests. |
| R-1.2-05 | §1.2 | Records which **policy_version** | Same untested-round-trip gap as R-1.2-04. |
| R-1.2-11 | §1.2 | Provenance "enough to **re-run** the same check" | Leans on R-1.2-04/05, whose legs are unasserted. Recording-surface only; no re-run trigger (acceptable for a surface, but the provenance is partly unverified). |
| **R-1.3c-17** | §1.3 protected | Override-rate gate **"wired into CI"** | Gate is computed and exposed (`GET /governance/override-rate` → `status: FAIL`, fail-closes to 500 on tampered trail), but **no CI consumes it**: repo has no `.github/`, workflow, Makefile, nox, or tox. Observable, not yet build-failing. |
| R-1.4-06 | §1.4 | Decorator carries **`source`** | Carried in metadata (`decorator.py:24,54`) but **never read** by any gate — inert. Roadmap insists these fields are "behavioural evidence, not vibe." |
| R-1.4-08 | §1.4 | Decorator carries **`invariant`** | Same: carried but never read/enforced. (3 of 5 decorator fields — `suppresses`, `test_ref`, `test_fingerprint` — are genuinely enforced by the honesty gate; `source` + `invariant` are not.) |
| R-2.2-04 | §2.2 | Trust-vocabulary convergence: one `@trust_boundary` grammar | legis carries Wardline's tier strings verbatim (no `tier1/2/3` rename) — its half is built — but the `@trust_boundary` *decorator grammar* itself is Wardline's Milestone 5, legitimately not in legis. Partial/Gated. |
| R-2.2-05 | §2.2 | Wardline `--fail-on`/exit codes become **inputs** to the cell-resolving policy | Not wired: severity is parsed (`ingest.py:21-30`) but never used to gate; the **caller** picks the cell (`body.cell`). Exit-code→cell mapping is doc-asserted, not implemented. |
| R-2.2-07 | §2.2 | Routes into a **coached or chill** override (simple cell) | One `surface_override` path delegates to `submit_override`, which splits coached/chill by injected judge — but coached-vs-chill is the app engine's config, not selectable per-scan, and **no test exercises the coached Wardline path**. |
| **R-2.3-01c** | §2.3 | Sign-off binding has **"the same tamper-binding structure as a governance verdict — HMAC-signed"** | **Overstated.** The binding sent to Filigree is `{entity_id, content_hash, actor}` — **no signature field**. The HMAC lives only on legis's local protected *verdict* record; structured sign-offs are unsigned by design. The binding is a bare SEI+hash pointer, a weaker subset. |
| R-2.3-02 | §2.3 | **RTM** linkage between issues, attestations, and code entities | issue↔code-entity link + reverse lookup are real, but the **attestation leg is not persisted**: `signoff_seq` is added only to the return dict and never transmitted by `attach`. The durable Filigree record joins to the sign-off only via SEI. |

## Gated — legis-side ready, sibling pending (NOT gaps)

- **§2.4 git-rename provider** (R-2.4-01, R-2.4-05, milestone 7): the rename-event shape exists
  end-to-end (model → `/git/renames` endpoint → passing contract test that mirrors Loomweave's
  `parse_legis_rename_json`). Operative end correctly waits on Loomweave committed-range driving
  (README:50). Caveat: the contract is legis's *mirror* of Loomweave's parser, not a cross-repo
  handshake (sibling repo absent) — consistent with Gated.
- **§2.2 Wardline routing** is `GATED`-typed overall (waits on Wardline grammar), but legis's
  intake/routing side is built and gradeable — hence the Partial/Missing findings above are
  legis-side shortfalls, not "blocked on Wardline."

## Fully clean (no gaps, adversarially survived)

- **§1.3 simple tier (chill + coached):** all record fields persisted; single-flag claim holds
  (zero HMAC/decay refs in the simple path); `OVERRIDDEN_BY_OPERATOR` genuinely unreachable
  from the simple path though defined in the shared enum. Minor: `agent_id` is a harmless
  superset vs. the roadmap's coached format block; `entity`→`entity_key`, `timestamp`→`recorded_at`
  are cosmetic renames.
- **§1.3 complex tier (except R-1.3c-17):** HMAC verified at load on every protected-trail read
  (`verified_governance_records` → `TrailVerifier.verify` → 500), proven by a test that re-chains
  a forged record past the unkeyed integrity check yet the keyed HMAC still rejects it; decay
  sweep re-invokes `judge.evaluate`; signed field-set is a *superset* of the roadmap's six.
- **§2.1 SEI-keyed attestations + Appendix A:** fully complete (32/32), correctly done not Gated
  (Loomweave shipped SEI 2026-06-02). REQ-L-01 is real snapshot-hash **divergence detection**
  (prefix-integrity), not polling dressed up. Pull-only confirmed (no push surface).
- **§2.3 lifecycle authority** (R-2.3-03): legis literally has no issue-status-transition method —
  Filigree authority preserved by construction.

---

## Documentation-drift contradictions (framing-level; capture, don't reconcile)

1. **"Design-ready, not implemented"** — roadmap line 58 and `README.md:7` both say legis is
   not implemented, yet `src/legis` is implemented, 147/147 green, and the README's *own*
   combination matrix marks rows "Live." Update the status lines.
2. **"None [of milestones 1–3] built either"** — roadmap §3 lines 335–339 frame milestones 1–3
   as unbuilt; they have source + green tests, and README:49–51 marks milestone-4/5/6
   capabilities "Live" (Sprint 5 / Sprint 6). Same stale forward-looking drift.

## Update 2026-06-02

Two Sprint-5 Known Limitations not captured as gap IDs above are now CLOSED by
`docs/superpowers/plans/2026-06-02-not-yets-track-1-protected-tier-integrity.md` (WP-A1/A2):
(1) the `loomweave` two-axis + lineage-snapshot block was carried on the simple-tier `/overrides`
record only — protected and sign-off records now also carry the block (unsigned extension;
signed identity binding unchanged); (2) orphan-gap and lineage-integrity detection read the
simple-tier engine trail only — `find_orphan_gaps` / `find_lineage_divergence` now consume
`verified_governance_records()`, scanning the HMAC-verified protected trail as well.
Full suite: 157 passed, 0 warnings.

## Suggested triage order

1. **R-2.3-01c** (Filigree binding unsigned) and **R-1.3c-17** (override-rate gate not CI-wired)
   — these touch the protected-cell tamper guarantees the roadmap leans on hardest.
2. **R-1.4-06/08/11** — the decorator's "behavioural evidence" promise is 3/5 honest + the YAML
   companion is absent; §1.4 is the self-described highest-leverage item.
3. **R-1.1-04/10, R-2.2-08/05/07** — surface/routing breadth gaps.
4. **R-1.2-04/05** — cheap: add round-trip assertions for `rule_set`/`policy_version`.
5. **Doc-drift** — one-line status edits to the roadmap and README.
