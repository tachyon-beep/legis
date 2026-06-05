# ADR-0001 — Stack & foundation architecture

**Date:** 2026-06-01
**Status:** Accepted
**Sprint:** Sprint 0 / WP-0.1 (see `docs/superpowers/plans/2026-06-01-legis-implementation-sprints.md`)

## Context

Legis is design-ready but unimplemented. Before any feature work, the
foundation sprint must fix the stack, the persistence model, and the API shape.
The suite has split precedent: Loomweave and Filigree are Rust; Wardline and the
elspeth design-ancestor are Python.

The decisive factors for legis specifically:

- The protected cell's machinery — the LLM judge gate, the `@trust_boundary`
  in-code policy decorator, the HMAC-signed tamper-bound audit trail, the decay
  sweep, the override-rate gate — is **already implemented in Python** in
  elspeth (`elspeth-lints`). Legis inherits these as suite-level mechanisms; a
  Python stack makes that a near-direct port rather than a re-implementation.
- In-code policy expression (WP-4.2) requires AST analysis of governed Python
  projects; Python's stdlib `ast` is native to that task (elspeth's dataflow
  walk is built on it).
- Wardline is Python, so the trust-vocabulary convergence work (WP-6.1) shares
  a language with one of its two endpoints.

## Decision

- **Language/runtime:** Python 3.12+, managed with `uv`, `src/` layout.
- **HTTP read API:** FastAPI (consumer model mirroring Loomweave's read API —
  consumers are HTTP clients).
- **Persistence:** SQLite via **SQLAlchemy Core** (not the ORM), matching
  elspeth's Landscape choice: an audit trail needs precise SQL control and a
  SQLite-dev → PostgreSQL-prod path. The audit store is **append-only** and
  **record-agnostic** — it persists opaque canonical-JSON payloads in a hash
  chain and knows nothing about the record types layered on top.
- **Canonical JSON:** sorted-key, tight-separator JSON for deterministic
  hashing in v1; RFC 8785 is a future hardening (elspeth uses RFC 8785, and
  legis should converge there before the protected cell ships cryptographic
  guarantees).
- **Identity:** an **opaque** entity-key type from line one — locator today,
  SEI later, with an `identity_stable` flag. This is the SEI-shape-independence
  guarantee (SEI spec §0.3): Sprint 5's SEI adoption is a value swap, not a
  schema change. No code path parses the key.

## Zero-human-config posture

The operating-model invariant (humans on the loop, not in the loop;
zero-*human*-config) is preserved by a single documented run command and an
agent-operable service. There is no human-facing configuration step to stand
the service up. The one human-setup act in the whole roadmap — provisioning the
protected cell's HMAC key — is deferred to Sprint 3 (WP-3.2) and is an explicit
"human on the loop" governance act, not operational config.

## Consequences

- **Positive:** elspeth judge/decorator/HMAC code ports near-directly; native
  AST for in-code policy; Wardline parity; SQLAlchemy Core gives the prod-DB
  path for free.
- **Cost / rejected alternative (Rust + axum + rusqlite):** would match
  Loomweave/Filigree's runtime tier and HTTP patterns and give better
  git-operation performance, but forces a full re-implementation of elspeth's
  judge, decorator, and HMAC logic and a separate AST path for governed Python
  code. The judge-port leverage outweighed the operational-tier parity.
- **Future:** if legis's git surface (WP-1.1) becomes performance-bound, the
  git layer specifically can be reconsidered without disturbing the Python
  governance engine — the contracts are language-agnostic (roadmap §4).
