from pathlib import Path


def test_enforcement_core_depends_on_store_protocol_not_audit_store():
    root = Path("src/legis/enforcement")

    offenders = []
    for path in root.glob("*.py"):
        text = path.read_text()
        if "from legis.store.audit_store import AuditStore" in text:
            offenders.append(path.as_posix())

    assert offenders == []
