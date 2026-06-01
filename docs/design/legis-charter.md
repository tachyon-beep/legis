# Legis Charter

## Summary

Legis is the planned fourth Loom product. It is responsible for project change provenance and the git/CI common operating picture.

## Authority boundary

Legis owns:

- branch, commit, and pull request context,
- CI/check context, and
- governance and attestation context over project change.

Legis does not own:

- code identity or structure,
- issue or workflow state, or
- policy findings and dossier truth.

## Operating modes

### Solo mode

Legis can describe repository change and CI state on its own.

### Pair mode

- With Clarion: Legis can supply git-history and rename evidence.
- With Filigree: Legis can connect work state to change state.
- With Wardline: Legis can connect policy findings to change and check context.

### Suite mode

Legis becomes the common operating picture for project change and governance while preserving the authority boundaries of the other Loom products.

## Near-term scope

The initial repository is documentation-first. It should make the intended role reviewable before runtime implementation starts.
