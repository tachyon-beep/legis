from fastapi.testclient import TestClient

from legis import __version__
from legis.api.app import create_app


def test_health_returns_ok():
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "legis"
    # Bound to the package version so it tracks bumps instead of drifting.
    assert body["version"] == __version__
