# Sprint 1 — Operating Picture: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Give legis its standalone value — answer "what changed?" (git surface) and "what is the check state?" (CI/check surface) with no sibling present.

**Architecture:** Two independent surfaces (roadmap WP-1.1 / WP-1.2), mounted on the existing FastAPI app.
- **GitSurface** is a *stateless* reader over a real git repository, implemented by shelling out to `git` (legis is the git interface; zero extra deps; rename detection is native `git -M`). The source of truth is the repo itself — no persistence.
- **CheckSurface** *is* persistent: CI outcomes and PR associations are not in git, so legis records them. Storage is a **proper indexed relational table** (`check_runs`, indexed on commit_sha / branch / pr) — deliberately **not** Sprint 0's append-only hash-chained audit log, which is the governance trail (tamper-evidence), a different concern. Check runs are operational facts, queried by dimension.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy Core + SQLite, subprocess `git`, pytest + TestClient.

**Status:** ✅ COMPLETE — both surfaces implemented test-first; 19 Sprint 1 tests green (37 total with Sprint 0); live service describes legis's own repo and records/serves check runs. See the Sprint 1 commit.

**Note on git parsing:** exact `git` output parsing is firmed up during TDD against real temp repos (built with subprocess in a fixture). The *contracts* below (models + method signatures + API + exit criteria) are fixed; the parsing internals are discovered test-first.

---

## File Structure

- `tests/conftest.py` — `git_repo` fixture: builds a real temp repo (branches, commits, a rename) for git-surface and API tests
- `src/legis/git/__init__.py`, `src/legis/git/models.py`, `src/legis/git/surface.py` — WP-1.1
- `src/legis/checks/__init__.py`, `src/legis/checks/models.py`, `src/legis/checks/surface.py` — WP-1.2
- `src/legis/api/app.py` — extended: `create_app(repo_path=None, check_surface=None)` + git/check routes
- `tests/git/test_git_surface.py`, `tests/checks/test_check_surface.py`, `tests/api/test_git_api.py`, `tests/api/test_check_api.py`

**Boundaries:** `git/` depends only on stdlib (subprocess) + its models. `checks/` depends on SQLAlchemy + its models. `api/` wires them. No cross-dependency between `git/` and `checks/`.

---

## Domain contracts (fixed)

```python
# git/models.py
@dataclass(frozen=True)
class BranchInfo:      name: str; head_sha: str; is_current: bool
@dataclass(frozen=True)
class CommitInfo:      sha: str; author_name: str; author_email: str; message: str
                       committed_at: str; parents: list[str]
                       files_changed: int; insertions: int; deletions: int
@dataclass(frozen=True)
class RenameEvidence:  commit_sha: str; old_path: str; new_path: str; similarity: int

# git/surface.py
class GitSurface:
    def __init__(self, repo_path: str | Path) -> None
    def branches(self) -> list[BranchInfo]
    def commit(self, sha: str) -> CommitInfo          # raises GitError if unknown
    def commits(self, ref: str = "HEAD", limit: int = 50) -> list[CommitInfo]
    def merge_base(self, a: str, b: str) -> str | None
    def renames(self, rev_range: str) -> list[RenameEvidence]

# checks/models.py
class CheckOutcome(str, Enum): PASS / FAIL / SKIPPED / TIMEOUT
@dataclass(frozen=True)
class CheckRun:  check_name: str; run_id: str; commit_sha: str; outcome: CheckOutcome
                 branch: str | None; pr: int | None; ran_against: str | None
                 rule_set: str | None; policy_version: str | None
                 started_at: str | None; finished_at: str | None

# checks/surface.py
class CheckSurface:
    def __init__(self, db_url: str) -> None
    def record(self, run: CheckRun) -> int
    def for_commit(self, sha: str) -> list[CheckRun]
    def for_branch(self, name: str) -> list[CheckRun]
    def for_pr(self, pr: int) -> list[CheckRun]
    def latest_state(self, commit_sha: str) -> dict[str, CheckRun]  # latest run per check_name
```

**Rename evidence is the git half only.** Legis supplies *path* rename evidence with similarity + commit (what `git -M` detects). Symbol-level identity resolution is Clarion's job (it combines this signal with body hashes per SEI spec §3); WP-6.3 re-exposes this surface to Clarion's matcher. The plan does not overclaim symbol-level rename detection.

---

## Task 1 — git_repo test fixture

**Files:** Create `tests/conftest.py`

- [ ] **Step 1:** Write a `git_repo` fixture that, in `tmp_path`, runs `git init`, configures a fixed user, and builds a known history: commit `a.txt`; create branch `feature`; commit `b.txt` on `main`; `git mv a.txt renamed.txt` + commit. Returns the repo path. Use `subprocess.run([...], cwd=repo, check=True, env=...)` with `GIT_*` identity env so commits are clean and warning-free. (No production code yet — a fixture is test infrastructure.)

---

## Task 2 — GitSurface.branches (WP-1.1)

**Files:** Create `git/models.py`, `git/surface.py`; Test `tests/git/test_git_surface.py`

- [ ] **Step 1 (RED):** test `branches()` returns `main` and `feature`, with `is_current` True for `main`.

```python
def test_branches_lists_all_with_current(git_repo):
    s = GitSurface(git_repo)
    by_name = {b.name: b for b in s.branches()}
    assert set(by_name) == {"main", "feature"}
    assert by_name["main"].is_current is True
    assert by_name["feature"].is_current is False
    assert all(len(b.head_sha) == 40 for b in by_name.values())
```

- [ ] **Step 2:** Run → FAIL (module missing).
- [ ] **Step 3 (GREEN):** implement `branches()` via `git for-each-ref --format=... refs/heads` + `git branch --show-current`.
- [ ] **Step 4:** Run → PASS.

## Task 3 — GitSurface.commit + commits (WP-1.1)

- [ ] **Step 1 (RED):** test `commit(head)` returns author, non-empty message, ≥1 files_changed, insertions ≥1, ISO `committed_at`, and that an unknown sha raises `GitError`. Test `commits(limit=2)` returns 2 ordered newest-first.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3 (GREEN):** implement via `git show -s --format=<x1f-delimited>` for metadata (body last, split with maxsplit) + `git show --numstat --format=` for file stats (treat `-` as 0). `commits()` via `git log`.
- [ ] **Step 4:** PASS.

## Task 4 — GitSurface.merge_base + renames (WP-1.1)

- [ ] **Step 1 (RED):** test `merge_base(main, feature)` returns the first commit's sha (their common ancestor); `renames("<first>..main")` (or the rename commit) yields a `RenameEvidence(old_path="a.txt", new_path="renamed.txt", similarity==100)`.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3 (GREEN):** `merge_base` via `git merge-base a b` (None on non-zero exit). `renames` via `git log -M --diff-filter=R --name-status --format=...` parsing `R<sim>\told\tnew`.
- [ ] **Step 4:** PASS.

## Task 5 — CheckSurface record + queries (WP-1.2)

**Files:** Create `checks/models.py`, `checks/surface.py`; Test `tests/checks/test_check_surface.py`

- [ ] **Step 1 (RED):** tests — `record()` then `for_commit(sha)` returns it; `for_branch` / `for_pr` filter correctly; `latest_state(sha)` returns the newest run per `check_name` (insert two runs of the same check; latest wins); outcomes round-trip as `CheckOutcome`.
- [ ] **Step 2:** FAIL (module missing).
- [ ] **Step 3 (GREEN):** SQLite table `check_runs` indexed on `commit_sha`, `branch`, `pr`; NullPool (clean lifecycle, like the audit store). `latest_state` orders by insert seq.
- [ ] **Step 4:** PASS.

## Task 6 — API wiring (WP-1.1 + WP-1.2 over HTTP)

**Files:** Modify `src/legis/api/app.py`; Test `tests/api/test_git_api.py`, `tests/api/test_check_api.py`

- [ ] **Step 1 (RED):** 
  - git API: `GET /git/branches` (injected `repo_path=git_repo`) returns the two branches; `GET /git/commits/{sha}` returns metadata; `GET /git/renames?rev_range=...` returns the rename.
  - check API: `POST /checks` ingests a run; `GET /checks/commit/{sha}` returns it; `GET /checks/branch/{n}` and `GET /checks/pr/{n}` filter.
  - existing `test_health` still passes with the new `create_app` signature (defaults).
- [ ] **Step 2:** FAIL.
- [ ] **Step 3 (GREEN):** `create_app(repo_path=None, check_surface=None)`. Git routes build `GitSurface(repo_path or os.getcwd())`. Check routes use the injected `check_surface`, else lazily build a default (file db) on first use — so the no-arg health test creates no db/warning. Pydantic request/response models for `/checks`.
- [ ] **Step 4:** PASS.

## Task 7 — Full-suite verification

- [ ] **Step 1:** `uv run pytest -q` → all green (Sprint 0 + Sprint 1).
- [ ] **Step 2:** Run the service against legis's own repo: `GET /git/branches` returns this repo's branches (standalone "what changed" proof).
- [ ] **Step 3:** Check Sprint 1 exit criteria line-by-line.

---

## Self-Review (coverage)

- **WP-1.1** (branches / commit metadata / merge-base / rename evidence, over the API) → Tasks 2–4, 6.
- **WP-1.2** (check runs recorded + queried by commit/branch/pr, latest state, over the API) → Tasks 5–6.
- Exit criteria: "what changed on branch X / commit Y" → branches + commit; "rename evidence queryable as structured data" → renames; "what is the check state, against what did each check run" → for_commit + ran_against + latest_state.
- PR *metadata* objects and full multi-forge ingestion are deferred (noted) — Sprint 1 models PR *association* via the `pr` field, satisfying "relationships between outcomes, branches, commits, PRs."
