"""Tests for legis.hooks — drift refresh and SessionStart context."""

from __future__ import annotations

import logging

from legis import hooks, install
from legis.hooks import (
    generate_session_context,
    refresh_instructions,
)
from legis.install import (
    SKILL_NAME,
    inject_instructions,
    install_codex_skills,
    install_skills,
)


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


def test_refresh_updates_on_version_bump_with_identical_content(tmp_path, monkeypatch):
    # Pins the documented "automatic versioning" contract: a package-version
    # bump re-injects even when instructions.md is byte-identical. This is the
    # only test that would catch a regression collapsing freshness to hash-only.
    inject_instructions(tmp_path / "CLAUDE.md")
    monkeypatch.setattr(install, "_instructions_version", lambda: "9.9.9")
    messages = refresh_instructions(tmp_path)
    assert any("CLAUDE.md" in m for m in messages)
    assert "v9.9.9:" in (tmp_path / "CLAUDE.md").read_text()


def test_refresh_reinstalls_drifted_codex_skill_pack(tmp_path):
    install_codex_skills(tmp_path)
    skill = tmp_path / ".agents" / "skills" / SKILL_NAME / "SKILL.md"
    source = skill.read_text()
    skill.write_text(source + "\nLOCAL EDIT\n")

    messages = refresh_instructions(tmp_path)

    assert any("Codex skill pack" in m for m in messages)
    assert skill.read_text() == source


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


def test_refresh_auto_fire_preserves_coresident_foreign_block(tmp_path):
    """SessionStart drift-refresh must not wipe a co-resident sibling block.

    This is the "deletes with no user action" path (hooks.py refresh →
    inject_instructions): a stale/unclosed legis block whose token has drifted
    triggers re-injection, and the bounded scan must spare the wardline block.
    """
    md = tmp_path / "CLAUDE.md"
    # Open marker carries a stale token (drift), but the block is NOT closed —
    # so the legacy truncate-to-EOF path would delete the wardline block below.
    md.write_text(
        "<!-- legis:instructions:vX:dead -->\n"
        "legis body, block NOT closed\n"
        "<!-- wardline:instructions:v1:abcd1234 -->\n"
        "wardline body\n"
        "<!-- /wardline:instructions -->\n"
    )
    messages = refresh_instructions(tmp_path)
    content = md.read_text()
    assert any("CLAUDE.md" in m for m in messages)  # drift was acted on
    assert "wardline body" in content
    assert "<!-- /wardline:instructions -->" in content


def test_refresh_warns_when_drift_reinjection_fails(tmp_path, monkeypatch, caplog):
    """A *detected-drift* re-injection that fails must not be dropped silently.

    ``inject_instructions`` returns ``(False, reason)`` (it does not raise) for a
    recoverable refusal such as a symlinked target, so the upstream ``except`` in
    the session-context path never sees it. If the refresh swallows the ``False``,
    agents run on drifted instructions with zero operator signal.
    """
    real = tmp_path / "real.md"
    inject_instructions(real)
    link = tmp_path / "CLAUDE.md"
    link.symlink_to(real)
    # Drift so the refresh attempts a re-injection (which then fails on the symlink).
    monkeypatch.setattr(install, "_instructions_text", lambda: "DRIFTED BODY\n")

    with caplog.at_level(logging.WARNING, logger="legis.hooks"):
        messages = refresh_instructions(tmp_path)

    assert not any("CLAUDE.md" in m for m in messages)  # no false success
    assert "CLAUDE.md" in caplog.text
    assert "symlink" in caplog.text.lower()


def test_refresh_warns_when_skill_reinstall_fails(tmp_path, monkeypatch, caplog):
    """A failed skill-pack re-install on drift must warn, not silently no-op."""
    install.install_skills(tmp_path)
    # Drift the installed pack so the refresh attempts a reinstall.
    next(
        (tmp_path / ".claude" / "skills" / install.SKILL_NAME).rglob("*.md")
    ).write_text("DRIFTED\n")
    monkeypatch.setattr(hooks, "install_skills", lambda _root: (False, "swap failed"))

    with caplog.at_level(logging.WARNING, logger="legis.hooks"):
        messages = refresh_instructions(tmp_path)

    assert not any("skill" in m.lower() for m in messages)  # no false success
    assert "swap failed" in caplog.text


def test_generate_session_context_swallows_errors(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)

    def boom(_root):
        raise OSError("disk gone")

    monkeypatch.setattr(hooks, "refresh_instructions", boom)
    with caplog.at_level(logging.WARNING, logger="legis.hooks"):
        assert generate_session_context() is None
    # Swallowing must not be silent — a regression dropping the warning would
    # hide a broken freshness check.
    assert "Instruction freshness check failed" in caplog.text
