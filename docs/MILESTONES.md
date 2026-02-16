# doeff Publication Milestones

Tracking document for publishing doeff as a production-ready Python library.

**Target**: `pip install doeff` works, docs are fresh, landing page is live, PyCon JP 2026 talk submitted.

**PyCon JP 2026**: August 21–22, Hiroshima. CFP expected ~April–May 2026.

---

## M0: Legal & Identity Cleanup

Must complete before anything goes public. Gates all other milestones.

### M0.1 — Move repo to proboscis org
- [x] Transfer GitHub repo from legacy org to proboscis (main+tags now aligned, origin points to proboscis)
- [x] Update all GitHub URLs across the codebase (see M0.3)

### M0.2 — Update LICENSE
- [x] Remove legacy org copyright line from LICENSE
- [x] Replace with appropriate copyright holder

### M0.3 — Purge all legacy-org references
45 occurrences across 19 files. All are GitHub URLs or copyright strings.

| Location | Count | Pattern |
|----------|-------|---------|
| `packages/*/pyproject.toml` (10 files) | ~30 | `github.com/<legacy-org>/doeff` in `[project.urls]` |
| `docs/index.md` | 4 | GitHub links |
| `ide-plugins/vscode/doeff-runner/package.json` | 1 | git repo URL |
| `ide-plugins/neovim/doeff.nvim/README.md` | 4 | plugin install URLs |
| `specs/effects/SPEC-EFF-003-writer.md` | 1 | issue link |
| `specs/effects/SPEC-EFF-004-control.md` | 1 | issue link |
| `packages/doeff-seedream/README.md` | 1 | link |
| `packages/doeff-openrouter/README.md` | 1 | link |
| `packages/doeff-openai/README.md` | 1 | link |
| `packages/doeff-linter/docs/rules/DOEFF014.md` | 1 | issue link |

### M0.4 — Purge all legacy project-name mentions
7 occurrences across 4 files:

- [x] `README.md:340` — remove old project-name attribution line
- [x] `docs/index.md:221` — same
- [x] `doeff/_vendor.py:2,4` — source attribution comments
- [x] `tests/misc/test_segmentation_pragmo.py.skip` — dead test with legacy imports → delete file

### M0.5 — Purge secrets
- [x] Remove local `packages/doeff-openrouter/.env` and confirm no committed OpenRouter key in git history
- [x] Add `.env` to root `.gitignore` (currently not covered)
- [x] Run `git log --all -p -- '*.env'` to check if key was ever committed to history
- [x] No other real secrets found (all others are fake/placeholder values in tests)

### M0.6 — Purge deprecated runtime terminology from public files
Only 2 files outside VAULT:

- [x] `docs/positioning/domain-angles/card-game-simulation.md` — remove old runtime terminology
- [x] `.semgrep.yaml:373` — update guard rule referencing deprecated runtime-module imports

### M0.7 — Remove VAULT symlink
- [x] Remove `VAULT` symlink from git (`git rm VAULT`)
- [x] Optionally add `VAULT` to `.gitignore` as defensive measure
- [x] VAULT contents already live in separate `doeff-VAULT` repo (since commit cf178aa)

### M0.8 — Drop phantom dependencies
Zero-risk removals — these are never imported:

- [x] Remove `returns>=0.22.0` from `pyproject.toml` (0 imports — doeff has own `Result`/`Maybe` in `_vendor.py`)
- [x] Remove `cytoolz>=1.0.0` from `pyproject.toml` (0 imports)
- [x] Move `beartype>=0.19.0` from `dependencies` to `[dependency-groups] dev` only (only used in 1 test file)
- [x] Consider making `phart>=1.1.4` optional (already has `try/except ImportError` guard)
- [x] Investigate and likely remove `ray>=2.0.0` from optional extras + dev (0 imports found)
- [x] Run `uv sync && uv run pytest` to verify (suite executes; pre-existing failure remains outside M0 dependency changes)

---

## M1: Release Infrastructure

Blocked on M0. Goal: `pip install doeff` works.

### M1.1 — doeff-vm platform wheels
The single biggest blocker. doeff-vm is a Rust/PyO3 extension — needs pre-built wheels on PyPI.

- [x] Create `.github/workflows/build-vm.yml` (mirror `build-indexer.yml` pattern)
- [x] Build wheels for: linux x86_64/aarch64, macos x86_64/aarch64, windows x86_64
- [ ] Configure PyPI OIDC trusted publishing for doeff-vm
- [ ] Publish doeff-vm to PyPI
- [ ] Verify: `pip install doeff-vm` works on all platforms

### M1.2 — Fix version mismatch (PUB-003)
- [x] `pyproject.toml` says `0.2.0`, `doeff/__init__.py` says `0.1.7`
- [x] Choose single source of truth (recommend `importlib.metadata` or hatch-vcs)
- [x] Align version everywhere

### M1.3 — CI: pytest on 3.10/3.11/3.12 (PUB-002)
Current CI only runs Python version compatibility check — no actual pytest.

- [x] Extend `.github/workflows/python-compatibility.yml` or create new workflow
- [x] Run `uv run pytest` on Python 3.10, 3.11, 3.12
- [x] Include doeff-vm build in CI (Rust dependency)

### M1.4 — CI: tag-to-publish workflow (PUB-004)
- [x] Create `.github/workflows/publish.yml`
- [ ] OIDC trusted publishing for doeff on PyPI
- [x] Publish workspace deps in order: doeff-vm → doeff-indexer → doeff
- [x] Trigger on tag push (e.g., `v0.2.0`)

### M1.5 — Consolidate repo URLs (PUB-005)
31+ files have split-brain: some point to legacy-org URLs, others to `proboscis/doeff`.

- [x] After M0.1 (repo transfer), update ALL URLs to canonical location
- [x] Root `pyproject.toml`
- [x] All `packages/*/pyproject.toml`
- [x] All `Cargo.toml` files
- [x] IDE plugin configs
- [x] Docs and specs

### M1.6 — Fix package name typo (PUB-007)
- [x] Rename `doeff-effect-anlyzer` → `doeff-effect-analyzer` (permanent once on PyPI)

### M1.7 — Verify workspace source overrides
- [x] Confirm `[tool.uv.sources]` workspace paths in pyproject.toml don't leak into built sdist/wheel
- [x] Test: build wheel, install in clean venv, verify `import doeff` works

### M1.8 — First publish
- [x] Draft release runbook and manual blockers in `docs/release-publish-runbook.md`
- [ ] Publish doeff to PyPI (PUB-001)
- [ ] Verify: `pip install doeff` in clean venv → `import doeff` → `doeff.run(...)` works

---

## M2: Type Quality

Can start in parallel with M1.

### M2.1 — Pyright config baseline
No pyrightconfig.json exists. Running at default strictness.

**Core `doeff/` package: 95 diagnostics (53 errors, 42 warnings)**

| Rule | Count | Action |
|------|-------|--------|
| `reportUnsupportedDunderAll` | 40 | Suppress — Rust VM `__all__` exports invisible to pyright |
| `reportAttributeAccessIssue` | 20 | Investigate — mix of real issues and Rust binding noise |
| `reportArgumentType` | 9 | Fix — likely real type mismatches |
| `reportReturnType` | 7 | Fix — real return type issues |
| `reportUndefinedVariable` | 4 | Fix — likely real bugs |
| Other | 15 | Triage individually |

- [ ] Create `pyrightconfig.json` with `reportUnsupportedDunderAll: false` (eliminates 40 diagnostics)
- [ ] Fix the ~20 real errors (`reportArgumentType` + `reportReturnType` + `reportUndefinedVariable`)
- [ ] Target: zero errors on `doeff/` core at basic strictness

### M2.2 — Package-level pyright triage
**Workspace packages: 418 diagnostics**

| Top rule | Count | Action |
|----------|-------|--------|
| `reportMissingImports` | 224 | Fix pyright config (extraPaths, workspace resolution) |
| `reportReturnType` | 83 | Fix real type issues |
| `reportArgumentType` | 38 | Fix real type issues |

Top offenders: doeff-vm (68), doeff-gemini (56), doeff-openai (43), doeff-indexer (42)

- [ ] Fix pyright workspace config to resolve 224 missing import errors
- [ ] Triage remaining ~194 real type errors across packages

### M2.3 — py.typed marker
- [ ] Add `py.typed` marker file to `doeff/` package
- [ ] Verify downstream type checking works: create test project, `pip install doeff`, run pyright

---

## M3: Professional Polish

Blocked on M1 (need PyPI package for badges, links).

### M3.1 — CHANGELOG (PUB-006)
Current `CHANGELOG.md` is a stub with only "Unreleased" section.

- [ ] Document 0.2.0 highlights (Rust VM, 28+ effects, CLI, generator do-notation, handler stacking)
- [ ] Follow Keep a Changelog format

### M3.2 — README badges
- [ ] PyPI version badge
- [ ] CI status badge
- [ ] Python versions badge
- [ ] License badge

### M3.3 — CONTRIBUTING.md
- [ ] Development setup instructions
- [ ] Code style guide (ruff, pyright)
- [ ] PR process
- [ ] Architecture overview for contributors

### M3.4 — Hosted documentation
No docs build system exists (no mkdocs.yml, no sphinx).

- [ ] Set up mkdocs (with material theme) or similar
- [ ] Deploy to GitHub Pages or readthedocs
- [ ] Link from README and PyPI metadata

---

## M4: Documentation Quality

### M4.1 — Fix broken internal links
5 broken links across 4 docs:

- [ ] `04-async-effects.md` → `21-effects-matrix.md` (does not exist)
- [ ] `04-async-effects.md` → `../specs/effects/SPEC-EFF-005-concurrency.md` (does not exist)
- [ ] `05-error-handling.md` → `21-effects-matrix.md` (does not exist)
- [ ] `14-cli-auto-discovery.md` → `specs/cli-auto-discovery/` (does not exist)
- [ ] `19-agent-tutorial.md` → `18-agent-session-management.md` (does not exist)

### M4.2 — Update deprecated ProgramInterpreter examples
8 docs still use `ProgramInterpreter` / `ExecutionContext` in code examples:

| File | Severity | Occurrences |
|------|----------|-------------|
| `14-cli-auto-discovery.md` | HIGH | 12 |
| `15-cli-script-execution.md` | HIGH | 10 |
| `cache.md` | MEDIUM | 3 |
| `seedream.md` | MEDIUM | 4 |
| `gemini_client_setup.md` | MEDIUM | 2 |
| `gemini_cost_hook.md` | MEDIUM | 2 |
| `09-advanced-effects.md` | LOW | 1 |
| `13-api-reference.md` | LOW | 1 (`ExecutionContext` section) |

- [ ] Rewrite all examples to use `run()` / `arun()` + `default_handlers()`

### M4.3 — Fix naming inconsistency
- [ ] Standardize on `arun` (not `async_run`) across all docs
- [ ] `01-getting-started.md` and `02-core-concepts.md` reference `async_run`

### M4.4 — Write missing critical docs
Biggest structural gaps:

- [ ] **WithHandler tutorial** — handler stacking, `Delegate()`, `Resume(k, value)` patterns
- [ ] **Custom effects guide** — full lifecycle: define → handler → register → use
- [ ] **Handler writing tutorial** — handler function signature and conventions
- [ ] **Testing guide** — how to mock effects, test handlers, use `WithHandler` for test stubs
- [ ] **Migration guide (standalone)** — comprehensive `ProgramInterpreter` → `run`/`arun` migration

### M4.5 — Clean up stale artifacts
- [ ] Delete empty legacy runtime examples directory
- [ ] Delete or archive `tests/misc/test_segmentation_pragmo.py.skip`
- [ ] Update `benchmarks/benchmark_runner.py` (uses deprecated `ProgramInterpreter`)
- [ ] Fix duplicate `__all__` entries in `doeff/__init__.py` (`CacheLifecycle`, `CachePolicy`, `CacheStorage`)
- [ ] Resolve `docs/filesystem-effect-architecture.md` TODO checklist (8 unchecked items)

---

## M5: Launch

Blocked on M3 + M4.

### M5.1 — Landing page
Startup-style single page (like Mojo).

- [ ] Hero: "Algebraic Effects for Python" + compelling code snippet
- [ ] Key value props: testability, replay, handler composition
- [ ] Interactive / animated code examples or side-by-side before/after
- [ ] Framework: Astro / Next.js on Vercel, or static GitHub Pages
- [ ] Domain: TBD (doeff.dev? doeff.io? effects.py?)

### M5.2 — Blog post
- [ ] Announce doeff 0.2.0 on PyPI
- [ ] Key narrative: "effects are the next step beyond DI"
- [ ] Concrete before/after examples
- [ ] Link to getting-started docs
- [ ] Target: dev.to, Python community blogs, Hacker News

### M5.3 — PyCon JP 2026 talk proposal
Conference: August 21–22, 2026 in Hiroshima. CFP expected April–May 2026.

- [ ] Format: 30-minute talk (no paper/proceedings track)
- [ ] Draft title and abstract by March 2026
- [ ] Prepare demo: live coding showing effect handler swapping
- [ ] Key angle: what effects uniquely enable that DI/monads cannot
- [ ] Submit when CFP opens (~April 2026)

### M5.4 — Getting-started content refresh
Current state: core tutorials (01-12) are mostly current. Specialized docs are stale.

- [ ] Ensure every numbered doc (01-20) uses current API
- [ ] Add runnable "hello world" example to `examples/`
- [ ] Create `examples/README.md` with descriptions
- [ ] Test all examples against latest doeff version

---

## Dependency Graph

```
M0 (legal) ─────────────────────────────────┐
  ├─ M0.1 repo transfer                     │
  ├─ M0.2 license                            │
  ├─ M0.3 legacy-org purge ← M0.1           │
  ├─ M0.4 legacy-name purge                 │
  ├─ M0.5 secrets (IMMEDIATE)               │
  ├─ M0.6 runtime-term purge                │
  ├─ M0.7 VAULT symlink                     │
  └─ M0.8 drop phantom deps                 │
                                             ▼
M1 (release infra) ─────────────────────────┐
  ├─ M1.1 doeff-vm wheels (BIGGEST BLOCKER) │
  ├─ M1.2 version mismatch                  │
  ├─ M1.3 CI pytest                         │
  ├─ M1.4 CI publish                        │
  ├─ M1.5 URL consolidation ← M0.1          │
  ├─ M1.6 package name typo                 │
  ├─ M1.7 verify workspace sources          │
  └─ M1.8 first publish ← all above        │
                                             ▼
M2 (types) ──── can start parallel with M1   │
  ├─ M2.1 pyrightconfig + core fixes        │
  ├─ M2.2 package pyright triage            │
  └─ M2.3 py.typed                          │
                                             │
M3 (polish) ← M1 ──────────────────────────┐│
  ├─ M3.1 CHANGELOG                        ││
  ├─ M3.2 badges ← M1.8                    ││
  ├─ M3.3 CONTRIBUTING                      ││
  └─ M3.4 hosted docs                      ││
                                            ▼▼
M4 (doc quality) ── can start parallel ──────┐
  ├─ M4.1 broken links                      │
  ├─ M4.2 deprecated examples               │
  ├─ M4.3 naming consistency                 │
  ├─ M4.4 missing critical docs             │
  └─ M4.5 stale artifacts                   │
                                             ▼
M5 (launch) ← M3 + M4 ─────────────────────
  ├─ M5.1 landing page
  ├─ M5.2 blog post
  ├─ M5.3 PyCon JP proposal (deadline ~Apr-May 2026)
  └─ M5.4 getting-started refresh
```

---

## Quick Reference: Existing PUB-* Issues

| ID | Maps to | Title |
|----|---------|-------|
| PUB-001 | M1.8 | Publish doeff to PyPI |
| PUB-002 | M1.3 | Add CI test job |
| PUB-003 | M1.2 | Fix version mismatch |
| PUB-004 | M1.4 | Add CI publish workflow |
| PUB-005 | M1.5 | Consolidate repo URLs |
| PUB-006 | M3.1 | Add CHANGELOG |
| PUB-007 | M1.6 | Fix anlyzer typo |
| PUB-008 | M2.1 | Triage pyright errors |
