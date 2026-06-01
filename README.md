# Legis

Legis is the planned fourth Loom product: the Git/CI and governance side of the suite's common operating picture.

## Status

Legis is **design-ready, not implemented**. This repository exists so Clarion, Filigree, and Wardline can review the intended shape of Legis before runtime code lands.

## What Legis is

Legis is the planned Loom authority for:

- project change provenance,
- branch / commit / pull request context,
- CI and check context, and
- governance and attestation context over change.

## What Legis is not

Legis is not:

- a federation registry,
- a hidden suite runtime,
- a replacement for Clarion's code identity authority,
- a replacement for Filigree's workflow authority, or
- a replacement for Wardline's policy authority.

## How Legis fits into Loom

### Clarion

Clarion remains the authority for code identity and structure. Legis may later provide git-history and rename evidence that Clarion can consume, but Clarion remains the SEI authority.

### Filigree

Filigree remains the authority for issue and workflow state. Legis adds branch, commit, pull request, and check context around that work without taking over issue lifecycle.

### Wardline

Wardline remains the authority for policy findings and dossier truth. Legis contributes project change and CI context that Wardline can cite when attaching findings or gates to real repository state.

## Stable Entity Identity

Legis consumes Stable Entity Identity (SEI) as an opaque identifier. Clarion is the SEI authority. See `docs/federation/sei-conformance.md`.

## Repository layout

- `docs/federation/` - Loom-facing contracts and participation notes
- `docs/design/` - product intent and design notes
- `docs/superpowers/specs/` - approved design specs
- `docs/superpowers/plans/` - implementation plans

## Near-term documents

- `docs/design/legis-charter.md`
- `docs/federation/README.md`
- `docs/federation/sei-conformance.md`

This repo should stay explicit, narrow, and honest about what exists today.
