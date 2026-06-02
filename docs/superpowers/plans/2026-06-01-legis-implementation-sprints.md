# Legis — implementation sprint & work-package breakdown

> **For agentic workers:** this is a **sprint / work-package** plan — the
> altitude *above* a bite-sized TDD plan. It sequences the roadmap
> (`docs/superpowers/specs/2026-06-01-legis-roadmap-to-first-class.md`) into
> demonstrable increments. Each work package (WP) is picked up by writing its
> own bite-sized plan (`superpowers:writing-plans`) **after** its design spike
> fixes the architecture — not before. Do not infer that exact module names,
> signatures, or schemas below are settled; they are scope sketches, and the
> first WP of each new area is explicitly a design spike.

**Goal:** Turn the legis roadmap's seven milestones into an ordered set of
sprints and work packages with explicit deliverables, dependency gates, and
exit criteria — honest about what is autonomous, what is sibling-gated, and
what must be designed before it can be built.

**Status:** legis is **design-ready, not implemented**. There is no code, no
chosen stack, and no architecture yet. Sprint 0 exists to close that gap before
any feature work starts.

**How to read a work package:** each WP lists the roadmap section it serves,
its **gate** (what unblocks it), its **scope**, its **deliverable**, and its
**exit criteria** (observable acceptance conditions, not code). A WP marked
*(design spike)* produces a decision/spec artifact, not a feature.

---

## The dependency spine

```
            ┌─────────────────────────────────────────────────────────┐
 Sprint 0   │ Foundation & contracts (stack, audit store, entity-key)  │  autonomous
            └───────────────┬─────────────────────────┬───────────────┘
                            │                         │
        ┌───────────────────┴────────┐   ┌────────────┴───────────────┐
 Sprint 1│ Operating picture (git/CI) │   │ Sprint 2  Simple tier:     │  autonomous
        │  — standalone "what changed"│   │  chill → coached (the 2×2  │  (S1 ∥ S2)
        └────────────────────────────┘   │  left column + top-right)  │
                                         └────────────┬───────────────┘
                                                      │
                              ┌───────────────────────┴───────────┐
                       Sprint 3│ Complex tier:                     │  autonomous
                              │  structured → protected            │
                              │  (completes the 2×2)               │
                              └───────────────────────┬───────────┘
                       Sprint 4│ Agent-programmable policy grammar  │  autonomous
                              │  (overlaps S3; consumes enforcement)│
                              └───────────────────────┬───────────┘
                                                      │  ── Half 1 complete ──
                              ┌───────────────────────┴───────────┐
                       Sprint 5│ SEI conformance                   │  GATED: Clarion SEI
                              └───────────────────────┬───────────┘
                       Sprint 6│ Suite combinations                 │  GATED: siblings
                              │  (Wardline / Filigree / git-rename) │
                              └────────────────────────────────────┘
```

**Parallelism.** After Sprint 0, **Sprint 1 and Sprint 2 are independent** and
can run concurrently (different surfaces, shared only by the Sprint 0 store).
Sprint 4 (grammar) can overlap Sprint 3 once the Sprint 2 enforcement core
exists to consume it. Sprints 5–6 cannot start until their siblings ship,
regardless of legis progress.

---

## Sprint 0 — Foundation & contracts *(autonomous)*

Everything depends on this. The point of Sprint 0 is to make the SEI-shape
independence and audit-integrity guarantees structural from line one, so later
sprints are swaps and extensions rather than rewrites.

### WP-0.1 — Stack & architecture decision *(design spike)*

- **Serves:** all of §1 (Half 1 foundation).
- **Gate:** none. This is the first thing built.
- **Scope:** Choose language/runtime, persistence engine, and the consumer-API
  shape. Record as an ADR. Realistic options surfaced in **Open decisions**
  below (Python vs. Rust; SQLite for the audit store; HTTP read API mirroring
  Clarion's consumer model).
- **Deliverable:** an ADR fixing stack + store + API; a running skeleton
  service that starts and serves a health endpoint.
- **Exit criteria:** the ADR is committed; the skeleton runs from a single
  documented command; the decision explicitly states how it preserves the
  zero-*human*-config operating-model invariant (the agent operates it; setup
  is plug-and-play).

### WP-0.2 — Append-only audit store

- **Serves:** §1.3 (every cell produces an append-only trail), §2.1 (audit
  spine).
- **Gate:** WP-0.1.
- **Scope:** The core persistence primitive — write-once, ordered, queryable
  records. Mutation and deletion of a written record are rejected, not merely
  discouraged.
- **Deliverable:** a store module that persists and reads back an ordered
  record sequence.
- **Exit criteria:** a written record cannot be mutated or deleted through any
  store API; records read back in a stable total order; an integrity check can
  detect an out-of-band edit (sets up the protected cell's tamper story).

### WP-0.3 — Entity-key abstraction (SEI-ready) *(load-bearing for SEI independence)*

- **Serves:** §2.1, Appendix A (SEI conformance); SEI spec §0.3 (build only
  what is true regardless of final SEI shape).
- **Gate:** WP-0.1.
- **Scope:** An **opaque** entity-key type that today holds a locator and later
  holds an SEI, carrying an `identity_stable` flag. Consumers never parse it.
  No SEI-shape assumptions anywhere — this is what keeps Sprint 5 a value swap
  rather than a schema migration.
- **Deliverable:** the entity-key type + the rule that all records key on it.
- **Exit criteria:** a record keys on the opaque type; switching a key's
  contents from a locator to an SEI-shaped value is a value change with **no
  schema change**; a test asserts no code path parses the key's internal
  structure.

### WP-0.4 — Core record schema

- **Serves:** §1.3 (chill cell record format).
- **Gate:** WP-0.2, WP-0.3.
- **Scope:** The override/verdict record — `policy`, entity-key, `rationale`,
  `agent_id`, `timestamp`, `identity_stable` — designed so judge fields
  (Sprint 2) and HMAC/binding fields (Sprint 3) are **additive**, requiring no
  migration.
- **Deliverable:** the record schema + persistence of a minimal chill-cell
  override.
- **Exit criteria:** a chill-cell override round-trips through the store;
  adding the later judge/HMAC fields is demonstrably additive (a written-down
  extension path, not a reshape).

---

## Sprint 1 — Operating picture *(autonomous; parallel with Sprint 2)*

Legis's standalone value: answer "what changed and what's the check state"
with no sibling present. Serves roadmap §1.1–§1.2.

### WP-1.1 — Git/change surface

- **Serves:** §1.1.
- **Gate:** WP-0.1 (stack/API).
- **Scope:** Read model over the repository: branch relationships (heads,
  merge-base, upstream status), commit metadata (author, message, timestamp,
  diff summary), PR context, and **rename/history evidence** (which symbols
  were renamed, at which commit, pre/post state) — the last is the raw
  material Sprint 6's git-rename provider re-exposes.
- **Deliverable:** the git/change surface over the consumer API.
- **Exit criteria:** legis answers "what changed on branch X / in commit Y /
  in PR Z?" standalone; rename evidence is queryable as structured data, not
  just a diff blob.

### WP-1.2 — CI/check surface

- **Serves:** §1.2.
- **Gate:** WP-0.1.
- **Scope:** Read model over checks: which checks ran (with run ids), what they
  ran against (code state, rule set, policy version), outcomes
  (pass/fail/skipped/timeout) with re-run provenance, and the relationships
  between outcomes, branches, commits, and PRs.
- **Deliverable:** the CI/check surface over the consumer API.
- **Exit criteria:** legis answers "what is the current check state of this
  project, and against what did each check run?" standalone; outcomes carry
  enough provenance to identify the exact code state checked.

---

## Sprint 2 — Simple tier: chill → coached *(autonomous)*

**Status:** ✅ implemented 2026-06-02 (chill + coached, end-to-end; see
`docs/superpowers/plans/2026-06-02-legis-sprint-2-simple-tier.md`).

The casual-coder product, shippable on its own. The left column and top-right
of the 2×2. Serves roadmap §1.3 (simple tier).

### WP-2.1 — Policy-fire → recordable override (chill cell)

- **Serves:** §1.3 (Simple + Judge OFF).
- **Gate:** WP-0.4 (record schema).
- **Scope:** The minimal enforcement loop. A policy fires at the CI/git
  boundary; the agent either refactors or supplies a rationale; an override
  record persists to the append-only trail and is surfaced to the human
  asynchronously. Nothing blocked, nothing silent.
- **Deliverable:** the chill cell, end-to-end.
- **Exit criteria:** a fired policy produces either a correction or a persisted,
  attributable override; the human can read the trail after the fact; no path
  lets a violation pass without *some* recorded event.

### WP-2.2 — Judge integration (coached cell — the config-flag cell)

- **Serves:** §1.3 (Simple + Judge ON).
- **Gate:** WP-2.1.
- **Scope:** An LLM judge inline **before** the override records. Verdicts
  `ACCEPTED` / `BLOCKED`. Turned on by **a single config flag** — no HMAC keys,
  no decay sweep, no deployment ceremony. A `BLOCKED` agent must correct the
  code or sharpen its rationale and re-submit; it **cannot self-clear past the
  judge** (no `OVERRIDDEN_BY_OPERATOR` in this tier). Judge rationale recorded
  verbatim.
- **Deliverable:** the coached cell behind one flag.
- **Exit criteria:** flipping the flag turns chill → coached with no other
  change; a `BLOCKED` override does **not** persist as accepted; the judge
  blocks but never edits code; the judge model identity is recorded on every
  verdict (see Open decisions: judge-model identity).

---

## Sprint 3 — Complex tier: structured → protected *(autonomous)*

**Status:** ✅ implemented 2026-06-02 (structured + protected, end-to-end; HMAC
tamper-binding, operator override, decay sweep, override-rate gate; see
`docs/superpowers/plans/2026-06-02-legis-sprint-3-complex-tier.md` and ADR-0002).

Completes the 2×2. The serious-coder product. Serves roadmap §1.3 (complex
tier). Builds **over** Sprint 2, not beside it.

### WP-3.1 — Block + escalate & structured sign-off (structured cell)

- **Serves:** §1.3 (Complex + Judge OFF).
- **Gate:** WP-2.1 (enforcement core).
- **Scope:** A hard gate with **no LLM in the critical path**: for high-stakes
  policies on protected entity classes, a designated human operator must sign
  off before the gate clears. Structured, multi-step sign-off records.
- **Deliverable:** the structured cell.
- **Exit criteria:** a designated policy cannot clear without a recorded human
  sign-off; no model is invoked on this path; the human is in the loop **by
  exception**, not by default.

### WP-3.2 — Tamper-binding (protected cell, part 1)

- **Serves:** §1.3 (Complex + Judge ON).
- **Gate:** WP-2.2 (judge) + WP-3.1 (complex tier).
- **Scope:** Extend the coached verdict record with two binding layers:
  (1) `file_fingerprint` + `ast_path` binding the verdict to the inspected
  source bytes and AST node; (2) `judge_metadata_signature`
  (`hmac-sha256:v1:<hex>`) over the key fields. HMAC key held outside the
  record. Load-time verification rejects missing/mismatched signatures. Add the
  `OVERRIDDEN_BY_OPERATOR` verdict — a distinct, recorded human-bypass signal.
- **Deliverable:** tamper-bound verdicts with load-time verification.
- **Exit criteria:** a record edited out-of-band is **rejected at load**, not
  silently accepted; `OVERRIDDEN_BY_OPERATOR` is distinguishable from
  `ACCEPTED` in the trail; the HMAC key is never stored alongside the records
  it signs (see Open decisions: HMAC key provisioning).

### WP-3.3 — Lifecycle gates (protected cell, part 2)

- **Serves:** §1.3 (decay sweep, override-rate gate).
- **Gate:** WP-3.2.
- **Scope:** **Decay sweep** — at renewal, existing suppressions re-run through
  the judge and must survive a fresh pass. **Override-rate gate** — a
  rolling-window threshold on the `OVERRIDDEN_BY_OPERATOR` ratio, wired into CI,
  with a minimum-sample floor so small corpora don't trip mechanically.
- **Deliverable:** both lifecycle gates, the override-rate one CI-wired.
- **Exit criteria:** a kept suppression that fails a fresh judge pass is
  flagged at renewal; the override-rate gate fails CI when the threshold is
  breached and passes-with-notice below the sample floor; threshold changes are
  policy (ADR), not a workflow-file edit an agent can tune to pass.

---

## Sprint 4 — Agent-programmable policy grammar *(autonomous; may overlap Sprint 3)*

The highest-leverage un-gated item — the hinge between "standalone tool" and
"first-class Loom citizen." Serves roadmap §1.4.

### WP-4.1 — Policy grammar (boundary types + rules)

- **Serves:** §1.4 (one grammar, open instance set).
- **Gate:** WP-2.1 (an enforcement loop to consume the grammar).
- **Scope:** Turn fixed rules into a **grammar**: agents define new policy
  boundary types and the rules enforced at them, builtins preloaded as
  defaults. A boundary the engine cannot prove emits an honest `UNKNOWN_POLICY`
  event + provenance gap — never a false-green. Same seam shape as Wardline's
  `TaintSourceProvider` / Clarion `Transport`.
- **Deliverable:** the grammar + builtins-as-defaults.
- **Exit criteria:** an agent defines a new policy type with **zero human
  config**; an unprovable agent-defined boundary emits `UNKNOWN_POLICY`, not a
  pass; builtins and agent-authored rules share one grammar.

### WP-4.2 — In-code policy expression

- **Serves:** §1.4 (in-code policy expression).
- **Gate:** WP-4.1.
- **Scope:** A decorator/annotation that moves common governance patterns out
  of external config into the code they govern, carrying `source`,
  `suppresses`, `invariant`, `test_ref`, `test_fingerprint` — behavioural
  evidence, not vibe-justification — plus companion honesty gates (the
  decorator's `test_ref` must point to a real test that exercises the boundary;
  its declared scope must match the code).
- **Deliverable:** in-code policy expression + its honesty gates.
- **Exit criteria:** an in-code policy with a passing behavioural-evidence gate
  suppresses only its declared scope; a stale or scope-violating decorator
  **fails its honesty gate**; the external allowlist is reserved for genuine
  one-offs.

*— End of Half 1: legis is a first-class tool in its own right. —*

---

## Sprint 5 — SEI conformance *(GATED: Clarion ships SEI)*

A consumer-layer change, thin and ready once Sprints 0–4 exist — but it
**cannot start until SEI locks and Clarion ships it** (SEI spec §0.3: defer
anything that pins a specific SEI shape). Serves roadmap §2.1 + Appendix A.

### WP-5.1 — SEI client swap

- **Serves:** §2.1, §5 obligations.
- **Gate:** Clarion ships SEI + advertises the `sei` capability; legis WP-0.3.
- **Scope:** Resolve locator→SEI via Clarion; re-key records on SEI; treat SEI
  opaque; **degrade gracefully** when the `sei` capability is absent (the
  `identity_stable: false` flag from WP-0.3/0.4).
- **Deliverable:** SEI-keyed records with a graceful-degrade path.
- **Exit criteria:** records key on SEI via the WP-0.3 abstraction (value swap,
  no schema change); with the capability absent, every record carries
  `identity_stable: false` and nothing guesses; legis never parses the SEI.

### WP-5.2 — Lineage spine + conformance oracle

- **Serves:** §2.1, Appendix A (REQ-L-01), SEI spec §8.
- **Gate:** WP-5.1; resolution of REQ-L-01 (lineage tamper-evidence approach).
- **Scope:** Consume `lineage(sei)` as the audit spine; an orphaned SEI
  (`resolve_sei → alive:false`) surfaces a **governance gap**; establish
  integrity over the lineage at the governance boundary per the REQ-L-01
  resolution. Run the SEI §8 conformance oracle.
- **Deliverable:** lineage-backed governance + a passing oracle run.
- **Exit criteria:** legis **passes the SEI §8 conformance oracle** (not
  assumed — demonstrated); an orphaned SEI produces a surfaced governance gap,
  never a silent drop; the lineage integrity approach matches the locked
  REQ-L-01 decision.

---

## Sprint 6 — Suite combinations *(GATED: siblings)*

The combination-matrix cells light up. Each WP waits on its sibling **in
addition to** legis Half 1. Serves roadmap §2.2–§2.4.

### WP-6.1 — Wardline + legis governed CI enforcement

- **Serves:** §2.2.
- **Gate:** Wardline's extensible grammar (Wardline roadmap §2.1) + legis
  Sprints 2–4.
- **Scope:** Wardline's `--fail-on` / exit codes become inputs to a legis
  policy that resolves into whichever 2×2 cell the project configured. One-judge
  discipline: legis governs, Wardline does not re-judge. Trust-vocabulary
  convergence — one `@trust_boundary` grammar across the suite, delivering
  elspeth's effects in Loom's own terms.
- **Deliverable:** the governed-CI combination + converged vocabulary.
- **Exit criteria:** a Wardline finding routes through legis enforcement and
  lands in the configured cell; Wardline analyses, legis governs — neither
  duplicates the other; the suite shares one trust grammar (no second naming
  scheme).

### WP-6.2 — Filigree + legis governed issue lifecycle

- **Serves:** §2.3.
- **Gate:** Filigree compatibility + legis Sprints 2–3.
- **Scope:** Governed verification states on Filigree issues — attested
  sign-offs (tamper-bound, SEI-keyed), requirement traceability (RTM), lifecycle
  gates — **without** taking over issue-state semantics (Filigree owns
  lifecycle; legis governs it).
- **Deliverable:** the governed-lifecycle combination.
- **Exit criteria:** a governed sign-off attaches to a Filigree issue with the
  same tamper-binding as a governance verdict; issue-state transitions remain
  Filigree's authority; the binding survives rename/move via SEI.

### WP-6.3 — Git-rename signal provider to Clarion

- **Serves:** §2.4, Appendix A (§A.3 / REQ-L-02).
- **Gate:** SEI §6 provider seam designed (REQ-L-02) + legis WP-1.1 (git
  surface).
- **Scope:** Re-expose WP-1.1's rename evidence as the typed git-rename event
  the SEI matcher consumes ("this symbol renamed, at this commit, from locator
  A to locator B"). Not an identity-authority claim — Clarion still mints,
  re-binds, and resolves.
- **Deliverable:** the git-rename provider feeding Clarion's matcher.
- **Exit criteria:** Clarion's matcher consumes legis's typed event with no
  change to the SEI model; identity decisions remain Clarion's; legis supplies
  signal, not identity.

---

## Gating summary

| Sprint | Autonomy | Gate beyond legis itself |
|---|---|---|
| 0 — Foundation | autonomous | none |
| 1 — Operating picture | autonomous | none |
| 2 — Simple tier | autonomous | none |
| 3 — Complex tier | autonomous | none |
| 4 — Policy grammar | autonomous | none |
| 5 — SEI conformance | **gated** | Clarion ships SEI + REQ-L-01 resolved |
| 6 — Suite combinations | **gated** | Wardline grammar / Filigree compat / SEI §6 seam |

**The honest picture:** Sprints 0–4 are legis's to build alone — five sprints
of greenfield work with no sibling dependency. They are also the *whole* of
"legis as a first-class tool in its own right." Sprints 5–6 are where legis
becomes a first-class Loom *citizen*, and there the wait is genuinely on
siblings (and on the SEI lock), not on legis. "Autonomous" means
sibling-independent; it does **not** mean already-built — none of this exists
yet.

---

## Open decisions (resolve in Sprint 0 / before the gated sprints)

These are real forks the breakdown deliberately does not pre-decide:

1. **Stack (WP-0.1).** Two realistic options: **Python** — shares lineage with
   elspeth's working judge implementation and with Wardline, fastest path to a
   judge gate; or **Rust** — matches Clarion/Filigree's runtime and their HTTP
   read-API patterns, better fit if legis sits next to them in the same
   operational tier. Decide with the ADR; everything downstream is written
   stack-agnostically until then.
2. **Persistence (WP-0.2).** SQLite is the suite default (Clarion, Filigree,
   elspeth's Landscape) and fits an append-only audit store; confirm or
   override in the ADR.
3. **Judge-model identity (WP-2.2).** Configurable per-project vs. fixed by
   legis. elspeth left this open for the production port; it is an
   operator-level decision recorded on every verdict regardless.
4. **HMAC key provisioning (WP-3.2).** How the protected cell's signing key is
   supplied and rotated. This is the one human-setup act in the otherwise
   zero-*human*-config model — defensible as a "human on the loop" governance
   act, but the mechanism must be chosen (env var, secret store, KMS).
5. **REQ-L-01 resolution (blocks WP-5.2).** Which of the three lineage
   tamper-evidence options Clarion commits to (see
   `docs/federation/sei-conformance.md`). Legis can ship v1 on option 3
   (self-established integrity over polled snapshots), but the choice must be
   explicit before Sprint 5 lineage work.

---

## Self-review — roadmap coverage

| Roadmap milestone | Covered by |
|---|---|
| §1.1 Git/change surface | WP-1.1 (+ WP-0.1) |
| §1.2 CI/check surface | WP-1.2 (+ WP-0.1) |
| §1.3 Graded enforcement (the 2×2) | WP-2.1, WP-2.2, WP-3.1, WP-3.2, WP-3.3 (+ WP-0.2/0.4) |
| §1.4 Agent-programmable policy grammar | WP-4.1, WP-4.2 |
| §2.1 SEI-keyed attestations | WP-5.1, WP-5.2 (+ WP-0.3) |
| §2.2 Wardline + legis | WP-6.1 |
| §2.3 Filigree + legis | WP-6.2 |
| §2.4 Git-rename provider | WP-6.3 |
| Appendix A (SEI conformance/REQs) | WP-0.3, WP-5.1, WP-5.2, WP-6.3 |

Every roadmap section maps to at least one work package; every gated WP names
its sibling gate; the SEI-shape-independence obligation is structural from
WP-0.3 forward.

---

## Next step

Pick the first sprint to detail into a bite-sized TDD plan. The natural order
is **Sprint 0**, and within it **WP-0.1 (stack & architecture decision)** —
because the stack ADR unblocks everything and is itself a small, well-bounded
piece of work. Once the stack is chosen, each subsequent WP can be expanded
into a `superpowers:writing-plans` bite-sized plan against the now-real
architecture.
