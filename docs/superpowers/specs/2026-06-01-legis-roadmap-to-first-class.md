# Legis — the road to first-class (roadmap & final form)

**Date:** 2026-06-01
**Status:** Living reference (roadmap; companion to the Weft goal-state case study)
**Scope:** Legis's **final form** as a first-class, governance-capable CI/git layer
— and the staged path to it — given the Weft operating model and invariants
settled across the 2026-06-01 design sessions. Sibling to
`2026-06-01-weft-goal-state-case-study.md` (suite umbrella) and
`2026-06-01-weft-stable-entity-identity-conformance.md` (the SEI keystone).
Design ancestor: `/home/john/elspeth` (the working "thick version" of the
protected cell — legis borrows *effects*, not vocabulary).

> **The thesis filter governs every line of this roadmap.** "Bring it to
> first-class" means first-class *capability* delivered as **opt-in layers** —
> **never** first-class *weight* in the base. The governance surface is a 2×2:
> simple/complex × judge-off/judge-on. A programmer running a personal project
> gets CI that records overrides and nothing else. A programmer who wants agents
> to push against an interactive LLM wall turns on one config flag. A programmer
> who needs cryptographic proof-of-review turns on the full protected cell.
> Each axis is independently agent-set; the human supervises from the loop's
> edge. Invisible until wanted; complete when switched on.

---

## 0. The final form, in one sentence

> Legis becomes the **best governance + git/CI layer** for agent-operated
> projects — **and** a first-class Weft citizen: an SEI-keyed, audit-grade
> enforcement engine whose policy grammar is agent-programmable, whose
> attestations survive refactors, and which **governs** (never re-analyses)
> trust while Wardline analyses it.

"First-class" has **two co-equal halves**. The standalone tool bar comes
*first*: legis must be able to describe project change state and enforce
governance policies with no sibling present. The suite-integration bar comes
*second*.

Legis's enforcement surface is a **2×2**: governance complexity (simple /
complex) orthogonal to the judge (off / on). Every cell is genuinely useful;
the axes are independently configurable.

|  | **Judge OFF** | **Judge ON** |
|---|---|---|
| **Simple** | **Chill.** CI flags the policy violation; agent self-reports an override with a rationale; the human reviews the trail asynchronously. No LLM, no crypto, no ceremony. Audit event exists; nothing is silent. | **Coached.** Same flow, but the agent pushes against an **interactive LLM wall** *before* the override records. Judge accepts or blocks; a blocked agent must correct the code or sharpen its rationale — it cannot self-clear past the judge. A single config flag — no HMAC keys, no decay sweep. |
| **Complex** | **Structured.** Block + escalate for high-stakes policies; a designated human must sign off before the gate clears. Procedural governance without LLM judgment — clear human gates, no model in the critical path. | **Protected.** Full machinery: HMAC-signed judge verdicts bound to source bytes, decay sweep at renewal, override-rate gate in CI. The judge is both the interactive challenger and the cryptographic record-keeper. |

The **coached cell** (simple + judge on) is the novel contribution. It is not
a compliance system — it is a **config option** that gives agents an
honest challenger: "you want to override this policy; make your case to an
LLM, and if the LLM blocks you, figure out a better answer." No key management.
No deployment ceremony. Just an interactive wall that raises the cost of lazy
overrides without raising the cost of honest ones.

The **protected cell** is the elspeth CI judge pattern re-expressed in Weft's
vocabulary — the "thick version" that carries cryptographic guarantees. It
builds on top of the coached cell, not beside it.

Legis is **implemented through Sprint 6**: every milestone in §1 (git/CI surface,
the graded 2×2 enforcement engine, the agent-programmable policy grammar) is built
and tested, and the Half-2 suite layers (§2) are live — except the git-rename
provider to Loomweave, which is contract-locked, operative pending Loomweave's
committed-range driving. The sections below describe the as-built shape; the
"greenfield" framing is the original forecast, retained for its design rationale.

---

## 1. Half 1 — legis as a first-class tool in its own right
*(the foundation; sibling-independent — built, Sprints 0–6)*

This is where "first-class" starts for legis. None of this half is gated on a
sibling tool, and it is now built (Sprints 0–6) from charter and design docs —
the git/CI surfaces, the enforcement engine, and the policy grammar are all live.
This is what lets the Weft combination matrix light up.

### 1.1 Git/change surface *(greenfield; autonomous)*

Legis's first standalone capability: describe project change state in a form
that agents and the suite can consume.

- **Branch relationships.** Which branches exist, their heads, merge-base, and
  status relative to the upstream.
- **Commit metadata.** Author, message, timestamp, diff summary, relationship
  to the branch graph.
- **Pull-request context.** PR metadata and the check outcomes associated with
  it.
- **Rename/history evidence.** The signal the SEI matcher needs: which symbols
  were renamed, at which commit, with what pre- and post-rename state. This is
  legis's eventual provider contribution to Loomweave (§2.4) — but the surface
  must be built first.

The git interface is both a standalone capability (legis can answer "what
changed?") and the upstream dependency of every integration with Loomweave.

### 1.2 CI/check surface *(greenfield; autonomous)*

Describe build and check state in a form that governance and the suite can
consume.

- **Which checks ran** on a given commit/PR, with their run identifiers.
- **What they ran against** — which code state, which rule set, which policy
  version.
- **Outcomes:** pass / fail / skipped / timeout, with enough provenance to
  re-run the same check against the same state.
- **Relationships** between check outcomes, branches, commits, and pull
  requests — the context that Wardline needs when it reports a finding against
  live project state.

A standalone legis with git/CI surfaces can answer: *"what is the current check
and governance state of this project?"* — with no other Weft tool present.

### 1.3 Graded enforcement engine — the 2×2 *(greenfield; autonomous; load-bearing)*

The four cells from §0, implemented. This is the single most important Half 1
deliverable, because it is what the Wardline + legis and Filigree + legis
combinations *consume*. The one shared primitive across all cells: when a
policy fires, the *cell* decides who answers and what is recorded. Every cell
produces an append-only audit trail; they differ in how much machinery sits
between the policy firing and the record being written.

#### Simple + Judge OFF — chill

Agent is allowed to proceed but must make a *recordable override*: a structured
audit event (`policy`, `entity`, `rationale`, `agent_id`, `timestamp`) stored
in the append-only trail. The human sees it asynchronously. Nothing is blocked;
nothing is silent. This is the default for every policy that does not require a
human gate or judge.

#### Simple + Judge ON — coached (the config-flag cell)

Same flow, but an LLM judge evaluates the proposed override *before* it
records. **This is a single config flag** — no HMAC key management, no decay
sweep infrastructure, no deployment ceremony. Verdicts: `ACCEPTED`, `BLOCKED`.
A BLOCKED verdict returns the failure to the agent with the judge's reasoning;
the agent revises its argument or fixes the underlying code, then re-submits.
A blocked agent cannot self-clear past the judge: there is no
`OVERRIDDEN_BY_OPERATOR` in this cell — it is the simple tier, and for
operator-override authority a project upgrades to complex. The wall is what the
user asked for: the agent must *correct or convince*, not merely assert.

What the coached cell provides that a raw surface+override does not: it raises
the cost of *lazy* overrides without raising the cost of *honest* ones. An
agent that genuinely understands why the policy fired will clear the judge
easily. An agent that is guessing, or repeating a boilerplate rationale, will
be blocked and pushed to actually think. The judge does not fix anything; it
is an interactive wall, not a code generator.

Verdict record format (simple + judge on):

```
policy:        <policy id>
entity:        <locator | SEI once available>
rationale:     <agent-supplied>
judge_verdict: ACCEPTED | BLOCKED
judge_model:   <model id>
judge_rationale: <verbatim, stored for async human review>
recorded_at:   <timestamp>
```

No signature. No HMAC key. The record is honest and human-readable; tamper
resistance is a complex-tier upgrade.

#### Complex + Judge OFF — structured

Block + escalate: a designated human operator must sign off before the gate
clears. Used for high-stakes policies on protected entity classes. No LLM in
the critical path — clear procedural governance, human authority explicit.
Structured sign-off records with multi-step attestation workflows. The human
is in the loop **by exception**, not by default.

#### Complex + Judge ON — protected

The full machinery, layered over the coached cell:

- **Tamper-bound verdict records.** The coached cell's record extended with two
  binding layers: (1) `file_fingerprint` + `ast_path` bind the verdict to the
  source bytes and AST node the judge inspected; (2)
  `judge_metadata_signature` is `hmac-sha256:v1:<hex>` over the key fields —
  verdict, model, timestamp, rationale, fingerprint, ast_path. The HMAC key is
  held outside the audit record. Signature verification at load time rejects
  missing or mismatched signatures: post-hoc edits are detectable, not just
  noticed. `OVERRIDDEN_BY_OPERATOR` is a first-class verdict here — distinct
  from `ACCEPTED`, it is the signal that a human used authority to bypass the
  judge.

- **Decay sweep.** At renewal time, existing suppressions re-run through the
  judge. Decisions to keep an entry must survive a fresh judge pass. Closes the
  "self-attested, never re-reviewed" failure mode that the coached cell does not
  address (coached records are point-in-time; protected records have a
  renewable lifecycle).

- **Override-rate gate.** A rolling-window threshold on the ratio of
  `OVERRIDDEN_BY_OPERATOR` verdicts. Too many overrides is an audit signal:
  either the policy is miscalibrated or the operator is bypassing their own
  rules to ship. Either way, observable rather than silent. Wired into CI.

- **Block + escalate** is also available in the protected cell, with the
  additional constraint that even a human sign-off produces a tamper-bound
  record — sign-offs in the protected cell are cryptographically bound to the
  entity state at signing time.

Every cell produces an audit trail keyed to SEI (once SEI is available —
pre-SEI, keyed on the locator with an explicit `identity_stable: false` flag).

**Design ancestry.** The two-pillar pattern (quality: judge gate; volume
reduction: in-code policy expression), the tamper-bound HMAC record structure,
the decay sweep, and the override-rate gate are proven in elspeth's CI judge
system (`/home/john/elspeth`). Legis inherits the *effects and guarantees* of
those mechanisms, re-expressed in Weft's vocabulary — not elspeth-internal
tool names or the `tier1/2/3` naming.

### 1.4 Agent-programmable policy grammar *(greenfield; autonomous; highest-leverage)*

Turn legis from a fixed-rule enforcement layer into a *grammar*: agents define
new policy boundary types and the rules enforced at them, with builtins as
preloaded defaults. This is the most-powerful-version of legis's governance
model, and the *substrate* for both the judge (§1.3 — it is the policy
vocabulary the judge evaluates overrides against) and Wardline
trust-vocabulary convergence (§2.2).

- **One grammar, open instance set.** The grammar (what a policy boundary *is*,
  how governance composes, what fail-closed means) is singular and shared; the
  boundary types and rules expressed in it are an open, agent-authored set.
  Same seam shape as Wardline's `TaintSourceProvider`, Loomweave `Transport`, and
  elspeth's pluggy plugin architecture.

- **In-code policy expression.** A companion to the YAML allowlist: a decorator
  (or annotation) that moves common governance patterns out of external
  configuration and into the code they govern, reducing the governance artifact
  surface. The decorator carries `source`, `suppresses`, `invariant`,
  `test_ref`, and `test_fingerprint` — behavioural evidence, not
  vibe-justification. The code carries the policy; the YAML allowlist is
  reserved for genuinely one-off exemptions.

- **Soundness is inherited, not waived.** An agent-defined policy boundary the
  engine cannot prove emits an honest `UNKNOWN_POLICY` event and a provenance
  gap, never a false-green. Agent-authored ≠ trusted-by-fiat.

- **Zero *human* config.** The agent authors the extension; no human fills in
  a form. This is the operating model (humans on the loop, not in it) applied
  to legis's configuration surface.

---

## 2. Half 2 — first-class Weft citizen
*(the layers; gated on siblings or on legis milestones 1–3)*

### 2.1 SEI-keyed attestations *(gated on Loomweave shipping SEI)*

Key governance attestations (verdicts, sign-offs, policy decisions) on **SEI**
instead of the qualname locator, so they survive the renames and moves that
developers actually perform instead of silently orphaning. Treat SEI opaque;
degrade gracefully when Loomweave lacks the `sei` capability. Legis's half is a
consumer-layer change; the gate is Loomweave implementing SEI.

Once SEI is available:

- Every verdict row carries an SEI as its entity key, not a locator.
- `lineage(sei)` from Loomweave becomes legis's audit spine — the append-only
  event log (born / locator_changed / moved / orphaned / superseded) is the
  same trail legis needs to track "this entity had an attestation; here is
  every identity event since." Loomweave's lineage is tamper-evidence-**able**;
  legis's protected cell establishes integrity over it at the governance
  boundary (custody axiom: re-reading persisted data is a fresh boundary; legis
  must re-verify, not assume).
- When an SEI is orphaned, legis surfaces a governance gap: "this entity had an
  attestation; its identity is now orphaned; the attestation is in limbo." This
  relies on Loomweave's existing §4 `resolve_sei` behavior (`alive: false,
  lineage: [...]`) — see Appendix A.

### 2.2 Wardline + legis governed CI enforcement
*(gated on Wardline grammar + legis milestones 1–3)*

Agent-defined policy enforced at the git/CI boundary with graded modes.
Wardline already has the gate primitive (`--fail-on`, exit codes); legis adds
the governed policy layer around it. Wardline's Milestone 5 ("governance &
trust-vocabulary convergence") is gated on legis existing — legis's half is
defining the intake.

- **One judge, not two.** Wardline analyses trust; legis governs it. A function
  flagged by Wardline is input to legis's enforcement decision; legis decides
  BLOCK / SURFACE / escalate — Wardline does not re-judge.
- **Trust-vocabulary convergence.** One `@trust_boundary` grammar across the
  suite, delivering elspeth's custody and fabrication-test guarantees in Weft's
  *own* terms — not a second naming scheme beside the first.
- **Graded enforcement at the gate.** `--fail-on` / exit codes from Wardline
  become inputs to a legis policy that resolves into whichever 2×2 cell the
  project has configured: block + escalate (complex), a coached or chill
  recordable override (simple), or a plain surface to the agent with no hard
  gate.

### 2.3 Filigree + legis governed issue lifecycle
*(gated on Filigree compatibility + legis milestones 1–3)*

Governed verification states on Filigree issues: sign-offs, requirement
traceability (RTM), and lifecycle gates without taking over issue state
semantics. Filigree already has a verification state machine; legis governs it.

- **Verification sign-offs.** An attested sign-off on a Filigree issue that
  carries the same tamper-binding structure as a governance verdict —
  structured, HMAC-signed, SEI-keyed.
- **RTM (requirements traceability).** Governed linkage between issues,
  attestations, and the code entities they concern.
- **Legis governs; Filigree owns lifecycle.** Issue state transitions remain
  Filigree's authority; legis adds a governance layer on top without freezing
  the surface.

### 2.4 Git-rename signal provider to Loomweave
*(gated on SEI §6 provider seam + legis git interface from §1.1)*

Once legis has a git interface, it becomes the natural supplier of the
git-rename and history signals the SEI matcher consumes. The SEI spec's §6
already names this seam: "the matcher consumes 'a git-rename signal,' not
Loomweave's git code." Legis claims that provider seam.

This is not an identity-authority claim. Loomweave remains the sole authority for
minting, persisting, re-binding, and resolving SEI. Legis supplies
a typed event ("this symbol was renamed, at this commit, from this locator to
this locator") and Loomweave's matcher consumes it. The seam design is legis's
concern; the identity decision is Loomweave's.

---

## 3. Staging — by capability milestone and dependency gate

No milestones before §1 shipped. The milestones below are **proposed**,
framed by what unblocks each — not numbered to imply the committed weight of
a shipped baseline.

| # | Milestone | Gate | Legis's position |
|---|---|---|---|
| 1 | **Git/CI operating picture** — git change surface + CI check surface | none (sibling-independent) | greenfield — legis must build it |
| 2 | **Graded enforcement engine** — surface+override, block+escalate, judge gate, tamper-binding, decay sweep, override-rate gate | none (sibling-independent) | greenfield; all four 2×2 cells (chill / coached / structured / protected) live here |
| 3 | **Agent-programmable policy grammar** — grammar + builtins-as-defaults + in-code policy expression | none (sibling-independent) | greenfield; **highest-leverage un-gated item**; the hinge between "standalone tool" and "first-class Weft citizen" |
| 4 | **SEI-keyed attestations** — attestations key on SEI; consume lineage as audit spine; graceful degrade | Loomweave ships SEI | consumer-layer change; thin & ready once milestones 1-3 exist; waits on sibling |
| 5 | **Wardline + legis governance** — governed CI gate, one-judge discipline, trust vocabulary convergence | Wardline grammar (Wardline §2.1) + legis milestones 1–3 | waits on Wardline and legis; legis's intake surface must be ready |
| 6 | **Filigree + legis governed lifecycle** — sign-offs, RTM, governed verification states | Filigree compat + legis milestones 1–3 | waits on legis and sibling compatibility |
| 7 | **Git-rename signal provider to Loomweave** — legis supplies git-rename event to SEI matcher | SEI §6 provider seam + legis milestone 1 git interface | legis claims the §6 seam once the git surface is built |

**Honest gating picture.** Milestones 1–3 were legis's to build alone — none
gated on a sibling — and are now built (Sprints 0–6: the git/CI surface, the
graded enforcement engine, the policy grammar). Milestones 4–6 are live
(SEI-keyed attestations; the Wardline and Filigree combinations); milestone 7
(git-rename provider) is contract-locked, operative pending Loomweave's
committed-range driving. The sibling-independence of 1–3 is why they could ship
first; 4–7 layer on siblings in addition to legis's own engine.

---

## 4. North Star — governance without a single language, done honestly

The *contracts* go project-agnostic: the policy grammar, the audit record
format, the governance verification protocol, and the SEI-keyed attestation
schema are language-independent and toolchain-independent. Other governance
producers can integrate the same store; the same graded enforcement engine
works across Python, Rust, TypeScript, or any language a Loomweave plugin can
describe.

The enforcement engine and git interface **stay focused**: the governance engine
is not a rewrite of Wardline's analysis, and the git interface is not a
replacement for `git` itself. Other languages, other workflows — other
*configurations* of the same grammar, not a rewrite of the engine. This keeps
"the most general version of the idea" honest without committing legis to
scope it cannot execute.

---

## 5. The throughline

Every item above is an **opt-in layer**, and the 2×2 grid is independently
navigable. The base stays weightless; the agent drives; the human supervises
from the loop's edge.

- A programmer who never switches legis on pays nothing and still gets the
  dossier, taint analysis, and issue tracking from the rest of the suite.
- A programmer who wants agents to push back against a policy wall turns on
  one config flag — no key management, no deployment, no new ceremony.
- A programmer who needs cryptographic proof of review enables the protected
  cell — HMAC keys, decay sweep, override-rate gate, tamper-bound records.
- A programmer who wants human sign-off authority without LLM judgment uses the
  structured cell — clear procedural gates, no model in the critical path.

All four are the same tool. All four are first-class.

---

## Appendix A — SEI conformance position and pre-lock requirements

This is legis's formal input to the SEI pre-lock requirements intake
(SEI spec §0.5).

### A.1 Conformance obligations (SEI spec §5)

Legis's §5 obligations, confirmed:

- Governance attestations keyed on **SEI** when the subject is a code entity.
  Locators are never the key for a binding that must survive refactors.
- SEI consumed as **opaque**: legis must never derive, parse, or reinterpret
  its internal structure.
- Consume `lineage(sei)` as the audit trail spine. The lineage event log
  (born / locator_changed / moved / orphaned / superseded) maps directly to
  governance lifecycle states.
- **Degrade honestly** when Loomweave does not advertise the `sei` capability:
  set an explicit `identity_stable: false` flag on every verdict row; do not
  guess or silently fall back to locators as if they were stable.
- As the suite's planned git-interface owner, may **supply** the git-rename
  signal the §3 matcher consumes — without moving identity authority out of
  Loomweave (see §A.3).

### A.2 Reliance on existing §4 behavior (not a new requirement)

Legis's governance gap surfacing ("this entity had an attestation; its
identity is now orphaned; the attestation is in limbo") relies on Loomweave's
**existing** §4 `resolve_sei` contract: an orphaned SEI returns
`{ alive: false, lineage: [...] }`, not merely a 404. This is already specified;
legis records *reliance* on it, not a request to add it.

### A.3 Legis claims the §6 git-rename provider seam

SEI spec §6 already names the seam: "if/when `legis` ships a git interface,
that signal can move behind it with no change to the SEI model — the matcher
consumes 'a git-rename signal,' not 'Loomweave's git code.'" Legis formally
claims this seam. The provider interface design (what a git-rename event looks
like on the wire) should be shaped with legis as the planned first implementer,
so the seam does not calcify as Loomweave-internal before legis ships. This is a
*sequencing* ask, not a wire-contract change.

### A.4 Lineage tamper-evidence (concrete emerging requirement)

**REQ-L-01.** Legis's protected cell is built on the custody axiom:
re-reading persisted data is a fresh boundary; integrity must be re-established
at the governance boundary, not assumed from the store. Loomweave's lineage is
described as tamper-evidence-**able** (capable of providing tamper evidence)
but the SEI spec §2.2 does not specify *how* — it says "gives legis a
ready-made, tamper-evidence-able audit trail." This is the seam legis needs
resolved before lock: what does "tamper-evidence-able" mean concretely for the
`lineage(sei)` response?

Options (not prescriptive — legis raises the question for Loomweave to decide):
1. The `lineage` response carries a hash chain or signature over the event log
   that legis can verify at governance boundary crossing.
2. Loomweave guarantees the lineage endpoint is served from an append-only store
   with no backfill path; legis trusts the transport (TLS) as the custody seal.
3. Out of scope for SEI v1; legis establishes its own integrity layer over the
   polled lineage snapshot.

Option 3 is acceptable to legis for v1 — legis can store a snapshot hash of
the lineage at each governance decision time and detect divergence on re-read.
But the approach should be *explicit*, not left ambiguous. This is legis's
pre-lock requirement: record which option Loomweave will implement so legis's
governance boundary code can be designed accordingly.

### A.5 Lineage polling vs. push surface (informational; not a lock-blocking requirement)

Legis's ideal governance workflow would *react* to SEI lifecycle events — e.g.,
"entity orphaned → surface a governance gap." A push/event surface on lineage
would enable this without polling. However, legis recognises that a push
surface is exactly the kind of apparatus (registry, multi-fetch, event bus)
that sank Weft-URI, and the SEI standard's minimal-apparatus posture is
correct. Legis's v1 design will use **pull-only polling** on `lineage(sei)`
and accept the latency. This note flags a possible future consideration for SEI
vN, not a lock-blocking wire-contract demand.

## Implementation notes

> 2026-06-05 implementation note: The legis-side closeout landed the
> policy-boundary CI gate (static scanner converged onto the runtime evidence
> gate), the additive `/git/rename-feed` endpoint and `git_rename_feed_get` MCP
> tool, and the `/filigree/issues/{id}/closure-gate` endpoint and
> `filigree_closure_gate_get` MCP tool. Sibling-side consumption (Filigree
> calling the closure gate; Loomweave re-pointing to the rename feed) is tracked
> as a follow-on spec.
