# Roadmap conformance audit — method

**Date:** 2026-06-02
**Status:** Reusable audit harness
**Target:** `docs/superpowers/specs/2026-06-01-legis-roadmap-to-first-class.md` vs. the `src/legis` + `tests` tree
**Purpose:** Systematically find where the roadmap promises a capability the code does
not deliver (and, conversely, where docs understate what exists).

The roadmap is a requirements document. This audit is requirements traceability:
every normative line becomes a numbered, atomic **claim**; every claim is graded
against real evidence (source that does the thing + a test that exercises it + that
test passing). Docs about docs — the README status table, the sprint plans — are
**audit targets, not oracles**. They never stand in for code as evidence.

---

## Step 1 — Extract atomic claims

Walk the roadmap top to bottom. Convert every normative statement (capability,
record field, behaviour, gate, guarantee) into one atomic claim with a stable ID
keyed to its section: `R-<section>-<seq>`, e.g. `R-1.3-coached-03`.

A claim is atomic when it has exactly one verifiable assertion. Split compound
sentences. "The decorator carries `source`, `suppresses`, `invariant`, `test_ref`,
and `test_fingerprint`" → five field-presence claims, because each can pass or fail
independently.

## Step 2 — Tag each claim by type (before grading)

The roadmap is not uniformly code-checkable. Mis-tagging is the main source of
false findings. Tag before you grade:

| Tag | What it is | How it's verified |
|---|---|---|
| **CODE** | Legis capability built now: §1.1–1.4, the 2×2 cells, record formats, store, API | Source + passing test. This is the core. |
| **GATED** | Cross-tool seam, §2.x / milestones 4–7, doc *itself* gates it on a sibling | Verify **legis's side of the seam** only. Separate "legis side built" from "sibling genuinely not ready." |
| **PRINCIPLE** | §4 North Star, §5 throughline, Appendix A asks *to Clarion*, design ancestry | Not legis code. Do not grade "missing code." |

Memory note (`clarion-sei-integration-readiness`): Clarion shipped SEI 2026-06-02,
so milestone 4 (SEI-keyed attestations) should be **fully** done, not gated.
Milestone 7's git-rename provider is legitimately "contract-locked, operative
pending Clarion committed-range driving" — that is a correct not-yet, **not a gap**.

## Step 3 — Build the trace map

For each CODE/GATED claim, name the expected evidence before looking at verdicts.
The tree already corresponds to roadmap sections:

| Roadmap section | Expected module(s) | Expected tests |
|---|---|---|
| §1.1 Git/change surface | `src/legis/git/` | `tests/git/`, `tests/contract/test_git_renames_contract.py`, `tests/api/test_git_api.py` |
| §1.2 CI/check surface | `src/legis/checks/` | `tests/checks/`, `tests/api/test_check_api.py` |
| §1.3 chill (simple+judge off) | `enforcement/engine.py`, `records/override_record.py` | `tests/enforcement/test_engine_chill.py` |
| §1.3 coached (simple+judge on) | `enforcement/judge.py`, `engine.py` | `test_engine_coached.py`, `test_judge.py`, `test_engine_flag_flip.py` |
| §1.3 structured (complex+judge off) | `enforcement/signoff.py`, `governance/signoff_binding.py` | `test_signoff.py`, `tests/governance/test_signoff_binding.py` |
| §1.3 protected (complex+judge on) | `enforcement/protected.py`, `signing.py`, `lifecycle.py`, `verdict.py` | `test_protected_*.py`, `test_signing.py`, `test_decay_sweep.py`, `test_override_rate.py`, `test_trail_verify.py` |
| §1.4 policy grammar | `policy/grammar.py`, `policy/decorator.py` | `tests/policy/` (incl. `test_honesty_gate.py`) |
| §2.1 SEI-keyed attestations | `identity/`, `store/audit_store.py` | `tests/identity/`, `tests/api/test_sei_api.py`, `tests/conformance/` |
| §2.2 Wardline + legis | `wardline/governor.py`, `wardline/ingest.py` | `tests/wardline/`, `tests/api/test_combinations_api.py` |
| §2.3 Filigree + legis | `filigree/client.py`, `governance/signoff_binding.py` | `tests/filigree/`, `tests/api/test_complex_api.py` |
| §2.4 Git-rename provider | `git/surface.py` (renames) | `tests/contract/test_git_renames_contract.py` |

A claim with no candidate evidence module is itself a finding (likely Missing).

## Step 4 — Grade each claim against evidence

Three checks per CODE claim: (a) does the artifact exist? (b) does it actually do
what the claim says — **read the code, not the filename**? (c) is there a test that
exercises it, and does it pass? Verdict set:

- **Implemented** — code does the thing; passing test exercises it; cite `file:line` for both.
- **Partial** — present but incomplete, or test exists but xfails/errors/doesn't cover the assertion.
- **Missing** — no code does the thing.
- **Contradicted** — code does something that conflicts with the claim.
- **Gated** — legis-side seam is built; the unbuilt remainder is a sibling's, and the doc says so. Not a gap.
- **N/A** — PRINCIPLE-tagged; not legis code.

## Step 5 — Adversarial pass on every Implemented verdict

The dominant failure mode is the **false-green**: a file named `judge.py` exists, so
the claim "looks" satisfied. For each Implemented verdict, a skeptic tries to
*refute* that the code does what the claim says — not that a file exists. Common
refutations: the function is a stub/`NotImplementedError`; the test asserts the
happy path only; the claimed field is accepted but never persisted or verified; the
HMAC is computed but never checked at load time. Downgrade to Partial on a
successful refutation.

## Step 6 — Report

Produce a traceability matrix: one row per claim — `ID | section | type | claim
(verbatim or tight paraphrase) | verdict | source evidence | test evidence |
note`. Then a gap list ranked by milestone and severity (Missing/Contradicted in
load-bearing §1.3/§1.4 first). Write it to
`docs/superpowers/specs/2026-06-02-roadmap-conformance-findings.md`.

## Known finding to capture up front (do not reconcile away)

Roadmap line 58 and `README.md` top-line both say legis is *"design-ready, not
implemented"* — yet `src/legis` is plainly implemented, the suite is 147/147 green,
and the README's own combination matrix says "Live." That is a real
documentation-drift contradiction. Record it; don't let it confuse the per-claim
framing.

## Execution shape

Fan out one reviewer per roadmap subsection (§1.1, §1.2, §1.3×4 cells, §1.4, §2.1–2.4),
each producing a schema-conforming partial matrix for its claims, then a verifier
pass runs the Step-5 adversarial refutation on the Implemented rows, then merge.
Sections are independent (no shared state), so they run concurrently.
