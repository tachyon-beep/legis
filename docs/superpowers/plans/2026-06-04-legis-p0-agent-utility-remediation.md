# Legis P0 Agent Utility Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the four highest-utility P0 gaps for agents: deployable judge configuration, policy-boundary CI enforcement, Clarion-ready git rename evidence, and Filigree lifecycle gating.

**Architecture:** Implement four independent workstreams with narrow seams and tests first. Keep Legis dependency-light: use stdlib HTTP clients and injected fetch seams like the existing Clarion and Filigree clients. Borrow Elspeth's governance posture where useful: model decisions are auditable artifacts, boundary suppressions are statically checked against tests, and lifecycle gates should emit actionable diagnostics rather than silent passes.

**Tech Stack:** Python 3.12, FastAPI, stdlib `urllib`, SQLAlchemy-backed `AuditStore`, pytest, existing Legis MCP/CLI surfaces.

---

## Scope And Execution Order

Run this in a dedicated worktree. These workstreams are intentionally independent; implement and commit one task at a time.

1. **Deployable LLM judge client/config**: highest agent utility because protected/coached cells become usable without test-only injection.
2. **Policy-boundary scanner and CI runner**: highest prevention utility because agents can author evidence-bearing policy boundaries and have CI enforce them.
3. **Clarion git-rename evidence feed**: highest refactor utility because agents can rename/move code without orphaning governance identity.
4. **Filigree lifecycle gate**: highest tracker utility because issue closure can consult Legis governance state instead of relying on convention.

Elspeth precedent to consult while implementing:

- `/home/john/elspeth/elspeth-lints/src/elspeth_lints/core/judge.py`: structured judge transport, model identity capture, fail-closed parsing posture.
- `/home/john/elspeth/elspeth-lints/src/elspeth_lints/core/judge_quality.py`: labelled corpus pattern for future judge quality gates.
- `/home/john/elspeth/elspeth-lints/src/elspeth_lints/core/judge_signature_diagnosis.py`: read-only diagnosis before operator-held signing/repair.
- `/home/john/elspeth/elspeth-lints/src/elspeth_lints/rules/trust_boundary/tests/rule.py`: static `test_ref` resolution and test fingerprint enforcement.
- `/home/john/elspeth/elspeth-lints/src/elspeth_lints/rules/trust_boundary/scope/rule.py`: static check that a boundary parameter is actually used.
- `/home/john/elspeth/.github/workflows/ci.yaml`: CI wording that gives operators diagnosis commands when governance gates fail.
- `/home/john/elspeth/docs/filigree/SCHEMA.md`: close-time tracker governance and periodic drift-audit posture.

## File Structure

### Workstream A: Deployable Judge

| File | Responsibility |
| --- | --- |
| `src/legis/enforcement/llm_client.py` | Concrete stdlib OpenRouter-compatible `LLMClient`, env loader, response validation. |
| `src/legis/enforcement/judge_factory.py` | Build `LLMJudge` or a fail-closed judge from env/config. Keeps API and MCP wiring DRY. |
| `src/legis/api/app.py` | Replace inline `FailClosedJudge` with the shared factory. |
| `src/legis/mcp.py` | Replace inline `FailClosedJudge` with the shared factory. |
| `src/legis/cli.py` | Add serve/MCP judge flags that set env before app/runtime construction. |
| `tests/enforcement/test_llm_client.py` | HTTP request/response contract, failure parsing, env loading. |
| `tests/enforcement/test_judge_factory.py` | Factory chooses OpenRouter judge only when explicitly configured. |
| `tests/api/test_complex_api.py` | Default app wires configured judge for protected overrides. |
| `tests/mcp/test_server.py` | MCP runtime wires configured judge. |
| `tests/test_cli.py` | CLI judge flags set env for serve/MCP. |

### Workstream B: Policy-Boundary Scanner

| File | Responsibility |
| --- | --- |
| `src/legis/policy/boundary_scan.py` | AST-only scanner for `@policy_boundary` metadata, `test_ref` resolution, fingerprint and exercise checks. |
| `src/legis/cli.py` | Add `policy-boundary-check` command with text/json output and CI exit code. |
| `.github/workflows/ci.yml` | Run `policy-boundary-check` after tests/mypy. |
| `tests/policy/test_boundary_scan.py` | Focused fixtures for valid boundary, missing test, drifted fingerprint, weak test. |
| `tests/test_cli.py` | CLI exit code and json output tests. |

### Workstream C: Clarion Git-Rename Feed

| File | Responsibility |
| --- | --- |
| `src/legis/git/rename_feed.py` | Structured committed and optional working-tree rename feed for Clarion. |
| `src/legis/git/surface.py` | Add safe helpers needed by the feed, without changing existing `/git/renames`. |
| `src/legis/api/app.py` | Add `GET /git/rename-feed`. |
| `src/legis/mcp.py` | Add `git_rename_feed_get` read-only tool for agents and sibling tools. |
| `tests/git/test_rename_feed.py` | Real git repo tests for committed and working-tree rename evidence. |
| `tests/api/test_git_api.py` | API contract tests. |
| `tests/mcp/test_server.py` | MCP tool exposure and call tests. |

### Workstream D: Filigree Lifecycle Gate

| File | Responsibility |
| --- | --- |
| `src/legis/governance/binding_ledger.py` | Add verified lookup by `issue_id`. |
| `src/legis/governance/filigree_gate.py` | Pure decision function for issue close eligibility. |
| `src/legis/api/app.py` | Add `GET /filigree/issues/{issue_id}/closure-gate`. |
| `src/legis/mcp.py` | Add `filigree_closure_gate_get` read-only tool for agents. |
| `tests/governance/test_filigree_gate.py` | Decision behavior without HTTP. |
| `tests/api/test_combinations_api.py` | API gate tests. |
| `tests/mcp/test_server.py` | MCP gate tool tests. |

---

## Task 1: Add A Deployable OpenRouter LLM Client

**Files:**
- Create: `src/legis/enforcement/llm_client.py`
- Test: `tests/enforcement/test_llm_client.py`

- [ ] **Step 1: Write the failing client tests**

Create `tests/enforcement/test_llm_client.py`:

```python
import pytest

from legis.enforcement.llm_client import (
    LLMClientConfig,
    LLMTransportError,
    OpenRouterLLMClient,
    llm_client_config_from_env,
)


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
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-key")
    assert llm_client_config_from_env() is None


def test_llm_client_config_from_env_builds_openrouter(monkeypatch):
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/enforcement/test_llm_client.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'legis.enforcement.llm_client'`.

- [ ] **Step 3: Implement the client**

Create `src/legis/enforcement/llm_client.py`:

```python
"""Concrete LLM clients for the governed judge seam."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

Fetch = Callable[[str, str, dict[str, Any], Mapping[str, str]], dict[str, Any]]

DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_JUDGE_MODEL = "anthropic/claude-opus-4-7"
DEFAULT_JUDGE_MAX_TOKENS = 1024
MAX_RESPONSE_BYTES = 1_000_000


class LLMTransportError(RuntimeError):
    """A judge LLM transport or response-shape failure."""


@dataclass(frozen=True)
class LLMClientConfig:
    provider: str
    api_key: str
    model_id: str
    max_tokens: int
    base_url: str = DEFAULT_OPENROUTER_BASE_URL


def llm_client_config_from_env() -> LLMClientConfig | None:
    provider = os.environ.get("LEGIS_JUDGE_PROVIDER")
    if provider is None or provider == "":
        return None
    if provider != "openrouter":
        raise LLMTransportError(f"unsupported LEGIS_JUDGE_PROVIDER: {provider}")
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise LLMTransportError("LEGIS_JUDGE_PROVIDER=openrouter requires OPENROUTER_API_KEY")
    raw_max_tokens = os.environ.get("LEGIS_JUDGE_MAX_TOKENS", str(DEFAULT_JUDGE_MAX_TOKENS))
    try:
        max_tokens = int(raw_max_tokens)
    except ValueError as exc:
        raise LLMTransportError("LEGIS_JUDGE_MAX_TOKENS must be an integer") from exc
    if max_tokens <= 0:
        raise LLMTransportError("LEGIS_JUDGE_MAX_TOKENS must be positive")
    return LLMClientConfig(
        provider="openrouter",
        api_key=api_key,
        model_id=os.environ.get("LEGIS_JUDGE_MODEL", DEFAULT_JUDGE_MODEL),
        max_tokens=max_tokens,
        base_url=os.environ.get("LEGIS_JUDGE_BASE_URL", DEFAULT_OPENROUTER_BASE_URL).rstrip("/"),
    )


def _urllib_fetch(
    method: str,
    url: str,
    body: dict[str, Any],
    headers: Mapping[str, str],
) -> dict[str, Any]:
    data = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for name, value in headers.items():
        req.add_header(name, value)
    try:
        with urllib.request.urlopen(req, timeout=30.0) as resp:  # noqa: S310
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, ValueError) as exc:
        raise LLMTransportError(f"{method} {url} failed: {exc}") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise LLMTransportError(f"{method} {url} response too large")
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise LLMTransportError(f"{method} {url} returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise LLMTransportError(f"{method} {url} returned {type(decoded).__name__}, expected object")
    return decoded


class OpenRouterLLMClient:
    def __init__(self, config: LLMClientConfig, *, fetch: Fetch | None = None) -> None:
        if config.provider != "openrouter":
            raise LLMTransportError(f"OpenRouterLLMClient cannot use provider {config.provider!r}")
        self._config = config
        self._fetch = fetch or _urllib_fetch
        self.model_id = f"openrouter:{config.model_id}"

    def complete(self, prompt: str) -> str:
        body = {
            "model": self._config.model_id,
            "max_tokens": self._config.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {"Authorization": f"Bearer {self._config.api_key}"}
        response = self._fetch(
            "POST",
            f"{self._config.base_url}/chat/completions",
            body,
            headers,
        )
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMTransportError("LLM response missing choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise LLMTransportError("LLM response choice is not an object")
        message = first.get("message")
        if not isinstance(message, dict):
            raise LLMTransportError("LLM response choice missing message")
        content = message.get("content")
        if not isinstance(content, str):
            raise LLMTransportError("LLM response content is not a string")
        if not content.strip():
            raise LLMTransportError("LLM response content is empty")
        return content
```

- [ ] **Step 4: Run the client tests**

Run: `uv run pytest tests/enforcement/test_llm_client.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/legis/enforcement/llm_client.py tests/enforcement/test_llm_client.py
git commit -m "feat(enforcement): add deployable OpenRouter judge client"
```

## Task 2: Wire The Judge Factory Into API And MCP

**Files:**
- Create: `src/legis/enforcement/judge_factory.py`
- Modify: `src/legis/api/app.py`
- Modify: `src/legis/mcp.py`
- Test: `tests/enforcement/test_judge_factory.py`
- Test: `tests/api/test_complex_api.py`
- Test: `tests/mcp/test_server.py`

- [ ] **Step 1: Write factory tests**

Create `tests/enforcement/test_judge_factory.py`:

```python
from legis.enforcement.judge_factory import build_judge_from_env
from legis.enforcement.verdict import Verdict
from legis.identity.entity_key import EntityKey
from legis.records.override_record import OverrideRecord


def _record():
    return OverrideRecord(
        policy="no-eval",
        entity_key=EntityKey.from_locator("src/x.py:f"),
        rationale="specific rationale",
        agent_id="agent-1",
        recorded_at="2026-06-04T00:00:00+00:00",
        extensions={},
    )


def test_build_judge_from_env_returns_fail_closed_when_unconfigured(monkeypatch):
    monkeypatch.delenv("LEGIS_JUDGE_PROVIDER", raising=False)
    judge = build_judge_from_env("API")

    opinion = judge.evaluate(_record())

    assert opinion.verdict is Verdict.BLOCKED
    assert opinion.model == "fail-closed-fallback"
    assert "No LLM judge client is configured on this API server." in opinion.rationale


def test_build_judge_from_env_uses_configured_client(monkeypatch):
    calls = []

    def fetch(method, url, body, headers):
        calls.append(body)
        return {"choices": [{"message": {"content": "ACCEPTED\nok"}}]}

    monkeypatch.setenv("LEGIS_JUDGE_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-key")
    monkeypatch.setenv("LEGIS_JUDGE_MODEL", "anthropic/claude-opus-4-7")

    judge = build_judge_from_env("API", fetch=fetch)
    opinion = judge.evaluate(_record())

    assert opinion.verdict is Verdict.ACCEPTED
    assert opinion.model == "openrouter:anthropic/claude-opus-4-7"
    assert calls[0]["messages"][0]["content"].startswith("You are a governance judge.")
```

- [ ] **Step 2: Run factory tests to verify they fail**

Run: `uv run pytest tests/enforcement/test_judge_factory.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'legis.enforcement.judge_factory'`.

- [ ] **Step 3: Implement shared judge factory**

Create `src/legis/enforcement/judge_factory.py`:

```python
"""Runtime construction for governed judges."""

from __future__ import annotations

from legis.enforcement.judge import LLMJudge
from legis.enforcement.llm_client import Fetch, OpenRouterLLMClient, llm_client_config_from_env
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.records.override_record import OverrideRecord


class FailClosedJudge:
    def __init__(self, surface: str) -> None:
        self._surface = surface

    def evaluate(self, record: OverrideRecord) -> JudgeOpinion:
        return JudgeOpinion(
            verdict=Verdict.BLOCKED,
            model="fail-closed-fallback",
            rationale=f"No LLM judge client is configured on this {self._surface} server.",
        )


def build_judge_from_env(surface: str, *, fetch: Fetch | None = None):
    cfg = llm_client_config_from_env()
    if cfg is None:
        return FailClosedJudge(surface)
    return LLMJudge(OpenRouterLLMClient(cfg, fetch=fetch))
```

- [ ] **Step 4: Replace API inline fail-closed judge**

In `src/legis/api/app.py`, replace the inline `FailClosedJudge` block in `create_app` with:

```python
        if protected_gate is None:
            from legis.enforcement.judge_factory import build_judge_from_env
            from legis.enforcement.protected import ProtectedGate

            protected_gate = ProtectedGate(gov_store, clock, build_judge_from_env("API"), hmac_key)
```

- [ ] **Step 5: Replace MCP inline fail-closed judge**

In `src/legis/mcp.py`, replace the inline `FailClosedJudge` block in `build_runtime` with:

```python
        from legis.enforcement.judge_factory import build_judge_from_env
        from legis.enforcement.signoff import SignoffGate

        protected_gate = ProtectedGate(store, clock, build_judge_from_env("MCP"), key)
        signoff_gate = SignoffGate(store, clock, signer=True, key=key)
```

- [ ] **Step 6: Add API wiring test**

Append to `tests/api/test_complex_api.py`:

```python
def test_create_app_wires_env_configured_openrouter_judge(tmp_path, monkeypatch):
    from legis.enforcement.llm_client import OpenRouterLLMClient

    class FakeClient:
        model_id = "openrouter:test-model"

        def complete(self, prompt):
            return "ACCEPTED\nconfigured judge accepted"

    monkeypatch.setenv("LEGIS_HMAC_KEY", "k")
    monkeypatch.setenv("LEGIS_JUDGE_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-key")
    monkeypatch.setenv("LEGIS_GOVERNANCE_DB", f"sqlite:///{tmp_path / 'gov.db'}")
    monkeypatch.setattr(OpenRouterLLMClient, "__init__", lambda self, config, fetch=None: None)
    monkeypatch.setattr(OpenRouterLLMClient, "model_id", FakeClient.model_id, raising=False)
    monkeypatch.setattr(OpenRouterLLMClient, "complete", FakeClient().complete, raising=False)

    c = TestClient(create_app(repo_path=tmp_path))

    resp = c.post("/protected/overrides", json=PBODY)

    assert resp.status_code == 201
    assert resp.json()["judge_model"] == "openrouter:test-model"
```

- [ ] **Step 7: Add MCP wiring test**

Append to `tests/mcp/test_server.py`:

```python
def test_build_runtime_wires_env_configured_openrouter_judge(tmp_path, monkeypatch):
    from legis.enforcement.llm_client import OpenRouterLLMClient
    from legis.mcp import build_runtime

    monkeypatch.setenv("LEGIS_HMAC_KEY", "k")
    monkeypatch.setenv("LEGIS_JUDGE_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-key")
    monkeypatch.setenv("LEGIS_GOVERNANCE_DB", f"sqlite:///{tmp_path / 'gov.db'}")
    monkeypatch.setattr(OpenRouterLLMClient, "__init__", lambda self, config, fetch=None: None)
    monkeypatch.setattr(OpenRouterLLMClient, "model_id", "openrouter:test-model", raising=False)
    monkeypatch.setattr(OpenRouterLLMClient, "complete", lambda self, prompt: "ACCEPTED\nok", raising=False)

    runtime = build_runtime("agent-launch")

    assert runtime.protected_gate is not None
```

- [ ] **Step 8: Run the wiring tests**

Run: `uv run pytest tests/enforcement/test_judge_factory.py tests/api/test_complex_api.py -k 'judge_factory or env_configured_openrouter' tests/mcp/test_server.py -k build_runtime_wires_env_configured_openrouter -q`

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/legis/enforcement/judge_factory.py src/legis/api/app.py src/legis/mcp.py tests/enforcement/test_judge_factory.py tests/api/test_complex_api.py tests/mcp/test_server.py
git commit -m "feat(runtime): wire configured judge into API and MCP"
```

## Task 3: Add Judge CLI Flags

**Files:**
- Modify: `src/legis/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write CLI tests**

Append to `tests/test_cli.py`:

```python
def test_serve_accepts_judge_configuration_flags():
    args = build_parser().parse_args(
        [
            "serve",
            "--judge-provider",
            "openrouter",
            "--judge-model",
            "anthropic/claude-opus-4-7",
            "--judge-max-tokens",
            "2048",
        ]
    )

    assert args.judge_provider == "openrouter"
    assert args.judge_model == "anthropic/claude-opus-4-7"
    assert args.judge_max_tokens == 2048


def test_main_serve_sets_judge_env(monkeypatch):
    import os

    calls = []

    def fake_run(app, **kw):
        calls.append(
            {
                "provider": os.environ.get("LEGIS_JUDGE_PROVIDER"),
                "model": os.environ.get("LEGIS_JUDGE_MODEL"),
                "max_tokens": os.environ.get("LEGIS_JUDGE_MAX_TOKENS"),
            }
        )

    monkeypatch.delenv("LEGIS_JUDGE_PROVIDER", raising=False)
    monkeypatch.delenv("LEGIS_JUDGE_MODEL", raising=False)
    monkeypatch.delenv("LEGIS_JUDGE_MAX_TOKENS", raising=False)

    rc = main(
        [
            "serve",
            "--judge-provider",
            "openrouter",
            "--judge-model",
            "anthropic/claude-opus-4-7",
            "--judge-max-tokens",
            "2048",
        ],
        run=fake_run,
    )

    assert rc == 0
    assert calls == [
        {
            "provider": "openrouter",
            "model": "anthropic/claude-opus-4-7",
            "max_tokens": "2048",
        }
    ]


def test_main_mcp_sets_judge_env(monkeypatch):
    import os

    import legis.mcp as mcp_module

    calls = []

    def fake_mcp_main(agent_id):
        calls.append(
            {
                "provider": os.environ.get("LEGIS_JUDGE_PROVIDER"),
                "model": os.environ.get("LEGIS_JUDGE_MODEL"),
                "max_tokens": os.environ.get("LEGIS_JUDGE_MAX_TOKENS"),
            }
        )
        return 0

    monkeypatch.setattr(mcp_module, "main", fake_mcp_main)
    rc = main(
        [
            "mcp",
            "--agent-id",
            "agent-1",
            "--judge-provider",
            "openrouter",
            "--judge-model",
            "anthropic/claude-opus-4-7",
            "--judge-max-tokens",
            "2048",
        ]
    )

    assert rc == 0
    assert calls == [
        {
            "provider": "openrouter",
            "model": "anthropic/claude-opus-4-7",
            "max_tokens": "2048",
        }
    ]
```

- [ ] **Step 2: Run CLI tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k judge_env -q`

Expected: FAIL because the parser does not know the judge flags.

- [ ] **Step 3: Add parser helper and wire env**

In `src/legis/cli.py`, add this helper near `build_parser`:

```python
def _add_judge_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--judge-provider",
        choices=("openrouter",),
        help="LLM judge provider. Omit to keep protected cells fail-closed.",
    )
    parser.add_argument(
        "--judge-model",
        help="LLM judge model id. Falls back to LEGIS_JUDGE_MODEL.",
    )
    parser.add_argument(
        "--judge-max-tokens",
        type=int,
        help="Maximum judge response tokens. Falls back to LEGIS_JUDGE_MAX_TOKENS.",
    )
```

Call the helper after `serve.add_argument("--binding-db", ...)` and after the MCP `--clarion-url` argument:

```python
    _add_judge_flags(serve)
    _add_judge_flags(mcp)
```

In `main`, add this helper near `_missing_sqlite_db`:

```python
def _apply_judge_env(args) -> None:
    import os

    if getattr(args, "judge_provider", None):
        os.environ["LEGIS_JUDGE_PROVIDER"] = args.judge_provider
    if getattr(args, "judge_model", None):
        os.environ["LEGIS_JUDGE_MODEL"] = args.judge_model
    if getattr(args, "judge_max_tokens", None) is not None:
        os.environ["LEGIS_JUDGE_MAX_TOKENS"] = str(args.judge_max_tokens)
```

Call `_apply_judge_env(args)` inside both the `serve` and `mcp` command branches before constructing the server/runtime.

- [ ] **Step 4: Run CLI tests**

Run: `uv run pytest tests/test_cli.py -k 'judge_configuration or judge_env' -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/legis/cli.py tests/test_cli.py
git commit -m "feat(cli): add deployable judge configuration flags"
```

## Task 4: Add Policy-Boundary AST Scanner

**Files:**
- Create: `src/legis/policy/boundary_scan.py`
- Test: `tests/policy/test_boundary_scan.py`

- [ ] **Step 1: Write scanner tests**

Create `tests/policy/test_boundary_scan.py`:

```python
from pathlib import Path

from legis.canonical import content_hash
from legis.policy.boundary_scan import scan_policy_boundaries
from legis.policy.decorator import get_normalized_ast_str


def _test_fingerprint(source: str) -> str:
    return content_hash(get_normalized_ast_str(source))


def test_scan_policy_boundaries_accepts_pinned_exercising_test(tmp_path):
    test_source = '''
def test_policy_boundary_exercises_subject():
    assert guarded({"policy": "PY-WL-101"}) == "ok"
'''
    fp = _test_fingerprint(test_source)
    src = tmp_path / "src" / "pkg"
    tests = tmp_path / "tests"
    src.mkdir(parents=True)
    tests.mkdir()
    (src / "subject.py").write_text(
        f'''
from legis.policy.decorator import policy_boundary

@policy_boundary(
    source="docs/spec.md:12",
    suppresses=("PY-WL-101",),
    invariant="guarded input rejects malformed records",
    test_ref="tests/test_subject.py::test_policy_boundary_exercises_subject",
    test_fingerprint="{fp}",
)
def guarded(payload):
    return "ok"
''',
        encoding="utf-8",
    )
    (tests / "test_subject.py").write_text(test_source, encoding="utf-8")

    findings = scan_policy_boundaries(src, repo_root=tmp_path)

    assert findings == []


def test_scan_policy_boundaries_reports_missing_test_ref(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "subject.py").write_text(
        '''
from legis.policy.decorator import policy_boundary

@policy_boundary(source="docs/spec.md:12", suppresses=("PY-WL-101",), invariant="guarded")
def guarded(payload):
    return "ok"
''',
        encoding="utf-8",
    )

    findings = scan_policy_boundaries(src, repo_root=tmp_path)

    assert [(f.rule_id, f.reason) for f in findings] == [
        ("POLICY_BOUNDARY_TEST_REF_MISSING", "test_ref is required")
    ]


def test_scan_policy_boundaries_reports_drifted_test_fingerprint(tmp_path):
    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir()
    tests.mkdir()
    (src / "subject.py").write_text(
        '''
from legis.policy.decorator import policy_boundary

@policy_boundary(
    source="docs/spec.md:12",
    suppresses=("PY-WL-101",),
    invariant="guarded",
    test_ref="tests/test_subject.py::test_policy_boundary_exercises_subject",
    test_fingerprint="sha256:stale",
)
def guarded(payload):
    return "ok"
''',
        encoding="utf-8",
    )
    (tests / "test_subject.py").write_text(
        '''
def test_policy_boundary_exercises_subject():
    assert guarded({"policy": "PY-WL-101"}) == "ok"
''',
        encoding="utf-8",
    )

    findings = scan_policy_boundaries(src, repo_root=tmp_path)

    assert findings[0].rule_id == "POLICY_BOUNDARY_TEST_FINGERPRINT_MISMATCH"


def test_scan_policy_boundaries_reports_weak_test_that_does_not_call_subject(tmp_path):
    test_source = '''
def test_policy_boundary_exercises_subject():
    assert "PY-WL-101"
'''
    fp = _test_fingerprint(test_source)
    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir()
    tests.mkdir()
    (src / "subject.py").write_text(
        f'''
from legis.policy.decorator import policy_boundary

@policy_boundary(
    source="docs/spec.md:12",
    suppresses=("PY-WL-101",),
    invariant="guarded",
    test_ref="tests/test_subject.py::test_policy_boundary_exercises_subject",
    test_fingerprint="{fp}",
)
def guarded(payload):
    return "ok"
''',
        encoding="utf-8",
    )
    (tests / "test_subject.py").write_text(test_source, encoding="utf-8")

    findings = scan_policy_boundaries(src, repo_root=tmp_path)

    assert findings[0].rule_id == "POLICY_BOUNDARY_TEST_DOES_NOT_EXERCISE_SUBJECT"
```

- [ ] **Step 2: Run scanner tests to verify they fail**

Run: `uv run pytest tests/policy/test_boundary_scan.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'legis.policy.boundary_scan'`.

- [ ] **Step 3: Implement the scanner**

Create `src/legis/policy/boundary_scan.py`:

```python
"""Static honesty gate for ``@policy_boundary`` declarations."""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from legis.canonical import content_hash
from legis.policy.decorator import get_normalized_ast_str


@dataclass(frozen=True)
class BoundaryFinding:
    rule_id: str
    file_path: str
    line: int
    qualname: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def scan_policy_boundaries(root: str | Path, *, repo_root: str | Path | None = None) -> list[BoundaryFinding]:
    scan_root = Path(root)
    resolved_repo = Path(repo_root) if repo_root is not None else scan_root
    findings: list[BoundaryFinding] = []
    for path in sorted(scan_root.rglob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError) as exc:
            findings.append(
                BoundaryFinding(
                    "POLICY_BOUNDARY_PARSE_ERROR",
                    _display(path, resolved_repo),
                    0,
                    "<module>",
                    f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        findings.extend(_scan_tree(tree, path, resolved_repo))
    return findings


def _scan_tree(tree: ast.AST, path: Path, repo_root: Path) -> list[BoundaryFinding]:
    findings: list[BoundaryFinding] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not _is_policy_boundary_call(decorator):
                continue
            kwargs = _literal_kwargs(decorator)
            findings.extend(_validate_boundary(node, kwargs, path, repo_root))
    return findings


def _is_policy_boundary_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "policy_boundary"
    if isinstance(func, ast.Attribute):
        return func.attr == "policy_boundary"
    return False


def _literal_kwargs(call: ast.Call) -> dict[str, Any] | None:
    if call.args:
        return None
    out: dict[str, Any] = {}
    for kw in call.keywords:
        if kw.arg is None:
            return None
        try:
            out[kw.arg] = ast.literal_eval(kw.value)
        except (ValueError, SyntaxError):
            return None
    return out


def _validate_boundary(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    kwargs: dict[str, Any] | None,
    path: Path,
    repo_root: Path,
) -> list[BoundaryFinding]:
    base = {
        "file_path": _display(path, repo_root),
        "line": node.lineno,
        "qualname": node.name,
    }
    if kwargs is None:
        return [BoundaryFinding("POLICY_BOUNDARY_NONLITERAL", **base, reason="decorator arguments must be static literals")]
    suppresses = kwargs.get("suppresses")
    if not isinstance(suppresses, tuple) or not all(isinstance(item, str) and item for item in suppresses):
        return [BoundaryFinding("POLICY_BOUNDARY_SUPPRESSES_INVALID", **base, reason="suppresses must be a non-empty tuple of strings")]
    test_ref = kwargs.get("test_ref")
    if not isinstance(test_ref, str) or not test_ref:
        return [BoundaryFinding("POLICY_BOUNDARY_TEST_REF_MISSING", **base, reason="test_ref is required")]
    test_fingerprint = kwargs.get("test_fingerprint")
    if not isinstance(test_fingerprint, str) or not test_fingerprint:
        return [BoundaryFinding("POLICY_BOUNDARY_TEST_FINGERPRINT_MISSING", **base, reason="test_fingerprint is required")]
    resolved = _resolve_test_ref(test_ref, repo_root)
    if isinstance(resolved, BoundaryFinding):
        return [BoundaryFinding(resolved.rule_id, **base, reason=resolved.reason)]
    test_node, test_source = resolved
    actual = content_hash(get_normalized_ast_str(ast.get_source_segment(test_source, test_node) or ""))
    if actual != test_fingerprint:
        return [
            BoundaryFinding(
                "POLICY_BOUNDARY_TEST_FINGERPRINT_MISMATCH",
                **base,
                reason=f"test fingerprint drifted: expected {test_fingerprint}, actual {actual}",
            )
        ]
    if not _test_calls_subject(test_node, node.name):
        return [
            BoundaryFinding(
                "POLICY_BOUNDARY_TEST_DOES_NOT_EXERCISE_SUBJECT",
                **base,
                reason=f"test_ref {test_ref!r} does not call {node.name!r}",
            )
        ]
    if not _test_mentions_policy(test_node, suppresses):
        return [
            BoundaryFinding(
                "POLICY_BOUNDARY_TEST_DOES_NOT_MENTION_POLICY",
                **base,
                reason=f"test_ref {test_ref!r} does not mention any suppressed policy",
            )
        ]
    return []


def _resolve_test_ref(
    test_ref: str, repo_root: Path
) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef, str] | BoundaryFinding:
    parts = test_ref.split("::")
    if len(parts) not in (2, 3):
        return BoundaryFinding("POLICY_BOUNDARY_TEST_REF_MALFORMED", "", 0, "", "test_ref must be tests/path.py::test_func or tests/path.py::Class::test_method")
    path = (repo_root / parts[0]).resolve()
    try:
        path.relative_to(repo_root.resolve())
    except ValueError:
        return BoundaryFinding("POLICY_BOUNDARY_TEST_REF_OUTSIDE_REPO", "", 0, "", "test_ref path resolves outside repo")
    if not path.is_file():
        return BoundaryFinding("POLICY_BOUNDARY_TEST_FILE_MISSING", "", 0, "", f"test file does not exist: {parts[0]}")
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError) as exc:
        return BoundaryFinding("POLICY_BOUNDARY_TEST_PARSE_ERROR", "", 0, "", f"{type(exc).__name__}: {exc}")
    target = parts[-1]
    class_name = parts[1] if len(parts) == 3 else None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == target:
            if class_name is None or _enclosing_class_name(tree, node) == class_name:
                return node, source
    return BoundaryFinding("POLICY_BOUNDARY_TEST_FUNCTION_MISSING", "", 0, "", f"test function not found: {test_ref}")


def _enclosing_class_name(tree: ast.AST, target: ast.AST) -> str | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and target in node.body:
            return node.name
    return None


def _test_calls_subject(test_node: ast.AST, subject_name: str) -> bool:
    for node in ast.walk(test_node):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == subject_name:
                return True
            if isinstance(node.func, ast.Attribute) and node.func.attr == subject_name:
                return True
    return False


def _test_mentions_policy(test_node: ast.AST, suppresses: tuple[str, ...]) -> bool:
    for node in ast.walk(test_node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if any(policy in node.value for policy in suppresses):
                return True
    return False


def _display(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()
```

- [ ] **Step 4: Run scanner tests**

Run: `uv run pytest tests/policy/test_boundary_scan.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/legis/policy/boundary_scan.py tests/policy/test_boundary_scan.py
git commit -m "feat(policy): add static policy-boundary honesty scanner"
```

## Task 5: Add Policy-Boundary CLI And CI Gate

**Files:**
- Modify: `src/legis/cli.py`
- Modify: `.github/workflows/ci.yml`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write CLI tests**

Append to `tests/test_cli.py`:

```python
def test_policy_boundary_check_command_outputs_json(monkeypatch, capsys, tmp_path):
    import legis.cli as cli_module

    class FakeFinding:
        def to_dict(self):
            return {"rule_id": "POLICY_BOUNDARY_TEST_REF_MISSING", "file_path": "src/x.py"}

    monkeypatch.setattr(cli_module, "scan_policy_boundaries", lambda root, repo_root=None: [FakeFinding()], raising=False)

    rc = main(["policy-boundary-check", "--root", str(tmp_path), "--repo-root", str(tmp_path), "--format", "json"])

    assert rc == 1
    assert "POLICY_BOUNDARY_TEST_REF_MISSING" in capsys.readouterr().out


def test_policy_boundary_check_passes_when_no_findings(monkeypatch, capsys, tmp_path):
    import legis.cli as cli_module

    monkeypatch.setattr(cli_module, "scan_policy_boundaries", lambda root, repo_root=None: [], raising=False)

    rc = main(["policy-boundary-check", "--root", str(tmp_path), "--repo-root", str(tmp_path)])

    assert rc == 0
    assert "policy-boundary-check: PASS" in capsys.readouterr().out
```

- [ ] **Step 2: Run CLI tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k policy_boundary_check -q`

Expected: FAIL because the command is missing.

- [ ] **Step 3: Add CLI command**

In `src/legis/cli.py`, import the scanner at module scope:

```python
from legis.policy.boundary_scan import scan_policy_boundaries
```

In `build_parser`, add:

```python
    boundary = subparsers.add_parser(
        "policy-boundary-check",
        help="Fail when @policy_boundary metadata is missing current behavioural evidence",
    )
    boundary.add_argument("--root", default="src", help="Python source root to scan")
    boundary.add_argument("--repo-root", default=".", help="Repository root for test_ref resolution")
    boundary.add_argument("--format", choices=("text", "json"), default="text")
```

In `main`, add before the final help block:

```python
    if args.command == "policy-boundary-check":
        import json

        findings = scan_policy_boundaries(args.root, repo_root=args.repo_root)
        if args.format == "json":
            print(json.dumps([finding.to_dict() for finding in findings], sort_keys=True))
        elif findings:
            for finding in findings:
                print(
                    f"{finding.file_path}:{finding.line}: {finding.rule_id}: "
                    f"{finding.qualname}: {finding.reason}"
                )
        else:
            print("policy-boundary-check: PASS")
        return 1 if findings else 0
```

- [ ] **Step 4: Add CI gate**

In `.github/workflows/ci.yml`, add this step after mypy:

```yaml
      - name: Run policy-boundary honesty gate
        run: uv run legis policy-boundary-check --root src --repo-root .
```

- [ ] **Step 5: Run tests and the local gate**

Run: `uv run pytest tests/test_cli.py -k policy_boundary_check -q`

Expected: PASS.

Run: `uv run legis policy-boundary-check --root src --repo-root .`

Expected: PASS with `policy-boundary-check: PASS` in the current tree.

- [ ] **Step 6: Commit**

```bash
git add src/legis/cli.py .github/workflows/ci.yml tests/test_cli.py
git commit -m "feat(policy): add policy-boundary CI gate"
```

## Task 6: Add Clarion-Ready Git Rename Feed

**Files:**
- Create: `src/legis/git/rename_feed.py`
- Modify: `src/legis/git/surface.py`
- Test: `tests/git/test_rename_feed.py`

- [ ] **Step 1: Write rename-feed tests**

Create `tests/git/test_rename_feed.py`:

```python
from legis.git.rename_feed import build_rename_feed


def test_build_rename_feed_reports_committed_renames(git_repo):
    repo, shas = git_repo

    feed = build_rename_feed(repo, base=shas["base"], head=shas["renamed"])

    assert feed["status"] == "committed_only"
    assert feed["base"] == shas["base"]
    assert feed["head"] == shas["renamed"]
    assert feed["committed"][0]["old_path"] == "a.txt"
    assert feed["committed"][0]["new_path"] == "renamed.txt"
    assert feed["working_tree"] == []


def test_build_rename_feed_can_include_worktree_renames(git_repo):
    import subprocess

    repo, shas = git_repo
    subprocess.run(["git", "-C", str(repo), "mv", "renamed.txt", "moved.txt"], check=True)

    feed = build_rename_feed(repo, base=shas["renamed"], head="HEAD", include_worktree=True)

    assert feed["status"] == "committed_and_worktree"
    assert feed["working_tree"][0]["old_path"] == "renamed.txt"
    assert feed["working_tree"][0]["new_path"] == "moved.txt"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/git/test_rename_feed.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'legis.git.rename_feed'`.

- [ ] **Step 3: Add working-tree rename helper to GitSurface**

In `src/legis/git/surface.py`, add this method to `GitSurface`:

```python
    def working_tree_renames(self, base: str) -> list[RenameEvidence]:
        import re
        if base.startswith("-") or not re.match(r"^[a-zA-Z0-9_/.~^-]+$", base):
            raise GitError(f"invalid base ref: {base}")
        out = self._run("diff", "-M", "--name-status", base)
        evidence: list[RenameEvidence] = []
        for line in out.splitlines():
            if not line.strip():
                continue
            status, _, rest = line.partition("\t")
            if not status.startswith("R"):
                continue
            old_path, _, new_path = rest.partition("\t")
            similarity = int(status[1:]) if status[1:].isdigit() else 0
            old_blob = self._blob(base, old_path)
            new_blob_result = self._run_raw("hash-object", "--", new_path)
            new_blob = new_blob_result.stdout.strip() if new_blob_result.returncode == 0 else ""
            evidence.append(
                RenameEvidence(
                    commit_sha="WORKTREE",
                    old_path=old_path,
                    new_path=new_path,
                    similarity=similarity,
                    old_blob=old_blob,
                    new_blob=new_blob,
                )
            )
        return evidence
```

- [ ] **Step 4: Implement rename feed module**

Create `src/legis/git/rename_feed.py`:

```python
"""Structured git rename evidence for Clarion's identity matcher."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from legis.git.surface import GitSurface


def build_rename_feed(
    repo_path: str | Path,
    *,
    base: str,
    head: str = "HEAD",
    include_worktree: bool = False,
) -> dict:
    surface = GitSurface(repo_path)
    committed = [asdict(item) for item in surface.renames(f"{base}..{head}")]
    working_tree = (
        [asdict(item) for item in surface.working_tree_renames(base)]
        if include_worktree
        else []
    )
    return {
        "status": "committed_and_worktree" if include_worktree else "committed_only",
        "base": base,
        "head": head,
        "committed": committed,
        "working_tree": working_tree,
    }
```

- [ ] **Step 5: Run rename-feed tests**

Run: `uv run pytest tests/git/test_rename_feed.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/legis/git/surface.py src/legis/git/rename_feed.py tests/git/test_rename_feed.py
git commit -m "feat(git): add Clarion-ready rename feed"
```

## Task 7: Expose Rename Feed Through API And MCP

**Files:**
- Modify: `src/legis/api/app.py`
- Modify: `src/legis/mcp.py`
- Test: `tests/api/test_git_api.py`
- Test: `tests/mcp/test_server.py`

- [ ] **Step 1: Add API test**

Append to `tests/api/test_git_api.py`:

```python
def test_git_rename_feed_endpoint_reports_committed_renames(git_repo):
    from fastapi.testclient import TestClient
    from legis.api.app import create_app

    repo, shas = git_repo
    c = TestClient(create_app(repo_path=repo))

    resp = c.get("/git/rename-feed", params={"base": shas["base"], "head": shas["renamed"]})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "committed_only"
    assert body["committed"][0]["old_path"] == "a.txt"
    assert body["committed"][0]["new_path"] == "renamed.txt"
```

- [ ] **Step 2: Add MCP test**

Append to `tests/mcp/test_server.py`:

```python
def test_git_rename_feed_get_tool_reports_feed(git_repo):
    from legis.mcp import McpRuntime

    repo, shas = git_repo
    runtime = McpRuntime(agent_id="agent-launch", git_surface=GitSurface(repo))

    responses = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "git_rename_feed_get",
                    "arguments": {"base": shas["base"], "head": shas["renamed"]},
                },
            }
        ),
        runtime,
    )

    body = responses[0]["result"]["structuredContent"]
    assert body["committed"][0]["old_path"] == "a.txt"
    assert body["committed"][0]["new_path"] == "renamed.txt"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_git_api.py -k rename_feed tests/mcp/test_server.py -k git_rename_feed_get -q`

Expected: FAIL because the route/tool is missing.

- [ ] **Step 4: Add API route**

In `src/legis/api/app.py`, import:

```python
from legis.git.rename_feed import build_rename_feed
```

After the existing `/git/renames` route, add:

```python
    @app.get("/git/rename-feed")
    def git_rename_feed(
        base: str = Query(...),
        head: str = Query("HEAD"),
        include_worktree: bool = Query(False),
    ) -> dict:
        try:
            return build_rename_feed(
                repo_path or os.getcwd(),
                base=base,
                head=head,
                include_worktree=include_worktree,
            )
        except GitError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
```

- [ ] **Step 5: Add MCP tool definition and dispatch**

In `src/legis/mcp.py`, add `"git_rename_feed_get"` to `_AGENT_TOOLS`.

In `tool_definitions()`, add this tool after `git_rename_list`:

```python
        {
            "name": "git_rename_feed_get",
            "description": "Read Clarion-ready committed and optional working-tree rename evidence.",
            "inputSchema": _schema(
                ["base"],
                {"base": string, "head": string, "include_worktree": {"type": "boolean"}},
            ),
        },
```

In the tools/call dispatch block, add:

```python
        if name == "git_rename_feed_get":
            from legis.git.rename_feed import build_rename_feed

            base = _require(args, "base")
            head = _optional_string(args, "head") or "HEAD"
            include_worktree = bool(args.get("include_worktree", False))
            repo_path = runtime.source_root or os.getcwd()
            return _tool_result(
                build_rename_feed(
                    repo_path,
                    base=base,
                    head=head,
                    include_worktree=include_worktree,
                )
            )
```

- [ ] **Step 6: Run API and MCP tests**

Run: `uv run pytest tests/api/test_git_api.py -k rename_feed tests/mcp/test_server.py -k 'tools_list or git_rename_feed_get' -q`

Expected: PASS after updating the tools-list expectation to include `git_rename_feed_get`.

- [ ] **Step 7: Commit**

```bash
git add src/legis/api/app.py src/legis/mcp.py tests/api/test_git_api.py tests/mcp/test_server.py
git commit -m "feat(api,mcp): expose Clarion-ready rename feed"
```

## Task 8: Add Filigree Closure Gate Decision Logic

**Files:**
- Modify: `src/legis/governance/binding_ledger.py`
- Create: `src/legis/governance/filigree_gate.py`
- Test: `tests/governance/test_filigree_gate.py`

- [ ] **Step 1: Write governance tests**

Create `tests/governance/test_filigree_gate.py`:

```python
from legis.clock import FixedClock
from legis.governance.binding_ledger import BindingLedger
from legis.governance.filigree_gate import evaluate_issue_closure
from legis.identity.entity_key import EntityKey
from legis.store.audit_store import AuditStore


KEY = b"binding-key"


def _ledger(tmp_path):
    return BindingLedger(
        AuditStore(f"sqlite:///{tmp_path / 'binding.db'}"),
        FixedClock("2026-06-04T00:00:00+00:00"),
        KEY,
    )


def test_issue_closure_gate_blocks_without_binding(tmp_path):
    ledger = _ledger(tmp_path)

    decision = evaluate_issue_closure(ledger, issue_id="legis-123")

    assert decision == {
        "issue_id": "legis-123",
        "allowed": False,
        "status": "missing_binding",
        "bindings": [],
    }


def test_issue_closure_gate_allows_verified_binding(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.record(
        signoff_seq=7,
        issue_id="legis-123",
        entity_key=EntityKey.from_sei("clarion:eid:abc"),
        content_hash="sha256:abc",
    )

    decision = evaluate_issue_closure(ledger, issue_id="legis-123")

    assert decision["allowed"] is True
    assert decision["status"] == "verified_binding"
    assert decision["bindings"][0]["signoff_seq"] == 7
```

- [ ] **Step 2: Run governance tests to verify they fail**

Run: `uv run pytest tests/governance/test_filigree_gate.py -q`

Expected: FAIL because `filigree_gate` and `BindingLedger.find_by_issue` do not exist.

- [ ] **Step 3: Add verified issue lookup to BindingLedger**

In `src/legis/governance/binding_ledger.py`, replace the current `verify` method and `get` method with this version, then add `find_by_issue` below `get`:

```python
    def _verified_payload(self, rec) -> dict[str, Any]:
        payload = dict(rec.payload)
        sig = payload.get("binding_signature")
        if not sig:
            raise BindingError(f"binding record seq={rec.seq} is missing its signature")
        try:
            fields = binding_signing_fields(payload)
        except KeyError as exc:
            raise BindingError(f"binding record seq={rec.seq} is structurally malformed: missing {exc}") from exc
        if not verify(fields, sig, self._key):
            raise BindingError(f"binding record seq={rec.seq} signature does not verify")
        return payload

    def verify(self) -> None:
        for rec in self._store.read_all():
            if rec.payload.get("kind") != BINDING_KIND:
                continue
            self._verified_payload(rec)

    def get(self, signoff_seq: int) -> dict[str, Any] | None:
        for rec in self._store.read_all():
            if rec.payload.get("kind") == BINDING_KIND and rec.payload.get("signoff_seq") == signoff_seq:
                return self._verified_payload(rec)
        return None

    def find_by_issue(self, issue_id: str) -> list[dict[str, Any]]:
        bindings: list[dict[str, Any]] = []
        for rec in self._store.read_all():
            if rec.payload.get("kind") == BINDING_KIND and rec.payload.get("issue_id") == issue_id:
                bindings.append(self._verified_payload(rec))
        return bindings
```

- [ ] **Step 4: Implement closure decision**

Create `src/legis/governance/filigree_gate.py`:

```python
"""Filigree lifecycle gate decisions backed by Legis binding evidence."""

from __future__ import annotations

from typing import Any

from legis.governance.binding_ledger import BindingLedger


def evaluate_issue_closure(ledger: BindingLedger, *, issue_id: str) -> dict[str, Any]:
    bindings = ledger.find_by_issue(issue_id)
    if not bindings:
        return {
            "issue_id": issue_id,
            "allowed": False,
            "status": "missing_binding",
            "bindings": [],
        }
    return {
        "issue_id": issue_id,
        "allowed": True,
        "status": "verified_binding",
        "bindings": bindings,
    }
```

- [ ] **Step 5: Run governance tests**

Run: `uv run pytest tests/governance/test_filigree_gate.py tests/governance/test_binding_ledger.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/legis/governance/binding_ledger.py src/legis/governance/filigree_gate.py tests/governance/test_filigree_gate.py
git commit -m "feat(governance): add Filigree closure gate decision"
```

## Task 9: Expose Filigree Closure Gate Through API And MCP

**Files:**
- Modify: `src/legis/api/app.py`
- Modify: `src/legis/mcp.py`
- Test: `tests/api/test_combinations_api.py`
- Test: `tests/mcp/test_server.py`

- [ ] **Step 1: Add API tests**

Append to `tests/api/test_combinations_api.py`:

```python
def test_filigree_closure_gate_blocks_without_verified_binding(tmp_path):
    from legis.governance.binding_ledger import BindingLedger
    from legis.store.audit_store import AuditStore

    ledger = BindingLedger(AuditStore(f"sqlite:///{tmp_path / 'binding.db'}"), FixedClock("2026-06-02T12:00:00+00:00"), b"k")
    c = _client(tmp_path, binding_ledger=ledger)

    resp = c.get("/filigree/issues/legis-123/closure-gate")

    assert resp.status_code == 409
    assert resp.json()["allowed"] is False
    assert resp.json()["status"] == "missing_binding"


def test_filigree_closure_gate_allows_verified_binding(tmp_path):
    from legis.governance.binding_ledger import BindingLedger
    from legis.identity.entity_key import EntityKey
    from legis.store.audit_store import AuditStore

    ledger = BindingLedger(AuditStore(f"sqlite:///{tmp_path / 'binding.db'}"), FixedClock("2026-06-02T12:00:00+00:00"), b"k")
    ledger.record(
        signoff_seq=1,
        issue_id="legis-123",
        entity_key=EntityKey.from_sei("clarion:eid:abc"),
        content_hash="sha256:abc",
    )
    c = _client(tmp_path, binding_ledger=ledger)

    resp = c.get("/filigree/issues/legis-123/closure-gate")

    assert resp.status_code == 200
    assert resp.json()["allowed"] is True
    assert resp.json()["status"] == "verified_binding"
```

- [ ] **Step 2: Add MCP test**

Append to `tests/mcp/test_server.py`:

```python
def test_filigree_closure_gate_get_reports_decision(tmp_path):
    from legis.clock import FixedClock
    from legis.governance.binding_ledger import BindingLedger
    from legis.identity.entity_key import EntityKey
    from legis.mcp import McpRuntime
    from legis.store.audit_store import AuditStore

    ledger = BindingLedger(
        AuditStore(f"sqlite:///{tmp_path / 'binding.db'}"),
        FixedClock("2026-06-02T12:00:00+00:00"),
        b"k",
    )
    ledger.record(
        signoff_seq=1,
        issue_id="legis-123",
        entity_key=EntityKey.from_sei("clarion:eid:abc"),
        content_hash="sha256:abc",
    )
    runtime = McpRuntime(agent_id="agent-launch")
    runtime.binding_ledger = ledger

    responses = _run(
        _messages(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "filigree_closure_gate_get",
                    "arguments": {"issue_id": "legis-123"},
                },
            }
        ),
        runtime,
    )

    body = responses[0]["result"]["structuredContent"]
    assert body["allowed"] is True
    assert body["status"] == "verified_binding"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_combinations_api.py -k closure_gate tests/mcp/test_server.py -k filigree_closure_gate -q`

Expected: FAIL because the route/tool is missing and `McpRuntime` has no `binding_ledger` field.

- [ ] **Step 4: Add API route**

In `src/legis/api/app.py`, import:

```python
from legis.governance.filigree_gate import evaluate_issue_closure
```

After `get_binding`, add:

```python
    @app.get("/filigree/issues/{issue_id}/closure-gate")
    def filigree_closure_gate(issue_id: str) -> dict:
        if binding_ledger is None:
            raise HTTPException(status_code=404, detail="binding ledger not enabled")
        try:
            decision = evaluate_issue_closure(binding_ledger, issue_id=issue_id)
        except BindingError as exc:
            raise HTTPException(status_code=500, detail=f"binding integrity failure: {exc}")
        if not decision["allowed"]:
            return JSONResponse(status_code=409, content=decision)
        return decision
```

Add this import near the other FastAPI imports if the file does not already import it:

```python
from fastapi.responses import JSONResponse
```

- [ ] **Step 5: Add MCP runtime field and tool**

In `src/legis/mcp.py`, add a field to `McpRuntime`:

```python
    binding_ledger: Any | None = None
```

In `build_runtime`, initialize the variable next to `protected_gate` and `signoff_gate`:

```python
    protected_gate = None
    signoff_gate = None
    binding_ledger = None
```

When `LEGIS_HMAC_KEY` is configured in `build_runtime`, add:

```python
        from legis.governance.binding_ledger import BindingLedger

        binding_ledger = BindingLedger(
            AuditStore(os.environ.get("LEGIS_BINDING_DB", "sqlite:///legis-binding.db")),
            clock,
            key,
        )
```

Pass `binding_ledger=binding_ledger` into the returned `McpRuntime`.

Add `"filigree_closure_gate_get"` to `_AGENT_TOOLS`.

Add this tool definition:

```python
        {
            "name": "filigree_closure_gate_get",
            "description": "Read whether Legis has verified binding evidence for closing a Filigree issue.",
            "inputSchema": _schema(["issue_id"], {"issue_id": string}),
        },
```

Add this dispatch case:

```python
        if name == "filigree_closure_gate_get":
            from legis.governance.filigree_gate import evaluate_issue_closure

            if runtime.binding_ledger is None:
                raise NotEnabledError("binding ledger not enabled")
            issue_id = _require(args, "issue_id")
            return _tool_result(evaluate_issue_closure(runtime.binding_ledger, issue_id=issue_id))
```

- [ ] **Step 6: Run API and MCP tests**

Run: `uv run pytest tests/api/test_combinations_api.py -k closure_gate tests/mcp/test_server.py -k 'tools_list or filigree_closure_gate' -q`

Expected: PASS after updating the tools-list expectation to include `filigree_closure_gate_get`.

- [ ] **Step 7: Commit**

```bash
git add src/legis/api/app.py src/legis/mcp.py tests/api/test_combinations_api.py tests/mcp/test_server.py
git commit -m "feat(api,mcp): expose Filigree closure gate"
```

## Task 10: Final Verification And Documentation Update

**Files:**
- Modify: `docs/superpowers/specs/2026-06-02-not-yets-completion-design.md`
- Modify: `docs/superpowers/specs/2026-06-01-legis-roadmap-to-first-class.md`
- Modify: `docs/superpowers/specs/2026-06-03-legis-mcp-surface-design.md`

- [ ] **Step 1: Run focused test suites**

Run:

```bash
uv run pytest \
  tests/enforcement/test_llm_client.py \
  tests/enforcement/test_judge_factory.py \
  tests/policy/test_boundary_scan.py \
  tests/git/test_rename_feed.py \
  tests/governance/test_filigree_gate.py \
  tests/api/test_complex_api.py \
  tests/api/test_git_api.py \
  tests/api/test_combinations_api.py \
  tests/mcp/test_server.py \
  tests/test_cli.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run static checks**

Run:

```bash
uv run mypy src/legis
uv run legis policy-boundary-check --root src --repo-root .
```

Expected: both PASS.

- [ ] **Step 3: Run full suite**

Run:

```bash
uv run pytest -q
```

Expected: PASS.

- [ ] **Step 4: Update specs with implementation notes**

Append a dated note to the relevant P0 sections:

```markdown
> 2026-06-04 implementation note: The P0 agent-utility remediation plan closes
> the Legis-side implementation for this item. Remaining sibling work, where
> applicable, is explicitly outside this repository: Clarion must consume
> `/git/rename-feed`, and Filigree must call `/filigree/issues/{issue_id}/closure-gate`
> before close transitions.
```

Place the note under:

- `docs/superpowers/specs/2026-06-02-not-yets-completion-design.md` WP-B3 and Filigree lifecycle sections.
- `docs/superpowers/specs/2026-06-01-legis-roadmap-to-first-class.md` git-rename and agent governance sections.
- `docs/superpowers/specs/2026-06-03-legis-mcp-surface-design.md` MCP tool surface section.

- [ ] **Step 5: Commit docs and verification**

```bash
git add docs/superpowers/specs/2026-06-02-not-yets-completion-design.md docs/superpowers/specs/2026-06-01-legis-roadmap-to-first-class.md docs/superpowers/specs/2026-06-03-legis-mcp-surface-design.md
git commit -m "docs: mark P0 agent utility gaps remediated on Legis side"
```

---

## Acceptance Criteria

- Protected API and MCP runtimes can use a real configured judge via `LEGIS_JUDGE_PROVIDER=openrouter` and `OPENROUTER_API_KEY`; without explicit configuration they remain fail-closed.
- `legis policy-boundary-check --root src --repo-root .` exists, exits non-zero on stale or weak boundary evidence, and runs in CI.
- `/git/rename-feed` and MCP `git_rename_feed_get` return Clarion-ready committed rename evidence and optional working-tree rename evidence.
- `/filigree/issues/{issue_id}/closure-gate` and MCP `filigree_closure_gate_get` return a verified binding decision and block missing evidence.
- All new behavior is covered by tests and the full suite passes.

## Self-Review

- **Spec coverage:** The plan covers all four P0s identified in the audit: deployable judge, policy-boundary enforcement, Clarion rename feed, and Filigree lifecycle gate.
- **Elspeth precedent applied:** Judge construction borrows Elspeth's explicit provider/model posture; policy-boundary checking borrows Elspeth's static `test_ref` and fingerprint gate; Filigree gating borrows close-time governance and drift-audit posture without importing Elspeth code.
- **Placeholder scan:** This plan contains concrete paths, code snippets, commands, and expected outcomes. It does not rely on undefined future placeholders.
- **Type consistency:** `LLMClientConfig`, `OpenRouterLLMClient`, `build_judge_from_env`, `BoundaryFinding`, `build_rename_feed`, and `evaluate_issue_closure` are introduced before use and referenced consistently.
