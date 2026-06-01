# Sprint 0 — Foundation & Contracts: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the legis Python project and build the four foundation primitives — a runnable skeleton service, an append-only audit store with integrity checking, the SEI-ready opaque entity-key, and the core override-record schema — so every later sprint is a swap or an extension, never a rewrite.

**Architecture:** Python 3.12 + FastAPI (HTTP read API, consumer model mirroring Clarion) + SQLite via SQLAlchemy Core (append-only audit store, SQLite dev → Postgres prod path, matching elspeth's Landscape). `src/` layout, `uv`-managed. The audit store is **record-agnostic**: it persists opaque canonical-JSON payloads in a hash chain and knows nothing about override records; the record schema serializes to JSON and hands bytes to the store. The entity-key is **opaque** from line one (locator today, SEI later, `identity_stable` flag), which is what makes Sprint 5's SEI adoption a value swap with no schema change.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, SQLAlchemy Core, SQLite, pytest, httpx (TestClient), `uv`.

**Status:** ✅ COMPLETE — all tasks implemented test-first; 18 tests green; `/health` served live. See ADR-0001 and the Sprint 0 commit. (Deviations for cause: tests tamper via raw `sqlite3` rather than test-only methods on the store; engine uses `NullPool`; a pytest filter silences Starlette's third-party TestClient deprecation.)

---

## File Structure

- `pyproject.toml` — project metadata + deps (uv-managed)
- `src/legis/__init__.py` — package marker + version
- `src/legis/clock.py` — injectable time source (deterministic tests; no hidden `datetime.now()`)
- `src/legis/api/__init__.py`, `src/legis/api/app.py` — FastAPI app + `GET /health` (WP-0.1)
- `src/legis/canonical.py` — canonical JSON + sha256 helper (shared by store + records)
- `src/legis/store/__init__.py`, `src/legis/store/audit_store.py` — append-only, hash-chained SQLite store (WP-0.2)
- `src/legis/identity/__init__.py`, `src/legis/identity/entity_key.py` — opaque SEI-ready entity-key (WP-0.3)
- `src/legis/records/__init__.py`, `src/legis/records/override_record.py` — core override record (WP-0.4)
- `docs/design/adr/0001-stack-and-architecture.md` — the WP-0.1 decision record
- `tests/` — mirror of the above

**Responsibility boundaries:** `canonical.py` is a leaf (no legis imports). `store/` depends only on `canonical` + SQLAlchemy — never on `records/` or `identity/` (record-agnostic). `records/` depends on `identity/` + `canonical`. `api/` is the only layer that wires them together. This mirrors elspeth's leaf-module discipline.

---

## Task 1 — Project scaffold + ADR (WP-0.1, part 1)

**Files:**
- Create: `pyproject.toml`, `src/legis/__init__.py`
- Create: `docs/design/adr/0001-stack-and-architecture.md`

- [ ] **Step 1: Init project with uv, src layout, deps**

Run: `uv init --lib --name legis --no-readme` then add deps:
`uv add fastapi "uvicorn[standard]" sqlalchemy` and dev deps
`uv add --dev pytest httpx`. Ensure `pyproject.toml` declares `requires-python = ">=3.12"` and the `src/` package path.

- [ ] **Step 2: Write the ADR**

`docs/design/adr/0001-stack-and-architecture.md` records: chosen stack (Python/FastAPI/SQLite/SQLAlchemy Core), the append-only audit store decision, the HTTP read-API consumer model, and how the setup preserves the zero-*human*-config invariant (single documented run command; the agent operates it). Include context (elspeth judge port, Wardline parity), decision, consequences, and the rejected Rust alternative.

- [ ] **Step 3: Verify the project builds**

Run: `uv run python -c "import legis; print(legis.__version__)"`
Expected: prints a version string, no import error.

---

## Task 2 — Health endpoint (WP-0.1, part 2)

**Files:**
- Create: `src/legis/clock.py`, `src/legis/api/__init__.py`, `src/legis/api/app.py`
- Test: `tests/api/test_health.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_health.py
from fastapi.testclient import TestClient
from legis.api.app import create_app

def test_health_returns_ok():
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "legis"
```

- [ ] **Step 2: Run it, confirm it fails** — `uv run pytest tests/api/test_health.py -v` → FAIL (no module `legis.api.app`).

- [ ] **Step 3: Implement `clock.py` and `app.py`**

```python
# src/legis/clock.py
from __future__ import annotations
from datetime import datetime, timezone
from typing import Protocol

class Clock(Protocol):
    def now_iso(self) -> str: ...

class SystemClock:
    def now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

class FixedClock:
    """Deterministic clock for tests."""
    def __init__(self, value: str) -> None:
        self._value = value
    def now_iso(self) -> str:
        return self._value
```

```python
# src/legis/api/app.py
from __future__ import annotations
from fastapi import FastAPI
from legis import __version__

def create_app() -> FastAPI:
    app = FastAPI(title="legis", version=__version__)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "legis", "version": __version__}

    return app
```

- [ ] **Step 4: Run it, confirm it passes** — `uv run pytest tests/api/test_health.py -v` → PASS.

---

## Task 3 — Canonical JSON helper (shared primitive)

**Files:**
- Create: `src/legis/canonical.py`
- Test: `tests/test_canonical.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_canonical.py
from legis.canonical import canonical_json, content_hash

def test_canonical_json_is_key_order_independent():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})

def test_content_hash_is_stable_and_hex():
    h1 = content_hash({"a": 1, "b": [1, 2, 3]})
    h2 = content_hash({"b": [1, 2, 3], "a": 1})
    assert h1 == h2
    assert len(h1) == 64 and all(c in "0123456789abcdef" for c in h1)
```

- [ ] **Step 2: Run it, confirm it fails** — `uv run pytest tests/test_canonical.py -v` → FAIL.

- [ ] **Step 3: Implement**

```python
# src/legis/canonical.py
from __future__ import annotations
import hashlib
import json
from typing import Any

def canonical_json(value: Any) -> str:
    """Deterministic JSON. v1 uses sorted keys + tight separators;
    RFC 8785 is a future hardening (see ADR-0001)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run it, confirm it passes** — PASS.

---

## Task 4 — Append-only audit store (WP-0.2)

**Files:**
- Create: `src/legis/store/__init__.py`, `src/legis/store/audit_store.py`
- Test: `tests/store/test_audit_store.py`

The store persists opaque dict payloads in an ordered, hash-chained, append-only table. Each row: `seq` (PK, monotonic), `payload` (canonical JSON text), `content_hash`, `prev_hash`, `chain_hash = sha256(prev_hash + content_hash)`. Genesis `prev_hash` is 64 zeros. No update/delete methods exist; a SQLite trigger rejects UPDATE/DELETE at the DB level so mutation is *rejected, not discouraged*. `verify_integrity()` recomputes content + chain hashes and detects any out-of-band edit or reordering.

- [ ] **Step 1: Write the failing tests**

```python
# tests/store/test_audit_store.py
import sqlite3
import pytest
from legis.store.audit_store import AuditStore

def make_store(tmp_path):
    return AuditStore(f"sqlite:///{tmp_path/'audit.db'}")

def test_append_returns_monotonic_seq(tmp_path):
    s = make_store(tmp_path)
    assert s.append({"k": "a"}) == 1
    assert s.append({"k": "b"}) == 2

def test_read_all_is_ordered(tmp_path):
    s = make_store(tmp_path)
    s.append({"k": "a"}); s.append({"k": "b"})
    seqs = [r.seq for r in s.read_all()]
    assert seqs == [1, 2]
    assert s.read_all()[0].payload == {"k": "a"}

def test_store_exposes_no_mutation_api(tmp_path):
    s = make_store(tmp_path)
    assert not hasattr(s, "update")
    assert not hasattr(s, "delete")

def test_db_trigger_rejects_update(tmp_path):
    s = make_store(tmp_path)
    s.append({"k": "a"})
    with pytest.raises(sqlite3.IntegrityError):
        s._raw_execute("UPDATE audit_log SET payload = '{}' WHERE seq = 1")

def test_db_trigger_rejects_delete(tmp_path):
    s = make_store(tmp_path)
    s.append({"k": "a"})
    with pytest.raises(sqlite3.IntegrityError):
        s._raw_execute("DELETE FROM audit_log WHERE seq = 1")

def test_verify_integrity_passes_on_clean_chain(tmp_path):
    s = make_store(tmp_path)
    s.append({"k": "a"}); s.append({"k": "b"})
    assert s.verify_integrity() is True

def test_verify_integrity_detects_tampered_payload(tmp_path):
    s = make_store(tmp_path)
    s.append({"k": "a"}); s.append({"k": "b"})
    s._raw_execute(
        "CREATE TEMP TRIGGER off1 BEFORE UPDATE ON audit_log BEGIN SELECT 1; END;",
        bypass=True,
    )
    # Out-of-band edit bypassing the app API (simulates direct DB tampering):
    s._tamper_for_test(seq=1, payload={"k": "EVIL"})
    assert s.verify_integrity() is False
```

- [ ] **Step 2: Run them, confirm they fail** — `uv run pytest tests/store -v` → FAIL.

- [ ] **Step 3: Implement the store**

```python
# src/legis/store/audit_store.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from sqlalchemy import (
    create_engine, MetaData, Table, Column, Integer, Text, text, insert, select,
)
from legis.canonical import canonical_json, content_hash

GENESIS = "0" * 64

@dataclass(frozen=True)
class AuditRecord:
    seq: int
    payload: dict[str, Any]
    content_hash: str
    prev_hash: str
    chain_hash: str

def _chain(prev_hash: str, c_hash: str) -> str:
    import hashlib
    return hashlib.sha256((prev_hash + c_hash).encode("utf-8")).hexdigest()

class AuditStore:
    def __init__(self, url: str) -> None:
        self._engine = create_engine(url, future=True)
        self._md = MetaData()
        self._log = Table(
            "audit_log", self._md,
            Column("seq", Integer, primary_key=True, autoincrement=True),
            Column("payload", Text, nullable=False),
            Column("content_hash", Text, nullable=False),
            Column("prev_hash", Text, nullable=False),
            Column("chain_hash", Text, nullable=False),
        )
        self._md.create_all(self._engine)
        self._install_append_only_triggers()

    def _install_append_only_triggers(self) -> None:
        with self._engine.begin() as conn:
            conn.execute(text(
                "CREATE TRIGGER IF NOT EXISTS audit_log_no_update "
                "BEFORE UPDATE ON audit_log BEGIN "
                "SELECT RAISE(ABORT, 'audit_log is append-only'); END;"))
            conn.execute(text(
                "CREATE TRIGGER IF NOT EXISTS audit_log_no_delete "
                "BEFORE DELETE ON audit_log BEGIN "
                "SELECT RAISE(ABORT, 'audit_log is append-only'); END;"))

    def append(self, payload: dict[str, Any]) -> int:
        c_hash = content_hash(payload)
        with self._engine.begin() as conn:
            prev = conn.execute(
                select(self._log.c.chain_hash).order_by(self._log.c.seq.desc()).limit(1)
            ).scalar()
            prev_hash = prev if prev is not None else GENESIS
            result = conn.execute(insert(self._log).values(
                payload=canonical_json(payload),
                content_hash=c_hash,
                prev_hash=prev_hash,
                chain_hash=_chain(prev_hash, c_hash),
            ))
            return int(result.inserted_primary_key[0])

    def read_all(self) -> list[AuditRecord]:
        import json
        with self._engine.begin() as conn:
            rows = conn.execute(
                select(self._log).order_by(self._log.c.seq.asc())
            ).all()
        return [AuditRecord(r.seq, json.loads(r.payload), r.content_hash,
                            r.prev_hash, r.chain_hash) for r in rows]

    def verify_integrity(self) -> bool:
        import json
        prev_hash = GENESIS
        for rec in self.read_all():
            recomputed = content_hash(json.loads(canonical_json(rec.payload)))
            if recomputed != rec.content_hash:
                return False
            if rec.prev_hash != prev_hash:
                return False
            if rec.chain_hash != _chain(rec.prev_hash, rec.content_hash):
                return False
            prev_hash = rec.chain_hash
        return True

    # --- test-only seams (underscore-prefixed; not part of the public API) ---
    def _raw_execute(self, sql: str, *, bypass: bool = False) -> None:
        with self._engine.begin() as conn:
            conn.execute(text(sql))

    def _tamper_for_test(self, *, seq: int, payload: dict[str, Any]) -> None:
        """Bypass the app + triggers to simulate direct-DB tampering."""
        with self._engine.begin() as conn:
            conn.execute(text("DROP TRIGGER IF EXISTS audit_log_no_update"))
            conn.execute(
                text("UPDATE audit_log SET payload = :p WHERE seq = :s"),
                {"p": canonical_json(payload), "s": seq},
            )
            self._install_append_only_triggers_conn(conn)

    def _install_append_only_triggers_conn(self, conn) -> None:
        conn.execute(text(
            "CREATE TRIGGER IF NOT EXISTS audit_log_no_update "
            "BEFORE UPDATE ON audit_log BEGIN "
            "SELECT RAISE(ABORT, 'audit_log is append-only'); END;"))
```

> Note during implementation: simplify the test seams if the trigger-bypass dance proves awkward — the load-bearing assertions are (a) no public mutation API, (b) triggers reject UPDATE/DELETE, (c) `verify_integrity()` returns False after an out-of-band payload edit. Adjust the test helper to whatever cleanly demonstrates (c).

- [ ] **Step 4: Run them, confirm they pass** — `uv run pytest tests/store -v` → PASS.

---

## Task 5 — Opaque SEI-ready entity-key (WP-0.3)

**Files:**
- Create: `src/legis/identity/__init__.py`, `src/legis/identity/entity_key.py`
- Test: `tests/identity/test_entity_key.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/identity/test_entity_key.py
from legis.identity.entity_key import EntityKey

def test_from_locator_is_not_identity_stable():
    k = EntityKey.from_locator("clarion:func:mod.foo")
    assert k.identity_stable is False
    assert k.value == "clarion:func:mod.foo"

def test_from_sei_is_identity_stable():
    k = EntityKey.from_sei("clarion:eid:01J...")
    assert k.identity_stable is True
    assert k.value == "clarion:eid:01J..."

def test_locator_to_sei_is_a_value_swap_not_a_schema_change():
    loc = EntityKey.from_locator("clarion:func:mod.foo")
    sei = EntityKey.from_sei("clarion:eid:01J...")
    # Same serialized shape; only value + identity_stable differ.
    assert set(loc.to_dict().keys()) == set(sei.to_dict().keys())

def test_round_trips_through_dict():
    k = EntityKey.from_sei("clarion:eid:01J...")
    assert EntityKey.from_dict(k.to_dict()) == k

def test_key_is_opaque_no_parse_api():
    k = EntityKey.from_locator("clarion:func:mod.foo")
    # Opacity discipline: the key offers no structural accessors.
    for forbidden in ("parse", "split", "components", "plugin_id", "kind", "qualname"):
        assert not hasattr(k, forbidden)
```

- [ ] **Step 2: Run them, confirm they fail** — FAIL.

- [ ] **Step 3: Implement**

```python
# src/legis/identity/entity_key.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class EntityKey:
    """Opaque cross-tool entity identity. Holds a locator today and an SEI
    later; consumers MUST NOT parse `value`. Switching locator->SEI is a value
    change with no schema change (the SEI-shape-independence guarantee)."""
    value: str
    identity_stable: bool

    @classmethod
    def from_locator(cls, locator: str) -> "EntityKey":
        return cls(value=locator, identity_stable=False)

    @classmethod
    def from_sei(cls, sei: str) -> "EntityKey":
        return cls(value=sei, identity_stable=True)

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "identity_stable": self.identity_stable}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EntityKey":
        return cls(value=d["value"], identity_stable=bool(d["identity_stable"]))
```

- [ ] **Step 4: Run them, confirm they pass** — PASS.

---

## Task 6 — Core override record + store integration (WP-0.4)

**Files:**
- Create: `src/legis/records/__init__.py`, `src/legis/records/override_record.py`
- Test: `tests/records/test_override_record.py`

The chill-cell override record: `policy`, `entity_key` (EntityKey), `rationale`, `agent_id`, `recorded_at`, and `identity_stable` (mirrored from the key for query convenience). Serializes to a flat dict for the store. Judge fields (Sprint 2) and HMAC fields (Sprint 3) are **additive** — the record carries an open `extensions: dict` so later sprints add fields without a schema reshape.

- [ ] **Step 1: Write the failing tests**

```python
# tests/records/test_override_record.py
from legis.identity.entity_key import EntityKey
from legis.records.override_record import OverrideRecord
from legis.store.audit_store import AuditStore

def make_record(**over):
    base = dict(
        policy="no-secret-in-log",
        entity_key=EntityKey.from_locator("clarion:func:mod.foo"),
        rationale="boundary validated by test_x",
        agent_id="agent-1",
        recorded_at="2026-06-01T00:00:00+00:00",
    )
    base.update(over)
    return OverrideRecord(**base)

def test_record_mirrors_identity_stable_from_key():
    r = make_record()
    assert r.identity_stable is False
    r2 = make_record(entity_key=EntityKey.from_sei("clarion:eid:x"))
    assert r2.identity_stable is True

def test_record_persists_through_store_and_round_trips(tmp_path):
    s = AuditStore(f"sqlite:///{tmp_path/'audit.db'}")
    seq = s.append(make_record().to_payload())
    stored = s.read_all()[0].payload
    assert stored["policy"] == "no-secret-in-log"
    assert stored["entity_key"]["value"] == "clarion:func:mod.foo"
    assert stored["identity_stable"] is False
    assert s.verify_integrity() is True

def test_judge_and_hmac_fields_are_additive_not_a_reshape(tmp_path):
    # Sprint 2/3 fields land in `extensions` with no change to the core schema.
    r = make_record(extensions={"judge_verdict": "ACCEPTED", "judge_model": "m"})
    payload = r.to_payload()
    assert payload["extensions"]["judge_verdict"] == "ACCEPTED"
    # Core fields unchanged in name/shape:
    assert set(payload) >= {"policy", "entity_key", "rationale", "agent_id",
                            "recorded_at", "identity_stable", "extensions"}
```

- [ ] **Step 2: Run them, confirm they fail** — FAIL.

- [ ] **Step 3: Implement**

```python
# src/legis/records/override_record.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from legis.identity.entity_key import EntityKey

@dataclass(frozen=True)
class OverrideRecord:
    """Chill-cell recordable override (Sprint 2 = WP-2.1). Judge fields
    (Sprint 2) and HMAC/binding fields (Sprint 3) attach via `extensions`,
    keeping the core schema stable across the 2x2."""
    policy: str
    entity_key: EntityKey
    rationale: str
    agent_id: str
    recorded_at: str
    extensions: dict[str, Any] = field(default_factory=dict)

    @property
    def identity_stable(self) -> bool:
        return self.entity_key.identity_stable

    def to_payload(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "entity_key": self.entity_key.to_dict(),
            "rationale": self.rationale,
            "agent_id": self.agent_id,
            "recorded_at": self.recorded_at,
            "identity_stable": self.identity_stable,
            "extensions": dict(self.extensions),
        }
```

- [ ] **Step 4: Run them, confirm they pass** — PASS.

---

## Task 7 — Full-suite verification

- [ ] **Step 1: Run the whole suite** — `uv run pytest -v` → all green.
- [ ] **Step 2: Confirm the service runs** — `uv run uvicorn legis.api.app:create_app --factory --port 8099` starts; `curl localhost:8099/health` returns the ok body. (Stop the server after.)
- [ ] **Step 3: Sanity-check exit criteria** against the sprint plan's Sprint 0 WP exit criteria (append-only enforced at DB level; integrity detects tampering; entity-key opaque + SEI-swap is value-only; chill override round-trips; judge/HMAC fields additive).

---

## Self-Review

- **WP-0.1** → Tasks 1–2 (ADR + runnable health service).
- **WP-0.2** → Task 4 (append-only, trigger-enforced, hash-chained, integrity check).
- **WP-0.3** → Task 5 (opaque SEI-ready entity-key, value-swap guarantee, no-parse).
- **WP-0.4** → Task 6 (core override record, store round-trip, additive extension path).
- Shared primitives (clock, canonical JSON) factored as leaves (Tasks 2–3).
- No placeholders; every code step shows the code; types are consistent across tasks (`EntityKey`, `OverrideRecord`, `AuditStore`, `AuditRecord`).
