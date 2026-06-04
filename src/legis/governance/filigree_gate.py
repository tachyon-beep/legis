"""Pure decision: may a Filigree issue be closed on legis governance evidence?

Fail-closed: an issue is closable only when the binding ledger holds a verified
``issue_binding`` record for it. A ledger integrity failure raises ``BindingError``
(the caller maps that to a server error); a missing binding returns a structured
not-allowed decision rather than an error.
"""

from __future__ import annotations

from typing import Any


def evaluate_issue_closure(ledger: Any, *, issue_id: str) -> dict[str, Any]:
    record = ledger.get_by_issue_id(issue_id)  # verifies the chain; may raise BindingError
    if record is None:
        return {
            "allowed": False,
            "issue_id": issue_id,
            "reason": "no verified governance binding for this issue",
            "evidence": None,
        }
    return {
        "allowed": True,
        "issue_id": issue_id,
        "reason": "verified governance binding present",
        "evidence": {
            "signoff_seq": record.get("signoff_seq"),
            "content_hash": record.get("content_hash"),
            "recorded_at": record.get("recorded_at"),
        },
    }
