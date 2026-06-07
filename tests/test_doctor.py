from __future__ import annotations

import json

from legis.doctor import DoctorCheck, render_json, render_text


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
