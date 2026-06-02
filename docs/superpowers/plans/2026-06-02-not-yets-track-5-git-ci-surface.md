# Not-Yets Track 5 (WP-A9/A10/A11) — Git/CI Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the git/CI surface gaps — branch upstream/ahead/behind status and pre/post-rename blob identity (WP-A9), a PR-context surface via an injectable provider seam (WP-A9), `rule_set`/`policy_version` round-trip test coverage (WP-A10), and a build-failing override-rate CI check (WP-A11).

**Architecture:** `GitSurface` keeps shelling out to `git` (no dependency); `BranchInfo` gains `upstream`/`ahead`/`behind` (honest `None` when no upstream), `RenameEvidence` gains `old_blob`/`new_blob` (git object SHAs — additive, so Clarion's `/git/renames` parser is unaffected). PR context is a forge concept with no local git source, so it is an **injectable `PullRequestSource` Protocol** (the identity/filigree seam pattern) wired into the API; no forge HTTP is baked in. WP-A11 extends the `legis` CLI with `check-override-rate` (evaluates the override-rate gate over the governance trail and exits non-zero on FAIL) and adds a GitHub Actions workflow that runs it.

**Tech Stack:** Python 3.12 (stdlib `argparse`/`subprocess`), the existing `GitSurface`/`CheckSurface`/`evaluate_override_rate`/`legis.cli`, FastAPI, pytest (warnings-as-errors). No new runtime dependency.

**Implements (design spec `2026-06-02-not-yets-completion-design.md`):** WP-A9 (R-1.1-04, R-1.1-10, R-1.1-14), WP-A10 (R-1.2-04, R-1.2-05), WP-A11 (R-1.3c-17).

**Locked design decisions (do not reopen — user-approved):**
1. **PR context = injectable `PullRequestSource` seam.** legis defines the `PullRequestContext` shape + a read endpoint backed by an injected provider; no forge HTTP baked in (a deployment wires `gh`/GitHub API). Mirrors `identity`/`filigree` client injection. Tested offline against a fake.
2. **Rename pre/post state = blob SHAs.** `RenameEvidence` gains `old_blob`/`new_blob` (git object SHAs via `git rev-parse <sha>^:<old>` / `<sha>:<new>`). Lightweight; Clarion does the body-hash matching it already owns. **Additive only** — `old_path`/`new_path` stay; the WP-6.3 contract test must still pass.
3. **Override-rate CI = CLI subcommand + GitHub Actions.** `legis check-override-rate` exits non-zero on `FAIL`; a `.github/workflows/` file runs it. Portable command, turnkey GitHub wiring.
4. **Branch status degrades honestly.** A branch with no upstream reports `upstream=None`, `ahead=None`, `behind=None` — never a guessed 0/0.

---

## File structure

| File | Change |
|---|---|
| `src/legis/git/models.py` | `BranchInfo` +`upstream`/`ahead`/`behind`; `RenameEvidence` +`old_blob`/`new_blob` |
| `src/legis/git/surface.py` | `branches()` computes upstream + ahead/behind; `renames()` resolves blob SHAs |
| `src/legis/git/pull_request.py` | `PullRequestContext`; `PullRequestSource` Protocol |
| `src/legis/api/app.py` | inject `pull_requests`; `GET /git/pull-requests/{number}` |
| `src/legis/cli.py` | `check-override-rate` subcommand |
| `.github/workflows/override-rate.yml` | runs `legis check-override-rate` |
| `tests/git/test_surface.py` | branch ahead/behind/upstream; rename blob SHAs (+ contract still holds) |
| `tests/git/test_pull_request_api.py` | PR endpoint via fake source; 404 when unwired |
| `tests/checks/test_check_surface.py`, `tests/api/test_check_api.py` | rule_set/policy_version readback |
| `tests/test_cli.py` | check-override-rate exit codes |

---

## WP-A9 — Git/change surface

### Task 1: Branch upstream + ahead/behind

**Files:**
- Modify: `src/legis/git/models.py`, `src/legis/git/surface.py`
- Test: `tests/git/test_surface.py` (append)

- [ ] **Step 1: Write the failing test** (append to `tests/git/test_surface.py`; reuse that file's existing repo-fixture helpers — confirm their names first, e.g. a `_git(repo, *args)` runner and a tmp repo)

```python
def test_branch_reports_upstream_and_ahead_behind(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "f.txt").write_text("1\n")
    _git(repo, "add", "-A"); _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "branch", "work")
    _git(repo, "branch", "--set-upstream-to=master", "work")  # local upstream
    # If the default branch is 'main' not 'master', set-upstream-to=main instead;
    # detect via `git branch --show-current` and use that as work's upstream base.
    _git(repo, "checkout", "-q", "work")
    (repo / "f.txt").write_text("1\n2\n")
    _git(repo, "add", "-A"); _git(repo, "commit", "-q", "-m", "ahead by one")

    surface = GitSurface(str(repo))
    work = next(b for b in surface.branches() if b.name == "work")
    assert work.upstream in ("master", "main")
    assert work.ahead == 1
    assert work.behind == 0
    base = next(b for b in surface.branches() if b.name in ("master", "main"))
    assert base.upstream is None and base.ahead is None and base.behind is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/git/test_surface.py -k upstream -v`
Expected: FAIL — `BranchInfo.__init__() got an unexpected keyword argument` / attribute missing.

- [ ] **Step 3: Write minimal implementation**

In `src/legis/git/models.py`, extend `BranchInfo` (new fields default to `None` so existing
construction sites and `asdict` consumers are unaffected):

```python
@dataclass(frozen=True)
class BranchInfo:
    name: str
    head_sha: str
    is_current: bool
    upstream: str | None = None
    ahead: int | None = None
    behind: int | None = None
```

In `src/legis/git/surface.py` `branches()`, read the upstream via `for-each-ref` and
compute counts with a deterministic `rev-list` when an upstream exists:

```python
    def branches(self) -> list[BranchInfo]:
        current = self._run("branch", "--show-current").strip()
        out = self._run(
            "for-each-ref",
            "--format=%(refname:short)%09%(objectname)%09%(upstream:short)",
            "refs/heads",
        )
        branches: list[BranchInfo] = []
        for line in out.splitlines():
            if not line.strip():
                continue
            name, sha, upstream = (line.split("\t") + ["", "", ""])[:3]
            up = upstream or None
            ahead = behind = None
            if up:
                # rev-list --left-right --count <upstream>...<branch> → "<behind>\t<ahead>"
                counts = self._run_raw(
                    "rev-list", "--left-right", "--count", f"{up}...{name}"
                )
                if counts.returncode == 0:
                    left, _, right = counts.stdout.strip().partition("\t")
                    if left.isdigit() and right.isdigit():
                        behind, ahead = int(left), int(right)
            branches.append(BranchInfo(
                name=name, head_sha=sha, is_current=(name == current),
                upstream=up, ahead=ahead, behind=behind,
            ))
        return branches
```

(The `/git/branches` endpoint uses `asdict(b)` — the new fields ride along automatically.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/git/test_surface.py -v`
Expected: PASS. Then `python -m pytest -q` — full suite green (additive fields; existing branch tests still pass).

- [ ] **Step 5: Commit**

```bash
git add src/legis/git/models.py src/legis/git/surface.py tests/git/test_surface.py
git commit -m "feat(git): branch upstream + ahead/behind (honest None when untracked) (WP-A9)"
```

---

### Task 2: Pre/post-rename blob SHAs

**Files:**
- Modify: `src/legis/git/models.py`, `src/legis/git/surface.py`
- Test: `tests/git/test_surface.py` (append)

- [ ] **Step 1: Write the failing test** (append to `tests/git/test_surface.py`)

```python
def test_renames_carry_pre_and_post_blob_shas(tmp_path):
    repo = tmp_path / "rr"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t"); _git(repo, "config", "user.name", "t")
    (repo / "a.py").write_text("def f():\n    return 1\n" * 5)
    _git(repo, "add", "-A"); _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "b.py").write_text((repo / "a.py").read_text())
    (repo / "a.py").unlink()
    _git(repo, "add", "-A"); _git(repo, "commit", "-q", "-m", "rename a->b")

    [ev] = GitSurface(str(repo)).renames(f"{base}..HEAD")
    assert (ev.old_path, ev.new_path) == ("a.py", "b.py")
    # Pre/post blob object SHAs (40-hex), and identical here (pure rename).
    assert len(ev.old_blob) == 40 and len(ev.new_blob) == 40
    assert ev.old_blob == ev.new_blob
```

> `_git(repo, "rev-parse", ...)` returns stdout; if the test file's helper does not
> return stdout, call `subprocess.run([...], capture_output=True, text=True).stdout`
> directly for the base sha, matching the pattern in `tests/contract/test_git_renames_contract.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/git/test_surface.py -k blob -v`
Expected: FAIL — `RenameEvidence` has no `old_blob`/`new_blob`.

- [ ] **Step 3: Write minimal implementation**

In `models.py`, extend `RenameEvidence` (additive — default `""` so the Clarion contract,
which reads only `old_path`/`new_path`, is unaffected):

```python
@dataclass(frozen=True)
class RenameEvidence:
    commit_sha: str
    old_path: str
    new_path: str
    similarity: int
    old_blob: str = ""
    new_blob: str = ""
```

In `surface.py` `renames()`, after building `old_path`/`new_path`, resolve the blob SHAs
for that commit (pre = parent side, post = commit side):

```python
            old_blob = self._blob(f"{current_sha}~1", old_path)
            new_blob = self._blob(current_sha, new_path)
            evidence.append(
                RenameEvidence(
                    commit_sha=current_sha, old_path=old_path, new_path=new_path,
                    similarity=similarity, old_blob=old_blob, new_blob=new_blob,
                )
            )
```

Add the helper:

```python
    def _blob(self, rev: str, path: str) -> str:
        """The git object SHA of ``path`` at ``rev`` ("" if it cannot be resolved)."""
        result = self._run_raw("rev-parse", f"{rev}:{path}")
        return result.stdout.strip() if result.returncode == 0 else ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/git/test_surface.py -v`
Expected: PASS. Then `python -m pytest tests/contract/test_git_renames_contract.py -q` — **the Clarion contract test must still pass** (additive fields don't change `old_path`/`new_path`). Then `python -m pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add src/legis/git/models.py src/legis/git/surface.py tests/git/test_surface.py
git commit -m "feat(git): rename evidence carries pre/post blob SHAs (additive; WP-A9)"
```

---

### Task 3: PR-context surface via an injectable `PullRequestSource`

**Files:**
- Create: `src/legis/git/pull_request.py`
- Modify: `src/legis/api/app.py`
- Test: `tests/git/test_pull_request_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/git/test_pull_request_api.py
from fastapi.testclient import TestClient

from legis.api.app import create_app
from legis.git.pull_request import PullRequestContext


class FakePullRequests:
    def __init__(self, prs):
        self._prs = prs  # {number: PullRequestContext}

    def get(self, number):
        return self._prs.get(number)


def test_pr_endpoint_returns_injected_context():
    pr = PullRequestContext(number=7, title="Add eval guard", base="main",
                            head="feature/guard", state="open")
    c = TestClient(create_app(pull_requests=FakePullRequests({7: pr})))
    resp = c.get("/git/pull-requests/7")
    assert resp.status_code == 200
    assert resp.json() == {"number": 7, "title": "Add eval guard", "base": "main",
                           "head": "feature/guard", "state": "open"}


def test_pr_endpoint_404_when_unknown():
    c = TestClient(create_app(pull_requests=FakePullRequests({})))
    assert c.get("/git/pull-requests/99").status_code == 404


def test_pr_endpoint_404_when_source_not_wired():
    c = TestClient(create_app())
    assert c.get("/git/pull-requests/7").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/git/test_pull_request_api.py -v`
Expected: FAIL — `ModuleNotFoundError: legis.git.pull_request`; `create_app() got an unexpected keyword argument 'pull_requests'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/legis/git/pull_request.py
"""Pull-request context — an injectable forge seam (WP-A9).

A PR's title/base/head/state are a forge concept (GitHub/GitLab), not local git,
so legis does not fetch them: it defines the shape and consumes an injected
``PullRequestSource`` (the same injection posture as the identity/filigree
clients). A deployment wires a provider backed by ``gh``/the GitHub API; tests
run offline against a fake. legis bakes in no forge HTTP and no GitHub assumption.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class PullRequestContext:
    number: int
    title: str
    base: str
    head: str
    state: str


@runtime_checkable
class PullRequestSource(Protocol):
    def get(self, number: int) -> "PullRequestContext | None": ...
```

In `src/legis/api/app.py`: import, add the `create_app` param, and the route.

```python
from legis.git.pull_request import PullRequestSource
```

```python
    pull_requests: PullRequestSource | None = None,
) -> FastAPI:
```

```python
    @app.get("/git/pull-requests/{number}")
    def get_pull_request(number: int) -> dict:
        if pull_requests is None:
            raise HTTPException(status_code=404, detail="pull-request source not wired")
        pr = pull_requests.get(number)
        if pr is None:
            raise HTTPException(status_code=404, detail=f"no pull request {number}")
        return asdict(pr)
```

(`asdict` is already imported in `app.py` for the other git models.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/git/test_pull_request_api.py -v`
Expected: PASS. Then `python -m pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add src/legis/git/pull_request.py src/legis/api/app.py tests/git/test_pull_request_api.py
git commit -m "feat(git): injectable PullRequestSource + PR-context endpoint (WP-A9)"
```

---

## WP-A10 — Provenance round-trip coverage

### Task 4: `rule_set` / `policy_version` survive readback (test-only)

**Files:**
- Modify: `tests/checks/test_check_surface.py`, `tests/api/test_check_api.py`

This adds **no production code** — it pins that the two provenance fields (persisted +
returned in code) survive a write→read round trip, closing the untested-leg gap
(R-1.2-04/05). A write-path bug nulling either must now fail a test.

- [ ] **Step 1: Write the tests**

Append to `tests/checks/test_check_surface.py`:

```python
def test_rule_set_and_policy_version_round_trip(tmp_path):
    s = surface(tmp_path)
    s.record(make_run(rule_set="wardline@3", policy_version="pv-9"))
    r = s.for_commit("a" * 40)[0]
    assert r.rule_set == "wardline@3"
    assert r.policy_version == "pv-9"


def test_none_provenance_round_trips_as_none(tmp_path):
    s = surface(tmp_path)
    s.record(make_run(run_id="r2", commit_sha="e" * 40, rule_set=None, policy_version=None))
    r = s.for_commit("e" * 40)[0]
    assert r.rule_set is None and r.policy_version is None
```

Append to `tests/api/test_check_api.py` (mirror that file's existing POST/GET helper +
fixture; confirm the readback route — `GET /checks/commit/{sha}`):

```python
def test_check_api_round_trips_rule_set_and_policy_version(tmp_path):
    c = _client(tmp_path)  # use this file's existing client/app helper
    body = {**BASE_CHECK, "rule_set": "wardline@3", "policy_version": "pv-9"}
    assert c.post("/checks", json=body).status_code == 201
    got = c.get(f"/checks/commit/{body['commit_sha']}").json()[0]
    assert got["rule_set"] == "wardline@3"
    assert got["policy_version"] == "pv-9"
```

> Adapt `BASE_CHECK`/`_client` to the actual fixture names in `test_check_api.py`
> (read the file first). The assertion is the point: both fields survive the HTTP round trip.

- [ ] **Step 2: Run to verify**

Run: `python -m pytest tests/checks/test_check_surface.py tests/api/test_check_api.py -v`
Expected: PASS. (If either FAILS, there is a real write-path bug — fix it in `checks/surface.py`, do not weaken the test.)

- [ ] **Step 3: Commit**

```bash
git add tests/checks/test_check_surface.py tests/api/test_check_api.py
git commit -m "test(checks): rule_set + policy_version survive readback (WP-A10)"
```

---

## WP-A11 — Override-rate gate wired into CI

### Task 5: `legis check-override-rate` + GitHub Actions workflow

**Files:**
- Modify: `src/legis/cli.py`
- Create: `.github/workflows/override-rate.yml`
- Test: `tests/test_cli.py` (append)

- [ ] **Step 1: Write the failing test** (append to `tests/test_cli.py`)

```python
def test_check_override_rate_exits_1_on_fail(tmp_path, capsys):
    from legis.clock import FixedClock
    from legis.enforcement.engine import EnforcementEngine
    from legis.store.audit_store import AuditStore
    from legis.enforcement.verdict import Verdict
    from legis.identity.entity_key import EntityKey

    db = f"sqlite:///{tmp_path / 'gov.db'}"
    eng = EnforcementEngine(AuditStore(db), FixedClock("2026-06-02T12:00:00+00:00"))
    # 25 final dispositions, all operator-overrides → rate 1.0 > 0.2 threshold → FAIL.
    for i in range(25):
        eng.record_event({"policy": "p", "entity_key": EntityKey.from_locator(f"x{i}").to_dict(),
                          "extensions": {"judge_verdict": Verdict.OVERRIDDEN_BY_OPERATOR.value}})
    rc = main(["check-override-rate", "--db", db])
    assert rc == 1
    assert "FAIL" in capsys.readouterr().out


def test_check_override_rate_exits_0_when_clean(tmp_path):
    from legis.clock import FixedClock
    from legis.enforcement.engine import EnforcementEngine
    from legis.store.audit_store import AuditStore
    from legis.enforcement.verdict import Verdict
    from legis.identity.entity_key import EntityKey

    db = f"sqlite:///{tmp_path / 'gov.db'}"
    eng = EnforcementEngine(AuditStore(db), FixedClock("2026-06-02T12:00:00+00:00"))
    for i in range(25):  # all ACCEPTED → rate 0.0 → PASS
        eng.record_event({"policy": "p", "entity_key": EntityKey.from_locator(f"x{i}").to_dict(),
                          "extensions": {"judge_verdict": Verdict.ACCEPTED.value}})
    assert main(["check-override-rate", "--db", db]) == 0
```

> Confirm `EnforcementEngine.record_event(payload)` appends a raw event (it does — used by
> the WP-A4 surface_only path). These seed final-disposition records the gate counts.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py -k override_rate -v`
Expected: FAIL — `check-override-rate` is not a known subcommand (argparse error / returns 2).

- [ ] **Step 3: Write minimal implementation**

In `src/legis/cli.py`, register the subcommand in `build_parser` and handle it in `main`:

```python
    rate = subparsers.add_parser(
        "check-override-rate",
        help="Fail (exit 1) if the override-rate gate is FAIL — for CI",
    )
    rate.add_argument(
        "--db", default="sqlite:///legis-governance.db",
        help="Governance store URL (default mirrors the server's DEFAULT_GOVERNANCE_DB)",
    )
```

```python
    if args.command == "check-override-rate":
        from legis.enforcement.lifecycle import GateStatus, evaluate_override_rate
        from legis.governance import params
        from legis.store.audit_store import AuditStore

        res = evaluate_override_rate(
            AuditStore(args.db).read_all(),
            threshold=params.OVERRIDE_RATE_THRESHOLD,
            window=params.OVERRIDE_RATE_WINDOW,
            min_sample=params.OVERRIDE_RATE_MIN_SAMPLE,
        )
        print(f"override-rate gate: {res.status.value} "
              f"(rate={res.rate:.3f}, sample={res.sample_size})")
        return 1 if res.status is GateStatus.FAIL else 0
```

(Keep the `serve` branch and the no-command→2 fallback unchanged.)

Create `.github/workflows/override-rate.yml`:

```yaml
name: override-rate gate
on:
  pull_request:
  push:
    branches: [main]
jobs:
  override-rate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e .
      - name: Enforce override-rate gate
        run: legis check-override-rate
```

> Note: the workflow runs against whatever `legis-governance.db` the CI environment
> provides; in a real deployment the governance trail is produced upstream and made
> available to the job. The gate is build-failing (`legis check-override-rate` exits 1
> on FAIL), which is the WP-A11 exit criterion — the prior state was "observable, not
> build-failing".

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli.py -v`
Expected: PASS. Then `python -m pytest -q` — full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/legis/cli.py .github/workflows/override-rate.yml tests/test_cli.py
git commit -m "feat(cli): check-override-rate subcommand + CI workflow (WP-A11)"
```

---

## Task 6: Docs + full-suite verification

**Files:**
- Modify: `docs/superpowers/specs/2026-06-02-not-yets-completion-design.md`

- [ ] **Step 1:** Append " — ✅ done 2026-06-02" to the WP-A9, WP-A10, WP-A11 headings (under "### Track 5 — Git/CI surface gaps"). Note in the WP-A9 bullet that PR context is the injectable `PullRequestSource` seam (forge fetch is the deployment's) and rename state is blob SHAs.

- [ ] **Step 2: Full suite green, zero warnings**

Run: `python -m pytest -q`
Expected: all green (was 203; +~11 new tests). Confirm count + zero warnings.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-06-02-not-yets-completion-design.md
git commit -m "docs: mark WP-A9/A10/A11 git-CI surface complete"
```

---

## Self-review — WP coverage

| WP | Exit criterion (design spec) | Proven by |
|---|---|---|
| A9 | branch ahead/behind/tracking against `@{u}`; honest None when untracked | Task 1 (`test_branch_reports_upstream_and_ahead_behind`) |
| A9 | a PR-context surface (title/base/head/state) distinct from the CheckRun.pr FK | Task 3 (`test_pr_endpoint_returns_injected_context`); injectable seam, no forge HTTP baked in |
| A9 | rename evidence captures pre/post state; does not break the Clarion `/git/renames` contract | Task 2 (`test_renames_carry_pre_and_post_blob_shas` + contract test still green) |
| A10 | `rule_set`/`policy_version` survive write→readback (a nulling bug now fails a test) | Task 4 (surface + API round-trip tests) |
| A11 | override-rate gate is build-failing in CI (was observable-only) | Task 5 (`check-override-rate` exits 1 on FAIL; `.github/workflows/override-rate.yml` runs it) |

**Out of scope / disclosed:** the GitHub Actions workflow runs against whatever governance trail the CI env provides (producing/seeding that trail in CI is deployment-specific); the `PullRequestSource` provider implementation (gh/GitHub API) is the deployment's — legis ships the seam + a fake-tested endpoint. Other tracks (A12, B-track, C1) per the design spec.
