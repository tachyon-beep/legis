from legis.pulls.models import PullRequest, PullRequestState
from legis.pulls.surface import PullSurface


def test_record_then_get_round_trips(tmp_path):
    s = PullSurface(f"sqlite:///{tmp_path / 'pulls.db'}")
    s.record(PullRequest(
        number=7,
        title="Add X",
        base="main",
        head="feature",
        state=PullRequestState.OPEN,
        url="https://forge/pr/7",
    ))
    pr = s.get(7)
    assert pr is not None
    assert pr.title == "Add X"
    assert pr.base == "main"
    assert pr.state is PullRequestState.OPEN


def test_get_unknown_pr_is_none(tmp_path):
    assert PullSurface(f"sqlite:///{tmp_path / 'pulls.db'}").get(999) is None


def test_record_upserts_pr_state(tmp_path):
    s = PullSurface(f"sqlite:///{tmp_path / 'pulls.db'}")
    s.record(PullRequest(7, "Add X", "main", "feature", PullRequestState.OPEN))
    s.record(PullRequest(7, "Add X", "main", "feature", PullRequestState.MERGED))
    assert s.get(7).state is PullRequestState.MERGED
