from legis.canonical import canonical_json, content_hash


def test_canonical_json_is_key_order_independent():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_content_hash_is_stable_and_hex():
    h1 = content_hash({"a": 1, "b": [1, 2, 3]})
    h2 = content_hash({"b": [1, 2, 3], "a": 1})
    assert h1 == h2
    assert len(h1) == 64
    assert all(c in "0123456789abcdef" for c in h1)
