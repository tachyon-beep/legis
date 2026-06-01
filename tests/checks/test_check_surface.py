from legis.checks.models import CheckOutcome, CheckRun
from legis.checks.surface import CheckSurface


def make_run(**over):
    base = dict(
        check_name="wardline",
        run_id="run-1",
        commit_sha="a" * 40,
        outcome=CheckOutcome.PASS,
        branch="main",
        pr=None,
        ran_against="tree:deadbeef",
        rule_set="wardline@1",
        policy_version="p1",
        started_at="2026-06-01T00:00:00+00:00",
        finished_at="2026-06-01T00:01:00+00:00",
    )
    base.update(over)
    return CheckRun(**base)


def surface(tmp_path):
    return CheckSurface(f"sqlite:///{tmp_path / 'checks.db'}")


def test_record_then_for_commit_round_trips(tmp_path):
    s = surface(tmp_path)
    s.record(make_run())
    runs = s.for_commit("a" * 40)
    assert len(runs) == 1
    r = runs[0]
    assert r.check_name == "wardline"
    assert r.outcome is CheckOutcome.PASS
    assert r.ran_against == "tree:deadbeef"


def test_for_branch_and_for_pr_filter(tmp_path):
    s = surface(tmp_path)
    s.record(make_run(run_id="r1", branch="main", pr=None))
    s.record(make_run(run_id="r2", branch="feature", pr=7, commit_sha="b" * 40))
    assert {r.run_id for r in s.for_branch("feature")} == {"r2"}
    assert {r.run_id for r in s.for_pr(7)} == {"r2"}
    assert {r.run_id for r in s.for_branch("main")} == {"r1"}


def test_latest_state_returns_newest_run_per_check(tmp_path):
    s = surface(tmp_path)
    sha = "c" * 40
    s.record(make_run(run_id="old", commit_sha=sha, check_name="wardline",
                      outcome=CheckOutcome.FAIL))
    s.record(make_run(run_id="new", commit_sha=sha, check_name="wardline",
                      outcome=CheckOutcome.PASS))
    s.record(make_run(run_id="lint", commit_sha=sha, check_name="lint",
                      outcome=CheckOutcome.SKIPPED))
    state = s.latest_state(sha)
    assert set(state) == {"wardline", "lint"}
    assert state["wardline"].run_id == "new"
    assert state["wardline"].outcome is CheckOutcome.PASS
    assert state["lint"].outcome is CheckOutcome.SKIPPED


def test_all_outcomes_round_trip(tmp_path):
    s = surface(tmp_path)
    for i, oc in enumerate(CheckOutcome):
        s.record(make_run(run_id=f"r{i}", commit_sha="d" * 40, check_name=f"c{i}",
                          outcome=oc))
    got = {r.check_name: r.outcome for r in s.for_commit("d" * 40)}
    assert set(got.values()) == set(CheckOutcome)
