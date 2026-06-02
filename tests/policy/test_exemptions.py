import tomllib

import pytest

from legis.policy.exemptions import Exemption, ExemptionRegistry, load_exemptions


def _write(tmp_path, text):
    p = tmp_path / "exemptions.toml"
    p.write_text(text)
    return p


def test_load_parses_exemptions(tmp_path):
    path = _write(tmp_path, """
[[exemption]]
policy = "import-allowlist"
value = "requests"
reason = "approved 2026-06-02, ticket-123"
""")
    reg = load_exemptions(path)
    ex = reg.is_exempt("import-allowlist", "requests")
    assert ex == Exemption("import-allowlist", "requests", "approved 2026-06-02, ticket-123")
    assert reg.is_exempt("import-allowlist", "os") is None
    assert reg.is_exempt("other-policy", "requests") is None


def test_malformed_entry_fails_closed(tmp_path):
    path = _write(tmp_path, '[[exemption]]\npolicy = "p"\nvalue = "v"\n')  # no reason
    with pytest.raises(ValueError, match="reason"):
        load_exemptions(path)


def test_malformed_toml_fails_closed(tmp_path):
    path = _write(tmp_path, "this is not = valid = toml = [[[")
    with pytest.raises(tomllib.TOMLDecodeError):
        load_exemptions(path)


def test_single_table_instead_of_array_fails_clearly(tmp_path):
    path = _write(tmp_path, '[exemption]\npolicy="p"\nvalue="v"\nreason="r"\n')
    with pytest.raises(ValueError, match="array of tables"):
        load_exemptions(path)


def test_empty_file_is_an_empty_registry(tmp_path):
    reg = load_exemptions(_write(tmp_path, ""))
    assert reg.is_exempt("import-allowlist", "requests") is None
