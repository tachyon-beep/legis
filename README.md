# Legis

Legis is the planned fourth Loom product: the git/CI and governance side of the suite's common operating picture.

## Status

Legis is **design-ready, not implemented**. This repository exists so Clarion, Filigree, and Wardline can review the intended shape of Legis before runtime code lands.

## The Loom suite

Loom is a suite of four tools that share a single substrate: a codebase modelled as **entities**, each carrying typed facts from different tools, all keyed on one durable identity, all freshness-honest, all consumable in one call.

```
                ┌──────────────── the entity (one durable identity: SEI) ───────────────┐
 Wardline ──taint facts──►                                                               │
 Clarion  ──structure/linkages/lineage──►   [ Clarion: identity authority + fact store ] │
 Legis    ──governance attestations──►                                                   │
 Filigree ──issue associations──►                                                        │
                └─────────────────────────────────────────────────────────────────────┘
                                          ▲
                      one freshness-honest read: dossier(entity) / traverse(...)
                                          ▲
                                      a coding agent
```

**Goal state:** a coding agent can ask *"what is true of this function, and what should I do about it?"* and get a complete, current, cited answer — and that answer stays correct when the function is renamed tomorrow.

### Operating model

One root invariant generates the entire stack:

> **Agent-first: humans on the loop, not in the loop.** The agent *operates and extends* the environment; the human *supervises, approves, and governs* from outside the operating cycle.

Consequences:

- **Zero *human* config.** Each tool stands itself up preloaded with agent-calibrated instructions — the instruction layer *is* the configuration mechanism.
- **Agent-programmable extension.** Agents can define new boundary types and the rules enforced at them, expressed in a shared grammar with builtins as preloaded defaults.
- **Legis graded enforcement.** When a policy fires, its mode decides who answers: **block + escalate** (the human operator signs off — in the loop by exception) or **surface + override** (the agent must *recordably* override, and the human reviews the trail asynchronously). The recorded override is what makes "humans not in the loop" safe: an attributable audit event, never a silent pass.

### The combination matrix

Loom's value is the *matrix* of its tools' combinations, not their sum. Each pair is an opt-in layer that lights up a capability neither tool has alone:

| Combination | Capability | Status |
|---|---|---|
| **Wardline + Clarion** | Structure + trust posture in one view (the dossier) | **Live** |
| **Wardline + Filigree** | Findings become tracked work | **Live** |
| **Clarion + Filigree** | Issues bound to live code, surviving refactors | **Partial** — orphans on rename (SEI gap) |
| **Wardline + Legis** | Agent-defined policy enforced at the CI/git boundary | **Future** |
| **Clarion + Legis** | Governance attestations keyed to stable code identity | **Future** |
| **Filigree + Legis** | Governed issue lifecycle — sign-offs, RTM, verification states | **Future** |

Higher-order: **all four** closes the loop — the agent understands the code (Clarion) and its trust posture (Wardline), Legis governs what it may do and records overrides, and every decision and unit of work is tracked against stable identity (Filigree + Clarion).

SEI is the connective tissue of the whole matrix: one non-conformant binding orphans every combination it participates in.

## What Legis is

Legis is the planned Loom authority for:

- project change provenance,
- branch / commit / pull request context,
- CI and check context, and
- governance and attestation context over change.

Legis answers: *what changed, in which branch/commit/PR/check context, and what governance or attestation state exists for that change?*

### The governance 2×2

Legis's enforcement surface is a **2×2**, and the base always stays weightless. Two independent axes: how much governance *structure* you want (simple / complex), and whether an LLM *judge* sits inline (off / on). Each axis is agent-set; every cell is genuinely useful.

|  | **Judge OFF** | **Judge ON** |
|---|---|---|
| **Simple** | **Chill** — CI flags the violation; the agent self-reports a recordable override; the human reviews the trail asynchronously. No LLM, no crypto, no ceremony. | **Coached** — same flow, but the agent pushes against an interactive LLM wall *before* the override records. One config flag. |
| **Complex** | **Structured** — block + escalate; a designated human signs off before the gate clears. Procedural gates, no model in the critical path. | **Protected** — the full machinery: HMAC-signed verdicts, decay sweep, override-rate gate. |

**Chill (simple, judge off).** Legis is invisible until you want it. No judge, no required attestations, no configuration burden. When a policy fires at the CI/git boundary, the agent has a choice: refactor, or make a *recordable override* — an attributable audit event the human reviews from the loop's edge, asynchronously. The trail exists; the human is not blocked. A solo project that never switches Legis on pays nothing.

**Coached (simple, judge on) — the config-flag cell.** The same flow, but an LLM judge evaluates the proposed override *before* it records. This is the casual coder's interactive wall: CI pushes back on a policy-breaking change until the agent corrects the code or explains itself convincingly. Verdicts are `ACCEPTED` or `BLOCKED`; a blocked agent must correct or sharpen its rationale and re-submit — it cannot self-clear past the judge. **A single config flag** — no HMAC key management, no decay sweep, no deployment ceremony. It raises the cost of lazy overrides without raising the cost of honest ones. There is no operator override here; for that authority, upgrade to complex.

**Structured (complex, judge off).** Block + escalate without an LLM in the loop: for high-stakes policies, a designated human operator must sign off before the gate clears. Clear procedural governance with explicit human authority — for teams that want hard gates but no model in the critical path. The human is in the loop by exception, not by default.

**Protected (complex, judge on) — the full machinery.** When both dials are up, Legis adds the cryptographic layer over the coached cell:

- **LLM judge gate** on every new suppression/attestation, now returning `ACCEPTED`, `BLOCKED`, or `OVERRIDDEN_BY_OPERATOR`. The judge *blocks but does not fix* — a BLOCKED verdict returns the failure to the agent; the agent figures out remediation. The judge's rationale is recorded verbatim as audit evidence, bound to the source bytes and AST node it inspected (`file_fingerprint` + `ast_path`), and HMAC-signed so tamper-attempts are detectable.
- **Decay sweep.** At renewal time, existing suppressions re-run through the judge. Decisions to keep an entry must survive a fresh judge pass. Closes the "self-attested, never re-reviewed" failure mode.
- **Override-rate gate.** A rolling-window threshold on the ratio of `OVERRIDDEN_BY_OPERATOR` verdicts. Too many overrides is itself an audit signal — either the policy is miscalibrated, or the operator is breaking their own rules to ship. Either way, it is observable rather than silent.
- **Block + escalate** is also available here, with the added constraint that even a human sign-off produces a tamper-bound record.
- **Audit lineage keyed on SEI.** Every verdict, override, and sign-off is recorded in an append-only trail keyed on Stable Entity Identity so the record survives rename/move.

The elspeth CI judge (`/home/john/elspeth`) is the working design ancestor of the protected cell — it is the "thick version" shipped inside elspeth's own codebase. Legis is where the same mechanisms land as a suite-level, opt-in layer.

### Graded enforcement

Across all four cells, one underlying primitive: when a policy fires, the *cell* decides who answers and what is recorded.

- **Surface + override** — agent may proceed, but makes a recordable override (with, in the coached/protected cells, a judge inline before it records). Human reviews the trail asynchronously. This is the simple tier's active state and the default for any policy that does not require a human gate.
- **Block + escalate** — hard gate; a designated human operator must sign off before the gate clears. The complex tier; used for high-stakes decisions.

Every cell produces an append-only audit trail keyed on SEI, so the record survives refactors.

## What Legis is not

Legis is not:

- a federation registry,
- a hidden suite runtime,
- a replacement for Clarion's code identity authority,
- a replacement for Filigree's workflow authority, or
- a replacement for Wardline's policy analysis authority.

## How Legis fits into Loom

### Clarion

Clarion remains the sole authority for code identity and structure, including SEI. Legis is an SEI *consumer* (governance attestations key on SEI; SEI lineage is Legis's audit spine). Legis is also a *potential provider*: once Legis ships a git interface, it may supply the git-rename and history signals the SEI re-binding matcher consumes — but that does not move identity authority out of Clarion.

### Filigree

Filigree remains the authority for issue and workflow state. Legis adds branch, commit, pull request, and check context around that work, and governs the issue lifecycle through verification sign-offs and requirement traceability — without taking over issue state or work-claim semantics.

### Wardline

Wardline remains the authority for policy findings, taint facts, and dossier truth. Legis contributes the git/CI context that Wardline cites when attaching findings or enforcement gates to real repository state, and adds governed enforcement modes on top of Wardline's analysis.

The division of responsibility is explicit: **Wardline analyses trust; Legis governs it — one judge, not two.** Wardline already has the gate primitive (`--fail-on`, exit codes); Legis adds the governed policy layer around it. This is Wardline's Milestone 5 (governance & trust-vocabulary convergence) from its roadmap — Wardline's half is thin and ready; the gate is Legis existing.

When Legis ships, the Wardline + Legis combination unlocks:
- agent-defined policy, enforced at the git/CI boundary with graded modes;
- trust-vocabulary convergence — one `@trust_boundary` grammar across the suite, delivering elspeth's custody and fabrication-test guarantees in Loom's own terms, not a second naming scheme bolted on beside the first; and
- the full chill → coached → protected progression across the 2×2, with Wardline's findings as the input and Legis's enforcement layer as the output.

## Stable Entity Identity

Legis is an SEI **consumer**, not an authority.

- Clarion mints, persists, re-binds, and resolves SEI.
- Legis treats SEI as opaque: never derived, parsed, or reinterpreted.
- Governance attestations key on **SEI** when the subject is a code entity, so attestations survive rename/move.
- SEI lineage (the append-only event log Clarion maintains) is Legis's ready-made, tamper-evidence-able audit trail.
- If Clarion does not advertise the `sei` capability, Legis degrades honestly rather than guessing.

See `docs/federation/sei-conformance.md` for Legis's specific conformance obligations.

## Goal-state checklist (Legis's contribution)

Legis is complete when:

- [ ] Legis ships as opt-in: invisible to a solo project, complete for a regulated one — all four 2×2 cells work end-to-end
- [ ] Governance attestations key on SEI and survive rename/move
- [ ] `lineage(sei)` is consumed as the audit spine for governance records
- [ ] Chill cell (simple, judge off): surface+override is live; agent overrides produce attributable audit events; human reviews async
- [ ] Coached cell (simple, judge on): LLM wall on overrides behind a single config flag (ACCEPTED / BLOCKED); no HMAC keys, no decay sweep; agent must correct or convince
- [ ] Protected cell (complex, judge on): judge gate adds OVERRIDDEN_BY_OPERATOR; verdicts HMAC-signed and SEI-keyed; decay sweep and override-rate gate wired into CI
- [ ] Structured cell (complex, judge off): human sign-off gate available for high-stakes policies, no model in the critical path
- [ ] Wardline + Legis: Wardline's `--fail-on` / exit codes governed by Legis's policy layer; trust-vocabulary converged to one grammar across the suite
- [ ] Legis governs trust while Wardline analyses it — one judge, not two
- [ ] Filigree + Legis: verification sign-offs and governed issue lifecycle work end-to-end
- [ ] Git-rename / history signal available for Clarion's SEI matcher (if/when the git interface ships)

## Repository layout

- `docs/federation/` — Loom-facing contracts and participation notes
- `docs/design/` — product intent and design notes
- `docs/superpowers/specs/` — approved design specs
- `docs/superpowers/plans/` — implementation plans

## Documents

**Design and federation:**
- `docs/design/legis-charter.md` — authority boundary, operating modes, near-term scope
- `docs/federation/README.md` — Loom participation overview
- `docs/federation/sei-conformance.md` — Legis-specific SEI posture and obligations

**Planning:**
- `docs/superpowers/specs/2026-06-01-legis-federation-repo-design.md` — federation repo design spec
- `docs/superpowers/specs/2026-06-01-legis-roadmap-to-first-class.md` — final-form roadmap (the two halves, the 2×2, dependency gates, SEI conformance)
- `docs/superpowers/plans/2026-06-01-legis-bootstrap.md` — bootstrap implementation plan (docs-first repo)
- `docs/superpowers/plans/2026-06-01-legis-implementation-sprints.md` — sprint / work-package breakdown of the roadmap

**Suite-wide context (lives in wardline/docs/superpowers/specs/):**
- `2026-06-01-loom-goal-state-case-study.md` — Loom goal state, operating model, combination matrix
- `2026-06-01-loom-stable-entity-identity-conformance.md` — SEI standard (Legis is a named consumer in §5)
- `2026-06-01-wardline-roadmap-to-first-class.md` — Wardline's staged path to first-class; Milestone 5 (governance & trust-vocabulary convergence) is gated on Legis existing

**Design ancestor:**
- `/home/john/elspeth` — elspeth's CI judge (`elspeth-lints justify / reaudit / check-judge-coverage / check-override-rate`) is the working "thick version" of Legis's protected cell. The judge gate, `@trust_boundary` decorator, HMAC-signed audit trail, decay sweep, and override-rate gate are all elspeth concepts that Legis inherits as suite-level mechanisms. Elspeth is a standalone project, not a Loom federation member — Legis borrows its *effects* and re-expresses them in Loom's vocabulary.

This repo stays explicit, narrow, and honest about what exists today.
