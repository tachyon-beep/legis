# Legis Federation Repo Design

**Date:** 2026-06-01  
**Status:** Approved design  
**Scope:** Bootstrap `/home/john/legis` as a docs-first GitHub repository that explains Legis's role in Loom and gives Clarion, Filigree, and Wardline something concrete to review before implementation starts.

---

## 1. Goal

Create an initial repository that is honest, reviewable, and implementation-friendly.
It must explain:

- what Legis is,
- what Legis is not,
- which authority it owns inside Loom, and
- how it composes with Clarion, Filigree, and Wardline without pretending that code or APIs already exist.

The repository is meant to be a durable coordination artifact for the suite, not a marketing stub.

## 2. Product role

Legis is the planned fourth Loom product. Its bounded authority is:

- **project change provenance,**
- **git/branch/commit/PR context,**
- **CI/check context,** and
- **governance/attestation context over project change.**

Legis should answer questions such as:

- What changed?
- In which branch / commit / pull request / check context did it change?
- What governance or attestation state exists for that change?

Legis is **not**:

- a federation registry,
- a suite-wide broker,
- a replacement for Clarion's code identity authority,
- a replacement for Filigree's workflow authority, or
- a replacement for Wardline's policy authority.

The repo should present Legis as a narrow Loom member with a clearly bounded domain, not as hidden suite infrastructure.

## 3. Pairwise composition with the suite

### 3.1 Clarion

Clarion remains the authority for code identity and structure, including Stable Entity Identity (SEI) once that standard lands. Legis may later provide git-history and rename evidence that Clarion consumes during re-index and lineage work, but Clarion remains the sole authority for SEI minting, persistence, re-binding, and resolution.

### 3.2 Filigree

Filigree remains the authority for issue and workflow state. Legis adds change context around that work: which branch, commit, pull request, or check run a given piece of work relates to. Legis does not own issue lifecycle, planning state, or work-claim semantics.

### 3.3 Wardline

Wardline remains the authority for policy findings, taint facts, and dossier truth. Legis contributes repository and CI context that Wardline can cite when explaining whether a finding is attached to live project state, a particular change, or a specific check outcome.

## 4. Planned capability surfaces

The initial repository should describe **planned** capabilities, not implemented ones.

### 4.1 Git / change surface

Legis will eventually describe repository state and change lineage:

- branch relationships,
- commit metadata,
- pull-request context,
- rename/history evidence, and
- other project-change facts that sibling Loom products may consume.

### 4.2 CI / check surface

Legis will eventually describe build and check state:

- which checks ran,
- what they ran against,
- whether they passed or failed, and
- how those outcomes relate to branches, commits, and pull requests.

### 4.3 Governance / attestation surface

Legis will eventually hold change-scoped governance facts and attestations. Once SEI exists, those attestations should key on **SEI as an opaque identifier** when the governance claim concerns a code entity rather than only a file or commit.

## 5. SEI and federation posture

Legis must align with the approved SEI direction without claiming authority it does not have.

- **Clarion is the SEI authority.**
- **Legis consumes SEI as opaque.**
- **Legis must never derive, parse, or reinterpret SEI.**
- **Legis may later provide git-rename/history signals that Clarion consumes, but that does not make Legis an identity authority.**
- **If Legis is absent, sibling products must still function and degrade honestly rather than guess.**

The initial repo should make this explicit so the suite does not accidentally drift toward a hidden central dependency.

## 6. Repository shape

The repository starts as a docs-first skeleton with minimal scaffolding.

### Required root files

- `README.md` — the authoritative entry point and product overview
- `LICENSE` — MIT, per user choice
- `.gitignore` — minimal repository hygiene
- `CONTRIBUTING.md` — contributor guidance for a design-ready, pre-implementation repo

### Required documentation trees

- `docs/federation/` — cross-product participation and SEI-related conformance notes
- `docs/design/` — Legis-specific product intent and charter docs
- `docs/superpowers/specs/` — approved design specs
- `docs/superpowers/plans/` — implementation plans

### Intentionally absent in v0

- fake CLIs,
- speculative API reference,
- install instructions,
- starter runtime code,
- empty package scaffolds that imply implementation exists.

## 7. README requirements

The root README should do four jobs well:

1. identify Legis as the planned fourth Loom product;
2. explain its bounded authority in plain language;
3. give a pairwise story for Clarion, Filigree, and Wardline; and
4. state the current status clearly: **design-ready, not implemented**.

It should also link readers to the federation docs and design docs so other projects can review the intended fit without reading internal planning material first.

## 8. Non-goals

The initial repo should **not**:

- claim a stable CLI or HTTP API,
- act as a landing page for speculative feature lists,
- recreate Loom URI / registry concepts,
- centralize authority already owned by Clarion, Filigree, or Wardline, or
- imply that Legis is required for existing suite members to function.

## 9. Quality bar

The initial repo is successful when:

1. a newcomer can understand Legis's bounded role in under five minutes;
2. a maintainer from Clarion, Filigree, or Wardline can review the repo and see how Legis is intended to compose with their product;
3. the docs are explicit that Legis is proposed/planned and not yet implemented; and
4. the SEI implications for Legis are stated clearly enough that later implementation work does not accidentally violate the suite-wide standard.

## 10. Result

The first Legis repository should behave like a clean contract boundary: small, explicit, and honest. It gives the Loom suite a fourth product shape to react to, while keeping future implementation work anchored to a clear authority boundary and a clear federation story.
