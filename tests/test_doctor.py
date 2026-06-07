from __future__ import annotations

import json

from legis.cli import main as cli_main
from legis.doctor import (
    DoctorCheck,
    check_gitignore,
    check_hook,
    check_instruction_block,
    check_mcp_json,
    check_skill_pack,
    render_json,
    render_text,
    run_doctor,
)
from legis import install as legis_install


def test_doctorcheck_to_dict_omits_empty_message():
    assert DoctorCheck("a.b", "ok").to_dict() == {"id": "a.b", "status": "ok", "fixed": False}
    assert DoctorCheck("a.b", "error", message="boom").to_dict() == {
        "id": "a.b",
        "status": "error",
        "fixed": False,
        "message": "boom",
    }


def test_render_json_shape():
    checks = [DoctorCheck("a", "ok"), DoctorCheck("b", "error", message="bad")]
    payload = json.loads(render_json(checks))
    assert payload["ok"] is False
    assert payload["checks"][0] == {"id": "a", "status": "ok", "fixed": False}
    assert payload["next_actions"] == ["b: bad"]


def test_render_text_lists_only_problems_when_healthy_says_ok():
    assert "legis doctor: ok" in render_text([DoctorCheck("a", "ok")])
    out = render_text([DoctorCheck("a", "ok"), DoctorCheck("b", "error", message="bad")])
    assert "b: error" in out
    assert "legis doctor: ok" not in out


def test_run_doctor_healthy_after_repair(tmp_path, capsys):
    # A project repaired via run_doctor renders healthy on re-check, exit 0.
    run_doctor(tmp_path, repair=True, fmt="text")
    capsys.readouterr()  # discard repair output
    rc = run_doctor(tmp_path, repair=False, fmt="text")
    assert rc == 0
    assert "legis doctor: ok" in capsys.readouterr().out


def test_run_doctor_json_format(tmp_path, capsys):
    run_doctor(tmp_path, repair=True, fmt="json")
    capsys.readouterr()  # discard repair output
    rc = run_doctor(tmp_path, repair=False, fmt="json")
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["next_actions"] == []


def test_cli_doctor_runs_and_exits_zero(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc = cli_main(["doctor", "--repair"])
    assert rc == 0
    assert "legis doctor: ok" in capsys.readouterr().out


def test_cli_doctor_json(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc = cli_main(["doctor", "--repair", "--format", "json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


# ---------------------------------------------------------------------------
# check_mcp_json
# ---------------------------------------------------------------------------


def test_mcp_json_absent_is_error(tmp_path):
    c = check_mcp_json(tmp_path, repair=False)
    assert c.id == "install.mcp_json"
    assert c.status == "error"
    assert c.fixed is False


def test_mcp_json_repair_fixes_it(tmp_path):
    c = check_mcp_json(tmp_path, repair=True)
    assert c.status == "ok"
    assert c.fixed is True
    assert (tmp_path / ".mcp.json").exists()


def test_mcp_json_present_is_ok(tmp_path):
    from legis.install import register_mcp_json

    register_mcp_json(tmp_path)
    c = check_mcp_json(tmp_path, repair=False)
    assert c.status == "ok"
    assert c.fixed is False


# ---------------------------------------------------------------------------
# Task 6: install-wiring checks (blocks, skills, hook, gitignore)
# ---------------------------------------------------------------------------


def test_instruction_block_absent_is_error(tmp_path):
    c = check_instruction_block(tmp_path, "CLAUDE.md", repair=False)
    assert c.id == "install.claude_md"
    assert c.status == "error"


def test_instruction_block_repair_creates_it(tmp_path):
    c = check_instruction_block(tmp_path, "CLAUDE.md", repair=True)
    assert c.status == "ok"
    assert c.fixed is True
    assert legis_install.INSTRUCTIONS_MARKER in (tmp_path / "CLAUDE.md").read_text()


def test_gitignore_absent_is_error_then_repaired(tmp_path):
    assert check_gitignore(tmp_path, repair=False).status == "error"
    fixed = check_gitignore(tmp_path, repair=True)
    assert fixed.status == "ok" and fixed.fixed is True
    assert ".weft/legis/" in (tmp_path / ".gitignore").read_text()


def test_skill_pack_absent_is_error(tmp_path):
    assert check_skill_pack(tmp_path, ".claude", repair=False).status == "error"


def test_skill_pack_repair_installs(tmp_path):
    c = check_skill_pack(tmp_path, ".claude", repair=True)
    assert c.status == "ok" and c.fixed is True
