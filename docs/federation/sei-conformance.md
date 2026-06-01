# Stable Entity Identity (SEI) Conformance Notes

Legis is a **consumer** of Stable Entity Identity (SEI), not the authority.

## Core posture

- Clarion mints, persists, re-binds, and resolves SEI.
- Legis treats SEI as opaque.
- Legis must never derive, parse, or reinterpret SEI structure.

## Planned Legis responsibilities

- Attach governance or attestation facts to SEI when the subject is a code entity.
- Preserve the distinction between identity state and content freshness rather than collapsing them.
- Degrade honestly if Clarion does not advertise SEI capability.

## Possible future contribution to Clarion

Legis may later provide git-history and rename evidence that Clarion can consume during SEI re-binding, but that does not move identity authority out of Clarion.
