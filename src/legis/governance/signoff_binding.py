"""Bind a cleared governed sign-off to a Filigree issue, keyed on SEI.

legis governs; Filigree owns issue state. This attaches the attestation as an
entity-association (``entity_id`` = the SEI, opaque to Filigree) so the code↔
governance binding survives rename/move. It does NOT mutate Filigree issue
status — lifecycle transitions remain Filigree's authority (locked decision 5).
A locator-keyed sign-off is rejected: an unstable binding would orphan on rename,
defeating the point.
"""

from __future__ import annotations

from typing import Any

from legis.filigree.client import FiligreeClient
from legis.identity.entity_key import EntityKey

BINDING_ACTOR = "legis"


def bind_signoff_to_issue(
    filigree: FiligreeClient,
    *,
    issue_id: str,
    entity_key: EntityKey,
    content_hash: str,
    signoff_seq: int,
) -> dict[str, Any]:
    if not entity_key.identity_stable:
        raise ValueError(
            "cannot bind a sign-off on an identity_stable=False (locator) key — "
            "the binding would orphan on rename; resolve to an SEI first"
        )
    result = filigree.attach(
        issue_id, entity_key.value, content_hash, actor=BINDING_ACTOR
    )
    return {**result, "signoff_seq": signoff_seq}
