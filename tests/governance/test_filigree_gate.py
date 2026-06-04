from legis.governance.binding_ledger import BindingError
from legis.governance.filigree_gate import evaluate_issue_closure


class _FakeLedger:
    def __init__(self, record, raises=None):
        self._record = record
        self._raises = raises

    def get_by_issue_id(self, issue_id):
        if self._raises is not None:
            raise self._raises
        return self._record


def test_allows_when_verified_binding_exists():
    ledger = _FakeLedger({"issue_id": "ISSUE-7", "signoff_seq": 3})

    decision = evaluate_issue_closure(ledger, issue_id="ISSUE-7")

    assert decision["allowed"] is True
    assert decision["issue_id"] == "ISSUE-7"
    assert decision["evidence"]["signoff_seq"] == 3


def test_blocks_when_no_binding():
    ledger = _FakeLedger(None)

    decision = evaluate_issue_closure(ledger, issue_id="ISSUE-7")

    assert decision["allowed"] is False
    assert "no verified" in decision["reason"].lower()


def test_propagates_binding_integrity_error():
    ledger = _FakeLedger(None, raises=BindingError("tampered"))

    try:
        evaluate_issue_closure(ledger, issue_id="ISSUE-7")
    except BindingError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected BindingError to propagate")
