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


# ---------------------------------------------------------------------------
# Task 6 (drift): stale block / stale skill pack are the headline behavior
# ---------------------------------------------------------------------------


def test_instruction_block_stale_token_is_error_then_repaired(tmp_path):
    # A real block with a mutated marker token: marker present, token mismatch.
    legis_install.inject_instructions(tmp_path / "CLAUDE.md")
    path = tmp_path / "CLAUDE.md"
    content = path.read_text()
    fresh_token = legis_install._marker_token()
    stale = content.replace(f":{fresh_token} -->", ":v0:deadbeef -->", 1)
    assert stale != content  # the token really was rewritten
    path.write_text(stale)
    assert legis_install._extract_marker_token(stale) != fresh_token

    c = check_instruction_block(tmp_path, "CLAUDE.md", repair=False)
    assert c.status == "error"

    fixed = check_instruction_block(tmp_path, "CLAUDE.md", repair=True)
    assert fixed.status == "ok"
    assert fixed.fixed is True
    assert legis_install._extract_marker_token((tmp_path / "CLAUDE.md").read_text()) == fresh_token


def test_skill_pack_stale_fingerprint_is_error_then_repaired(tmp_path):
    legis_install.install_skills(tmp_path)
    pack = tmp_path / ".claude" / "skills" / legis_install.SKILL_NAME
    # Mutate a file under the installed pack so its fingerprint diverges from source.
    skill_md = pack / "SKILL.md"
    skill_md.write_text(skill_md.read_text() + "\n<!-- drift -->\n")

    c = check_skill_pack(tmp_path, ".claude", repair=False)
    assert c.status == "error"

    fixed = check_skill_pack(tmp_path, ".claude", repair=True)
    assert fixed.status == "ok"
    assert fixed.fixed is True


# ---------------------------------------------------------------------------
# Task 6: hook check
# ---------------------------------------------------------------------------


def test_hook_absent_is_error_then_repaired(tmp_path):
    c = check_hook(tmp_path, repair=False)
    assert c.id == "install.hook"
    assert c.status == "error"

    fixed = check_hook(tmp_path, repair=True)
    assert fixed.status == "ok"
    assert fixed.fixed is True


# ---------------------------------------------------------------------------
# Task 7: config & store checks (weft.toml report-only, store dir, db overrides, legacy)
# ---------------------------------------------------------------------------


from legis.doctor import check_weft_toml, check_store_dir, check_db_overrides, check_legacy_stray_db


def test_weft_toml_absent_is_ok(tmp_path):
    assert check_weft_toml(tmp_path).status == "ok"


def test_weft_toml_valid_legis_table_is_ok(tmp_path):
    (tmp_path / "weft.toml").write_text('[legis]\nstore_dir = ".weft/legis"\n')
    assert check_weft_toml(tmp_path).status == "ok"


def test_weft_toml_malformed_is_error_and_unchanged(tmp_path):
    wt = tmp_path / "weft.toml"
    wt.write_text("[legis]\nstore_dir = \n")  # malformed TOML
    before = wt.read_text()
    c = check_weft_toml(tmp_path)
    assert c.status == "error"
    assert wt.read_text() == before  # C-9(b): never written


def test_weft_toml_legis_not_a_table_is_error(tmp_path):
    (tmp_path / "weft.toml").write_text('legis = "oops"\n')
    assert check_weft_toml(tmp_path).status == "error"


def test_store_dir_writable_parent_is_ok(tmp_path):
    assert check_store_dir(tmp_path).status == "ok"


def test_db_override_bad_url_is_error(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGIS_GOVERNANCE_DB", "::not a url::")
    assert check_db_overrides(tmp_path).status == "error"


def test_legacy_stray_db_is_warn(tmp_path):
    (tmp_path / "legis-governance.db").write_text("x")
    assert check_legacy_stray_db(tmp_path).status == "warn"
