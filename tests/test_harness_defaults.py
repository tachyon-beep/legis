import os


def test_unsafe_flags_are_not_autouse_defaults():
    assert os.environ.get("LEGIS_UNSAFE_DEV_AUTH") is None
    assert os.environ.get("LEGIS_UNSAFE_WARDLINE_REQUEST_ROUTING") is None
