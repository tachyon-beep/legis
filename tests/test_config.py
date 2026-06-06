"""Store-location resolver: the federated ``.weft/legis`` subtree.

These pin the contract from the weft config/store consolidation:
  * machine-written DBs default under ``.weft/legis/`` (cwd-anchored, the same
    notion the installer uses for project root);
  * the operator-authored ``weft.toml`` ``[legis]`` table may relocate the
    subtree but is enrich-only — absent, section-less, or malformed weft.toml
    must still boot on built-in defaults (never load-bearing);
  * computing a URL is pure (creates nothing); the directory materialises only
    when a DB is actually opened, via ``ensure_sqlite_parent``.
"""

from __future__ import annotations

from legis import config


def test_all_four_db_urls_default_under_weft_legis(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert config.check_db_url() == "sqlite:///.weft/legis/legis-checks.db"
    assert config.governance_db_url() == "sqlite:///.weft/legis/legis-governance.db"
    assert config.binding_db_url() == "sqlite:///.weft/legis/legis-binding.db"
    assert config.pull_db_url() == "sqlite:///.weft/legis/legis-pulls.db"


def test_db_urls_use_builtin_defaults_with_no_weft_toml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert not (tmp_path / "weft.toml").exists()
    assert config.governance_db_url() == "sqlite:///.weft/legis/legis-governance.db"


def test_weft_toml_store_dir_relocates_the_subtree(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "weft.toml").write_text(
        '[legis]\nstore_dir = "var/legis-state"\n', encoding="utf-8"
    )
    assert config.governance_db_url() == "sqlite:///var/legis-state/legis-governance.db"
    assert config.check_db_url() == "sqlite:///var/legis-state/legis-checks.db"


def test_weft_toml_absolute_store_dir_yields_absolute_url(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    abs_dir = tmp_path / "srv" / "legis"
    (tmp_path / "weft.toml").write_text(
        f'[legis]\nstore_dir = "{abs_dir.as_posix()}"\n', encoding="utf-8"
    )
    assert config.governance_db_url() == f"sqlite:///{abs_dir.as_posix()}/legis-governance.db"


def test_weft_toml_without_legis_section_uses_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "weft.toml").write_text('[filigree]\ndb = "x"\n', encoding="utf-8")
    assert config.governance_db_url() == "sqlite:///.weft/legis/legis-governance.db"


def test_malformed_weft_toml_is_not_load_bearing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "weft.toml").write_text("this is = = not valid toml [[[", encoding="utf-8")
    assert config.governance_db_url() == "sqlite:///.weft/legis/legis-governance.db"


def test_computing_db_url_creates_no_directories(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _ = config.governance_db_url()
    _ = config.check_db_url()
    _ = config.binding_db_url()
    _ = config.pull_db_url()
    assert not (tmp_path / ".weft").exists()


def test_ensure_sqlite_parent_creates_dir_for_relative_file_url(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config.ensure_sqlite_parent("sqlite:///.weft/legis/legis-checks.db")
    assert (tmp_path / ".weft" / "legis").is_dir()


def test_ensure_sqlite_parent_creates_dir_for_absolute_file_url(tmp_path):
    target = tmp_path / "a" / "b" / "x.db"
    config.ensure_sqlite_parent(f"sqlite:///{target.as_posix()}")
    assert (tmp_path / "a" / "b").is_dir()


def test_ensure_sqlite_parent_is_noop_for_in_memory_and_non_sqlite(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config.ensure_sqlite_parent("sqlite://")
    config.ensure_sqlite_parent("sqlite:///:memory:")
    config.ensure_sqlite_parent("postgresql://localhost/x")
    assert list(tmp_path.iterdir()) == []


def test_ensure_sqlite_parent_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config.ensure_sqlite_parent("sqlite:///.weft/legis/legis-checks.db")
    config.ensure_sqlite_parent("sqlite:///.weft/legis/legis-checks.db")
    assert (tmp_path / ".weft" / "legis").is_dir()


def test_suite_isolates_store_locations_to_tmp():
    """Regression guard for legis-3d295a6f7f: the autouse conftest fixture must
    redirect every store env var off the repo-relative `.weft/legis/` default,
    so a test that builds a default-path store can't leak a subtree into the
    working tree."""
    import os

    for var in (
        "LEGIS_CHECK_DB",
        "LEGIS_GOVERNANCE_DB",
        "LEGIS_BINDING_DB",
        "LEGIS_PULL_DB",
    ):
        val = os.environ.get(var, "")
        assert val.startswith("sqlite:"), f"{var} not redirected: {val!r}"
        assert "legis-store" in val, f"{var} not pointed at the isolated tmp dir: {val!r}"
