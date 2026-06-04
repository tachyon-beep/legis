# Legis Agent-Facing MCP Surface — Design Spec

**Date:** 2026-06-03
**Status:** Design-ready — decomposition approved by the owner; tool surface ratified by the agent
users; awaiting per-WP implementation plans
**Baseline:** `src/legis` + `tests`, 214/214 collected green at HEAD `ffbda95`.
**Provenance:** Requirements for the *surface* were elicited from the actual users of this surface —
two AI-agent instances (one Sonnet-backed, one Opus-backed) interviewed as the customer over two
cross-examination rounds. The owner (John) explicitly deferred surface decisions to those users
("I'm not the senior user for the surface, the agents are") and retained the build/architecture
decisions. Interview transcript summarized in the Appendix.

## Purpose

Loom is **agent-first**: the primary users of legis are autonomous AI coding agents, not humans.
Today every agent↔legis interaction is a raw HTTP `POST`/`GET` against the FastAPI service, which
forces the agent into *status-code archaeology* (a `409` means "judge BLOCKED" on `/overrides` but
"sign-off not cleared" on `/bind-issue`; a `404` means both "no such PR" and "this cell isn't wired
on this deployment"). This spec defines an **MCP tool surface** that replaces those raw calls with a
discovered, schema-typed, structured-result interface that makes the *honest governed path* the path
of least resistance.

This is a **decomposition spec**, not an implementation plan. It defines *what* and *in what order*;
each work-package (WP) becomes its own dated TDD plan (the Sprint 5/6 / not-yets format) after this
spec is approved.

## Scope

In scope: the full agent-first surface the owner selected — a transport-agnostic service layer, a
declarative policy→cell registry, an in-process MCP stdio server, the structured tool surface in §3,
and the safety invariants in §4.

Out of scope (named-and-deferred, NOT built in v1; no design owed here): idempotency keys, batch
policy evaluation, `trail_id` correlation handles, `identity_stable` warnings on `policy_explain`.
These are real but speculative; YAGNI applies. `identity_stable` is deferred, not killed — both users
flagged it as a future nice-to-have, not a v1 blocker.

## Design principles (carried from prior sprints + established by this interview; do not reopen)

1. **No new runtime dependency.** The MCP server is hand-rolled JSON-RPC 2.0 over stdio, modelled on
   the sibling Wardline's `mcp/protocol.py` + `mcp/server.py` (dependency-free, stdlib only). The
   policy→cell registry loads via stdlib `tomllib`, mirroring `policy/exemptions.py`.
2. **Outcome-first, never status-code archaeology.** Every tool result carries a discriminated
   `outcome` field the agent switches on. The governing `cell` rides along as *explanation* — present
   but ignorable in the easy "recorded, proceed" case, load-bearing only when a judge or human is
   implicated.
3. **`isError` is reserved for infrastructure/integrity failure only.** A judge `BLOCKED` verdict and
   a `ESCALATED_PENDING` human gate are *successful, expected governance outcomes* — NOT `isError`.
   (Both users: mark a BLOCK as `isError` and the agent treats it as transient and blind-retries it.)
4. **Opaque input, transparent output.** The agent does not pre-select the cell; it submits and the
   registry routes. The *result* always names the cell and on whose authority the next move sits. No
   opaque router that hides whether a human is now in the loop.
5. **Honesty is structural, not exhortative.** Anti-gaming teeth live in the judge and the
   override-rate gate (server-side), never in a client-authored input schema the agent controls.
   `rationale` is free-text — structured sub-fields are checkbox theater an optimizing agent
   boilerplates.
6. **Degrade honestly.** `UNKNOWN` policy results are loud and distinct from `CLEAR` (no silent
   false-green). A reason classification that cannot be established is `UNCLASSIFIED`, never invented.

## Architecture

Today the governance logic lives **inline in FastAPI route closures** in `create_app`
(`src/legis/api/app.py`). To put MCP and HTTP over identical behavior without duplicating it:

- **`legis.service` (new):** a transport-agnostic layer — plain functions/objects taking dataclasses,
  returning result objects. It owns the governance logic currently inlined in the route closures.
- **`legis.api.app` (refactored):** a thin adapter — parse JSON → call service → map result to HTTP
  status. Behavior-preserving; the existing 214 tests stay green.
- **`legis.mcp` (new):** the other thin adapter — JSON-RPC params → call service → map result to the
  outcome envelope (§3). Hand-rolled stdio server, zero new deps.

The MCP server runs **in-process** (embeds the service + stores directly, like Wardline), launched as
a stdio subprocess — *not* a thin client to a running `legis serve`. This is what makes
`agent_id`-at-launch (§4.2) and self-contained deployment work. New console script: `legis mcp`,
alongside the existing `legis serve` and `legis check-override-rate`.

### The policy→cell registry (the keystone new domain model)

There is **no policy→cell mapping in legis today.** The cell is determined by (a) what is wired into
`create_app` on a deployment (is a judge attached? are `protected_gate`/`signoff_gate` wired?) and
(b) which endpoint the caller chose. The Wardline path keys cells on *severity*, not policy name. So
the users' headline ask — `policy_explain(policy) → "this policy is in the structured cell"` —
presumes a registry that does not exist and must be built.

Design: a declarative **`policy/cells.toml`** mapping policy-name (or glob) → cell, with a default
for unlisted policies, loaded at startup via stdlib `tomllib` (the `policy/exemptions.py` pattern:
fails closed on malformed input). It backs both `policy_explain` (report the cell) and
`override_submit` (route transparently). A policy's cell is still also constrained by
deployment wiring — `policy_explain.enabled` reports whether the mapped cell's gate is actually wired,
so the agent never discovers a disabled cell by a `404` surprise.

## §3. The tool surface (ratified by both agent users)

Tool names follow the Filigree MCP convention: lowercase `<entity>_<verb>` with
no project prefix. MCP hosts already surface server identity as
`mcp__legis__<tool>`, so names like `legis_policy_explain` would repeat the
server token and fight cross-suite consistency.

Result envelope convention, all tools: a discriminated `outcome` enum; `isError:true` reserved for
infrastructure/integrity failure only; `seq` returned on any trail-writing call; `agent_id` injected
by the server at launch (§4.2), never a tool input.

### Discovery
- **`policy_explain(policy, entity)`** — *Optional, never a precondition.* Look-before-leap, justified
  because every override attempt is an attributable trail write.
  - → `{cell, judge_inline, self_clearable, human_in_loop, enabled, available_moves[], required_inputs[]}`
    where `required_inputs` is the same `[{field, how}]` shape `NEED_INPUTS` returns, so the agent's
    recovery is mechanical against one field shape across both tools.
  - error: `{isError:true, error_code:"LEGIS_UNAVAILABLE"}`

### The unified write path
- **`override_submit(policy, entity, rationale, [file_fingerprint, ast_path])`** — ONE tool,
  opaque input / transparent output. Consults the registry, routes to the governing cell, always
  names the cell in the result. `rationale` is free-text.
  - `{outcome:"ACCEPTED_SELF", cell:"chill", seq, ...}` — self-cleared, no judge, human reviews async
  - `{outcome:"ACCEPTED_BY_JUDGE", cell:"coached", seq, judge_model, judge_rationale, note:"may be re-judged later"}`
  - `{outcome:"BLOCKED", cell, seq, judge_model, judge_rationale, blocked_reason_code, self_clearable:false, next_actions:["REVISE_CODE","REVISE_RATIONALE"], note:"this attempt does not count toward your override-rate"}`
  - `{outcome:"ESCALATED_PENDING", cell:"structured", seq, cleared:false, human_required:true, operator_instruction:"<verbatim string to surface to the operator>", poll_tool:"signoff_status_get", poll_handle:<seq>}`
  - `{outcome:"NEED_INPUTS", cell:"protected", required_inputs:[{field:"file_fingerprint", how:"sha256 of the target file contents"}, {field:"ast_path", how:"dotted path to the AST node"}]}` — non-error guided result; resubmit with the fields. No attributable failed write on the trail.
  - error: `{isError:true, error_code:"CELL_NOT_ENABLED"|"AUDIT_INTEGRITY_FAILURE"|"LEGIS_UNAVAILABLE"}`

  `blocked_reason_code` ∈ `{RATIONALE_INSUFFICIENT, CODE_VIOLATION, POLICY_HARD_BLOCK, UNCLASSIFIED}`.
  The field is present from M4 (populated `UNCLASSIFIED`); M6 fills it from the judge — a value
  upgrade, never a schema change.

### Escalation lifecycle
- **`signoff_status_get(seq)`** — cheap, idempotent poll. Vocabulary consistent with
  `ESCALATED_PENDING` (`cleared:true`, no third synonym for "the human signed").
  - → `{cleared:false, seq}` | `{cleared:true, seq, signed_by, signed_at}`
  - error: `{isError:true, error_code:"NO_SUCH_REQUEST"}`

### Pre-commit check
- **`policy_evaluate(policy, target)`** — distinct pre-commit "am I in violation *right now*"
  niche (the fix-first-vs-override decision); does not record an override.
  - → `{outcome:"CLEAR"|"VIOLATION"|"UNKNOWN", detail, provenance_gap}` — `UNKNOWN` explicitly
    distinct from `CLEAR`, with guidance: do not treat `UNKNOWN` as permission.

### Wardline handoff
- **`scan_route(scan, [cell], [severity_map])`** — exactly one of `cell` (force one cell) or
  `severity_map` (route by severity); two named optional params, NOT an overloaded union; server-side
  validation errors if both or neither.
  - → `{outcome:"ROUTED", routed:[…]}` | `{isError:true, error_code:"INVALID_CELL_SPEC"}`

### Reads (thin, 1:1)
- `git_branch_list`, `git_commit_get(sha)`, `git_rename_list(rev_range)`,
  `pull_request_get(number)` (PR + joined check outcomes), `check_list(target_type, target)`.
- **`override_rate_get`** — no tuning inputs. Result carries the lock-note in-payload:
  `{status, rate, sample_size, note:"measures operator force-pasts; not movable by agent retries"}`.

### Deliberately ABSENT from the agent surface (the single most important honesty property)
- Operator sign-off (`POST /signoff/{seq}/sign`) and operator-override
  (`POST /protected/operator-override`) — anything taking an `operator_id`. These clear gates a human
  must own; exposing them to the agent makes self-clearing tempting and turns attribution into
  theater. They live only on the HTTP/human surface. Enforced by a structural test (§4.1).

## §4. Safety invariants (non-negotiable; asserted from M3 onward, not deferred)

1. **Operator surface absent.** A structural test fails the build if any MCP tool schema ever exposes
   an `operator_id` parameter, or if sign-off-sign / operator-override appear on the agent surface.
   Wired at M3 and rides every subsequent milestone — an invariant tested only at the end is an
   invariant that was violable in between.
2. **`agent_id` bound at launch.** The host sets `--agent-id` / `LEGIS_AGENT_ID` at subprocess
   launch; the server captures it once at startup and stamps every recorded override. It is **not** a
   JSON-RPC param — the security property is *absence of the input*, not validation of it. Honest
   scope (stated so it does not over-promise): this defeats **in-session spoofing** (the threat the
   agent surface actually introduces — a per-call field a prompt-injection payload could set), **not**
   a lying host. Cross-host non-repudiation requires a signed launch token (HMAC machinery the
   protected cell already has) — a named future step, explicitly NOT in v1. Today `agent_id` is
   free-text copied verbatim into the audit payload; this closes that hole.
3. **Tamper → non-retryable.** The existing `TamperError` / HTTP-500 path
   (`verified_governance_records()`) maps to `{isError:true, error_code:"AUDIT_INTEGRITY_FAILURE"}` —
   distinct and non-retryable; the agent must stop and surface to a human, never hammer a compromised
   trail.

## §5. Decomposition (WP, TDD per the house workflow)

Sequenced to land a thin **end-to-end vertical slice through the simplest write path first** — both
users independently demanded this over reads-first, because the novel/risky part of the design is the
discriminated outcome envelope, and *a read tool never exercises it* (reads only return data or
`NOT_FOUND`). Get one write outcome through the envelope end-to-end early; every later cell then fills
a proven pattern.

- **WP-M1 — Service-layer extraction.** Extract `legis.service` from the FastAPI route closures;
  refactor `legis.api.app` to a thin adapter. Behavior-preserving; existing 214 tests stay green.
  *Foundational — everything sits on it.*
- **WP-M2 — Registry + explain.** `policy/cells.toml` + stdlib `tomllib` loader (fails closed);
  `policy_explain` over the service layer. *The routing brain `submit` depends on.*
- **WP-M3 — Vertical slice.** Hand-rolled MCP JSON-RPC/stdio server + `legis mcp` console script +
  the outcome envelope + **`override_submit` for the chill cell only** (`ACCEPTED_SELF`) +
  `policy_explain` + ONE read tool (`check_list`) to prove the read path. `agent_id` bound at
  launch. The operator-absent structural test (§4.1) wired here and riding from now on. *First real
  user value: discover → attempt override → transparent governed result, end-to-end over MCP, on the
  path legis exists for.*
- **WP-M4 — Widen the write path.** Extend `override_submit` through coached
  (`ACCEPTED_BY_JUDGE` / `BLOCKED` with the `blocked_reason_code` field present, populated
  `UNCLASSIFIED`), structured (`ESCALATED_PENDING`), and protected (`NEED_INPUTS` + signature) cells;
  add `signoff_status_get`, `policy_evaluate`, `scan_route`. *Extending a proven path,
  not building the envelope and the hard cells at once.*
- **WP-M5 — Reads + safety hardening.** Fold in remaining read tools (`git_*`, `pull_request`,
  `override_rate` with its in-payload note) — low-risk, slot in once the envelope is proven. Land the
  safety-invariant milestone: confirm/consolidate the operator-absent + `agent_id`-at-launch tests;
  map tamper → `AUDIT_INTEGRITY_FAILURE`. *Safety before polish — never inverted.*
- **WP-M6 — Judge reason-classification.** Extend the judge prompt + parse to emit
  `blocked_reason_code`, fail-safe to `UNCLASSIFIED` (never invent a category). A quality upgrade to
  the field M4 already ships — not a schema change.

## Exit criterion (whole)

An autonomous agent, given only the MCP tool list, can: discover a policy's cell and legal moves;
attempt an override and receive a transparent, cell-named, discriminated result over every cell
(self-clear / judge-accept / judge-block / human-escalate / need-inputs); poll a pending human
sign-off; and never reach an operator-authority tool — with `agent_id` it cannot spoof in-session and
an audit trail that fails closed on tamper. The existing HTTP surface and its 214 tests remain green
throughout.

## Appendix — interview provenance

Two agent instances were interviewed as the customer, two rounds each with cross-examination:
- **Round 1:** elicited the pains of raw-HTTP (status-code archaeology, invisible cells, 404-on-
  disabled-gate), tool granularity, BLOCKED-result shape, escalation handling, proactive context,
  and anti-gaming.
- **Round 2 (cross-examination):** resolved the one real split — the Sonnet instance had proposed an
  opaque router hiding the cell; the Opus instance rejected hiding human-in-loop. Both converged on
  *opaque input / transparent output*. Both conceded structured-rationale sub-fields are checkbox
  theater (→ free-text). The Opus instance read the source and corrected two claims to transport
  reality: the override-rate gate measures **operator force-pasts**, not agent retries (so a blocked
  agent cannot inflate it), and `agent_id` is today free-text in the payload with no auth anywhere
  (grounding §4.2).
- **Ratification:** both accepted the unified `override_submit` + `NEED_INPUTS` over separate
  per-cell tools, and both independently demanded the M3 chill-write vertical slice over reads-first.
