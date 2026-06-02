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
    c = HttpFiligreeClient("http://f", fetch=fetch)
    out = c.attach("ISSUE-1", "clarion:eid:abc", "h", actor="legis")
    assert out == resp
    assert fetch.calls[-1] == ("POST", "http://f/api/issue/ISSUE-1/entity-associations",
                               {"entity_id": "clarion:eid:abc", "content_hash": "h", "actor": "legis"})


def test_associations_for_entity_url_encodes_colons():
    fetch = _fake_fetch({("GET", "/api/entity-associations"): {"associations": []}})
    c = HttpFiligreeClient("http://f", fetch=fetch)
    assert c.associations_for_entity("clarion:eid:abc") == []
    url = fetch.calls[-1][1]
    assert "entity_id=clarion%3Aeid%3Aabc" in url   # colons percent-encoded
