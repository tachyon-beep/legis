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
    # all-ok: banner present, no problem lines
    assert render_text([DoctorCheck("a", "ok")]) == "legis doctor: ok"

    # error present: no "ok" in headline, error listed
    out = render_text([DoctorCheck("a", "ok"), DoctorCheck("b", "error", message="bad")])
    assert "b: error" in out
    assert "legis doctor: ok" not in out

    # warn-only: banner present with warning count AND warn check is listed
    out_warn = render_text([DoctorCheck("a", "ok"), DoctorCheck("b", "warn", message="heads up")])
    assert "legis doctor: ok" in out_warn
    assert "b: warn" in out_warn


def test_run_doctor_healthy_after_repair(tmp_path, capsys):
    # A project repaired via run_doctor renders healthy on re-check, exit 0.
    run_doctor(tmp_path, repair=True, fmt="text")
    capsys.readouterr()  # discard repair output
    rc = run_doctor(tmp_path, repair=False, fmt="text")
    assert rc == 0
    assert "legis doctor: ok" in capsys.readouterr().out


def test_run_doctor_json_format(tmp_path, capsys, monkeypatch):
    # Clear the governance-enablement env so the two report-only N3 checks
    # deterministically warn (an unwired fresh project). They are NOT repairable
    # (operator must set env / author cells.toml out-of-band) and are the honest
    # C-10(c) signal — so a repaired-but-ungoverned project is ok-with-warns,
    # not error, and its only next_actions are those two enablement hints.
    for var in (
        "LEGIS_POLICY_CELLS", "LEGIS_DEV_DEFAULT_CELLS", "LEGIS_SOURCE_ROOT",
        "LEGIS_WARDLINE_CELL", "LEGIS_WARDLINE_CELL_BY_SEVERITY",
    ):
        monkeypatch.delenv(var, raising=False)
    run_doctor(tmp_path, repair=True, fmt="json")
    capsys.readouterr()  # discard repair output
    rc = run_doctor(tmp_path, repair=False, fmt="json")
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert {a.split(":", 1)[0] for a in payload["next_actions"]} == {
        "runtime.policy_cells",
        "runtime.wardline_routing",
    }


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


def test_mcp_json_stale_command_is_error_then_repaired(tmp_path):
    """An entry with a dead command path is stale and must trigger repair."""
    stale_entry = {
        "mcpServers": {
            "legis": {
                "type": "stdio",
                "command": "/nonexistent/legis-xyz",
                "args": ["mcp", "--agent-id", "claude-code"],
                "env": {},
            }
        }
    }
    (tmp_path / ".mcp.json").write_text(json.dumps(stale_entry))
    c = check_mcp_json(tmp_path, repair=False)
    assert c.id == "install.mcp_json"
    assert c.status == "error"

    fixed = check_mcp_json(tmp_path, repair=True)
    assert fixed.status == "ok"
    assert fixed.fixed is True


# ---------------------------------------------------------------------------
# Direct unit tests for mcp_entry_is_current predicate
# ---------------------------------------------------------------------------


from legis.install import mcp_entry_is_current, register_mcp_json as _register_mcp_json


def test_mcp_entry_is_current_absent_file(tmp_path):
    assert mcp_entry_is_current(tmp_path) is False


def test_mcp_entry_is_current_malformed_json(tmp_path):
    (tmp_path / ".mcp.json").write_text("{not valid json")
    assert mcp_entry_is_current(tmp_path) is False


def test_mcp_entry_is_current_non_dict_top_level(tmp_path):
    (tmp_path / ".mcp.json").write_text('["just", "an", "array"]')
    assert mcp_entry_is_current(tmp_path) is False


def test_mcp_entry_is_current_missing_mcp_servers(tmp_path):
    (tmp_path / ".mcp.json").write_text('{"other": {}}')
    assert mcp_entry_is_current(tmp_path) is False


def test_mcp_entry_is_current_mcp_servers_not_dict(tmp_path):
    (tmp_path / ".mcp.json").write_text('{"mcpServers": "not a dict"}')
    assert mcp_entry_is_current(tmp_path) is False


def test_mcp_entry_is_current_no_legis_entry(tmp_path):
    (tmp_path / ".mcp.json").write_text('{"mcpServers": {"other": {}}}')
    assert mcp_entry_is_current(tmp_path) is False


def test_mcp_entry_is_current_legis_entry_not_dict(tmp_path):
    (tmp_path / ".mcp.json").write_text('{"mcpServers": {"legis": "string"}}')
    assert mcp_entry_is_current(tmp_path) is False


def test_mcp_entry_is_current_args_without_mcp(tmp_path):
    entry = {"mcpServers": {"legis": {"command": "legis", "args": ["serve"]}}}
    (tmp_path / ".mcp.json").write_text(json.dumps(entry))
    assert mcp_entry_is_current(tmp_path) is False


def test_mcp_entry_is_current_empty_command(tmp_path):
    entry = {"mcpServers": {"legis": {"command": "", "args": ["mcp"]}}}
    (tmp_path / ".mcp.json").write_text(json.dumps(entry))
    assert mcp_entry_is_current(tmp_path) is False


def test_mcp_entry_is_current_dead_command_path(tmp_path):
    entry = {
        "mcpServers": {
            "legis": {
                "command": "/nonexistent/legis-xyz",
                "args": ["mcp", "--agent-id", "claude-code"],
            }
        }
    }
    (tmp_path / ".mcp.json").write_text(json.dumps(entry))
    assert mcp_entry_is_current(tmp_path) is False


def test_mcp_entry_is_current_fresh_registered_entry(tmp_path):
    """A freshly registered entry must read as current."""
    _register_mcp_json(tmp_path)
    assert mcp_entry_is_current(tmp_path) is True


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


# ---------------------------------------------------------------------------
# Task 8: governance integrity + runtime/sibling checks
# ---------------------------------------------------------------------------


from legis.doctor import check_audit_chain, check_hmac_key, check_sibling_url


def test_audit_chain_absent_db_is_ok(tmp_path):
    c = check_audit_chain("store.governance_chain", "sqlite:///" + str(tmp_path / "nope.db"))
    assert c.status == "ok"
    # No-leak invariant: must NOT create the file
    assert not (tmp_path / "nope.db").exists()


def test_audit_chain_intact_db_is_ok(tmp_path):
    from legis.store.audit_store import AuditStore

    url = "sqlite:///" + str(tmp_path / "gov.db")
    AuditStore(url)  # creates schema
    assert check_audit_chain("store.governance_chain", url).status == "ok"


def test_hmac_key_warn_when_protected_set_without_key(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGIS_PROTECTED_POLICIES", "secrets.read")
    monkeypatch.delenv("LEGIS_HMAC_KEY", raising=False)
    c = check_hmac_key(tmp_path)
    assert c.status == "warn"


def test_hmac_key_never_prints_value(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGIS_PROTECTED_POLICIES", "secrets.read")
    monkeypatch.setenv("LEGIS_HMAC_KEY", "super-secret-value")
    c = check_hmac_key(tmp_path)
    assert c.status == "ok"
    assert "super-secret-value" not in (c.message or "")


def test_sibling_url_invalid_is_error(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOMWEAVE_API_URL", "localhost:9620")  # no scheme
    c = check_sibling_url("runtime.loomweave_url", "LOOMWEAVE_API_URL")
    assert c.status == "error"


# --- N3 (weft-df8d2ef454): report-only enablement checks (C-10(c)) ----------
from legis.doctor import check_policy_cells, check_wardline_routing


def test_policy_cells_warn_when_unconfigured_names_the_path(tmp_path, monkeypatch):
    # Fresh launch, no cells.toml, dev opt-in off -> warn, fail-closed in effect,
    # message names the concrete enablement keys.
    monkeypatch.delenv("LEGIS_POLICY_CELLS", raising=False)
    monkeypatch.delenv("LEGIS_DEV_DEFAULT_CELLS", raising=False)
    monkeypatch.delenv("LEGIS_SOURCE_ROOT", raising=False)
    c = check_policy_cells(tmp_path)
    assert c.status == "warn"
    msg = c.message or ""
    assert "LEGIS_POLICY_CELLS" in msg or "policy/cells.toml" in msg
    assert "LEGIS_DEV_DEFAULT_CELLS" in msg


def test_policy_cells_ok_when_cells_toml_resolves(tmp_path, monkeypatch):
    monkeypatch.delenv("LEGIS_POLICY_CELLS", raising=False)
    monkeypatch.delenv("LEGIS_DEV_DEFAULT_CELLS", raising=False)
    (tmp_path / "policy").mkdir()
    (tmp_path / "policy" / "cells.toml").write_text('default_cell = "structured"\n')
    c = check_policy_cells(tmp_path)
    assert c.status == "ok"


def test_policy_cells_ok_via_env_path(tmp_path, monkeypatch):
    cells = tmp_path / "elsewhere.toml"
    cells.write_text('default_cell = "structured"\n')
    monkeypatch.setenv("LEGIS_POLICY_CELLS", str(cells))
    c = check_policy_cells(tmp_path)
    assert c.status == "ok"


def test_wardline_routing_warn_when_unconfigured_names_the_key(tmp_path, monkeypatch):
    monkeypatch.delenv("LEGIS_WARDLINE_CELL", raising=False)
    monkeypatch.delenv("LEGIS_WARDLINE_CELL_BY_SEVERITY", raising=False)
    c = check_wardline_routing(tmp_path)
    assert c.status == "warn"
    assert "LEGIS_WARDLINE_CELL" in (c.message or "")


def test_wardline_routing_ok_when_cell_set(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGIS_WARDLINE_CELL", "surface_only")
    monkeypatch.delenv("LEGIS_WARDLINE_CELL_BY_SEVERITY", raising=False)
    c = check_wardline_routing(tmp_path)
    assert c.status == "ok"


def test_n3_checks_never_write_files_or_render_keys(tmp_path, monkeypatch):
    # C-8 / C-9(b): report-only. They must not create any file (no scaffolding)
    # and must never echo a secret value.
    monkeypatch.delenv("LEGIS_POLICY_CELLS", raising=False)
    monkeypatch.delenv("LEGIS_DEV_DEFAULT_CELLS", raising=False)
    monkeypatch.setenv("LEGIS_HMAC_KEY", "super-secret-value")
    before = set(tmp_path.rglob("*"))
    msgs = [
        check_policy_cells(tmp_path).message or "",
        check_wardline_routing(tmp_path).message or "",
    ]
    assert set(tmp_path.rglob("*")) == before  # wrote nothing
    # never render a secret value (the "render_keys" half of the contract)
    assert all("super-secret-value" not in m for m in msgs)
    # neither check signature takes a `repair` parameter (cannot be coerced to write)
    import inspect
    assert "repair" not in inspect.signature(check_policy_cells).parameters
    assert "repair" not in inspect.signature(check_wardline_routing).parameters


# ---------------------------------------------------------------------------
# Review follow-ups: root-anchored store_dir + empty-override precedence
# ---------------------------------------------------------------------------


from legis.doctor import _store_url


def test_store_dir_root_anchored_via_weft_toml(tmp_path, monkeypatch):
    # --root != cwd, with a weft.toml that relocates the store. Resolution must
    # honor root/weft.toml, not cwd's, and stay under root (review #1).
    monkeypatch.chdir(tmp_path)  # cwd has no weft.toml
    # Clear the conftest store override so weft.toml resolution is exercised.
    monkeypatch.delenv("LEGIS_GOVERNANCE_DB", raising=False)
    root = tmp_path / "proj"
    (root / "custom_store").mkdir(parents=True)
    (root / "weft.toml").write_text('[legis]\nstore_dir = "custom_store"\n')

    c = check_store_dir(root)
    assert c.status == "ok"

    # The audit-chain URL must point under root/custom_store, not cwd/.weft.
    url = _store_url(root, "legis-governance.db", "LEGIS_GOVERNANCE_DB")
    assert (root / "custom_store" / "legis-governance.db").as_posix() in url
    assert ".weft" not in url


def test_db_override_empty_string_is_error(tmp_path, monkeypatch):
    # Present-but-empty override is a verbatim broken override, not "unset"
    # (matches config precedence; review #3).
    monkeypatch.setenv("LEGIS_GOVERNANCE_DB", "")
    assert check_db_overrides(tmp_path).status == "error"


# ---------------------------------------------------------------------------
# Task 9: end-to-end --repair pipeline + invariant tests
# ---------------------------------------------------------------------------


def test_repair_makes_fresh_project_healthy(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Hermetic: an inherited sibling URL env var (valid or not) would otherwise
    # leak into the repair → exit 0 assertion. Unset both so the check is "not
    # configured" (ok), never a non-repairable error.
    monkeypatch.delenv("LOOMWEAVE_API_URL", raising=False)
    monkeypatch.delenv("FILIGREE_API_URL", raising=False)
    # First run: unhealthy (no install artifacts, no .mcp.json).
    assert run_doctor(tmp_path, repair=False, fmt="text") == 1
    # Repair run: install-wiring + .mcp.json get fixed; re-check is healthy.
    assert run_doctor(tmp_path, repair=True, fmt="text") == 0
    # Third run, no repair: stays healthy.
    assert run_doctor(tmp_path, repair=False, fmt="text") == 0


def test_repair_never_writes_weft_toml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "weft.toml").write_text("[legis]\nstore_dir = \n")  # malformed
    before = (tmp_path / "weft.toml").read_text()
    run_doctor(tmp_path, repair=True, fmt="json")
    assert (tmp_path / "weft.toml").read_text() == before


def test_json_output_has_no_secret(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LEGIS_PROTECTED_POLICIES", "secrets.read")
    monkeypatch.setenv("LEGIS_HMAC_KEY", "TOP-SECRET")
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        run_doctor(tmp_path, repair=False, fmt="json")
    out = buf.getvalue()
    assert "TOP-SECRET" not in out
    # Prove the secret-bearing path actually ran: with both the protected policy
    # and the key set, check_hmac_key reads the key and reports ok. Asserting the
    # check is present (and ok) keeps this guard from passing vacuously if the
    # key-reading check were ever removed.
    payload = json.loads(out)
    hmac_checks = [c for c in payload["checks"] if c["id"] == "runtime.hmac_key"]
    assert hmac_checks and hmac_checks[0]["status"] == "ok"
