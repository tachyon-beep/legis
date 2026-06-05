import sqlite3

import pytest

from legis.store.audit_store import AuditStore


def db_path(tmp_path):
    return tmp_path / "audit.db"


def make_store(tmp_path):
    return AuditStore(f"sqlite:///{db_path(tmp_path)}")


def raw_conn(tmp_path):
    return sqlite3.connect(db_path(tmp_path))


def test_append_returns_monotonic_seq(tmp_path):
    s = make_store(tmp_path)
    assert s.append({"k": "a"}) == 1
    assert s.append({"k": "b"}) == 2


def test_read_all_is_ordered_and_decodes_payload(tmp_path):
    s = make_store(tmp_path)
    s.append({"k": "a"})
    s.append({"k": "b"})
    recs = s.read_all()
    assert [r.seq for r in recs] == [1, 2]
    assert recs[0].payload == {"k": "a"}


def test_store_exposes_no_mutation_api(tmp_path):
    s = make_store(tmp_path)
    assert not hasattr(s, "update")
    assert not hasattr(s, "delete")


def test_trigger_rejects_update(tmp_path):
    s = make_store(tmp_path)
    s.append({"k": "a"})
    conn = raw_conn(tmp_path)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE audit_log SET payload = '{}' WHERE seq = 1")
            conn.commit()
    finally:
        conn.close()


def test_trigger_rejects_delete(tmp_path):
    s = make_store(tmp_path)
    s.append({"k": "a"})
    conn = raw_conn(tmp_path)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM audit_log WHERE seq = 1")
            conn.commit()
    finally:
        conn.close()


def test_verify_integrity_passes_on_clean_chain(tmp_path):
    s = make_store(tmp_path)
    s.append({"k": "a"})
    s.append({"k": "b"})
    assert s.verify_integrity() is True


def test_verify_integrity_detects_out_of_band_tamper(tmp_path):
    s = make_store(tmp_path)
    s.append({"k": "a"})
    s.append({"k": "b"})
    # Tamper directly, bypassing the app API and the append-only triggers:
    conn = raw_conn(tmp_path)
    try:
        conn.execute("DROP TRIGGER audit_log_no_update")
        conn.execute(
            "UPDATE audit_log SET payload = :p WHERE seq = 1",
            {"p": '{"k":"EVIL"}'},
        )
        conn.commit()
    finally:
        conn.close()
    assert s.verify_integrity() is False


def test_verify_integrity_handles_malformed_json_as_integrity_failure(tmp_path):
    s = make_store(tmp_path)
    s.append({"k": "a"})
    conn = raw_conn(tmp_path)
    try:
        conn.execute("DROP TRIGGER audit_log_no_update")
        conn.execute(
            "UPDATE audit_log SET payload = :p WHERE seq = 1",
            {"p": "{not-json"},
        )
        conn.commit()
    finally:
        conn.close()

    assert s.verify_integrity() is False


def test_audit_store_concurrent_writes(tmp_path):
    import threading
    s = make_store(tmp_path)

    errors = []
    def run_appends(tid, count):
        try:
            for i in range(count):
                s.append({"thread": tid, "idx": i})
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=run_appends, args=(t, 20)) for t in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, f"Concurrent appends failed with errors: {errors}"
    recs = s.read_all()
    assert len(recs) == 100
    assert s.verify_integrity() is True


def test_verify_integrity_handles_non_finite_float_as_integrity_failure(tmp_path):
    # json.loads accepts Infinity/NaN, so the payload survives read_all's
    # decode guard, but content_hash -> canonical_json(allow_nan=False) raises
    # ValueError. verify_integrity must report tamper as False, not crash
    # (Q-M3 / audit M6).
    s = make_store(tmp_path)
    s.append({"k": "a"})
    conn = raw_conn(tmp_path)
    try:
        conn.execute("DROP TRIGGER audit_log_no_update")
        conn.execute(
            "UPDATE audit_log SET payload = :p WHERE seq = 1",
            {"p": '{"k": Infinity}'},
        )
        conn.commit()
    finally:
        conn.close()

    assert s.verify_integrity() is False
