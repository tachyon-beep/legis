"""Tests for legis.hooks — drift refresh and SessionStart context."""

from __future__ import annotations

from legis import hooks, install
from legis.hooks import (
    _extract_marker_token,
    generate_session_context,
    refresh_instructions,
)
from legis.install import (
    SKILL_NAME,
    _marker_token,
    inject_instructions,
    install_skills,
)


def test_extract_marker_token_roundtrip():
    token = _marker_token()
    content = f"x\n<!-- legis:instructions:{token} -->\nbody\n"
    assert _extract_marker_token(content) == token


def test_extract_marker_token_absent():
    assert _extract_marker_token("no marker here") is None


def test_refresh_noop_when_fresh(tmp_path):
    inject_instructions(tmp_path / "CLAUDE.md")
    inject_instructions(tmp_path / "AGENTS.md")
    assert refresh_instructions(tmp_path) == []


def test_refresh_updates_drifted_block_in_both_files(tmp_path, monkeypatch):
    inject_instructions(tmp_path / "CLAUDE.md")
    inject_instructions(tmp_path / "AGENTS.md")

    # Simulate drift: the bundled content now hashes differently.
    monkeypatch.setattr(install, "_instructions_text", lambda: "DRIFTED BODY\n")
    messages = refresh_instructions(tmp_path)

    assert any("CLAUDE.md" in m for m in messages)
    assert any("AGENTS.md" in m for m in messages)
    assert "DRIFTED BODY" in (tmp_path / "CLAUDE.md").read_text()
    assert "DRIFTED BODY" in (tmp_path / "AGENTS.md").read_text()


def test_refresh_skips_file_without_marker(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# plain file, no legis marker\n")
    assert refresh_instructions(tmp_path) == []
    assert "legis:instructions" not in (tmp_path / "CLAUDE.md").read_text()


def test_refresh_skips_absent_files(tmp_path):
    # Neither CLAUDE.md nor AGENTS.md exists and no skills installed.
    assert refresh_instructions(tmp_path) == []


def test_refresh_reinstalls_drifted_skill_pack(tmp_path):
    install_skills(tmp_path)
    skill = tmp_path / ".claude" / "skills" / SKILL_NAME / "SKILL.md"
    source = skill.read_text()
    # Corrupt the installed copy so its fingerprint diverges from source.
    skill.write_text(source + "\nLOCAL EDIT THAT MUST BE OVERWRITTEN\n")

    messages = refresh_instructions(tmp_path)

    assert any("skill pack" in m for m in messages)
    assert skill.read_text() == source


def test_refresh_does_not_create_skill_pack_when_absent(tmp_path):
    # No skill installed → refresh must not create one.
    refresh_instructions(tmp_path)
    assert not (tmp_path / ".claude" / "skills" / SKILL_NAME).exists()


def test_generate_session_context_returns_none_when_fresh(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    inject_instructions(tmp_path / "CLAUDE.md")
    assert generate_session_context() is None


def test_generate_session_context_returns_messages_on_drift(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    inject_instructions(tmp_path / "CLAUDE.md")
    monkeypatch.setattr(install, "_instructions_text", lambda: "DRIFTED\n")
    context = generate_session_context()
    assert context is not None
    assert "CLAUDE.md" in context


def test_generate_session_context_swallows_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def boom(_root):
        raise OSError("disk gone")

    monkeypatch.setattr(hooks, "refresh_instructions", boom)
    assert generate_session_context() is None
