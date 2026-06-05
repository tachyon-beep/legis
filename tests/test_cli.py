from legis.cli import build_parser, main


def test_serve_defaults():
    args = build_parser().parse_args(["serve"])
    assert args.command == "serve"
    assert args.host == "127.0.0.1"
    assert args.port == 8000


def test_serve_custom_host_and_port():
    args = build_parser().parse_args(["serve", "--host", "0.0.0.0", "--port", "9001"])
    assert args.host == "0.0.0.0"
    assert args.port == 9001


def test_main_serve_invokes_runner_with_factory():
    calls = []

    def fake_run(app, **kw):
        calls.append((app, kw))

    rc = main(["serve", "--host", "0.0.0.0", "--port", "9001"], run=fake_run)
    assert rc == 0
    assert calls == [("legis.api.app:create_app",
                      {"host": "0.0.0.0", "port": 9001, "factory": True})]


def test_serve_rejects_hmac_key_argument():
    import pytest

    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["serve", "--hmac-key", "secret"])
    assert excinfo.value.code == 2


def test_main_no_command_returns_2():
    rc = main([], run=lambda *a, **k: None)
    assert rc == 2


def test_check_override_rate_exits_1_on_fail(tmp_path, capsys):
    from legis.clock import FixedClock
    from legis.enforcement.engine import EnforcementEngine
    from legis.enforcement.verdict import Verdict
    from legis.identity.entity_key import EntityKey
    from legis.store.audit_store import AuditStore

    db = f"sqlite:///{tmp_path / 'gov.db'}"
    eng = EnforcementEngine(AuditStore(db), FixedClock("2026-06-02T12:00:00+00:00"))
    for i in range(25):  # 25 operator-overrides → rate 1.0 > 0.2, n>=20 → FAIL
        eng.record_event({"policy": "p", "entity_key": EntityKey.from_locator(f"x{i}").to_dict(),
                          "extensions": {"judge_verdict": Verdict.OVERRIDDEN_BY_OPERATOR.value}})
    rc = main(["check-override-rate", "--db", db])
    assert rc == 1
    assert "FAIL" in capsys.readouterr().out


def test_check_override_rate_exits_0_when_clean(tmp_path, capsys):
    from legis.clock import FixedClock
    from legis.enforcement.engine import EnforcementEngine
    from legis.enforcement.verdict import Verdict
    from legis.identity.entity_key import EntityKey
    from legis.store.audit_store import AuditStore

    db = f"sqlite:///{tmp_path / 'gov.db'}"
    eng = EnforcementEngine(AuditStore(db), FixedClock("2026-06-02T12:00:00+00:00"))
    for i in range(25):  # all ACCEPTED → rate 0.0 → PASS
        eng.record_event({"policy": "p", "entity_key": EntityKey.from_locator(f"x{i}").to_dict(),
                          "extensions": {"judge_verdict": Verdict.ACCEPTED.value}})
    assert main(["check-override-rate", "--db", db]) == 0
    assert "PASS" in capsys.readouterr().out


def test_check_override_rate_fails_for_missing_sqlite_db_without_creating_it(tmp_path, capsys):
    db_path = tmp_path / "missing.db"
    rc = main(["check-override-rate", "--db", f"sqlite:///{db_path}"])
    assert rc == 1
    assert not db_path.exists()
    assert "missing" in capsys.readouterr().err.lower()


def test_mcp_command_accepts_store_and_policy_cell_flags():
    from legis.cli import build_parser

    args = build_parser().parse_args(
        [
            "mcp",
            "--agent-id",
            "agent-1",
            "--governance-db",
            "sqlite:///gov.db",
            "--check-db",
            "sqlite:///checks.db",
            "--policy-cells",
            "policy/cells.toml",
        ]
    )

    assert args.command == "mcp"
    assert args.agent_id == "agent-1"
    assert args.governance_db == "sqlite:///gov.db"
    assert args.check_db == "sqlite:///checks.db"
    assert args.policy_cells == "policy/cells.toml"


def test_main_mcp_sets_store_and_policy_cell_env(monkeypatch):
    import os

    import legis.mcp as mcp_module

    calls = []

    def fake_mcp_main(agent_id):
        calls.append(
            {
                "agent_id": agent_id,
                "governance_db": os.environ.get("LEGIS_GOVERNANCE_DB"),
                "check_db": os.environ.get("LEGIS_CHECK_DB"),
                "policy_cells": os.environ.get("LEGIS_POLICY_CELLS"),
            }
        )
        return 0

    monkeypatch.delenv("LEGIS_GOVERNANCE_DB", raising=False)
    monkeypatch.delenv("LEGIS_CHECK_DB", raising=False)
    monkeypatch.delenv("LEGIS_POLICY_CELLS", raising=False)
    monkeypatch.setattr(mcp_module, "main", fake_mcp_main)

    rc = main(
        [
            "mcp",
            "--agent-id",
            "agent-1",
            "--governance-db",
            "sqlite:///gov.db",
            "--check-db",
            "sqlite:///checks.db",
            "--policy-cells",
            "policy/cells.toml",
        ]
    )

    assert rc == 0
    assert calls == [
        {
            "agent_id": "agent-1",
            "governance_db": "sqlite:///gov.db",
            "check_db": "sqlite:///checks.db",
            "policy_cells": "policy/cells.toml",
        }
    ]


def test_check_override_rate_fails_closed_for_protected_records_without_hmac_key(tmp_path, monkeypatch, capsys):
    from legis.clock import FixedClock
    from legis.enforcement.protected import ProtectedGate
    from legis.enforcement.verdict import JudgeOpinion, Verdict
    from legis.identity.entity_key import EntityKey
    from legis.store.audit_store import AuditStore

    class ScriptedJudge:
        def evaluate(self, record):
            return JudgeOpinion(Verdict.ACCEPTED, "judge@1", "ok")

    db = f"sqlite:///{tmp_path / 'gov.db'}"
    gate = ProtectedGate(
        AuditStore(db),
        FixedClock("2026-06-02T12:00:00+00:00"),
        judge=ScriptedJudge(),
        key=b"protected-key",
    )
    gate.submit(
        policy="no-eval",
        entity_key=EntityKey.from_locator("src/x.py:f"),
        rationale="approved",
        agent_id="agent-1",
        file_fingerprint="sha256:abc",
        ast_path="Module/Call[eval]",
    )

    monkeypatch.delenv("LEGIS_HMAC_KEY", raising=False)
    monkeypatch.delenv("LEGIS_PROTECTED_POLICIES", raising=False)

    rc = main(["check-override-rate", "--db", db])

    assert rc == 1
    assert "LEGIS_HMAC_KEY" in capsys.readouterr().err


def test_check_override_rate_rejects_rechained_protected_tampering(tmp_path, monkeypatch, capsys):
    import json
    import sqlite3

    from legis.canonical import canonical_json, content_hash
    from legis.clock import FixedClock
    from legis.enforcement.protected import ProtectedGate
    from legis.enforcement.verdict import JudgeOpinion, Verdict
    from legis.identity.entity_key import EntityKey
    from legis.store.audit_store import GENESIS, AuditStore, _chain

    class ScriptedJudge:
        def evaluate(self, record):
            return JudgeOpinion(Verdict.BLOCKED, "judge@1", "no")

    db_path = tmp_path / "gov.db"
    db = f"sqlite:///{db_path}"
    gate = ProtectedGate(
        AuditStore(db),
        FixedClock("2026-06-02T12:00:00+00:00"),
        judge=ScriptedJudge(),
        key=b"protected-key",
    )
    for i in range(25):
        gate.operator_override(
            policy="no-eval",
            entity_key=EntityKey.from_locator(f"src/x{i}.py:f"),
            rationale="security lead approved release exception",
            operator_id="op-sec-lead",
            file_fingerprint=f"sha256:{i}",
            ast_path="Module/Call[eval]",
        )

    con = sqlite3.connect(db_path)
    con.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
    con.execute("DROP TRIGGER IF EXISTS audit_log_no_delete")
    rows = con.execute("SELECT seq, payload FROM audit_log ORDER BY seq ASC").fetchall()
    for seq, payload in rows:
        parsed = json.loads(payload)
        parsed["extensions"]["judge_verdict"] = Verdict.ACCEPTED.value
        con.execute("UPDATE audit_log SET payload=? WHERE seq=?", (canonical_json(parsed), seq))
    previous = GENESIS
    for seq, payload in con.execute("SELECT seq, payload FROM audit_log ORDER BY seq ASC").fetchall():
        current_content_hash = content_hash(json.loads(payload))
        current_chain_hash = _chain(previous, current_content_hash)
        con.execute(
            "UPDATE audit_log SET content_hash=?, prev_hash=?, chain_hash=? WHERE seq=?",
            (current_content_hash, previous, current_chain_hash, seq),
        )
        previous = current_chain_hash
    con.commit()
    con.close()

    monkeypatch.setenv("LEGIS_HMAC_KEY", "protected-key")
    monkeypatch.setenv("LEGIS_PROTECTED_POLICIES", "no-eval")

    rc = main(["check-override-rate", "--db", db])

    assert rc == 1
    assert "verification failed" in capsys.readouterr().err
