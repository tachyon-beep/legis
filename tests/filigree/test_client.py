import pytest

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
    resp = {"issue_id": "ISSUE-1", "clarion_entity_id": "clarion:eid:abc",
            "content_hash_at_attach": "h", "attached_at": "t", "attached_by": "legis"}
    fetch = _fake_fetch({("POST", "/api/issue/ISSUE-1/entity-associations"): resp})
    c = HttpFiligreeClient("http://localhost", fetch=fetch)
    out = c.attach("ISSUE-1", "clarion:eid:abc", "h", actor="legis")
    assert out == resp
    assert fetch.calls[-1] == ("POST", "http://localhost/api/issue/ISSUE-1/entity-associations",
                               {"entity_id": "clarion:eid:abc", "content_hash": "h", "actor": "legis"})


def test_attach_posts_signoff_attestation_when_supplied():
    resp = {"attached": True}
    fetch = _fake_fetch({("POST", "/api/issue/ISSUE-1/entity-associations"): resp})
    c = HttpFiligreeClient("http://localhost", fetch=fetch)
    out = c.attach(
        "ISSUE-1",
        "clarion:eid:abc",
        "h",
        actor="legis",
        signoff_seq=7,
        signature="hmac-sha256:v2:abc",
    )
    assert out == resp
    assert fetch.calls[-1][2] == {
        "entity_id": "clarion:eid:abc",
        "content_hash": "h",
        "actor": "legis",
        "signoff_seq": 7,
        "signature": "hmac-sha256:v2:abc",
    }


def test_associations_for_entity_url_encodes_colons():
    fetch = _fake_fetch({("GET", "/api/entity-associations"): {"associations": []}})
    c = HttpFiligreeClient("http://localhost", fetch=fetch)
    assert c.associations_for_entity("clarion:eid:abc") == []
    url = fetch.calls[-1][1]
    assert "entity_id=clarion%3Aeid%3Aabc" in url   # colons percent-encoded


def test_attach_escapes_path_traversal_payload():
    resp = {"attached": True}
    # Expected URL has quoted/escaped path traversal characters
    fetch = _fake_fetch({("POST", "/api/issue/..%2F..%2Fadmin%2Fdelete/entity-associations"): resp})
    c = HttpFiligreeClient("http://localhost", fetch=fetch)
    c.attach("../../admin/delete", "clarion:eid:abc", "h", actor="legis")
    assert fetch.calls[-1][1] == "http://localhost/api/issue/..%2F..%2Fadmin%2Fdelete/entity-associations"


def test_attach_rejects_non_object_response():
    fetch = _fake_fetch({("POST", "/api/issue/ISSUE-1/entity-associations"): []})
    with pytest.raises(FiligreeError):
        HttpFiligreeClient("http://localhost", fetch=fetch).attach(
            "ISSUE-1", "clarion:eid:abc", "h", actor="legis"
        )


def test_associations_rejects_non_object_and_non_list_response():
    fetch = _fake_fetch({("GET", "/api/entity-associations"): []})
    with pytest.raises(FiligreeError):
        HttpFiligreeClient("http://localhost", fetch=fetch).associations_for_entity("clarion:eid:abc")

    fetch = _fake_fetch({("GET", "/api/entity-associations"): {"associations": "bad"}})
    with pytest.raises(FiligreeError):
        HttpFiligreeClient("http://localhost", fetch=fetch).associations_for_entity("clarion:eid:abc")


def test_client_rejects_unsafe_base_urls():
    for url in ("file:///tmp/filigree.json", "http://example.com", "not-a-url"):
        with pytest.raises(FiligreeError):
            HttpFiligreeClient(url)
