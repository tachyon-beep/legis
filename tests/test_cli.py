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


def test_governance_gate_alias_exits_1_on_fail(tmp_path, capsys):
    from legis.clock import FixedClock
    from legis.enforcement.engine import EnforcementEngine
    from legis.enforcement.verdict import Verdict
    from legis.identity.entity_key import EntityKey
    from legis.store.audit_store import AuditStore

    db = f"sqlite:///{tmp_path / 'gov.db'}"
    eng = EnforcementEngine(AuditStore(db), FixedClock("2026-06-02T12:00:00+00:00"))
    for i in range(25):
        eng.record_event({"policy": "p", "entity_key": EntityKey.from_locator(f"x{i}").to_dict(),
                          "extensions": {"judge_verdict": Verdict.OVERRIDDEN_BY_OPERATOR.value}})
    rc = main(["governance-gate", "--db", db])
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


def test_governance_gate_missing_sqlite_db_is_pass_with_notice_without_creating_it(tmp_path, capsys):
    db_path = tmp_path / "missing.db"
    rc = main(["governance-gate", "--db", f"sqlite:///{db_path}"])
    assert rc == 0
    assert not db_path.exists()
    assert "PASS_WITH_NOTICE" in capsys.readouterr().out


def test_governance_gate_missing_sqlite_db_fails_closed_in_ci(tmp_path, capsys, monkeypatch):
    db_path = tmp_path / "missing.db"
    monkeypatch.setenv("CI", "true")

    rc = main(["governance-gate", "--db", f"sqlite:///{db_path}"])

    assert rc == 1
    assert not db_path.exists()
    captured = capsys.readouterr()
    assert "missing" in captured.err


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


def test_serve_accepts_judge_configuration_flags():
    args = build_parser().parse_args(
        [
            "serve",
            "--judge-provider",
            "openrouter",
            "--judge-model",
            "anthropic/claude-opus-4.7",
            "--judge-max-tokens",
            "2048",
        ]
    )

    assert args.judge_provider == "openrouter"
    assert args.judge_model == "anthropic/claude-opus-4.7"
    assert args.judge_max_tokens == 2048


def test_main_serve_sets_judge_env(monkeypatch):
    import os

    calls = []

    def fake_run(app, **kw):
        calls.append(
            {
                "provider": os.environ.get("LEGIS_JUDGE_PROVIDER"),
                "model": os.environ.get("LEGIS_JUDGE_MODEL"),
                "max_tokens": os.environ.get("LEGIS_JUDGE_MAX_TOKENS"),
            }
        )

    monkeypatch.delenv("LEGIS_JUDGE_PROVIDER", raising=False)
    monkeypatch.delenv("LEGIS_JUDGE_MODEL", raising=False)
    monkeypatch.delenv("LEGIS_JUDGE_MAX_TOKENS", raising=False)

    rc = main(
        [
            "serve",
            "--judge-provider",
            "openrouter",
            "--judge-model",
            "anthropic/claude-opus-4.7",
            "--judge-max-tokens",
            "2048",
        ],
        run=fake_run,
    )

    assert rc == 0
    assert calls == [
        {
            "provider": "openrouter",
            "model": "anthropic/claude-opus-4.7",
            "max_tokens": "2048",
        }
    ]


def test_main_mcp_sets_judge_env(monkeypatch):
    import os

    import legis.mcp as mcp_module

    calls = []

    def fake_mcp_main(agent_id):
        calls.append(
            {
                "provider": os.environ.get("LEGIS_JUDGE_PROVIDER"),
                "model": os.environ.get("LEGIS_JUDGE_MODEL"),
                "max_tokens": os.environ.get("LEGIS_JUDGE_MAX_TOKENS"),
            }
        )
        return 0

    monkeypatch.delenv("LEGIS_JUDGE_PROVIDER", raising=False)
    monkeypatch.delenv("LEGIS_JUDGE_MODEL", raising=False)
    monkeypatch.delenv("LEGIS_JUDGE_MAX_TOKENS", raising=False)
    monkeypatch.setattr(mcp_module, "main", fake_mcp_main)
    rc = main(
        [
            "mcp",
            "--agent-id",
            "agent-1",
            "--judge-provider",
            "openrouter",
            "--judge-model",
            "anthropic/claude-opus-4.7",
            "--judge-max-tokens",
            "2048",
        ]
    )

    assert rc == 0
    assert calls == [
        {
            "provider": "openrouter",
            "model": "anthropic/claude-opus-4.7",
            "max_tokens": "2048",
        }
    ]


def test_sei_backfill_command_defaults_to_dry_run():
    args = build_parser().parse_args(
        ["sei-backfill", "--db", "sqlite:///gov.db", "--clarion-url", "http://localhost"]
    )

    assert args.command == "sei-backfill"
    assert args.db == "sqlite:///gov.db"
    assert args.clarion_url == "http://localhost"
    assert args.execute is False
    assert args.actor == "legis-sei-backfill"


def test_main_sei_backfill_dispatches_service(monkeypatch, capsys):
    import legis.cli as cli_module

    calls = []

    class FakeStore:
        def __init__(self, db):
            self.db = db

    class FakeClient:
        def __init__(self, url, *, hmac_key=None):
            self.url = url
            self.hmac_key = hmac_key

    class FakeClock:
        pass

    class FakeReport:
        def to_dict(self):
            return {"dry_run": False, "appended": 2}

    def fake_run_pre_sei_backfill(store, client, clock, *, dry_run, actor):
        calls.append(
            {
                "db": store.db,
                "url": client.url,
                "hmac_key": client.hmac_key,
                "clock_type": type(clock).__name__,
                "dry_run": dry_run,
                "actor": actor,
            }
        )
        return FakeReport()

    monkeypatch.setattr(cli_module, "AuditStore", FakeStore, raising=False)
    monkeypatch.setattr(cli_module, "HttpClarionIdentity", FakeClient, raising=False)
    monkeypatch.setattr(cli_module, "SystemClock", FakeClock, raising=False)
    monkeypatch.setenv("LEGIS_CLARION_HMAC_KEY", "clarion-secret")
    monkeypatch.setattr(
        cli_module,
        "run_pre_sei_backfill",
        fake_run_pre_sei_backfill,
        raising=False,
    )

    rc = main(
        [
            "sei-backfill",
            "--db",
            "sqlite:///gov.db",
            "--clarion-url",
            "http://localhost",
            "--execute",
            "--actor",
            "operator-1",
        ]
    )

    assert rc == 0
    assert calls == [
        {
            "db": "sqlite:///gov.db",
            "url": "http://localhost",
            "hmac_key": b"clarion-secret",
            "clock_type": "FakeClock",
            "dry_run": False,
            "actor": "operator-1",
        }
    ]
    assert '"appended": 2' in capsys.readouterr().out
