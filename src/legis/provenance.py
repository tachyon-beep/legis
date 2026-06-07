"""Provenance vocabulary shared by recorded forge/CI facts.

``CheckRun`` and ``PullRequest`` are both *writer-supplied claims* — legis
records what a writer asserted, not what a forge cryptographically attested. The
provenance axis names how far that claim is backed. Today there is exactly one
member; an authenticated path (e.g. a signed forge webhook) would add a stronger
value here rather than as another hand-typed string literal.

This is the single vocabulary source for both ``checks`` and ``pulls``; neither
package imports the other, so the enum lives at the package root they share. The
field stays typed ``str`` on the wire-facing dataclasses (matching the
``Suppressed`` precedent in the rc-series str,Enum conversion): a ``str,Enum``
member *is* its wire string, so ``json.dumps`` / ``canonical_json`` emit
byte-identical payloads, and raw values read back out of the ``Text`` DB columns
never need coercion that could raise on a legacy/unexpected value.
"""

from __future__ import annotations

from enum import Enum


class Provenance(str, Enum):
    """How far a recorded forge/CI claim is backed."""

    # A writer-asserted fact with no signature or forge attestation behind it.
    UNAUTHENTICATED = "unauthenticated"
