from fastapi.testclient import TestClient

from legis.api.app import create_app
from legis.checks.models import CheckOutcome, CheckRun
from legis.checks.surface import CheckSurface
from legis.git.pull_request import PullRequestContext


class FakePullRequests:
    def __init__(self, prs):
        self._prs = prs  # {number: PullRequestContext}

    def get(self, number):
        return self._prs.get(number)


def _checks(tmp_path):
    # Inject a tmp-backed check surface so tests never touch the default file DB.
    return CheckSurface(f"sqlite:///{tmp_path / 'checks.db'}")


def test_pr_endpoint_returns_injected_context_with_no_checks(tmp_path):
    pr = PullRequestContext(number=7, title="Add eval guard", base="main",
                            head="feature/guard", state="open")
    c = TestClient(create_app(pull_requests=FakePullRequests({7: pr}),
                              check_surface=_checks(tmp_path)))
    resp = c.get("/git/pull-requests/7")
    assert resp.status_code == 200
    body = resp.json()
    assert body["number"] == 7 and body["title"] == "Add eval guard"
    assert body["base"] == "main" and body["head"] == "feature/guard"
    assert body["state"] == "open"
    assert body["checks"] == []  # no check runs recorded for this PR yet


def test_pr_endpoint_joins_associated_check_outcomes(tmp_path):
    # The roadmap §1.1 bullet: "PR metadata AND the check outcomes associated
    # with it." The endpoint joins CheckSurface.for_pr onto the PR context.
    pr = PullRequestContext(number=7, title="Add eval guard", base="main",
                            head="feature/guard", state="open")
    surface = _checks(tmp_path)
    surface.record(CheckRun(check_name="wardline", run_id="r1", commit_sha="a" * 40,
                            outcome=CheckOutcome.FAIL, pr=7))
    surface.record(CheckRun(check_name="lint", run_id="r2", commit_sha="a" * 40,
                            outcome=CheckOutcome.PASS, pr=7))
    surface.record(CheckRun(check_name="other", run_id="r3", commit_sha="b" * 40,
                            outcome=CheckOutcome.PASS, pr=99))  # different PR — excluded
    c = TestClient(create_app(pull_requests=FakePullRequests({7: pr}),
                              check_surface=surface))
    body = c.get("/git/pull-requests/7").json()
    assert body["number"] == 7
    got = {ck["check_name"]: ck["outcome"] for ck in body["checks"]}
    assert got == {"wardline": "fail", "lint": "pass"}  # only PR-7 checks, joined


def test_pr_endpoint_404_when_unknown(tmp_path):
    c = TestClient(create_app(pull_requests=FakePullRequests({}),
                              check_surface=_checks(tmp_path)))
    assert c.get("/git/pull-requests/99").status_code == 404


def test_pr_endpoint_404_when_source_not_wired():
    c = TestClient(create_app())
    assert c.get("/git/pull-requests/7").status_code == 404
