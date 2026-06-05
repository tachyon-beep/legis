from pathlib import Path


def test_governance_core_depends_on_store_protocol_not_audit_store():
    # binding_ledger, sei_backfill, and gaps consume the append-only trail but
    # must type against store.protocol so they can be unit-tested against a
    # protocol fake (Q-L3 / audit M12). Concrete AuditStore/AuditRecord
    # construction belongs at the composition roots (api/cli/mcp), not here.
    root = Path("src/legis/governance")
    core = {"binding_ledger.py", "sei_backfill.py", "gaps.py"}

    offenders = []
    for path in root.glob("*.py"):
        if path.name not in core:
            continue
        text = path.read_text()
        if "from legis.store.audit_store import" in text:
            offenders.append(path.as_posix())

    assert offenders == []


def test_binding_ledger_runs_against_a_protocol_fake():
    # Proof the migration is real: a fake AppendOnlyStore that does not derive
    # from AuditStore can drive BindingLedger end to end.
    from legis.governance.binding_ledger import BindingLedger
    from legis.identity.entity_key import EntityKey

    class FakeClock:
        def now_iso(self) -> str:
            return "2026-01-01T00:00:00+00:00"

    class FakeRecord:
        def __init__(self, seq, payload, content_hash, prev_hash):
            self.seq = seq
            self.payload = payload
            self.content_hash = content_hash
            self.prev_hash = prev_hash

    class FakeStore:
        """In-memory AppendOnlyStore — no AuditStore, no SQLAlchemy."""

        def __init__(self):
            self._rows: list[FakeRecord] = []

        def append(self, payload):
            seq = len(self._rows) + 1
            self._rows.append(FakeRecord(seq, payload, f"h{seq}", "p"))
            return seq

        def read_all(self):
            return list(self._rows)

        def read_by_seq(self, seq):
            for r in self._rows:
                if r.seq == seq:
                    return r
            return None

        def verify_integrity(self) -> bool:
            return True

    ledger = BindingLedger(FakeStore(), FakeClock(), key=b"k")
    seq = ledger.record(
        signoff_seq=1,
        issue_id="legis-x",
        entity_key=EntityKey.from_sei("loomweave:eid:abc"),
        content_hash="ch",
    )
    assert seq == 1
    ledger.verify()  # fail-closed verify passes against the fake trail
    assert ledger.get(1)["issue_id"] == "legis-x"
