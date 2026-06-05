# Validation Report — arch-analysis-2026-06-06-0158

**Validator:** independent analysis-validation gate (read-only)
**Date:** 2026-06-06
**Target of validation:** `docs/arch-analysis-2026-06-06-0158/` deliverables 01–06, evidence base `temp/catalog-*.md` and `temp/AUDIT-*.md`
**Method:** source-level spot-check of highest-stakes claims (Read/Grep), live tooling re-run (ruff, coverage), internal-consistency sweep across 02/04/05, contract-conformance checklist, citation/metric hallucination hunt.

---

## Overall verdict: **PASS-WITH-NOTES**

The analysis is **evidence-backed and accurate** on every high-stakes structural and security claim spot-checked. Every required claim verified to `confirmed` against source at the cited (or adjacent) `file:line`. No claim refuted. No subsystem, finding, or metric was hallucinated. Tooling metrics (mypy-clean, 90% coverage / 3,453 stmts / 329 missed, 2 ruff F401, 63 files, ~7,353 LOC) reproduce against the live tree.

Three **NOTE-level** issues hold it back from a clean PASS — all are label/metric/citation imprecision, none refutes a finding or breaks a contract section, none is BLOCK-level:

- **N1 (consistency):** `04 §6` mislabels finding **M6** as "new this pass / not in prior audits" while `05` and `02` correctly call it a prior-audit baseline. The prior audit *does* contain it (`AUDIT-comprehensive.md:340`). Internal contradiction; underlying defect is source-confirmed.
- **N2 (metric):** `05` reports **480 test functions**; live count is **492** `def test_` across the same 68 files. Minor over-precision; direction (492>480) rules out parametrize-expansion as the explanation.
- **N3 (citation precision):** `05` cites Q-M1 at `service/source_binding.py:82-89`, which is the fail-closed *guard*; the actual "signs unverified" mechanism is the early-return at `:46-50` + write at `governance.py:170`. Substance correct, citation adjacent-not-exact.

---

## Spot-checked claims (evidence-based)

| Claim | Verdict | Evidence (file:line) |
|---|---|---|
| **Q-H1** `_verify_secret` returns actor on `LEGIS_API_SECRET` match **without** consulting `required_scope` | **Confirmed** | `api/app.py:108-116` — secret path returns `LEGIS_API_ACTOR`/default at :116; `required_scope` param (:103) never read on this branch |
| **Q-H1** `/protected/operator-override` is operator-scoped | **Confirmed** | `api/app.py:558-559` route → `Depends(verify_operator)`; `verify_operator`→`_verify_secret(...,"operator")` :142-143 |
| **Q-H1** `/signoff/{seq}/sign` is operator-scoped | **Confirmed** | `api/app.py:677` `post_signoff_sign(... operator=Depends(verify_operator))` — both operator routes thus reachable by a writer secret |
| **C3 RESOLVED** mcp `_verified_records` routes through `service.verified_records`/`TrailVerifier` | **Confirmed** | `mcp.py:649-651` `_verified_records`→`service_verified_records` (import alias :51); `TrailVerifier` imported :25, constructed :141 |
| **M11 RESOLVED** `override_submit` has idempotency-key handling | **Confirmed** | `mcp.py:562` `_override_idempotency_request_hash`; :690-736 override_submit reads `idempotency_key`, computes request-hash, replays via :587-596 |
| **C2 RESOLVED** mcp Wardline routing is server-owned (not caller-chosen) | **Confirmed** | `mcp.py:872-881` rejects caller routing — "Wardline routing is server-owned"; mirrors HTTP |
| **M9 RESOLVED** unknown mcp args rejected | **Confirmed** | `mcp.py:375` `_validate_argument_keys`, invoked :678 |
| **M10 RESOLVED** `poll_handle` integer | **Confirmed** | `mcp.py:620,791` `poll_handle` = integer `seq` |
| **Q-M3 / M6** verify_integrity loop-body `content_hash(rec.payload)` unguarded while `read_all()` guarded | **Confirmed** | `store/audit_store.py:163-166` try/except wraps `read_all()`; :168 `content_hash(rec.payload)` is OUTSIDE the try, inside the loop — `allow_nan=False` raises `ValueError` on tampered non-finite payload |
| **Dependency** enforcement does NOT import `legis.governance` or `legis.policy` | **Confirmed** | `grep src/legis/enforcement/` → 0 matches for governance/policy; all imports are canonical/clock/records/identity/store/intra-enforcement |
| **mcp → api coupling** mcp imports `DEFAULT_GOVERNANCE_DB`/`DEFAULT_CHECK_DB` from `legis.api.app` | **Confirmed** | `mcp.py:115,496` `from legis.api.app import DEFAULT_GOVERNANCE_DB`; :505 `DEFAULT_CHECK_DB` (defined `api/app.py:146-147`) |
| **Q-M1** non-`.py` protected entities sign `source_binding: unverified` (guard fails to catch) | **Confirmed** (substance) | `service/source_binding.py:46-50` returns `status:"unverified"` for non-`.py`; `require_verified_source_binding` :84-85 early-returns (no-op) when not a `.py` locator; `governance.py:157-170` writes that binding into signed extensions. **Cited :82-89 is the guard, not the signing site → N3.** |
| **Q-M6** signoff binding rejects `identity_stable=False` (locator) keys | **Confirmed** | `governance/signoff_binding.py:38-42` exact reject at cited lines |
| **Q-M1 mitigation** `.py` entities DO fail closed on unverified | **Confirmed** | `service/source_binding.py:82-89` raises `InvalidArgumentError` when a `.py` locator isn't verified |
| **ruff** 2 × F401 incl. `Hashable` in `policy/grammar.py:15` "+ one more" | **Confirmed** | live `ruff check src/` → 2 errors: `grammar.py:15` Hashable + `api/app.py:56` `WardlinePayloadError` |
| **coverage** 90% / 3,453 stmts / 329 missed | **Confirmed** | live `coverage report` TOTAL 3453 / 329 / 90% |
| **LOC** mcp 1123, api 830, policy 1072, enforcement 1062, 63 files, ~7,353 total | **Confirmed** | `wc -l`: mcp.py 1123, api/app.py 830, policy 1072, enforcement 1062; `find` → 63 files / 7,353 total |
| **test count** 480 test functions / 68 files | **Partially confirmed** | 68 test-module files correct; `def test_` count is **492**, not 480 → **N2** |

**Tally: 16 confirmed · 1 partially-confirmed (test count) · 0 refuted · 0 unverifiable.**

---

## Internal-consistency findings

| # | Status | Detail |
|---|---|---|
| **N1** | **Contradiction (NOTE)** | **M6 provenance.** `04 §6` (line ~190) lists "M6 unguarded `content_hash` in the verify loop" under *"New findings surfaced this pass (not in prior audits)"* — yet the same `04 §6` table (line 187) calls M6 a baseline finding "Confirmed live," and `05` Q-M3 + `02` Store concern both label it "Baseline M6, PARTIALLY closed." Prior audit `AUDIT-comprehensive.md:340` ("M6. Audit integrity verification can raise decode exceptions") confirms M6 IS a prior-audit finding. So `04 §6`'s "new" tag is wrong; `05`/`02` are correct. Defect itself is source-confirmed (`audit_store.py:168`); only the new-vs-baseline label is inconsistent. |
| ✓ | Consistent | Finding-ID mapping Q-M3↔M6, Q-M1↔M1, Q-M6↔M4, Q-M7↔H6, Q-H1↔H7-adjacent is applied uniformly across 04/05/02. |
| ✓ | Consistent | Resolved/live status agrees across docs for C1/C2/C3/H1/H5/M9/M10/M11 (resolved), M1/M2/M7/H3/H6 (live), M5/M12/M13 (not-reproduced / partial). |
| ✓ | Consistent | `04 §3.4` three-implementation override-rate claim matches `05` Q-H2, `06` item 2, and the diagram dashed CLI-bypass edges (`03:85-86`). |
| ✓ | Consistent | Diagram ↔ catalog: `03` L0–L7 layering (canonical/clock/identity.*/filigree.client/governance.params @L0; resolver/records/store/policy @L1; enforcement @L2; governance/wardline @L3; service @L4; api/mcp/cli @L5–7) matches `02`/`04 §2` exactly. |
| ~ | Minor | `01` lists `api/` 831 LOC; `04`/`wc` use 830 (`api/app.py` 830, package incl. `__init__` 831). Off-by-one, harmless. |

---

## Contract conformance (Option-C / Architect-Ready)

| Deliverable | Required | Verdict |
|---|---|---|
| `02` catalog | Location · Responsibility · Dependencies (bidirectional, file:line) · Concerns · Confidence per subsystem | **PASS** — every subsystem carries all five; edges grepped with `file:line`; inbound+outbound both stated; per-subsystem confidence noted |
| `03` diagrams | present, abstraction-appropriate (C4 levels), match catalog | **PASS** — 5 mermaid: L1 Context, L2 Container (with central partial-seam finding), protected-flow Component, L4 dependency-layer; subsystems/layers match `02` |
| `04` final report | exec summary · subsystem map · cross-flows · strengths · concerns · remediation delta · confidence/limits | **PASS** (with N1 label inconsistency in §6) — all sections present, cross-flows are the load-bearing addition; limitations section honest about cross-repo wire contracts |
| `05` quality | real tooling signals (measured), finding inventory, CI review, verdict | **PASS** (with N2 metric) — mypy/ruff/coverage/CI signals are live-measured and reproduce; per-subsystem coverage table; severity-tiered inventory with status reconciliation |
| `06` handover | risk-ordered roadmap, concrete entry points, architect decisions | **PASS** — Tier 1/2/3 risk-ordered, every item has `file:line` entry point + effort, sequencing + receiving-architect checklist |
| `01` discovery | inventory, stack, entry points, orchestration decision | **PASS** — inventory/LOC/entry-points verified by direct measurement |

---

## BLOCK-level issues

**None.** No claim refuted, no contract section missing, no hallucinated subsystem/finding/metric. The single internal contradiction (N1) is a provenance label, not a defect-existence error, and the defect is source-confirmed.

## Must-fix (NOTE) before downstream consumption

1. **N1** — reconcile M6's new-vs-baseline label in `04 §6` to match `05`/`02` (it is a prior-audit baseline finding, partially closed).
2. **N2** — correct the `05` test-function count (live: 492, not 480) or document the counting method.
3. **N3** — repoint the Q-M1 citation in `05` from `source_binding.py:82-89` (the guard) to the unverified-return site (`:46-50`) and/or `governance.py:170` (the signing-into-extensions site).
