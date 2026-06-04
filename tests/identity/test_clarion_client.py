import hashlib
import hmac

import pytest

from legis.identity.clarion_client import (
    ClarionError,
    HttpClarionIdentity,
    clarion_hmac_key_from_env,
    sign_clarion_request,
    _urllib_fetch,
)


def _fake_fetch(responses):
    calls = []

    def fetch(method, url, body, headers):
        calls.append((method, url, body, dict(headers)))
        for (m, suffix), resp in responses.items():
            if method == m and url.endswith(suffix):
                return resp
        raise ClarionError(f"no canned response for {method} {url}")

    fetch.calls = calls
    return fetch


def test_capability_true_when_sei_supported():
    fetch = _fake_fetch({("GET", "/api/v1/_capabilities"): {"sei": {"supported": True, "version": 1}}})
    assert HttpClarionIdentity("http://localhost", fetch=fetch).capability() is True


def test_capability_false_when_absent_or_unsupported():
    fetch = _fake_fetch({("GET", "/api/v1/_capabilities"): {"registry_backend": True}})
    assert HttpClarionIdentity("http://localhost", fetch=fetch).capability() is False


def test_resolve_locator_alive_passthrough():
    body = {"sei": "clarion:eid:abc", "current_locator": "python:function:m.f", "content_hash": "h", "alive": True}
    fetch = _fake_fetch({("POST", "/api/v1/identity/resolve"): body})
    c = HttpClarionIdentity("http://localhost", fetch=fetch)
    assert c.resolve_locator("python:function:m.f") == body
    assert fetch.calls[-1] == (
        "POST",
        "http://localhost/api/v1/identity/resolve",
        {"locator": "python:function:m.f"},
        {},
    )


def test_resolve_batch_posts_locators_to_clarion_batch_endpoint():
    body = {
        "resolved": {
            "python:function:m.f": {
                "sei": "clarion:eid:abc",
                "current_locator": "python:function:m.f",
                "content_hash": "h",
                "alive": True,
            }
        },
        "invalid": ["malformed"],
        "not_found": ["python:function:gone"],
    }
    fetch = _fake_fetch({("POST", "/api/v1/identity/resolve:batch"): body})
    c = HttpClarionIdentity("http://localhost", fetch=fetch)

    assert c.resolve_batch(["python:function:m.f", "python:function:gone"]) == body
    assert fetch.calls[-1] == (
        "POST",
        "http://localhost/api/v1/identity/resolve:batch",
        {"locators": ["python:function:m.f", "python:function:gone"]},
        {},
    )


def test_resolve_sei_orphaned_carries_lineage():
    body = {"sei": "clarion:eid:abc", "alive": False, "lineage": [{"event": "orphaned"}]}
    fetch = _fake_fetch({("GET", "/api/v1/identity/sei/clarion%3Aeid%3Aabc"): body})
    assert HttpClarionIdentity("http://localhost", fetch=fetch).resolve_sei("clarion:eid:abc") == body


def test_lineage_returns_event_list():
    body = {"sei": "clarion:eid:abc", "lineage": [{"event": "born"}, {"event": "locator_changed"}]}
    fetch = _fake_fetch({("GET", "/api/v1/identity/lineage/clarion%3Aeid%3Aabc"): body})
    assert HttpClarionIdentity("http://localhost", fetch=fetch).lineage("clarion:eid:abc") == body["lineage"]


def test_resolve_sei_escapes_path_traversal_payload():
    body = {"alive": False}
    # Expected URL has quoted/escaped path traversal characters
    fetch = _fake_fetch({("GET", "/api/v1/identity/sei/..%2F..%2Fadmin%2Fdelete"): body})
    c = HttpClarionIdentity("http://localhost", fetch=fetch)
    c.resolve_sei("../../admin/delete")
    assert fetch.calls[-1][1] == "http://localhost/api/v1/identity/sei/..%2F..%2Fadmin%2Fdelete"


def test_sign_clarion_request_matches_clarion_hmac_contract():
    body = {"locator": "python:function:m.f"}
    body_bytes = b'{"locator":"python:function:m.f"}'
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    message = (
        "POST\n/api/v1/identity/resolve\n"
        f"{body_hash}\n1900000000\nnonce-1"
    ).encode("utf-8")
    expected = hmac.new(b"s3cr3t", message, hashlib.sha256).hexdigest()

    headers = sign_clarion_request(
        b"s3cr3t",
        "POST",
        "http://localhost/api/v1/identity/resolve",
        body,
        timestamp=1_900_000_000,
        nonce="nonce-1",
    )

    assert headers == {
        "X-Loom-Component": f"clarion:{expected}",
        "X-Loom-Timestamp": "1900000000",
        "X-Loom-Nonce": "nonce-1",
    }


def test_resolve_locator_sends_loom_hmac_headers_when_key_is_provisioned():
    body = {"sei": "clarion:eid:abc", "current_locator": "python:function:m.f", "content_hash": "h", "alive": True}
    fetch = _fake_fetch({("POST", "/api/v1/identity/resolve"): body})
    c = HttpClarionIdentity(
        "http://localhost",
        fetch=fetch,
        hmac_key="s3cr3t",
        clock=lambda: 1_900_000_000,
        nonce_factory=lambda: "nonce-1",
    )

    assert c.resolve_locator("python:function:m.f") == body

    headers = fetch.calls[-1][3]
    expected = sign_clarion_request(
        b"s3cr3t",
        "POST",
        "http://localhost/api/v1/identity/resolve",
        {"locator": "python:function:m.f"},
        timestamp=1_900_000_000,
        nonce="nonce-1",
    )
    assert headers == expected


def test_clarion_hmac_key_from_env_prefers_clarion_specific_key(monkeypatch):
    monkeypatch.setenv("LEGIS_HMAC_KEY", "general-secret")
    monkeypatch.setenv("LEGIS_CLARION_HMAC_KEY", "clarion-secret")

    assert clarion_hmac_key_from_env() == b"clarion-secret"


def test_resolve_locator_rejects_non_object_response():
    fetch = _fake_fetch({("POST", "/api/v1/identity/resolve"): []})
    with pytest.raises(ClarionError):
        HttpClarionIdentity("http://localhost", fetch=fetch).resolve_locator("python:function:m.f")


def test_lineage_rejects_non_object_and_non_list_lineage():
    fetch = _fake_fetch({("GET", "/api/v1/identity/lineage/clarion%3Aeid%3Aabc"): []})
    with pytest.raises(ClarionError):
        HttpClarionIdentity("http://localhost", fetch=fetch).lineage("clarion:eid:abc")

    fetch = _fake_fetch({("GET", "/api/v1/identity/lineage/clarion%3Aeid%3Aabc"): {"lineage": "bad"}})
    with pytest.raises(ClarionError):
        HttpClarionIdentity("http://localhost", fetch=fetch).lineage("clarion:eid:abc")


def test_client_rejects_unsafe_base_urls():
    for url in ("file:///tmp/clarion.json", "http://example.com", "not-a-url"):
        with pytest.raises(ClarionError):
            HttpClarionIdentity(url)


def test_urllib_fetch_rejects_oversized_responses(monkeypatch):
    class Response:
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size=-1):
            return b"{" + (b" " * 1_100_000)

    def fake_urlopen(req, timeout):
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(ClarionError, match="too large"):
        _urllib_fetch("GET", "http://localhost/api/v1/_capabilities", None)
