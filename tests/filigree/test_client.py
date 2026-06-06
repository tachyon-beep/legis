import pytest

import legis.filigree.client as client_mod
from legis.filigree.client import FiligreeError, HttpFiligreeClient


def _fake_fetch(responses):
    calls = []

    def fetch(method, url, body):
        calls.append((method, url, body))
        for (m, suffix), resp in responses.items():
            if method == m and url.split("?")[0].endswith(suffix):
                return resp
        raise FiligreeError(f"no canned response for {method} {url}")

    fetch.calls = calls
    return fetch


def test_attach_posts_entity_id_and_hash():
    resp = {"issue_id": "ISSUE-1", "loomweave_entity_id": "loomweave:eid:abc",
            "content_hash_at_attach": "h", "attached_at": "t", "attached_by": "legis"}
    fetch = _fake_fetch({("POST", "/api/issue/ISSUE-1/entity-associations"): resp})
    c = HttpFiligreeClient("http://localhost", fetch=fetch)
    out = c.attach("ISSUE-1", "loomweave:eid:abc", "h", actor="legis")
    assert out == resp
    assert fetch.calls[-1] == ("POST", "http://localhost/api/issue/ISSUE-1/entity-associations",
                               {"entity_id": "loomweave:eid:abc", "content_hash": "h", "actor": "legis"})


def test_attach_posts_signoff_attestation_when_supplied():
    resp = {"attached": True}
    fetch = _fake_fetch({("POST", "/api/issue/ISSUE-1/entity-associations"): resp})
    c = HttpFiligreeClient("http://localhost", fetch=fetch)
    out = c.attach(
        "ISSUE-1",
        "loomweave:eid:abc",
        "h",
        actor="legis",
        signoff_seq=7,
        signature="hmac-sha256:v2:abc",
    )
    assert out == resp
    assert fetch.calls[-1][2] == {
        "entity_id": "loomweave:eid:abc",
        "content_hash": "h",
        "actor": "legis",
        "signoff_seq": 7,
        "signature": "hmac-sha256:v2:abc",
    }


def test_associations_for_entity_url_encodes_colons():
    fetch = _fake_fetch({("GET", "/api/entity-associations"): {"associations": []}})
    c = HttpFiligreeClient("http://localhost", fetch=fetch)
    assert c.associations_for_entity("loomweave:eid:abc") == []
    url = fetch.calls[-1][1]
    assert "entity_id=loomweave%3Aeid%3Aabc" in url   # colons percent-encoded


def test_attach_escapes_path_traversal_payload():
    resp = {"attached": True}
    # Expected URL has quoted/escaped path traversal characters
    fetch = _fake_fetch({("POST", "/api/issue/..%2F..%2Fadmin%2Fdelete/entity-associations"): resp})
    c = HttpFiligreeClient("http://localhost", fetch=fetch)
    c.attach("../../admin/delete", "loomweave:eid:abc", "h", actor="legis")
    assert fetch.calls[-1][1] == "http://localhost/api/issue/..%2F..%2Fadmin%2Fdelete/entity-associations"


def test_attach_rejects_non_object_response():
    fetch = _fake_fetch({("POST", "/api/issue/ISSUE-1/entity-associations"): []})
    with pytest.raises(FiligreeError):
        HttpFiligreeClient("http://localhost", fetch=fetch).attach(
            "ISSUE-1", "loomweave:eid:abc", "h", actor="legis"
        )


def test_associations_rejects_non_object_and_non_list_response():
    fetch = _fake_fetch({("GET", "/api/entity-associations"): []})
    with pytest.raises(FiligreeError):
        HttpFiligreeClient("http://localhost", fetch=fetch).associations_for_entity("loomweave:eid:abc")

    fetch = _fake_fetch({("GET", "/api/entity-associations"): {"associations": "bad"}})
    with pytest.raises(FiligreeError):
        HttpFiligreeClient("http://localhost", fetch=fetch).associations_for_entity("loomweave:eid:abc")


def test_client_rejects_unsafe_base_urls():
    for url in ("file:///tmp/filigree.json", "http://example.com", "not-a-url"):
        with pytest.raises(FiligreeError):
            HttpFiligreeClient(url)


# --- Q-M4: Weft-component HMAC on the Filigree transport ---

def test_sign_filigree_request_is_deterministic_and_namespaced():
    from legis.filigree.client import sign_filigree_request

    headers = sign_filigree_request(
        b"weft-key", "POST", "https://filigree/api/issue/ISSUE-1/entity-associations",
        {"entity_id": "loomweave:eid:abc", "content_hash": "h", "actor": "legis"},
        timestamp=1_700_000_000, nonce="cafef00d",
    )
    assert headers["X-Weft-Component"].startswith("filigree:")
    assert headers["X-Weft-Timestamp"] == "1700000000"
    assert headers["X-Weft-Nonce"] == "cafef00d"
    # Stable for the same inputs; sensitive to the body.
    again = sign_filigree_request(
        b"weft-key", "POST", "https://filigree/api/issue/ISSUE-1/entity-associations",
        {"entity_id": "loomweave:eid:abc", "content_hash": "h", "actor": "legis"},
        timestamp=1_700_000_000, nonce="cafef00d",
    )
    assert again == headers
    tampered = sign_filigree_request(
        b"weft-key", "POST", "https://filigree/api/issue/ISSUE-1/entity-associations",
        {"entity_id": "loomweave:eid:abc", "content_hash": "TAMPERED", "actor": "legis"},
        timestamp=1_700_000_000, nonce="cafef00d",
    )
    assert tampered["X-Weft-Component"] != headers["X-Weft-Component"]


def test_filigree_hmac_key_from_env(monkeypatch):
    from legis.filigree.client import filigree_hmac_key_from_env

    monkeypatch.delenv("LEGIS_FILIGREE_HMAC_KEY", raising=False)
    monkeypatch.delenv("LEGIS_HMAC_KEY", raising=False)
    assert filigree_hmac_key_from_env() is None
    monkeypatch.setenv("LEGIS_HMAC_KEY", "shared")
    assert filigree_hmac_key_from_env() == b"shared"
    monkeypatch.setenv("LEGIS_FILIGREE_HMAC_KEY", "channel")
    assert filigree_hmac_key_from_env() == b"channel"  # channel-specific wins


def test_real_transport_signs_when_key_present(monkeypatch):
    # The default (non-injected) transport path attaches Weft-component HMAC
    # headers when a key is configured, and none when it is not.
    import legis.filigree.client as client_mod

    captured = {}

    def capture(method, url, body, headers=None):
        captured["headers"] = headers or {}
        return {"ok": True}

    monkeypatch.setattr(client_mod, "_urllib_fetch", capture)

    signed = HttpFiligreeClient("https://filigree.example", hmac_key=b"weft-key")
    signed.attach("ISSUE-1", "loomweave:eid:abc", "h", actor="legis")
    assert captured["headers"].get("X-Weft-Component", "").startswith("filigree:")

    captured.clear()
    # With no key configured (neither injected nor in env), the transport is
    # unsigned — backward compatible.
    monkeypatch.delenv("LEGIS_FILIGREE_HMAC_KEY", raising=False)
    monkeypatch.delenv("LEGIS_HMAC_KEY", raising=False)
    unsigned = HttpFiligreeClient("https://filigree.example")
    unsigned.attach("ISSUE-1", "loomweave:eid:abc", "h", actor="legis")
    assert "X-Weft-Component" not in captured["headers"]


def test_signed_wire_body_is_byte_identical_to_signed_bytes(monkeypatch):
    # Q-M4 regression: the bytes put on the wire MUST equal the bytes the
    # X-Weft signature commits to. If _urllib_fetch re-serialised the body with
    # default json.dumps (spaces / source key order), a Filigree verifier
    # checking the body hash against the actual request bytes would reject every
    # signed POST. Drive the real transport end to end and verify the captured
    # request body verifies against the captured signature.
    import hashlib
    import hmac
    import urllib.request

    import legis.filigree.client as client_mod

    captured = {}

    class _FakeResp:
        headers = {"Content-Type": "application/json"}

        def read(self, _n):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=None):
        captured["data"] = req.data
        captured["headers"] = dict(req.header_items())
        return _FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    key = b"weft-key"
    c = HttpFiligreeClient("https://filigree.example", hmac_key=key)
    c.attach("ISSUE-1", "loomweave:eid:abc", "h", actor="legis")

    # The wire body is exactly the canonical signed bytes.
    assert captured["data"] == client_mod._json_body_bytes(
        {"entity_id": "loomweave:eid:abc", "content_hash": "h", "actor": "legis"}
    )

    # And that body verifies against the transmitted signature.
    headers = {k.lower(): v for k, v in captured["headers"].items()}
    component = headers["x-weft-component"]
    assert component.startswith("filigree:")
    signature = component.split(":", 1)[1]
    body_hash = hashlib.sha256(captured["data"]).hexdigest()
    message = (
        f"POST\n/api/issue/ISSUE-1/entity-associations\n"
        f"{body_hash}\n{headers['x-weft-timestamp']}\n{headers['x-weft-nonce']}"
    ).encode("utf-8")
    expected = hmac.new(key, message, hashlib.sha256).hexdigest()
    assert signature == expected


# --- roadmap 13: transport / error-path branches (the surface a security
# reviewer cares about, and the unsigned-transport seam tied to Q-M4) ---

def test_json_body_bytes_none_is_empty():
    # A None body signs and sends zero bytes (the body-hash is over b"").
    assert client_mod._json_body_bytes(None) == b""


def test_path_and_query_includes_query_string():
    # The signed message commits to path AND query; a verifier that dropped the
    # query would compute a different signature, so the query must be carried.
    assert (
        client_mod._path_and_query("https://filigree/api/entity-associations?entity_id=x")
        == "/api/entity-associations?entity_id=x"
    )
    # No query -> bare path; empty path -> "/".
    assert client_mod._path_and_query("https://filigree/api/x") == "/api/x"
    assert client_mod._path_and_query("https://filigree") == "/"


def test_urllib_fetch_wraps_transport_error(monkeypatch):
    # A urllib URLError (DNS failure, connection refused, timeout) surfaces as a
    # typed FiligreeError, never an unhandled urllib exception.
    import urllib.request

    def boom(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(FiligreeError, match="connection refused"):
        client_mod._urllib_fetch("GET", "https://filigree.example/api/x", None)


def test_decode_rejects_non_json_content_type():
    # A proxy/error page returning text/html must not be json-parsed; it is a
    # typed transport error.
    class _HtmlResp:
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def read(self, _n):  # pragma: no cover - not reached; type check first
            return b"<html>503</html>"

    with pytest.raises(FiligreeError, match="non-JSON content type"):
        client_mod._decode_json_response(_HtmlResp(), "GET /api/x")


def test_decode_rejects_oversized_response():
    # A response larger than MAX_RESPONSE_BYTES is rejected before decode so a
    # hostile/buggy Filigree cannot exhaust memory.
    big = b"x" * (client_mod.MAX_RESPONSE_BYTES + 1)

    class _BigResp:
        headers = {"Content-Type": "application/json"}

        def read(self, n):
            return big[:n]

    with pytest.raises(FiligreeError, match="response too large"):
        client_mod._decode_json_response(_BigResp(), "GET /api/x")
