import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from legis.enforcement.llm_client import (
    LLMClientConfig,
    LLMTransportError,
    OpenRouterLLMClient,
    _urllib_fetch,
    llm_client_config_from_env,
)


def _clear_judge_env(monkeypatch):
    for name in (
        "LEGIS_JUDGE_PROVIDER",
        "OPENROUTER_API_KEY",
        "LEGIS_JUDGE_MODEL",
        "LEGIS_JUDGE_MAX_TOKENS",
        "LEGIS_JUDGE_BASE_URL",
        "LEGIS_ALLOW_INSECURE_REMOTE_HTTP",
    ):
        monkeypatch.delenv(name, raising=False)


def test_openrouter_client_sends_chat_completion_and_returns_text():
    calls = []

    def fetch(method, url, body, headers):
        calls.append((method, url, body, headers))
        return {
            "choices": [
                {"message": {"content": "ACCEPTED\nspecific enough"}}
            ]
        }

    client = OpenRouterLLMClient(
        LLMClientConfig(
            provider="openrouter",
            api_key="secret-key",
            model_id="anthropic/claude-opus-4-7",
            max_tokens=321,
            base_url="https://openrouter.ai/api/v1",
        ),
        fetch=fetch,
    )

    assert client.model_id == "openrouter:anthropic/claude-opus-4-7"
    assert client.complete("judge this") == "ACCEPTED\nspecific enough"
    method, url, body, headers = calls[0]
    assert method == "POST"
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert headers["Authorization"] == "Bearer secret-key"
    assert body == {
        "model": "anthropic/claude-opus-4-7",
        "max_tokens": 321,
        "messages": [{"role": "user", "content": "judge this"}],
    }


def test_openrouter_client_rejects_malformed_response():
    def fetch(method, url, body, headers):
        return {"choices": [{"message": {"content": ""}}]}

    client = OpenRouterLLMClient(
        LLMClientConfig(
            provider="openrouter",
            api_key="secret-key",
            model_id="anthropic/claude-opus-4-7",
            max_tokens=1024,
            base_url="https://openrouter.ai/api/v1",
        ),
        fetch=fetch,
    )

    with pytest.raises(LLMTransportError, match="empty content"):
        client.complete("judge this")


def test_llm_client_config_from_env_requires_explicit_provider(monkeypatch):
    _clear_judge_env(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-key")
    assert llm_client_config_from_env() is None


def test_llm_client_config_from_env_builds_openrouter(monkeypatch):
    _clear_judge_env(monkeypatch)
    monkeypatch.setenv("LEGIS_JUDGE_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-key")
    monkeypatch.setenv("LEGIS_JUDGE_MODEL", "anthropic/claude-opus-4-7")
    monkeypatch.setenv("LEGIS_JUDGE_MAX_TOKENS", "777")

    cfg = llm_client_config_from_env()

    assert cfg == LLMClientConfig(
        provider="openrouter",
        api_key="secret-key",
        model_id="anthropic/claude-opus-4-7",
        max_tokens=777,
        base_url="https://openrouter.ai/api/v1",
    )


def test_llm_client_config_from_env_uses_current_openrouter_default_model(monkeypatch):
    _clear_judge_env(monkeypatch)
    monkeypatch.setenv("LEGIS_JUDGE_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-key")

    cfg = llm_client_config_from_env()

    assert cfg is not None
    assert cfg.model_id == "anthropic/claude-opus-4.7"


def test_llm_client_config_rejects_unsafe_remote_http_base_url(monkeypatch):
    _clear_judge_env(monkeypatch)
    monkeypatch.setenv("LEGIS_JUDGE_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-key")
    monkeypatch.setenv("LEGIS_JUDGE_BASE_URL", "http://example.com/api/v1")

    with pytest.raises(LLMTransportError, match="must use HTTPS"):
        llm_client_config_from_env()


def test_llm_client_config_rejects_remote_http_even_with_global_insecure_override(monkeypatch):
    _clear_judge_env(monkeypatch)
    monkeypatch.setenv("LEGIS_JUDGE_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-key")
    monkeypatch.setenv("LEGIS_JUDGE_BASE_URL", "http://example.com/api/v1")
    monkeypatch.setenv("LEGIS_ALLOW_INSECURE_REMOTE_HTTP", "1")

    with pytest.raises(LLMTransportError, match="must use HTTPS"):
        llm_client_config_from_env()


def test_llm_client_config_allows_loopback_http_base_url(monkeypatch):
    _clear_judge_env(monkeypatch)
    monkeypatch.setenv("LEGIS_JUDGE_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-key")
    monkeypatch.setenv("LEGIS_JUDGE_BASE_URL", "http://localhost:8787/api/v1/")

    cfg = llm_client_config_from_env()

    assert cfg is not None
    assert cfg.base_url == "http://localhost:8787/api/v1"


def test_openrouter_client_validates_direct_config_base_url():
    with pytest.raises(LLMTransportError, match="must use HTTPS"):
        OpenRouterLLMClient(
            LLMClientConfig(
                provider="openrouter",
                api_key="secret-key",
                model_id="anthropic/claude-opus-4-7",
                max_tokens=1024,
                base_url="http://example.com/api/v1",
            )
        )


def test_urllib_fetch_rejects_redirects_without_forwarding_authorization():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_POST(self):
            requests.append((self.path, self.headers.get("Authorization")))
            self.send_response(302)
            self.send_header(
                "Location",
                f"http://127.0.0.1:{self.server.server_port}/capture",
            )
            self.end_headers()

        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization")))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"unexpected":true}')

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        with pytest.raises(LLMTransportError, match="redirect not allowed"):
            _urllib_fetch(
                "POST",
                f"http://127.0.0.1:{server.server_port}/chat/completions",
                {},
                {"Authorization": "Bearer secret"},
            )
        assert requests == [("/chat/completions", "Bearer secret")]
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_urllib_fetch_wraps_invalid_utf8_response(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, size):
            return b"\xff"

    def fake_open_no_redirect(request):
        return FakeResponse()

    monkeypatch.setattr("legis.enforcement.llm_client._open_no_redirect", fake_open_no_redirect)

    with pytest.raises(LLMTransportError, match="invalid JSON"):
        _urllib_fetch("POST", "https://example.test/chat/completions", {}, {})


def test_urllib_fetch_rejects_oversized_response(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, size):
            return b"x" * size

    def fake_open_no_redirect(request):
        return FakeResponse()

    monkeypatch.setattr("legis.enforcement.llm_client._open_no_redirect", fake_open_no_redirect)

    with pytest.raises(LLMTransportError, match="response too large"):
        _urllib_fetch("POST", "https://example.test/chat/completions", {}, {})


def test_urllib_fetch_wraps_read_time_transport_errors(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, size):
            raise TimeoutError("read timed out")

    def fake_open_no_redirect(request):
        return FakeResponse()

    monkeypatch.setattr("legis.enforcement.llm_client._open_no_redirect", fake_open_no_redirect)

    with pytest.raises(LLMTransportError, match="read timed out"):
        _urllib_fetch("POST", "https://example.test/chat/completions", {}, {})
