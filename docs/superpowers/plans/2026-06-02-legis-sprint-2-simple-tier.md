# Legis Sprint 2 — Simple tier (chill → coached) Implementation Plan

> **Status:** ✅ implemented 2026-06-02 — all 8 tasks complete, 60 tests green.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the simple-tier enforcement loop — the 2×2's left column and top-right cell: **chill** (policy fires → recordable override, nothing blocked, nothing silent) and **coached** (an LLM judge stands between the proposed override and the record, turned on by a single config flag).

**Architecture:** A record-agnostic `EnforcementEngine` wraps the Sprint 0 append-only, hash-chained `AuditStore` and the existing `OverrideRecord`. The judge is injected: `judge=None` is **chill**, a `Judge` instance is **coached** — that injection *is* the config flag. The judge's logic (prompt build, fail-closed verdict parse, model-id capture) is real and tested; only the model network call sits behind an injected `LLMClient.complete()` seam, so tests need no network and production wires a real client. Verdict parsing is **fail-closed**: BLOCKED wins ambiguity, anything unparseable → BLOCKED (no-false-green). Both ACCEPTED and BLOCKED attempts are recorded to the append-only trail (`judge_verdict` distinguishes them); only ACCEPTED means the override took effect. The engine stamps `recorded_at` from an injected `Clock` — single source of truth.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy Core, SQLite (governance DB, separate from the operational checks DB), pytest (warnings-as-errors).

---

## Deliberate design decisions (locked — do not reopen)

1. **Record BLOCKED attempts, not just accepted ones.** The roadmap's verdict-record format lists `judge_verdict: ACCEPTED | BLOCKED` as a *stored* field — a BLOCKED value only ever exists in a stored record if blocked attempts are recorded. Combined with audit-completeness (no silent path), the engine records both. `accepted=False` + `judge_verdict=BLOCKED` fully satisfies the WP-2.2 exit criterion "a BLOCKED override does not persist as accepted." *(Sprint 3 forward-flag: the override-rate gate must compute its ratio over final dispositions / accepted overrides per entity — NOT raw submit attempts — or a hammering agent dilutes the denominator.)*
2. **The judge injection is the config flag.** `EnforcementEngine(store, clock, judge=None)` is chill; passing a `Judge` is coached. Flipping it changes nothing else — proven by an explicit test (Task 6).
3. **Enforcement writes to the hash-chained `AuditStore`** (Sprint 0 governance trail), never the indexed checks table. This is also what seeds Sprint 3's tamper-binding.
4. **Fail-closed verdict parse.** BLOCKED wins on ambiguity; unknown/empty/unparseable → BLOCKED. The judge never accepts on a response it cannot read as an explicit ACCEPTED.
5. **`judge_model` recorded on every verdict** (accepted and blocked alike).

---

## File structure

| File | Responsibility |
|---|---|
| `src/legis/enforcement/__init__.py` | package docstring |
| `src/legis/enforcement/verdict.py` | `Verdict` str-enum (`ACCEPTED`/`BLOCKED`); `JudgeOpinion` dataclass (verdict, model, rationale) |
| `src/legis/enforcement/judge.py` | `Judge` protocol; `LLMClient` protocol; `parse_verdict` (fail-closed); `build_prompt`; `LLMJudge` |
| `src/legis/enforcement/engine.py` | `EnforcementResult` dataclass; `EnforcementEngine` (chill + coached) |
| `src/legis/api/app.py` | inject an engine; `POST /overrides` (201/409) + `GET /overrides` trail |
| `tests/enforcement/test_verdict_parse.py` | fail-closed parse contract |
| `tests/enforcement/test_judge.py` | `LLMJudge` records model + verbatim rationale; honours parse |
| `tests/enforcement/test_engine_chill.py` | WP-2.1 chill cell |
| `tests/enforcement/test_engine_coached.py` | WP-2.2 coached cell |
| `tests/enforcement/test_engine_flag_flip.py` | the WP-2.2 exit-criterion proof |
| `tests/api/test_override_api.py` | end-to-end over HTTP |

---

## Task 1: Verdict + JudgeOpinion value types

**Files:**
- Create: `src/legis/enforcement/__init__.py`
- Create: `src/legis/enforcement/verdict.py`
- Test: `tests/enforcement/test_verdict_parse.py` (parse comes in Task 2; this task only needs the enum to exist — assert via Task 2's import). Use a tiny direct test here.

- [ ] **Step 1: Write the failing test**

Create `tests/enforcement/__init__.py` (empty) and `tests/enforcement/test_verdict_types.py`:

```python
from legis.enforcement.verdict import JudgeOpinion, Verdict


def test_verdict_values_are_stable_strings():
    assert Verdict.ACCEPTED.value == "ACCEPTED"
    assert Verdict.BLOCKED.value == "BLOCKED"


def test_judge_opinion_carries_verdict_model_rationale():
    op = JudgeOpinion(verdict=Verdict.BLOCKED, model="m-1", rationale="too vague")
    assert op.verdict is Verdict.BLOCKED
    assert op.model == "m-1"
    assert op.rationale == "too vague"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/enforcement/test_verdict_types.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'legis.enforcement'`

- [ ] **Step 3: Write minimal implementation**

`src/legis/enforcement/__init__.py`:

```python
"""Simple-tier enforcement (Sprint 2): chill + coached cells of the 2×2."""
```

`src/legis/enforcement/verdict.py`:

```python
"""Judge verdict value types — shared by the judge and the engine.

A ``str`` enum so verdicts serialize to plain JSON in the audit trail and on the
HTTP surface (same discipline as ``CheckOutcome``).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Verdict(str, Enum):
    ACCEPTED = "ACCEPTED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class JudgeOpinion:
    verdict: Verdict
    model: str
    rationale: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/enforcement/test_verdict_types.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/legis/enforcement/__init__.py src/legis/enforcement/verdict.py tests/enforcement/
git commit -m "feat(enforcement): verdict value types"
```

---

## Task 2: Fail-closed verdict parsing

**Files:**
- Create: `src/legis/enforcement/judge.py`
- Test: `tests/enforcement/test_verdict_parse.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest

from legis.enforcement.judge import parse_verdict
from legis.enforcement.verdict import Verdict


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("ACCEPTED", Verdict.ACCEPTED),
        ("accepted — the rationale is specific and correct", Verdict.ACCEPTED),
        ("VERDICT: ACCEPTED\nbecause ...", Verdict.ACCEPTED),
        ("BLOCKED", Verdict.BLOCKED),
        ("blocked: rationale is boilerplate", Verdict.BLOCKED),
        # Ambiguity is fail-closed: BLOCKED wins when both tokens appear.
        ("I would say ACCEPTED but actually BLOCKED", Verdict.BLOCKED),
        # Unparseable / unknown is fail-closed.
        ("", Verdict.BLOCKED),
        ("   ", Verdict.BLOCKED),
        ("maybe?", Verdict.BLOCKED),
        ("the model timed out", Verdict.BLOCKED),
    ],
)
def test_parse_verdict_is_fail_closed(raw, expected):
    assert parse_verdict(raw) is expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/enforcement/test_verdict_parse.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_verdict'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/legis/enforcement/judge.py`:

```python
"""The coached-cell judge: an interactive wall, not a code generator.

The judge's *logic* — prompt construction, fail-closed verdict parsing, and
model-identity capture — is real and fully tested. Only the model call itself
sits behind the injected ``LLMClient`` seam, so tests need no network and a
production deployment wires a real client. Borrowed *effect* from elspeth's CI
judge, not its vocabulary.
"""

from __future__ import annotations

import re
from typing import Protocol

from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.records.override_record import OverrideRecord

_TOKEN = re.compile(r"[A-Z]+")


def parse_verdict(raw: str) -> Verdict:
    """Read a model response as a verdict, fail-closed.

    BLOCKED wins on ambiguity; anything that is not an explicit, unambiguous
    ACCEPTED is BLOCKED. The judge never accepts on a response it cannot read.
    """
    tokens = set(_TOKEN.findall(raw.upper()))
    if Verdict.BLOCKED.value in tokens:
        return Verdict.BLOCKED
    if Verdict.ACCEPTED.value in tokens:
        return Verdict.ACCEPTED
    return Verdict.BLOCKED
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/enforcement/test_verdict_parse.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add src/legis/enforcement/judge.py tests/enforcement/test_verdict_parse.py
git commit -m "feat(enforcement): fail-closed verdict parsing"
```

---

## Task 3: LLMJudge over an injected client

**Files:**
- Modify: `src/legis/enforcement/judge.py`
- Test: `tests/enforcement/test_judge.py`

- [ ] **Step 1: Write the failing test**

```python
from legis.enforcement.judge import LLMJudge
from legis.enforcement.verdict import Verdict
from legis.identity.entity_key import EntityKey
from legis.records.override_record import OverrideRecord


class FakeClient:
    """A scripted LLM client — captures the prompt, returns a canned response."""

    def __init__(self, response: str) -> None:
        self.model_id = "fake-judge@1"
        self.response = response
        self.seen_prompt: str | None = None

    def complete(self, prompt: str) -> str:
        self.seen_prompt = prompt
        return self.response


def _record() -> OverrideRecord:
    return OverrideRecord(
        policy="no-broad-except",
        entity_key=EntityKey.from_locator("src/app.py:handler"),
        rationale="third-party lib raises bare Exception; we re-raise after logging",
        agent_id="agent-7",
        recorded_at="2026-06-02T00:00:00+00:00",
    )


def test_judge_returns_accepted_with_model_and_verbatim_rationale():
    client = FakeClient("ACCEPTED — rationale is specific and correct")
    op = LLMJudge(client).evaluate(_record())
    assert op.verdict is Verdict.ACCEPTED
    assert op.model == "fake-judge@1"
    assert op.rationale == "ACCEPTED — rationale is specific and correct"


def test_judge_is_fail_closed_on_unparseable_response():
    op = LLMJudge(FakeClient("the model is unsure")).evaluate(_record())
    assert op.verdict is Verdict.BLOCKED
    assert op.model == "fake-judge@1"


def test_judge_prompt_carries_policy_entity_and_rationale():
    client = FakeClient("BLOCKED")
    LLMJudge(client).evaluate(_record())
    assert "no-broad-except" in client.seen_prompt
    assert "src/app.py:handler" in client.seen_prompt
    assert "third-party lib raises bare Exception" in client.seen_prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/enforcement/test_judge.py -v`
Expected: FAIL — `ImportError: cannot import name 'LLMJudge'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/legis/enforcement/judge.py`:

```python
class LLMClient(Protocol):
    model_id: str

    def complete(self, prompt: str) -> str: ...


class Judge(Protocol):
    def evaluate(self, record: OverrideRecord) -> JudgeOpinion: ...


def build_prompt(record: OverrideRecord) -> str:
    return (
        "You are a governance judge. An agent wants to override a policy that "
        "fired. Reply with ACCEPTED or BLOCKED on the first line, then your "
        "reasoning. Accept only if the rationale is specific, correct, and "
        "actually addresses why the policy fired.\n\n"
        f"policy: {record.policy}\n"
        f"entity: {record.entity_key.value}\n"
        f"rationale: {record.rationale}\n"
    )


class LLMJudge:
    """A ``Judge`` backed by an injected ``LLMClient``."""

    def __init__(self, client: LLMClient) -> None:
        self._client = client

    def evaluate(self, record: OverrideRecord) -> JudgeOpinion:
        raw = self._client.complete(build_prompt(record))
        return JudgeOpinion(
            verdict=parse_verdict(raw),
            model=self._client.model_id,
            rationale=raw,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/enforcement/test_judge.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/legis/enforcement/judge.py tests/enforcement/test_judge.py
git commit -m "feat(enforcement): LLMJudge over an injected client seam"
```

---

## Task 4: EnforcementEngine — chill cell (WP-2.1)

**Files:**
- Create: `src/legis/enforcement/engine.py`
- Test: `tests/enforcement/test_engine_chill.py`

- [ ] **Step 1: Write the failing test**

```python
from legis.clock import FixedClock
from legis.enforcement.engine import EnforcementEngine
from legis.identity.entity_key import EntityKey
from legis.store.audit_store import AuditStore


def engine(tmp_path, judge=None):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    return EnforcementEngine(store, FixedClock("2026-06-02T12:00:00+00:00"), judge=judge), store


def test_chill_override_is_accepted_and_recorded(tmp_path):
    eng, store = engine(tmp_path)
    result = eng.submit_override(
        policy="no-broad-except",
        entity_key=EntityKey.from_locator("src/app.py:handler"),
        rationale="re-raised after logging",
        agent_id="agent-7",
    )
    assert result.accepted is True
    assert result.verdict is None          # no judge in the chill cell
    assert result.judge_model is None
    assert result.seq >= 1

    trail = store.read_all()
    assert len(trail) == 1
    payload = trail[0].payload
    assert payload["policy"] == "no-broad-except"
    assert payload["rationale"] == "re-raised after logging"
    assert payload["agent_id"] == "agent-7"
    assert payload["recorded_at"] == "2026-06-02T12:00:00+00:00"  # clock-stamped
    assert payload["identity_stable"] is False                    # locator, pre-SEI
    assert payload["extensions"] == {}                            # no judge fields


def test_chill_trail_is_append_only_and_integrity_holds(tmp_path):
    eng, store = engine(tmp_path)
    for i in range(3):
        eng.submit_override(
            policy="p",
            entity_key=EntityKey.from_locator(f"e{i}"),
            rationale="r",
            agent_id="a",
        )
    assert len(store.read_all()) == 3
    assert store.verify_integrity() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/enforcement/test_engine_chill.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'legis.enforcement.engine'`

- [ ] **Step 3: Write minimal implementation**

`src/legis/enforcement/engine.py`:

```python
"""The simple-tier enforcement engine — chill and coached cells.

One method, ``submit_override``. Whether a judge is injected is the *only*
difference between the two cells (the "single config flag"):

* **chill**  (``judge=None``): the proposed override records as-is, accepted.
* **coached** (``judge`` present): the judge evaluates *before* the record is
  written; ACCEPTED records the override as taken, BLOCKED records the attempt
  with ``accepted=False`` and returns the judge's reasoning so the agent can
  revise. There is no operator self-clear in this tier.

Every submission produces exactly one append-only, hash-chained audit record —
no silent path. The engine stamps ``recorded_at`` from the injected clock.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from legis.clock import Clock
from legis.enforcement.judge import Judge
from legis.enforcement.verdict import Verdict
from legis.identity.entity_key import EntityKey
from legis.records.override_record import OverrideRecord
from legis.store.audit_store import AuditStore


@dataclass(frozen=True)
class EnforcementResult:
    accepted: bool
    seq: int
    verdict: Verdict | None
    judge_model: str | None
    judge_rationale: str | None


class EnforcementEngine:
    def __init__(
        self,
        store: AuditStore,
        clock: Clock,
        judge: Judge | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._judge = judge

    def submit_override(
        self,
        *,
        policy: str,
        entity_key: EntityKey,
        rationale: str,
        agent_id: str,
    ) -> EnforcementResult:
        record = OverrideRecord(
            policy=policy,
            entity_key=entity_key,
            rationale=rationale,
            agent_id=agent_id,
            recorded_at=self._clock.now_iso(),
        )

        if self._judge is None:
            seq = self._store.append(record.to_payload())
            return EnforcementResult(
                accepted=True,
                seq=seq,
                verdict=None,
                judge_model=None,
                judge_rationale=None,
            )

        opinion = self._judge.evaluate(record)
        judged = replace(
            record,
            extensions={
                **record.extensions,
                "judge_verdict": opinion.verdict.value,
                "judge_model": opinion.model,
                "judge_rationale": opinion.rationale,
            },
        )
        seq = self._store.append(judged.to_payload())
        return EnforcementResult(
            accepted=opinion.verdict is Verdict.ACCEPTED,
            seq=seq,
            verdict=opinion.verdict,
            judge_model=opinion.model,
            judge_rationale=opinion.rationale,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/enforcement/test_engine_chill.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/legis/enforcement/engine.py tests/enforcement/test_engine_chill.py
git commit -m "feat(enforcement): chill cell — recordable override (WP-2.1)"
```

---

## Task 5: EnforcementEngine — coached cell (WP-2.2)

**Files:**
- Modify: none (engine already supports a judge)
- Test: `tests/enforcement/test_engine_coached.py`

- [ ] **Step 1: Write the failing test**

```python
from legis.clock import FixedClock
from legis.enforcement.engine import EnforcementEngine
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.identity.entity_key import EntityKey
from legis.store.audit_store import AuditStore


class ScriptedJudge:
    def __init__(self, opinion: JudgeOpinion) -> None:
        self.opinion = opinion

    def evaluate(self, record):
        return self.opinion


def engine(tmp_path, opinion):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    eng = EnforcementEngine(
        store,
        FixedClock("2026-06-02T12:00:00+00:00"),
        judge=ScriptedJudge(opinion),
    )
    return eng, store


def submit(eng):
    return eng.submit_override(
        policy="no-broad-except",
        entity_key=EntityKey.from_locator("src/app.py:handler"),
        rationale="re-raised after logging",
        agent_id="agent-7",
    )


def test_coached_accepted_records_with_judge_fields(tmp_path):
    eng, store = engine(
        tmp_path, JudgeOpinion(Verdict.ACCEPTED, "judge@1", "specific and correct")
    )
    result = submit(eng)
    assert result.accepted is True
    assert result.verdict is Verdict.ACCEPTED
    assert result.judge_model == "judge@1"
    ext = store.read_all()[0].payload["extensions"]
    assert ext["judge_verdict"] == "ACCEPTED"
    assert ext["judge_model"] == "judge@1"
    assert ext["judge_rationale"] == "specific and correct"


def test_coached_blocked_does_not_persist_as_accepted_but_is_recorded(tmp_path):
    eng, store = engine(
        tmp_path, JudgeOpinion(Verdict.BLOCKED, "judge@1", "rationale is boilerplate")
    )
    result = submit(eng)
    assert result.accepted is False
    assert result.verdict is Verdict.BLOCKED
    assert result.judge_rationale == "rationale is boilerplate"
    # The blocked attempt IS recorded — judge_verdict distinguishes it; the
    # async human sees the full trail. It is not recorded as accepted.
    trail = store.read_all()
    assert len(trail) == 1
    ext = trail[0].payload["extensions"]
    assert ext["judge_verdict"] == "BLOCKED"
    assert ext["judge_model"] == "judge@1"   # model recorded on every verdict
    assert store.verify_integrity() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/enforcement/test_engine_coached.py -v`
Expected: FAIL — first assertion to bite is the missing test file's import? No — the engine exists. Run and confirm the file is new and the behaviour holds. If any assertion fails, fix the engine (NOT the test). Expected on first run: the test file does not exist yet → write it, then it should PASS against the Task-4 engine. To honour RED: temporarily break the expectation (e.g. assert `result.accepted is True` for the blocked case), watch it fail, then correct it.

Run: `uv run pytest tests/enforcement/test_engine_coached.py -v`
Expected: with the deliberately-wrong assertion, FAIL (`assert False is True`). Restore the correct assertion.

- [ ] **Step 3: Write minimal implementation**

None — Task 4's engine already implements coached behaviour. (If RED above passed immediately with correct assertions, that is acceptable here because the coached path is exercised for the first time by *these* tests; the deliberate-break step proves the assertions bite.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/enforcement/test_engine_coached.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/enforcement/test_engine_coached.py
git commit -m "test(enforcement): coached cell — judge gate before record (WP-2.2)"
```

---

## Task 6: The flag-flip exit-criterion proof (WP-2.2)

**Files:**
- Test: `tests/enforcement/test_engine_flag_flip.py`

This test IS the WP-2.2 exit criterion: "flipping the flag turns chill → coached with no other change."

- [ ] **Step 1: Write the failing test**

```python
from legis.clock import FixedClock
from legis.enforcement.engine import EnforcementEngine
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.identity.entity_key import EntityKey
from legis.store.audit_store import AuditStore


class ScriptedJudge:
    def __init__(self, opinion):
        self.opinion = opinion

    def evaluate(self, record):
        return self.opinion


SAME_CALL = dict(
    policy="no-broad-except",
    entity_key=EntityKey.from_locator("src/app.py:handler"),
    rationale="re-raised after logging",
    agent_id="agent-7",
)


def _engine(tmp_path, name, judge):
    store = AuditStore(f"sqlite:///{tmp_path / name}")
    return EnforcementEngine(store, FixedClock("2026-06-02T12:00:00+00:00"), judge=judge), store


def test_flipping_only_the_judge_turns_chill_into_coached(tmp_path):
    # Identical construction and identical submit call; the ONLY difference is
    # whether a judge is injected.
    chill, chill_store = _engine(tmp_path, "chill.db", None)
    coached, coached_store = _engine(
        tmp_path, "coached.db",
        ScriptedJudge(JudgeOpinion(Verdict.ACCEPTED, "judge@1", "ok")),
    )

    chill_result = chill.submit_override(**SAME_CALL)
    coached_result = coached.submit_override(**SAME_CALL)

    # Both accept; both record exactly one event; the engine and call are equal.
    assert chill_result.accepted is True
    assert coached_result.accepted is True

    chill_ext = chill_store.read_all()[0].payload["extensions"]
    coached_ext = coached_store.read_all()[0].payload["extensions"]

    # The flag's entire effect: chill writes no judge fields, coached does.
    assert chill_ext == {}
    assert chill_result.verdict is None
    assert coached_ext["judge_verdict"] == "ACCEPTED"
    assert coached_result.verdict is Verdict.ACCEPTED
```

- [ ] **Step 2: Run test to verify it fails**

To honour RED with no production change due: temporarily assert `chill_result.verdict is Verdict.ACCEPTED` (wrong), run, watch it fail, then restore `is None`.

Run: `uv run pytest tests/enforcement/test_engine_flag_flip.py -v`
Expected (with wrong assertion): FAIL. Restore correct assertion.

- [ ] **Step 3: Write minimal implementation**

None — proves existing behaviour.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/enforcement/test_engine_flag_flip.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/enforcement/test_engine_flag_flip.py
git commit -m "test(enforcement): flag-flip chill↔coached exit-criterion proof"
```

---

## Task 7: HTTP override surface — end-to-end

**Files:**
- Modify: `src/legis/api/app.py`
- Test: `tests/api/test_override_api.py`

The engine is injected into the app the same way `check_surface` is. `POST /overrides` accepts a locator + the override fields; ACCEPTED → 201, BLOCKED → 409, both with the full result body. `GET /overrides` returns the trail for async human review.

- [ ] **Step 1: Write the failing test**

```python
from fastapi.testclient import TestClient

from legis.api.app import create_app
from legis.clock import FixedClock
from legis.enforcement.engine import EnforcementEngine
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.store.audit_store import AuditStore


class ScriptedJudge:
    def __init__(self, opinion):
        self.opinion = opinion

    def evaluate(self, record):
        return self.opinion


def chill_client(tmp_path):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    eng = EnforcementEngine(store, FixedClock("2026-06-02T12:00:00+00:00"))
    return TestClient(create_app(enforcement=eng))


def coached_client(tmp_path, opinion):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    eng = EnforcementEngine(
        store, FixedClock("2026-06-02T12:00:00+00:00"), judge=ScriptedJudge(opinion)
    )
    return TestClient(create_app(enforcement=eng))


BODY = {
    "policy": "no-broad-except",
    "entity": "src/app.py:handler",
    "rationale": "re-raised after logging",
    "agent_id": "agent-7",
}


def test_chill_post_override_returns_201_and_records(tmp_path):
    c = chill_client(tmp_path)
    resp = c.post("/overrides", json=BODY)
    assert resp.status_code == 201
    body = resp.json()
    assert body["accepted"] is True
    assert body["verdict"] is None

    trail = c.get("/overrides").json()
    assert len(trail) == 1
    assert trail[0]["policy"] == "no-broad-except"
    assert trail[0]["identity_stable"] is False


def test_coached_blocked_post_returns_409_with_judge_reasoning(tmp_path):
    c = coached_client(
        tmp_path, JudgeOpinion(Verdict.BLOCKED, "judge@1", "rationale is boilerplate")
    )
    resp = c.post("/overrides", json=BODY)
    assert resp.status_code == 409
    body = resp.json()
    assert body["accepted"] is False
    assert body["verdict"] == "BLOCKED"
    assert body["judge_rationale"] == "rationale is boilerplate"
    # Even blocked, the attempt is in the trail for async review.
    assert len(c.get("/overrides").json()) == 1


def test_coached_accepted_post_returns_201(tmp_path):
    c = coached_client(
        tmp_path, JudgeOpinion(Verdict.ACCEPTED, "judge@1", "specific and correct")
    )
    resp = c.post("/overrides", json=BODY)
    assert resp.status_code == 201
    body = resp.json()
    assert body["accepted"] is True
    assert body["verdict"] == "ACCEPTED"
    assert body["judge_model"] == "judge@1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_override_api.py -v`
Expected: FAIL — `TypeError: create_app() got an unexpected keyword argument 'enforcement'`

- [ ] **Step 3: Write minimal implementation**

In `src/legis/api/app.py`:

Add imports near the others:

```python
from fastapi import Response

from legis.enforcement.engine import EnforcementEngine
from legis.identity.entity_key import EntityKey
```

Add an input model near `CheckRunIn`:

```python
class OverrideIn(BaseModel):
    policy: str
    entity: str          # a locator today (pre-SEI); identity_stable=False
    rationale: str
    agent_id: str
```

Extend the factory signature and wire the engine (mirror the `check_surface` lazy-default pattern; default governance DB is separate from the checks DB):

```python
DEFAULT_GOVERNANCE_DB = "sqlite:///legis-governance.db"
```

```python
def create_app(
    repo_path: str | Path | None = None,
    check_surface: CheckSurface | None = None,
    enforcement: EnforcementEngine | None = None,
) -> FastAPI:
    app = FastAPI(title="legis", version=__version__)
    state: dict[str, object | None] = {
        "checks": check_surface,
        "enforcement": enforcement,
    }
    ...
    def engine() -> EnforcementEngine:
        if state["enforcement"] is None:
            from legis.clock import SystemClock
            from legis.store.audit_store import AuditStore

            state["enforcement"] = EnforcementEngine(
                AuditStore(DEFAULT_GOVERNANCE_DB), SystemClock()
            )
        return state["enforcement"]
```

(Update the existing `checks()` accessor to read `state["checks"]` unchanged.)

Add the routes:

```python
    @app.post("/overrides")
    def post_override(body: OverrideIn, response: Response) -> dict:
        result = engine().submit_override(
            policy=body.policy,
            entity_key=EntityKey.from_locator(body.entity),
            rationale=body.rationale,
            agent_id=body.agent_id,
        )
        response.status_code = 201 if result.accepted else 409
        return {
            "accepted": result.accepted,
            "seq": result.seq,
            "verdict": result.verdict.value if result.verdict else None,
            "judge_model": result.judge_model,
            "judge_rationale": result.judge_rationale,
        }

    @app.get("/overrides")
    def get_overrides() -> list[dict]:
        return [rec.payload for rec in engine()._store.read_all()]
```

NOTE on `engine()._store`: reading the trail through a private attribute is a smell. Prefer adding a public `trail()` method to `EnforcementEngine` returning `list[dict]` (the decoded payloads) and call that instead. Do that: add to the engine —

```python
    def trail(self) -> list[dict]:
        return [rec.payload for rec in self._store.read_all()]
```

and change the route to `return engine().trail()`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_override_api.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/legis/api/app.py src/legis/enforcement/engine.py tests/api/test_override_api.py
git commit -m "feat(api): /overrides surface — chill+coached end-to-end (WP-2.1/2.2)"
```

---

## Task 8: Full suite + docs

- [ ] **Step 1: Run the whole suite**

Run: `uv run pytest -q`
Expected: all tests pass (Sprint 0+1's 37 + the new enforcement/api tests), zero warnings.

- [ ] **Step 2: Mark the sprint plan complete**

In `docs/superpowers/plans/2026-06-01-legis-implementation-sprints.md`, no status field exists per-sprint; instead append a one-line note under the Sprint 2 heading: `**Status:** ✅ implemented 2026-06-02 (chill + coached, end-to-end).` Add the same to this plan's header.

- [ ] **Step 3: Commit docs**

```bash
git add docs/
git commit -m "docs: mark Sprint 2 simple tier complete"
```

---

## Self-review — WP coverage

| WP | Exit criterion | Proven by |
|---|---|---|
| WP-2.1 chill | fired policy → correction or persisted attributable override; human reads trail; no silent path | Task 4 (`test_chill_override_is_accepted_and_recorded`), Task 7 (`GET /overrides`) |
| WP-2.2 coached | single flag flips chill→coached; BLOCKED not persisted as accepted; judge blocks never edits; model id on every verdict | Task 5 (both record shapes), Task 6 (flag-flip proof), Task 7 (409 + reasoning) |
| Fail-closed (suite invariant) | judge never accepts on an unreadable response | Task 2 (parse), Task 3 (`test_judge_is_fail_closed`) |
| SEI-shape independence | pre-SEI records carry `identity_stable: false` | Task 4, Task 7 assertions |

All judge LLM calls are behind the injected `LLMClient` seam — no network in tests, real client wired in production.
