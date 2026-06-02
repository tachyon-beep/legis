# ADR-0002 — Complex-tier governance parameters

**Date:** 2026-06-02
**Status:** Accepted
**Sprint:** Sprint 3 / WP-3.2, WP-3.3 (see `docs/superpowers/plans/2026-06-02-legis-sprint-3-complex-tier.md`)

## Context

The complex tier (structured + protected cells) introduces three parameters
that are **governance policy**, not implementation detail: the HMAC signing key,
the set of policies designated "protected," and the override-rate gate's
threshold/window/floor. The defining property of a governance parameter is that
the party being governed must not be able to tune it to pass — so where each one
lives, and who can change it, is itself a security decision.

## Decisions

### 1. The protected-policy set comes from config, never from the record

`TrailVerifier` is constructed with `protected_policies: frozenset[str]`. A
record whose `policy` is in that set **must** carry a valid signature; a missing
signature is tampering, not "unsigned, skip." This is the guard against the
signature-stripping downgrade: if "is this protected?" lived in the record's own
mutable payload, an attacker could strip the signature *and* flip the bit. The
designation must therefore originate outside the record — from this policy
config. Which policies are protected is a deployment decision recorded alongside
the project's policy set.

### 2. HMAC key provisioning — injected, held outside the record

The signing key is injected into `ProtectedGate` / `TrailVerifier` as bytes and
is **never written to any payload** (asserted by test). This ADR fixes the
*contract*; the production *mechanism* (env var → secret store → KMS, plus
rotation) is **Open Decision #4** and is deferred to app-wiring, exactly as the
judge-model `LLMClient` seam (Open Decision #3) is deferred. Key rotation will
require a `:v2` signature epoch (see decision 4) or a re-sign migration; until a
mechanism is chosen, deployments inject a single key from their own secret
source. This is the one human-setup act in the otherwise zero-*human*-config
model — defensible as a "human on the loop" governance act.

### 3. Override-rate gate constants are reviewed policy, not tunable knobs

`src/legis/governance/params.py` holds:

| Constant | Value | Meaning |
|---|---|---|
| `OVERRIDE_RATE_THRESHOLD` | `0.2` | max share of kept suppressions forced past the judge by an operator |
| `OVERRIDE_RATE_WINDOW` | `100` | rolling window of final-disposition records |
| `OVERRIDE_RATE_MIN_SAMPLE` | `20` | below this, pass-with-notice (small-corpus floor) |

The `GET /governance/override-rate` endpoint reads these from the module — **not
from request parameters**. An agent cannot widen its own threshold by passing a
query string. Changing any of them is an amendment to *this ADR*, reviewed like
any policy change; it is deliberately **not** a workflow-file or environment
edit. The denominator is final-disposition records (`ACCEPTED` +
`OVERRIDDEN_BY_OPERATOR`); `BLOCKED` attempts are excluded so a hammering agent
cannot dilute the ratio (the Sprint 2 forward-flag).

### 4. Signature is versioned: `hmac-sha256:v1`

`v1` pins canonical-JSON v1 (`legis.canonical`). RFC 8785 convergence — flagged
in `canonical.py` as the hardening to land before cryptographic guarantees
mature — becomes `:v2`. The versioned prefix makes that a clean migration and
also gives key-rotation an epoch boundary to hang on. The signed field set binds
**entity and policy** in addition to the roadmap's six fields (verdict, model,
timestamp, rationale, fingerprint, ast_path), closing a verdict-transplant gap.

## Consequences

- A protected deployment has exactly one human-setup obligation: supply the HMAC
  key. Everything else is agent-operable.
- Trail consumers that read "active suppressions" (the decay sweep) filter to
  judge-`ACCEPTED` records; the rate gate owns `OVERRIDDEN_BY_OPERATOR`. Both
  obligations are documented at their call sites.
- Open Decisions #3 (judge-model identity) and #4 (key provisioning mechanism)
  remain open; Sprint 3 ships the mechanisms behind their seams, not the
  production wiring.
