# Legis Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a docs-first GitHub repository for Legis that explains its role in Loom, its bounded authority, and its planned federation surfaces without pretending the product is already implemented.

**Architecture:** Build the repository as a documentation-first product skeleton. The root README carries the high-level Legis story and links to focused docs under `docs/federation/` and `docs/design/`, while the remaining root files establish minimal contributor hygiene for a planned-but-not-built project.

**Tech Stack:** Markdown, Git, shell verification with `rg` and `git diff --check`

---

## File Structure

- Create: `README.md` — authoritative repository overview, current status, pairwise Loom composition, and links to deeper docs
- Create: `LICENSE` — MIT license for the repo
- Create: `.gitignore` — minimal editor/OS noise exclusions
- Create: `CONTRIBUTING.md` — contribution rules for a design-ready repo
- Create: `docs/federation/README.md` — Legis's Loom participation overview
- Create: `docs/federation/sei-conformance.md` — Legis-specific SEI posture and obligations
- Create: `docs/design/README.md` — index of product design docs
- Create: `docs/design/legis-charter.md` — detailed charter for Legis's role, authority, and near-term scope
- Modify: `README.md` — add final links to the design and federation docs after they exist

### Task 1: Create root repository scaffolding

**Files:**
- Create: `README.md`
- Create: `LICENSE`
- Create: `.gitignore`
- Create: `CONTRIBUTING.md`

- [ ] **Step 1: Create `README.md`**

```markdown
# Legis

Legis is the planned fourth Loom product: the Git/CI and governance side of the suite's common operating picture.

## Status

Legis is **design-ready, not implemented**. This repository exists so Clarion, Filigree, and Wardline can review the intended shape of Legis before runtime code lands.

## What Legis is

Legis is the planned Loom authority for:

- project change provenance,
- branch / commit / pull request context,
- CI and check context, and
- governance and attestation context over change.

## What Legis is not

Legis is not:

- a federation registry,
- a hidden suite runtime,
- a replacement for Clarion's code identity authority,
- a replacement for Filigree's workflow authority, or
- a replacement for Wardline's policy authority.

## How Legis fits into Loom

### Clarion

Clarion remains the authority for code identity and structure. Legis may later provide git-history and rename evidence that Clarion can consume, but Clarion remains the SEI authority.

### Filigree

Filigree remains the authority for issue and workflow state. Legis adds branch, commit, pull request, and check context around that work without taking over issue lifecycle.

### Wardline

Wardline remains the authority for policy findings and dossier truth. Legis contributes project change and CI context that Wardline can cite when attaching findings or gates to real repository state.

## Stable Entity Identity

Legis consumes Stable Entity Identity (SEI) as an opaque identifier. Clarion is the SEI authority. See `docs/federation/sei-conformance.md`.

## Repository layout

- `docs/federation/` - Loom-facing contracts and participation notes
- `docs/design/` - product intent and design notes
- `docs/superpowers/specs/` - approved design specs
- `docs/superpowers/plans/` - implementation plans

## Near-term documents

- `docs/design/legis-charter.md`
- `docs/federation/README.md`
- `docs/federation/sei-conformance.md`

This repo should stay explicit, narrow, and honest about what exists today.
```

- [ ] **Step 2: Verify `README.md` coverage**

Run: `rg -n "planned fourth Loom product|design-ready, not implemented|Clarion|Filigree|Wardline|SEI authority" README.md`

Expected: Matches for status, all three sibling sections, and the SEI posture.

- [ ] **Step 3: Create `LICENSE`**

```text
MIT License

Copyright (c) 2026 John Morrissey

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 4: Create `.gitignore` and `CONTRIBUTING.md`**

```gitignore
.DS_Store
Thumbs.db
.idea/
.vscode/
```

```markdown
# Contributing

Legis is currently a docs-first, design-ready repository. Please keep changes aligned with that status.

## Ground rules

- Keep capability claims honest. Do not add install, CLI, or API documentation for features that do not exist yet.
- Preserve bounded authority language: Legis owns change, git/CI, and governance context; sibling Loom products keep their own authorities.
- When changing federation expectations, update the relevant files in `docs/federation/` and `README.md` together.
- When changing product intent or scope, update `docs/design/legis-charter.md` and the active plan/spec docs together.
- Prefer small, reviewable documentation changes over speculative scaffolding.
```

- [ ] **Step 5: Verify root scaffolding**

Run: `test -f README.md && test -f LICENSE && test -f .gitignore && test -f CONTRIBUTING.md && echo ok`

Expected: `ok`

- [ ] **Step 6: Commit the root scaffolding**

```bash
git add README.md LICENSE .gitignore CONTRIBUTING.md
git commit -m "docs: add Legis repository scaffolding"

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

### Task 2: Add federation documentation

**Files:**
- Create: `docs/federation/README.md`
- Create: `docs/federation/sei-conformance.md`

- [ ] **Step 1: Create `docs/federation/README.md`**

```markdown
# Federation Notes

This directory describes how Legis is expected to participate in Loom as a planned product.

## Legis in the federation

Legis contributes change-oriented context:

- repository and branch state,
- commit and pull request relationships,
- CI/check outcomes, and
- governance or attestation facts about change.

Legis does not replace sibling authorities. Clarion still owns code identity and structure, Filigree still owns workflow state, and Wardline still owns policy findings and dossier truth.

## Current documents

- `sei-conformance.md` - how Legis relates to the Stable Entity Identity standard
```

- [ ] **Step 2: Create `docs/federation/sei-conformance.md`**

```markdown
# Stable Entity Identity (SEI) Conformance Notes

Legis is a **consumer** of Stable Entity Identity (SEI), not the authority.

## Core posture

- Clarion mints, persists, re-binds, and resolves SEI.
- Legis treats SEI as opaque.
- Legis must never derive, parse, or reinterpret SEI structure.

## Planned Legis responsibilities

- Attach governance or attestation facts to SEI when the subject is a code entity.
- Preserve the distinction between identity state and content freshness rather than collapsing them.
- Degrade honestly if Clarion does not advertise SEI capability.

## Possible future contribution to Clarion

Legis may later provide git-history and rename evidence that Clarion can consume during SEI re-binding, but that does not move identity authority out of Clarion.
```

- [ ] **Step 3: Verify federation docs**

Run: `rg -n "Clarion|Filigree|Wardline|opaque|SEI" docs/federation`

Expected: Matches in both files covering Legis's bounded role and SEI posture.

- [ ] **Step 4: Commit the federation docs**

```bash
git add docs/federation/README.md docs/federation/sei-conformance.md
git commit -m "docs: add Legis federation notes"

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

### Task 3: Add product design docs

**Files:**
- Create: `docs/design/README.md`
- Create: `docs/design/legis-charter.md`

- [ ] **Step 1: Create `docs/design/README.md`**

```markdown
# Design Notes

This directory holds Legis-specific design material.

## Current documents

- `legis-charter.md` - product role, authority boundary, and near-term scope
```

- [ ] **Step 2: Create `docs/design/legis-charter.md`**

```markdown
# Legis Charter

## Summary

Legis is the planned fourth Loom product. It is responsible for project change provenance and the git/CI common operating picture.

## Authority boundary

Legis owns:

- branch, commit, and pull request context,
- CI/check context, and
- governance and attestation context over project change.

Legis does not own:

- code identity or structure,
- issue or workflow state, or
- policy findings and dossier truth.

## Operating modes

### Solo mode

Legis can describe repository change and CI state on its own.

### Pair mode

- With Clarion: Legis can supply git-history and rename evidence.
- With Filigree: Legis can connect work state to change state.
- With Wardline: Legis can connect policy findings to change and check context.

### Suite mode

Legis becomes the common operating picture for project change and governance while preserving the authority boundaries of the other Loom products.

## Near-term scope

The initial repository is documentation-first. It should make the intended role reviewable before runtime implementation starts.
```

- [ ] **Step 3: Verify design docs**

Run: `rg -n "Authority boundary|Solo mode|Pair mode|Suite mode" docs/design`

Expected: Matches in `docs/design/legis-charter.md` for all four sections.

- [ ] **Step 4: Commit the design docs**

```bash
git add docs/design/README.md docs/design/legis-charter.md
git commit -m "docs: add Legis product charter"

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

### Task 4: Link, polish, and verify consistency

**Files:**
- Modify: `README.md`
- Modify: `docs/design/README.md`
- Modify: `docs/federation/README.md`

- [ ] **Step 1: Add cross-links from the root README**

Append this section near the bottom of `README.md` if it is not already present:

```markdown
## Working documents

- Design spec: `docs/superpowers/specs/2026-06-01-legis-federation-repo-design.md`
- Implementation plan: `docs/superpowers/plans/2026-06-01-legis-bootstrap.md`
- Product charter: `docs/design/legis-charter.md`
- Federation notes: `docs/federation/README.md`
```

- [ ] **Step 2: Add back-links from the doc indexes**

Add these lines to both `docs/design/README.md` and `docs/federation/README.md`:

```markdown
## Related planning docs

- Spec: `../superpowers/specs/2026-06-01-legis-federation-repo-design.md`
- Plan: `../superpowers/plans/2026-06-01-legis-bootstrap.md`
```

- [ ] **Step 3: Run consistency checks**

Run: `git diff --check && ! rg -n "T[O]DO|TB[D]|stub[[:space:]]text|coming[[:space:]]soon" README.md CONTRIBUTING.md docs`

Expected: no output from either command.

- [ ] **Step 4: Commit the final polish**

```bash
git add README.md docs/design/README.md docs/federation/README.md
git commit -m "docs: cross-link Legis planning docs"

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```
