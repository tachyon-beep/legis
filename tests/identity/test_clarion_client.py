from legis.identity.clarion_client import ClarionError, HttpClarionIdentity


def _fake_fetch(responses):
    calls = []

    def fetch(method, url, body):
        calls.append((method, url, body))
        for (m, suffix), resp in responses.items():
            if method == m and url.endswith(suffix):
                return resp
        raise ClarionError(f"no canned response for {method} {url}")

    fetch.calls = calls
    return fetch


def test_capability_true_when_sei_supported():
    fetch = _fake_fetch({("GET", "/api/v1/_capabilities"): {"sei": {"supported": True, "version": 1}}})
    assert HttpClarionIdentity("http://c", fetch=fetch).capability() is True


def test_capability_false_when_absent_or_unsupported():
    fetch = _fake_fetch({("GET", "/api/v1/_capabilities"): {"registry_backend": True}})
    assert HttpClarionIdentity("http://c", fetch=fetch).capability() is False


def test_resolve_locator_alive_passthrough():
    body = {"sei": "clarion:eid:abc", "current_locator": "python:function:m.f", "content_hash": "h", "alive": True}
    fetch = _fake_fetch({("POST", "/api/v1/identity/resolve"): body})
    c = HttpClarionIdentity("http://c", fetch=fetch)
    assert c.resolve_locator("python:function:m.f") == body
    assert fetch.calls[-1] == ("POST", "http://c/api/v1/identity/resolve", {"locator": "python:function:m.f"})


def test_resolve_sei_orphaned_carries_lineage():
    body = {"sei": "clarion:eid:abc", "alive": False, "lineage": [{"event": "orphaned"}]}
    fetch = _fake_fetch({("GET", "/api/v1/identity/sei/clarion%3Aeid%3Aabc"): body})
    assert HttpClarionIdentity("http://c", fetch=fetch).resolve_sei("clarion:eid:abc") == body


def test_lineage_returns_event_list():
    body = {"sei": "clarion:eid:abc", "lineage": [{"event": "born"}, {"event": "locator_changed"}]}
    fetch = _fake_fetch({("GET", "/api/v1/identity/lineage/clarion%3Aeid%3Aabc"): body})
    assert HttpClarionIdentity("http://c", fetch=fetch).lineage("clarion:eid:abc") == body["lineage"]


def test_resolve_sei_escapes_path_traversal_payload():
    body = {"alive": False}
    # Expected URL has quoted/escaped path traversal characters
    fetch = _fake_fetch({("GET", "/api/v1/identity/sei/..%2F..%2Fadmin%2Fdelete"): body})
    c = HttpClarionIdentity("http://c", fetch=fetch)
    c.resolve_sei("../../admin/delete")
    assert fetch.calls[-1][1] == "http://c/api/v1/identity/sei/..%2F..%2Fadmin%2Fdelete"
