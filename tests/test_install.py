"""Tests for legis.install — instruction injection, skills, hooks, gitignore."""

from __future__ import annotations

import json
import os
import stat

import pytest

from legis import install
from legis.install import (
    INSTRUCTIONS_MARKER,
    SKILL_NAME,
    UnsafeInstallPathError,
    _build_instructions_block,
    _instructions_hash,
    _instructions_text,
    _instructions_version,
    _marker_token,
    _skill_tree_fingerprint,
    ensure_gitignore,
    inject_instructions,
    install_claude_code_hooks,
    install_codex_skills,
    install_skills,
    reject_symlink,
)


# ---------------------------------------------------------------------------
# Instructions block primitives
# ---------------------------------------------------------------------------


def test_instructions_text_is_nonempty_and_marker_free():
    text = _instructions_text()
    assert text.strip()
    # The body must not contain markers; they are added programmatically.
    assert INSTRUCTIONS_MARKER not in text
    assert "/legis:instructions" not in text


def test_instructions_hash_is_stable_8_hex():
    h = _instructions_hash()
    assert len(h) == 8
    assert all(c in "0123456789abcdef" for c in h)
    assert h == _instructions_hash()


def test_instructions_version_prefers_dist_metadata():
    import importlib.metadata

    # Prefers installed distribution metadata; falls back to legis.__version__.
    # (In a dev venv the editable dist metadata can lag the source __version__;
    # in a real release they agree. Assert the documented preference, not a
    # hardcoded string.)
    try:
        expected = importlib.metadata.version("legis")
    except importlib.metadata.PackageNotFoundError:
        from legis import __version__

        expected = __version__
    assert _instructions_version() == expected
    assert _instructions_version()  # non-empty


def test_instructions_version_falls_back_to_dunder(monkeypatch):
    import importlib.metadata

    def _raise(_name):
        raise importlib.metadata.PackageNotFoundError("legis")

    monkeypatch.setattr(install.importlib.metadata, "version", _raise)
    from legis import __version__

    assert _instructions_version() == __version__


def test_build_block_has_open_and_close_markers():
    block = _build_instructions_block()
    assert block.startswith(f"{INSTRUCTIONS_MARKER}:{_marker_token()} -->")
    assert block.rstrip().endswith("<!-- /legis:instructions -->")
    assert _instructions_text() in block


# ---------------------------------------------------------------------------
# inject_instructions
# ---------------------------------------------------------------------------


def test_inject_creates_missing_file(tmp_path):
    target = tmp_path / "CLAUDE.md"
    ok, msg = inject_instructions(target)
    assert ok
    assert "Created" in msg
    content = target.read_text()
    assert INSTRUCTIONS_MARKER in content
    assert "<!-- /legis:instructions -->" in content


def test_inject_appends_to_existing_file_without_marker(tmp_path):
    target = tmp_path / "AGENTS.md"
    target.write_text("# My project\n\nExisting guidance.\n")
    ok, msg = inject_instructions(target)
    assert ok
    assert "Appended" in msg
    content = target.read_text()
    assert "Existing guidance." in content
    assert content.index("Existing guidance.") < content.index(INSTRUCTIONS_MARKER)


def test_inject_replaces_existing_block_preserving_surrounding_text(tmp_path, monkeypatch):
    target = tmp_path / "CLAUDE.md"
    target.write_text("TOP\n\n")
    inject_instructions(target)
    # Append trailing user content after the block.
    target.write_text(target.read_text() + "\nBOTTOM\n")

    monkeypatch.setattr(install, "_instructions_text", lambda: "NEW BODY CONTENT\n")
    ok, msg = inject_instructions(target)
    assert ok
    assert "Updated" in msg
    content = target.read_text()
    assert "TOP" in content
    assert "BOTTOM" in content
    assert "NEW BODY CONTENT" in content
    # Exactly one block remains.
    assert content.count(INSTRUCTIONS_MARKER) == 1
    assert content.count("<!-- /legis:instructions -->") == 1


def test_inject_idempotent_when_content_unchanged(tmp_path):
    target = tmp_path / "CLAUDE.md"
    inject_instructions(target)
    first = target.read_text()
    inject_instructions(target)
    assert target.read_text() == first


def test_inject_repairs_block_with_missing_end_marker(tmp_path):
    target = tmp_path / "CLAUDE.md"
    # Open marker but no close marker, plus trailing junk.
    target.write_text(f"HEAD\n{INSTRUCTIONS_MARKER}:vX:dead -->\norphan body no close\n")
    ok, msg = inject_instructions(target)
    assert ok
    content = target.read_text()
    assert "HEAD" in content
    assert "orphan body no close" not in content
    assert content.count(INSTRUCTIONS_MARKER) == 1
    assert "<!-- /legis:instructions -->" in content


def test_inject_rejects_symlink_target(tmp_path):
    real = tmp_path / "real.md"
    real.write_text("x")
    link = tmp_path / "CLAUDE.md"
    link.symlink_to(real)
    ok, msg = inject_instructions(link)
    assert ok is False
    assert "symlink" in msg.lower()


# ---------------------------------------------------------------------------
# _atomic_write_text
# ---------------------------------------------------------------------------


def test_atomic_write_preserves_existing_mode(tmp_path):
    target = tmp_path / "CLAUDE.md"
    target.write_text("seed")
    os.chmod(target, 0o640)
    inject_instructions(target)
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o640


def test_reject_symlink_raises_on_symlink(tmp_path):
    real = tmp_path / "r"
    real.write_text("x")
    link = tmp_path / "l"
    link.symlink_to(real)
    with pytest.raises(UnsafeInstallPathError):
        reject_symlink(link)


# ---------------------------------------------------------------------------
# Skill pack
# ---------------------------------------------------------------------------


def test_install_skills_copies_pack(tmp_path):
    ok, msg = install_skills(tmp_path)
    assert ok
    skill = tmp_path / ".claude" / "skills" / SKILL_NAME / "SKILL.md"
    assert skill.is_file()
    assert "legis-workflow" in skill.read_text()


def test_install_codex_skills_targets_agents_dir(tmp_path):
    ok, _ = install_codex_skills(tmp_path)
    assert ok
    assert (tmp_path / ".agents" / "skills" / SKILL_NAME / "SKILL.md").is_file()


def test_install_skills_idempotent(tmp_path):
    install_skills(tmp_path)
    skill = tmp_path / ".claude" / "skills" / SKILL_NAME / "SKILL.md"
    first = skill.read_text()
    ok, _ = install_skills(tmp_path)
    assert ok
    assert skill.read_text() == first


def test_skill_tree_fingerprint_changes_with_content(tmp_path):
    root = tmp_path / "pack"
    root.mkdir()
    (root / "a.md").write_text("one")
    fp1 = _skill_tree_fingerprint(root)
    (root / "a.md").write_text("two")
    fp2 = _skill_tree_fingerprint(root)
    assert fp1 != fp2


# ---------------------------------------------------------------------------
# Hook registration
# ---------------------------------------------------------------------------


def _session_commands(settings: dict) -> list[str]:
    cmds: list[str] = []
    for block in settings.get("hooks", {}).get("SessionStart", []):
        for hook in block.get("hooks", []):
            cmds.append(hook.get("command", ""))
    return cmds


def test_install_hooks_fresh(tmp_path):
    ok, msg = install_claude_code_hooks(tmp_path)
    assert ok
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    cmds = _session_commands(settings)
    assert any(c.endswith("session-context") for c in cmds)


def test_install_hooks_idempotent_no_duplicate(tmp_path):
    install_claude_code_hooks(tmp_path)
    install_claude_code_hooks(tmp_path)
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    cmds = [c for c in _session_commands(settings) if c.endswith("session-context")]
    assert len(cmds) == 1


def test_install_hooks_upgrades_bare_command(tmp_path, monkeypatch):
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text(
        json.dumps(
            {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "legis session-context"}]}]}}
        )
    )
    # Force a resolved binary path so the bare command must be upgraded.
    monkeypatch.setattr(install, "_find_legis_command", lambda: ["/opt/bin/legis"])
    ok, msg = install_claude_code_hooks(tmp_path)
    assert ok
    settings = json.loads((claude / "settings.json").read_text())
    cmds = _session_commands(settings)
    assert "/opt/bin/legis session-context" in cmds
    assert cmds.count("/opt/bin/legis session-context") == 1


def test_install_hooks_backs_up_malformed_settings(tmp_path):
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text("{ this is not json")
    ok, _ = install_claude_code_hooks(tmp_path)
    assert ok
    assert (claude / "settings.json.bak").is_file()
    settings = json.loads((claude / "settings.json").read_text())
    assert any(c.endswith("session-context") for c in _session_commands(settings))


def test_install_hooks_does_not_reuse_scoped_block(tmp_path):
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {"matcher": "resume", "hooks": [{"type": "command", "command": "legis session-context"}]}
                    ]
                }
            }
        )
    )
    install_claude_code_hooks(tmp_path)
    settings = json.loads((claude / "settings.json").read_text())
    # A new unscoped block must be added — the scoped one does not cover cold start.
    blocks = settings["hooks"]["SessionStart"]
    unscoped = [b for b in blocks if "matcher" not in b or b.get("matcher") in (None, "*")]
    assert unscoped
    assert any(h["command"].endswith("session-context") for b in unscoped for h in b["hooks"])


# ---------------------------------------------------------------------------
# _hook_cmd_matches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command,expected",
    [
        ("legis session-context", True),
        ("/usr/local/bin/legis session-context", True),
        ("/path/python -P -m legis session-context", True),
        ("/path/python -m legis session-context", True),
        ("echo legis session-context", False),
        ("legis serve", False),
    ],
)
def test_hook_cmd_matches(command, expected):
    assert install._hook_cmd_matches(command, "legis session-context") is expected


# ---------------------------------------------------------------------------
# .gitignore
# ---------------------------------------------------------------------------


def test_ensure_gitignore_creates_file(tmp_path):
    ok, msg = ensure_gitignore(tmp_path)
    assert ok
    content = (tmp_path / ".gitignore").read_text()
    assert ".legis/" in content
    assert "legis.yaml" in content


def test_ensure_gitignore_appends_missing_rules(tmp_path):
    (tmp_path / ".gitignore").write_text("*.db\n")
    ok, msg = ensure_gitignore(tmp_path)
    assert ok
    content = (tmp_path / ".gitignore").read_text()
    assert "*.db" in content
    assert ".legis/" in content
    assert "legis.yaml" in content


def test_ensure_gitignore_idempotent(tmp_path):
    ensure_gitignore(tmp_path)
    first = (tmp_path / ".gitignore").read_text()
    ok, msg = ensure_gitignore(tmp_path)
    assert ok
    assert "already" in msg
    assert (tmp_path / ".gitignore").read_text() == first


# ---------------------------------------------------------------------------
# Command resolution and safe-path edges
# ---------------------------------------------------------------------------


def test_find_legis_command_prefers_binary_on_path(monkeypatch):
    monkeypatch.setattr(install.shutil, "which", lambda _name: "/opt/bin/legis")
    assert install._find_legis_command() == ["/opt/bin/legis"]


def test_find_legis_command_module_fallback(monkeypatch):
    monkeypatch.setattr(install.shutil, "which", lambda _name: None)
    cmd = install._find_legis_command()
    assert cmd[-3:] == ["-P", "-m", "legis"]


def test_project_path_rejects_symlinked_component(tmp_path):
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    link_dir = tmp_path / ".claude"
    link_dir.symlink_to(real_dir, target_is_directory=True)
    with pytest.raises(UnsafeInstallPathError):
        install.project_path(tmp_path, ".claude", "settings.json")


def test_ensure_project_dir_creates_and_returns_dir(tmp_path):
    created = install.ensure_project_dir(tmp_path, ".claude", "skills")
    assert created.is_dir()
    assert created == tmp_path / ".claude" / "skills"


def test_install_skills_reports_missing_source(tmp_path, monkeypatch):
    empty = tmp_path / "no_skills_here"
    empty.mkdir()
    monkeypatch.setattr(install, "_get_skills_source_dir", lambda: empty)
    ok, msg = install_skills(tmp_path)
    assert ok is False
    assert "not found" in msg


def test_upgrade_hook_commands_tolerates_non_dict_settings():
    assert install._upgrade_hook_commands({"hooks": []}, "legis session-context", "x") is False
    assert install._upgrade_hook_commands({}, "legis session-context", "x") is False


def test_has_unscoped_session_start_hook_tolerates_non_dict():
    assert install._has_unscoped_session_start_hook({"hooks": "nope"}, "legis session-context") is False
    assert install._has_unscoped_session_start_hook({}, "legis session-context") is False
