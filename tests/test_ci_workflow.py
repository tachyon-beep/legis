from pathlib import Path

import yaml


def _ci_steps():
    workflow = yaml.safe_load(Path(".github/workflows/ci.yml").read_text())
    return workflow["jobs"]["test"]["steps"]


def test_ci_enforces_coverage_threshold():
    commands = "\n".join(str(step.get("run", "")) for step in _ci_steps())

    assert "--cov=legis" in commands
    assert "--cov-fail-under=" in commands


def test_ci_runs_sei_and_live_loomweave_conformance_targets():
    commands = "\n".join(str(step.get("run", "")) for step in _ci_steps())

    assert "tests/conformance/test_sei_oracle.py" in commands
    assert "tests/conformance/test_live_loomweave_oracle.py" in commands
