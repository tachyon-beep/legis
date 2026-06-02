from fastapi.testclient import TestClient

from legis.api.app import create_app
from legis.git.pull_request import PullRequestContext


class FakePullRequests:
    def __init__(self, prs):
        self._prs = prs  # {number: PullRequestContext}

    def get(self, number):
        return self._prs.get(number)


def test_pr_endpoint_returns_injected_context():
    pr = PullRequestContext(number=7, title="Add eval guard", base="main",
                            head="feature/guard", state="open")
    c = TestClient(create_app(pull_requests=FakePullRequests({7: pr})))
    resp = c.get("/git/pull-requests/7")
    assert resp.status_code == 200
    assert resp.json() == {"number": 7, "title": "Add eval guard", "base": "main",
                           "head": "feature/guard", "state": "open"}


def test_pr_endpoint_404_when_unknown():
    c = TestClient(create_app(pull_requests=FakePullRequests({})))
    assert c.get("/git/pull-requests/99").status_code == 404


def test_pr_endpoint_404_when_source_not_wired():
    c = TestClient(create_app())
    assert c.get("/git/pull-requests/7").status_code == 404
