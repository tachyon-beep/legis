import tomllib
from pathlib import Path

import pytest

from legis.policy.cells import (
    PolicyCellRegistry,
    PolicyCellRule,
    default_policy_cells,
    load_policy_cells,
)


def test_policy_cell_registry_uses_exact_then_glob_then_default():
    registry = PolicyCellRegistry(
        default_cell="chill",
        rules=(
            PolicyCellRule(pattern="security.*", cell="protected"),
            PolicyCellRule(pattern="security.low", cell="coached"),
            PolicyCellRule(pattern="human.release", cell="structured"),
        ),
    )

    assert registry.cell_for("security.low") == "coached"
    assert registry.cell_for("security.sql-injection") == "protected"
    assert registry.cell_for("human.release") == "structured"
    assert registry.cell_for("unlisted.policy") == "chill"


def test_default_policy_cells_is_chill_for_unlisted_policies():
    registry = default_policy_cells()

    assert registry.cell_for("anything") == "chill"


def test_load_policy_cells_reads_default_exact_and_glob_rules(tmp_path):
    path = tmp_path / "cells.toml"
    path.write_text(
        """
default_cell = "chill"

[[policy]]
pattern = "import-allowlist"
cell = "coached"

[[policy]]
pattern = "protected.*"
cell = "protected"

[[policy]]
pattern = "human.*"
cell = "structured"
""",
        encoding="utf-8",
    )

    registry = load_policy_cells(path)

    assert registry.cell_for("import-allowlist") == "coached"
    assert registry.cell_for("protected.source-integrity") == "protected"
    assert registry.cell_for("human.release-signoff") == "structured"
    assert registry.cell_for("ordinary.policy") == "chill"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('[[policy]]\npattern = "x"\ncell = "chill"\n', "missing/empty default_cell"),
        ('default_cell = "invalid"\n', "unknown cell"),
        ('default_cell = "chill"\npolicy = "x"\n', "policy table must be an array"),
        ('default_cell = "chill"\n[[policy]]\ncell = "chill"\n', "missing/empty pattern"),
        ('default_cell = "chill"\n[[policy]]\npattern = "x"\ncell = "invalid"\n', "unknown cell"),
    ],
)
def test_load_policy_cells_fails_closed_on_malformed_entries(tmp_path, body, message):
    path = tmp_path / "cells.toml"
    path.write_text(body, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_policy_cells(path)


def test_load_policy_cells_propagates_toml_decode_errors(tmp_path):
    path = tmp_path / "cells.toml"
    path.write_text("default_cell = [", encoding="utf-8")

    with pytest.raises(tomllib.TOMLDecodeError):
        load_policy_cells(path)


def test_repository_default_policy_cells_file_loads():
    repo_root = Path(__file__).resolve().parents[2]
    registry = load_policy_cells(repo_root / "policy" / "cells.toml")

    assert registry.cell_for("import-allowlist") == "coached"
    assert registry.cell_for("protected.source-integrity") == "protected"
    assert registry.cell_for("human.release-signoff") == "structured"
    assert registry.cell_for("ordinary.policy") == "structured"
