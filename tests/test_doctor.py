from __future__ import annotations

import json

from legis.cli import main as cli_main
from legis.doctor import DoctorCheck, render_json, render_text, run_doctor


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


def test_run_doctor_empty_is_healthy(tmp_path, capsys):
    # With no checks registered yet, an empty list renders healthy, exit 0.
    rc = run_doctor(tmp_path, repair=False, fmt="text")
    assert rc == 0
    assert "legis doctor: ok" in capsys.readouterr().out


def test_run_doctor_json_format(tmp_path, capsys):
    rc = run_doctor(tmp_path, repair=False, fmt="json")
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": True, "checks": [], "next_actions": []}


def test_cli_doctor_runs_and_exits_zero(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc = cli_main(["doctor"])
    assert rc == 0
    assert "legis doctor: ok" in capsys.readouterr().out


def test_cli_doctor_json(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc = cli_main(["doctor", "--format", "json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
