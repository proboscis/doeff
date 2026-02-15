# KPC Spec Extraction — Clean Stale KPC-as-Effect Content

## TL;DR

> **Quick Summary**: Extract KPC content from SPEC-TYPES-001/SPEC-008/SPEC-009 into a new dedicated SPEC-KPC-001 spec that describes the KPC-as-macro model (doeff-13). Clean all stale KPC-as-effect references across 6 spec files.
>
> **Deliverables**:
> - New `specs/core/SPEC-KPC-001-kleisli-program-call-macro.md` — authoritative KPC spec
> - Updated `specs/core/SPEC-TYPES-001-program-effect-separation.md` — stale sections cleaned
> - Updated `specs/vm/SPEC-008-rust-vm.md` — R15-A added, KPC handler pseudo-code superseded
> - Updated `specs/vm/SPEC-009-rust-vm-migration.md` — R9-A added, KPC handler/presets/imports cleaned
> - Updated cascade specs: SPEC-SCHED-001, SPEC-EFF-004
>
> **Estimated Effort**: Medium
> **Parallel Execution**: YES — 3 waves
> **Critical Path**: Task 1 → Tasks 2/3/4 (parallel) → Task 5

---

## Context

### Original Request

Extract all KPC (KleisliProgramCall) content from existing spec files into a dedicated SPEC-KPC-001 spec, and clean stale KPC-as-effect references from SPEC-TYPES-001, SPEC-008, and SPEC-009. This is spec-only work — no code changes.

### Interview Summary

**Key Discussions**:
- KPC is now a call-time macro: `KleisliProgram.__call__()` directly returns a `Call` DoCtrl (which IS a DoExpr). No intermediate KPC type needed at runtime.
- KPC handler is being removed: `KpcHandlerFactory`, `KpcHandlerProgram`, `ConcurrentKpcHandlerProgram`, `kpc` in `default_handlers()`
- **KPC is no longer an effect.** It does not extend `PyEffectBase`. It is not `EffectValue`. It is not a standalone type — `__call__()` returns `Call(Pure(kernel), [DoExpr args], kwargs, metadata)` directly.
- The `KleisliProgramCall` type may be eliminated entirely — metadata goes into `CallMetadata` on the `Call` DoCtrl.
- Unannotated `@do` function args default to `should_unwrap=True`
- Auto-unwrap strategy computed at `KleisliProgram.__call__()` time from annotations
- `classify_yielded` unchanged — KPC no longer appears in classifier (users yield the `Call` DoCtrl)
- `default_handlers()` no longer includes `kpc`
- OPEN QUESTION from Rev 12 (lines 31-36) remains open — auto-unwrap at call time for unannotated EffectBase args

**Research Findings**:
- Grep found 103 KPC/KleisliProgramCall matches across 8 spec files
- SPEC-TYPES-001 has heaviest contamination (section 3 entirely stale, sections 4/5/6/8/9/10/11 partially stale)
- SPEC-008 has 28 KPC references spanning handler pseudo-code, R11-A, implementation notes
- SPEC-009 has 29 KPC references spanning R6-C/R6-D/R7-C, section 3 Program, section 4 Effects, kpc handler section, presets, imports, invariants
- program-architecture.md has extensive KPC references but is explicitly OUT OF SCOPE
- Audit semgrep rules reference KPC — also out of scope

### Metis Review

**Identified Gaps** (all addressed):

1. **SPEC-TYPES-001 scope underestimated** — Originally 8 stale sections, actually 12+. Added section 1.5 table (line 388), section 2 hierarchy (line 426), section 6 taxonomy (lines 971-983), section 7 (line 1023), section 8 migration (multiple subsections), section 9 Q2-Q6+Q11+Q13+Q17, section 10 Q2/Q3.
2. **SPEC-009 scope underestimated** — Originally 2-3 references, actually 29 matches spanning R7-C (line 22), section 3 Program (lines 386-412), section 4 Effects (lines 459-467), kpc handler section (lines 812-858), presets (lines 835-836), default_handlers (lines 841-858), API-15 (line 1028), imports (line 883-886).
3. **SPEC-EFF-004 line 19** — `Perform(KPC(...))` is stale under macro model (macro emits `Call`, not `Perform(KPC)`).
4. **Cross-reference validity** — Wave 2 tasks edit specs that reference each other. Shared cross-reference redirect table provided.
5. **Section 3.2/3.3 classification rules** — Content is valid under macro model (rules unchanged, only WHERE they run changed). Must be preserved in SPEC-KPC-001 before section 3 is superseded.
6. **Section 5.6 metadata flow** — Concept (CallMetadata) still valid; mechanism changed. SPEC-KPC-001 describes new flow.
7. **KPC type identity resolved** — KPC is no longer an effect. Not EffectValue. Does not extend PyEffectBase. `__call__()` returns `Call` DoCtrl directly — KPC as a standalone type may be eliminated.

---

## Work Objectives

### Core Objective

Create a single authoritative SPEC-KPC-001 describing the KPC-as-macro model, then redirect/supersede all stale KPC-as-effect content in SPEC-TYPES-001, SPEC-008, SPEC-009, and cascade specs.

### Concrete Deliverables

1. `specs/core/SPEC-KPC-001-kleisli-program-call-macro.md` — NEW file
2. `specs/core/SPEC-TYPES-001-program-effect-separation.md` — EDITED (heavy)
3. `specs/vm/SPEC-008-rust-vm.md` — EDITED (medium)
4. `specs/vm/SPEC-009-rust-vm-migration.md` — EDITED (medium-heavy)
5. `specs/vm/SPEC-SCHED-001-cooperative-scheduling.md` — EDITED (1 line)
6. `specs/effects/SPEC-EFF-004-control.md` — EDITED (1 line)

### Definition of Done

- [x] No normative KPC handler references (KpcHandlerFactory, KpcHandlerProgram, ConcurrentKpcHandler) outside SPEC-KPC-001 without SUPERSEDED markers
- [x] No normative "extends=PyEffectBase" for KPC outside SPEC-KPC-001 without SUPERSEDED markers
- [x] No normative `Perform(KPC` outside SPEC-KPC-001 without SUPERSEDED markers
- [x] SPEC-KPC-001 exists and contains auto-unwrap classification rules (from old section 3.2/3.3)
- [x] SPEC-KPC-001 contains metadata population description (from old section 5.6, updated for macro model)
- [x] SPEC-KPC-001 describes `__call__()` returning `Call` DoCtrl directly (not a standalone KPC type)
- [x] No spec file has internal contradictions between Rev 12 content and stale content
- [x] KPC removed from EffectValue taxonomy in SPEC-TYPES-001 (section 1.5 table, section 2 hierarchy)
- [x] No normative old-model claims exist outside `[SUPERSEDED]` or `[REVERSED]` markers in IN-SCOPE specs
- [x] All references to "creates a KleisliProgramCall" updated to "returns a Call DoCtrl via macro expansion" in normative content

### Must Have

- SPEC-KPC-001 as the single authoritative KPC reference
- All stale KPC handler references superseded with `[SUPERSEDED BY SPEC-KPC-001]` markers
- New revision tags (R15-A in SPEC-008, R9-A in SPEC-009) documenting the change
- Auto-unwrap classification rules preserved (moved from SPEC-TYPES-001 section 3.2/3.3 to SPEC-KPC-001)
- CallMetadata population description updated for macro model in SPEC-KPC-001
- KPC removed from EffectValue type hierarchy (section 1.5 table, section 2 hierarchy, TH-04)
- Description of `KleisliProgram.__call__()` returning `Call` DoCtrl directly

### Must NOT Have (Guardrails)

- **G1**: MUST NOT renumber SPEC-TYPES-001 sections — audit report cross-references (SA-001 through SA-008) reference section numbers. Section headers stay; bodies get supersession markers.
- **G2**: MUST NOT delete historical changelogs — mark as `[SUPERSEDED BY SPEC-KPC-001]` instead. Historical context must be preserved for audit trail.
- **G3**: MUST NOT edit implementation code — no `.py`, `.rs`, `.toml` files. Spec-only work.
- **G4**: MUST NOT touch audit reports (SA-001 through SA-008) or `specs/features/program-architecture.md`. These are out of scope per explicit decision.
- **G5**: MUST follow existing supersession pattern — use `[SUPERSEDED BY SPEC-KPC-001]` tags consistently (same pattern as SPEC-008's existing `[SUPERSEDED BY R13-C]` tags).
- **G6**: MUST add new revision tags rather than silently editing historical entries.
- **G7**: MUST NOT move section 1.3/1.4 from SPEC-TYPES-001 — these Rev 12 sections stay as summary/context. SPEC-KPC-001 elaborates.
- **G8**: MUST NOT introduce AI-generated commentary, opinions, or "Note:" blocks beyond what the spec pattern requires.
- **G9**: MUST NOT touch valid KPC references (see "Valid References — DO NOT TOUCH" list in each task).

### Deferred Work (Out of Scope)

- `specs/features/program-architecture.md` — Contains extensive stale KPC-as-effect content (40+ references). Out of scope per user decision. Will need separate cleanup.
- Audit reports (SA-001 through SA-008) — Historical artifacts referencing spec at time of audit.
- Implementation code changes (doeff-13) — Separate issue, separate plan.
- `specs/audits/SA-002/semgrep/rules.yml` line 18 — References `KleisliProgramCall` class pattern. Out of scope with audits.

---

## Shared Context for All Tasks

### Cross-Reference Redirect Table

When updating cross-references between specs, use this table:

| Old Reference | New Reference |
|---------------|---------------|
| SPEC-TYPES-001 section 3 (The KPC Handler) | SPEC-KPC-001 |
| SPEC-TYPES-001 section 3.2/3.3 (classification rules) | SPEC-KPC-001 section 3 |
| SPEC-TYPES-001 section 4.6 (KPC metadata) | SPEC-KPC-001 section 5 |
| SPEC-TYPES-001 section 5.6 (KPC handler populates metadata) | SPEC-KPC-001 section 4 |
| SPEC-TYPES-001 Rev 9 (KPC as pyclass) | SPEC-KPC-001 section 8 (Historical) |
| SPEC-008 R11-A (KPC parts) | SPEC-KPC-001 |
| SPEC-009 R6-C/R6-D (KPC dispatch) | SPEC-KPC-001 |
| SPEC-009 kpc handler section | SPEC-KPC-001 |

### KPC Terminology Policy (NORMATIVE)

**The term "KleisliProgramCall" is HISTORICAL TERMINOLOGY in this plan.**

Under the macro model, `KleisliProgram.__call__()` returns a `Call` DoCtrl directly. There is no separate `KleisliProgramCall` runtime type. The term "KPC" / "KleisliProgramCall" is used in this plan ONLY to:
1. Identify stale content that needs supersession markers (e.g., "KPC handler", "KPC is an Effect")
2. Refer to the historical concept in supersession markers and SPEC-KPC-001 Historical section
3. Describe the `KleisliProgram` class (the decorator result) — NOT the call result

**Normative wording for new/updated content**:
- OLD (stale): "creates a KleisliProgramCall" → NEW: "returns a `Call` DoCtrl via macro expansion"
- OLD (stale): "KPC is dispatched to the KPC handler" → NEW: "the `Call` DoCtrl is evaluated directly by the VM"
- OLD (stale): "KPC is an Effect" → NEW: "KPC is a call-time macro"

**When editing existing spec text (Tasks 2-4)**:
- **Normative sections** (current descriptions, requirements): MUST use new terminology
- **Historical/superseded sections** (inside `[SUPERSEDED]` blocks, revision changelogs): MAY retain old terminology with supersession markers — these serve as audit trail
- **Test IDs** (KD-01, KD-02, etc.): Keep the IDs unchanged but update their descriptions

### Normative Language Policy for Superseded Sections

When marking a section as superseded:
- The **section header** is KEPT (for numbering stability per G1)
- The **body** is REPLACED with a redirect block that:
  1. States `[SUPERSEDED BY SPEC-KPC-001]` prominently
  2. Briefly explains what changed (1-2 sentences)
  3. Points to the new authoritative location
- **NO normative old-model claims may remain outside supersession markers.** If text says "KPC is an Effect" without `[SUPERSEDED]` or `[REVERSED]` wrapper, that is a bug.
- Historical changelogs (Rev 9, R6-C, etc.) are KEPT with markers prepended — they are audit trail, not normative.

### Line Number Drift Rule

**IMPORTANT**: Line numbers in this plan are based on pre-edit file state. After Tasks 1 and 2 edit files, line numbers in later tasks may drift. When executing tasks:
- **Prefer section headings/IDs over absolute line numbers** (e.g., "section 4.6" rather than "line 694")
- **Use grep/rg to locate content** rather than jumping to a specific line
- Line numbers are provided as starting hints, not exact targets

### Supersession Marker Format

Use consistently across ALL tasks:

Long form: `[SUPERSEDED BY SPEC-KPC-001 — KPC is now a call-time macro, not a runtime effect]`

Short inline: `[SUPERSEDED BY SPEC-KPC-001]`

For reversed questions: `[REVERSED BY Rev 12 — see SPEC-KPC-001]`

### KPC Type Identity (RESOLVED)

**KPC is no longer an effect.** It does not extend `PyEffectBase`. It is not `EffectValue`.

**`KleisliProgram.__call__()` returns a `Call` DoCtrl directly.** The `Call` IS a DoExpr, giving users full composability (`.map()`, `.flat_map()`, `yield`, `run()`). The `KleisliProgramCall` as a standalone type may be eliminated entirely — metadata goes into `CallMetadata` on the `Call` DoCtrl.

All affected locations get concrete edits:
- SPEC-TYPES-001 line 388 (section 1.5 table): Mark KPC row as no longer EffectValue
- SPEC-TYPES-001 line 426 (section 2 hierarchy): Remove KPC from EffectValue hierarchy
- SPEC-TYPES-001 line 1374 (section 11.1 TH-04): REVERSED — KPC is no longer EffectBase
- SPEC-KPC-001 section 5: Describe `__call__()` returning `Call` DoCtrl directly

---

## Verification Strategy

### Test Decision

- **Infrastructure exists**: N/A — spec-only work, no code
- **User wants tests**: N/A
- **Framework**: N/A

### Automated Verification (grep-based)

Each TODO includes grep-based acceptance criteria that agents can run directly. All verification is automated — no user intervention needed.

| Task | Verification Method |
|------|-------------------|
| 1 (Create SPEC-KPC-001) | File exists, contains required sections, grep for key content |
| 2 (Update SPEC-TYPES-001) | Grep for stale patterns returns 0 normative matches |
| 3 (Update SPEC-008) | Grep for stale patterns returns 0 normative matches |
| 4 (Update SPEC-009) | Grep for stale patterns returns 0 normative matches |
| 5 (Cascade + final) | Grep across all specs returns 0 normative matches |

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately):
  Task 1: Create SPEC-KPC-001 [no dependencies]

Wave 2 (After Wave 1 completes):
  Task 2: Update SPEC-TYPES-001 [depends: Task 1]
  Task 3: Update SPEC-008 [depends: Task 1]
  Task 4: Update SPEC-009 [depends: Task 1]

Wave 3 (After Wave 2 completes):
  Task 5: Fix cascade specs + final verification [depends: Tasks 2,3,4]

Critical Path: Task 1 -> Task 2 -> Task 5
Parallel Speedup: ~30% faster than sequential (Wave 2 parallelism)
```

### Dependency Matrix

| Task | Depends On | Blocks | Can Parallelize With |
|------|------------|--------|---------------------|
| 1 | None | 2, 3, 4 | None (must be first) |
| 2 | 1 | 5 | 3, 4 |
| 3 | 1 | 5 | 2, 4 |
| 4 | 1 | 5 | 2, 3 |
| 5 | 2, 3, 4 | None | None (must be last) |

### Agent Dispatch Summary

| Wave | Tasks | Recommended Agents |
|------|-------|-------------------|
| 1 | Task 1 | delegate_task(category="unspecified-high", load_skills=["python-coding-style"]) |
| 2 | Tasks 2, 3, 4 | 3x delegate_task(category="unspecified-high", load_skills=["python-coding-style"], run_in_background=true) |
| 3 | Task 5 | delegate_task(category="quick", load_skills=["python-coding-style"]) |

---

## TODOs

### Task 1: Create SPEC-KPC-001 — Dedicated KPC Specification

- [x] 1. Create `specs/core/SPEC-KPC-001-kleisli-program-call-macro.md`

  **What to do**:

  Create the authoritative KPC specification. This is the NEW forward-looking spec describing KPC under the macro model (doeff-13). Structure:

  1. **Header + Status**: `# SPEC-KPC-001: KleisliProgramCall — Call-Time Macro Expansion` / `Status: WIP Discussion Draft (Rev 1)`
  2. **section 1 Overview**: KPC is a call-time macro. `KleisliProgram.__call__()` returns a `Call` DoCtrl directly — no intermediate KPC type, no handler dispatch, no `Perform(KPC)`. The `Call` IS a DoExpr, giving full composability. Reference doeff-13.
  3. **section 2 Macro Expansion Semantics**: What happens at `kp(arg1, arg2)` call time. Step-by-step: inspect annotations, build auto-unwrap strategy, emit `Call(Pure(kernel), [DoExpr args], kwargs, metadata)`. Include the code example from SPEC-TYPES-001 section 1.3 lines 295-311.
  4. **section 3 Auto-Unwrap Classification Rules**: COPY from SPEC-TYPES-001 section 3.2 (lines 465-486) and section 3.3 (lines 488-519). These rules are VALID under macro model — only the execution context changed (call-time vs handler dispatch time). Preserve the classification logic exactly.
  5. **section 4 Metadata Population**: ADAPT from SPEC-TYPES-001 section 5.6 (lines 915-945). Under macro model, `CallMetadata` is populated by `KleisliProgram.__call__()` at call time, NOT by the KPC handler. Fields remain the same: `function_name`, `source_file`, `source_line`, `program_call`. Show updated pseudo-code.
  6. **section 5 Return Type and Composability**: `KleisliProgram.__call__()` returns `Call` DoCtrl directly. The `Call` IS a DoExpr. Users get: `.map(f)` -> `Map(Call(...), f)`, `.flat_map(f)`, `yield`, `run()`. The `KleisliProgramCall` as a standalone `#[pyclass]` type may be eliminated — metadata goes into `CallMetadata` on the `Call` DoCtrl.
  7. **section 6 Strategy Caching**: `_build_auto_unwrap_strategy` runs once at decoration time. Strategy cached on `KleisliProgram` instance. No per-call computation.
  8. **section 7 Why KPC is a Macro, Not an Effect**: Reference SPEC-TYPES-001 section 1.4. The fatal flaw: infinite recursion with `@do` handlers. Copy the analysis.
  9. **section 8 What Changed (Historical)**: Summary table of old vs new model. Historical reference to SPEC-TYPES-001 Rev 9. Note: KPC handler removed, default_handlers no longer includes kpc, Perform(KPC) no longer occurs, KPC no longer extends PyEffectBase, `__call__()` returns Call DoCtrl instead of KPC Effect.
  10. **section 9 Open Questions**: Inherit the OPEN QUESTION from SPEC-TYPES-001 Rev 12 lines 31-36 (auto-unwrap at call time for unannotated EffectBase args).
  11. **section 10 References**: Link to SPEC-TYPES-001 (section 1.3, section 1.4), SPEC-008 (R15-A), SPEC-009 (R9-A), doeff-13 issue.

  **Source Material** (read these sections to extract content):
  - `specs/core/SPEC-TYPES-001-program-effect-separation.md` lines 285-340 (section 1.3/1.4 — macro description)
  - `specs/core/SPEC-TYPES-001-program-effect-separation.md` lines 442-619 (section 3 — classification rules in 3.2/3.3, rest is stale handler content)
  - `specs/core/SPEC-TYPES-001-program-effect-separation.md` lines 694-710 (section 4.6 — KPC metadata fields)
  - `specs/core/SPEC-TYPES-001-program-effect-separation.md` lines 915-945 (section 5.6 — metadata population, adapt for macro model)
  - `specs/core/SPEC-TYPES-001-program-effect-separation.md` lines 5-38 (Rev 12 changelog — authoritative summary)

  **Must NOT do**:
  - Do NOT copy stale KPC-as-effect content as normative (only in section 8 Historical)
  - Do NOT invent new requirements — extract and adapt existing spec content only
  - Do NOT include implementation code examples from `.py`/`.rs` files
  - Do NOT touch any other files in this task

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high` — Substantial spec writing requiring careful content extraction and adaptation
  - **Skills**: [`python-coding-style`] — Domain context for doeff patterns and naming conventions

  **Parallelization**:
  - **Can Run In Parallel**: NO (Wave 1 — must complete first)
  - **Blocks**: Tasks 2, 3, 4
  - **Blocked By**: None

  **References**:
  - `specs/core/SPEC-TYPES-001-program-effect-separation.md:1-38` — Rev 12 changelog: authoritative summary
  - `specs/core/SPEC-TYPES-001-program-effect-separation.md:285-340` — section 1.3/1.4: macro semantics (COPY as foundation)
  - `specs/core/SPEC-TYPES-001-program-effect-separation.md:442-519` — section 3 through 3.3: classification rules (COPY 3.2/3.3)
  - `specs/core/SPEC-TYPES-001-program-effect-separation.md:694-710` — section 4.6: KPC metadata fields (COPY)
  - `specs/core/SPEC-TYPES-001-program-effect-separation.md:915-945` — section 5.6: metadata population (ADAPT)
  - `~/repos/doeff-VAULT/issues/doeff-13-do-handler-kpc-infinite-recursion.md` — doeff-13 issue

  **Acceptance Criteria**:

  ```bash
  # File exists
  test -f specs/core/SPEC-KPC-001-kleisli-program-call-macro.md && echo "PASS" || echo "FAIL"

  # Contains auto-unwrap classification rules
  rg -c "should_unwrap" specs/core/SPEC-KPC-001-kleisli-program-call-macro.md
  # Expected: >= 3

  # Contains metadata population section
  rg -c "CallMetadata|function_name|source_file|source_line" specs/core/SPEC-KPC-001-kleisli-program-call-macro.md
  # Expected: >= 4

  # Contains macro expansion — Call DoCtrl
  rg -c "Call.Pure.kernel" specs/core/SPEC-KPC-001-kleisli-program-call-macro.md
  # Expected: >= 1

  # Describes __call__ returning Call DoCtrl
  rg -c "__call__|returns.*Call.*DoCtrl|returns.*Call.*DoExpr" specs/core/SPEC-KPC-001-kleisli-program-call-macro.md
  # Expected: >= 2

  # References doeff-13
  rg -c "doeff-13" specs/core/SPEC-KPC-001-kleisli-program-call-macro.md
  # Expected: >= 2

  # States KPC is not an effect
  rg -c "not.*extend.*PyEffectBase|no longer.*effect|NOT.*EffectValue" specs/core/SPEC-KPC-001-kleisli-program-call-macro.md
  # Expected: >= 1
  ```

  **Commit**: YES
  - Message: `docs(specs): create SPEC-KPC-001 — dedicated KPC macro expansion spec`
  - Files: `specs/core/SPEC-KPC-001-kleisli-program-call-macro.md`
  - Pre-commit: grep checks above

---

### Task 2: Update SPEC-TYPES-001 — Clean Stale KPC-as-Effect Content

- [x] 2. Update `specs/core/SPEC-TYPES-001-program-effect-separation.md`

  **What to do**:

  This is the heaviest edit. SPEC-TYPES-001 has Rev 12 forward content (section 1.3/1.4 — KEEP) mixed with stale KPC-as-effect content in multiple sections. Clean the stale content while preserving section structure.

  **Section-by-section edit plan:**

  **Section 1.5 Table (line 388)** — `KleisliProgramCall | no | yes`:
  - REPLACE row with: `| KleisliProgramCall | no | ~~yes~~ **no** [Rev 12: KPC is no longer EffectValue — see SPEC-KPC-001] |`

  **Section 2 Hierarchy (line 426)** — KPC under EffectValue:
  - REPLACE with: `~~KleisliProgramCall[T]~~ [Rev 12: removed — KPC is no longer EffectValue, see SPEC-KPC-001]`

  **Section 3 The KPC Handler (lines ~442-619)** — ENTIRELY STALE:
  - Keep section header: `## 3. The KPC Handler`
  - Replace body with redirect block (~10 lines):
    `[SUPERSEDED BY SPEC-KPC-001 — KPC is now a call-time macro, not a runtime effect]`
    Briefly note: This section previously described the KPC handler which dispatched KPC effects through the handler stack. Under the macro model (Rev 12, doeff-13), KPC resolution happens at `KleisliProgram.__call__()` time via macro expansion to a `Call` DoCtrl. The KPC handler is removed.
    Redirect: For auto-unwrap rules (formerly 3.2/3.3), see SPEC-KPC-001 section 3. For metadata (formerly 5.6), see SPEC-KPC-001 section 4.
  - Do NOT preserve 3.2/3.3 inline — they are now in SPEC-KPC-001.

  **Section 4.1 @do contract (lines 636-643)** — PARTIALLY STALE:
  - Line 638: UPDATE — "Calling it returns a `Call` DoCtrl via macro expansion — does NOT execute the body" [replaces "creates a KleisliProgramCall"]
  - Line 639: "KPC is an Effect — dispatched to KPC handler" — REPLACE with macro expansion description
  - Line 640: KEEP ("composable" — still true, Call is DoExpr)
  - Line 642: "KPC handler resolves args" — REPLACE with macro expansion description

  **Section 4.6 KPC metadata (lines 694-710)** — STALE:
  - Replace body with redirect to SPEC-KPC-001 section 5. Keep header.

  **Section 5.6 KPC handler populates metadata (lines 915-945)** — STALE:
  - Replace body with redirect to SPEC-KPC-001 section 4. Keep header.

  **Section 6 Taxonomy table (lines 971-983)** — PARTIALLY STALE:
  - Line 972: KPC -> KPC handler — REPLACE with SUPERSEDED marker
  - Lines 981-983: "KPC is a regular Effect" prose — REPLACE with SUPERSEDED marker

  **Section 7.1 line 1023** — BORDERLINE VALID:
  - "what fields KleisliProgramCall has" — KEEP as-is (VM dumb pipe principle still valid)

  **Section 8 Migration Path (lines 1084-1168)** — PARTIALLY STALE:
  - Add [SUPERSEDED BY Rev 12] markers to all KPC handler, extends=PyEffectBase, and KPC dispatch lines
  - Do NOT delete — only add markers

  **Section 9 Resolved Questions — MULTIPLE STALE:**
  - Q2 (1182): "KPC is an Effect" — Prepend: [REVERSED BY Rev 12]
  - Q3 (1185): "Auto-unwrap strategy is handler's responsibility" — Prepend: [REVERSED BY Rev 12]
  - Q4 (1191): "Default KPC handler resolves sequentially" — Prepend: [REVERSED BY Rev 12]
  - Q5 (1197): "Arg resolution uses Eval" — Prepend: [REVERSED BY Rev 12]
  - Q6 (1204): "Sequential vs concurrent is handler's choice" — Prepend: [REVERSED BY Rev 12]
  - Q11 (1242): "run() requires explicit KPC handler" — Prepend: [REVERSED BY Rev 12]
  - Q13 (1252): KPC in effect list — Add [Rev 12: KPC no longer an effect]
  - Q17 (1285): "KPC handler can pre-resolve" — Prepend: [REVERSED BY Rev 12]
  - Do NOT delete — only prepend reversal markers.

  **Section 10 Open Questions:**
  - Q2 (1308): "run() does NOT auto-include KPC handler" — Add [Rev 12 UPDATE: moot — KPC handler removed]
  - Q3 (1326): "Every @do call becomes an effect dispatch" — Add [Rev 12 UPDATE: macro expansion, not dispatch]

  **Section 11.1 TH-04 (line 1374)** — STALE:
  - "KPC is an instance of EffectBase" — Add: [REVERSED BY Rev 12 — KPC is no longer EffectBase]

  **Section 11.3 KPC dispatch tests (lines 1401-1419):**
  - KD-01 (1407): UPDATE — "calls KleisliProgram.__call__() which returns a Call DoCtrl via macro expansion (not executed immediately)" [aligns with macro model]
  - KD-02 (1408): "dispatched via Perform(KPC(...))" — REPLACE with macro expansion
  - KD-03 (1409): "run(kpc, handlers=[]) fails" — REPLACE: run succeeds, no KPC handler needed
  - KD-04 (1410): KEEP but add note: default_handlers no longer includes kpc

  **Section 11.5 RC-02 (line 1445)** — PARTIALLY STALE:
  - "state+reader+writer+kpc" — REPLACE with: "state+reader+writer [Rev 12: kpc removed]"

  **Valid References — DO NOT TOUCH**:
  - Section 1.3 (lines 285-311), section 1.4 (lines 313-340): Rev 12 content — CORRECT
  - Sections 4.2-4.5, 4.7+, 5.1-5.5, 7, 12: Non-KPC content — CORRECT
  - Section 9 Q1,Q7,Q8,Q9,Q10,Q12,Q14,Q15,Q16,Q18: Non-KPC — CORRECT
  - Section 11.1 (except TH-04), 11.2, 11.4, 11.5, 11.6: Non-KPC — CORRECT

  **Must NOT do**:
  - Do NOT renumber sections
  - Do NOT delete historical changelogs
  - Do NOT modify section 1.3 or 1.4
  - Do NOT modify section 12 Type Validation
  - Do NOT modify non-KPC content

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high` — Heavy editing requiring careful section-by-section changes
  - **Skills**: [`python-coding-style`]

  **Parallelization**:
  - **Can Run In Parallel**: YES (Wave 2, with Tasks 3, 4)
  - **Blocks**: Task 5
  - **Blocked By**: Task 1

  **References**:
  - `specs/core/SPEC-TYPES-001-program-effect-separation.md` — THE file being edited
  - `specs/core/SPEC-KPC-001-kleisli-program-call-macro.md` — Created by Task 1, target for redirects
  - Cross-Reference Table in Shared Context above

  **Acceptance Criteria**:

  ```bash
  # Section 4.1 no longer says "KPC is an Effect"
  rg "KleisliProgramCall is an.*Effect.*dispatched" specs/core/SPEC-TYPES-001-program-effect-separation.md
  # Expected: 0 matches

  # Section 4.6 superseded
  rg -A2 "### 4.6" specs/core/SPEC-TYPES-001-program-effect-separation.md | rg -c "SUPERSEDED"
  # Expected: >= 1

  # Section 5.6 superseded
  rg -A2 "### 5.6" specs/core/SPEC-TYPES-001-program-effect-separation.md | rg -c "SUPERSEDED"
  # Expected: >= 1

  # Section 6 taxonomy: KPC row no longer normatively points to "KPC handler"
  rg "KleisliProgramCall.*KPC handler" specs/core/SPEC-TYPES-001-program-effect-separation.md | rg -v "SUPERSEDED"
  # Expected: 0 matches

  # Section 1.5 table: KPC no longer listed as EffectValue
  rg "KleisliProgramCall.*\| \*\*yes\*\*" specs/core/SPEC-TYPES-001-program-effect-separation.md
  # Expected: 0 matches

  # Section 9 Q2 reversed
  rg -B1 "KPC is an Effect, not a DoCtrl" specs/core/SPEC-TYPES-001-program-effect-separation.md | rg -c "REVERSED"
  # Expected: >= 1

  # Section 11.1 TH-04 reversed
  rg -A1 "TH-04" specs/core/SPEC-TYPES-001-program-effect-separation.md | rg -c "REVERSED"
  # Expected: >= 1

  # Section 11.3 KD-02 updated (no Perform(KPC))
  rg "KD-02" specs/core/SPEC-TYPES-001-program-effect-separation.md | rg -v "REVERSED|SUPERSEDED" | rg -c "Perform"
  # Expected: 0

  # Section 1.3 and 1.4 untouched
  rg -c "### 1.3 KPC is a call-time macro" specs/core/SPEC-TYPES-001-program-effect-separation.md
  # Expected: 1
  rg -c "### 1.4 Why KPC is a macro" specs/core/SPEC-TYPES-001-program-effect-separation.md
  # Expected: 1
  ```

  **Commit**: YES
  - Message: `docs(specs): clean stale KPC-as-effect content from SPEC-TYPES-001`
  - Files: `specs/core/SPEC-TYPES-001-program-effect-separation.md`

---

### Task 3: Update SPEC-008 — Clean KPC Handler References

- [x] 3. Update `specs/vm/SPEC-008-rust-vm.md`

  **What to do**:

  Add R15-A revision tag and supersede KPC handler content.

  **Edit plan:**

  **Add R15-A** (after R14-D, line ~14):
  `| **R15-A** | KPC model | **KPC is a call-time macro, not a runtime effect (doeff-13).** KPC handler removed. KleisliProgramCall no longer extends PyEffectBase. __call__() returns Call DoCtrl directly. See SPEC-KPC-001. |`

  **R11-A (line 50)**: Add SUPERSEDED marker after KPC-specific sentence. Keep non-KPC content.

  **Implementation notes (lines 58-69)**: Add SUPERSEDED markers to KPC handler impl, PyKPC extends=PyEffectBase, and AutoUnwrapStrategy lines.

  **CallMetadata lines 546-554**: KEEP but add R15-A note about metadata populated at call time.

  **Line 693**: KEEP (programs still include KPC conceptually).

  **KPC Handler pseudo-code (lines 1312-1337)**: Add block SUPERSEDED marker. Keep code but mark as historical.

  **Lines 3379, 3577, 3627**: Add SUPERSEDED markers to KPC dispatch/handler references.

  **Lines 4712, 4736, 4747**: KEEP (conceptual KPC references still valid).

  **Must NOT do**: Do NOT delete pseudo-code. Do NOT modify R13-C or R14-D.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high` — Large file (4767 lines), careful targeted edits
  - **Skills**: [`python-coding-style`]

  **Parallelization**: Wave 2 (with Tasks 2, 4). Blocks Task 5. Blocked by Task 1.

  **Acceptance Criteria**:

  ```bash
  rg -c "R15-A" specs/vm/SPEC-008-rust-vm.md
  # Expected: >= 1

  rg "extends=PyEffectBase" specs/vm/SPEC-008-rust-vm.md | rg -i "kpc|KleisliProgramCall" | rg -v "SUPERSEDED"
  # Expected: 0 matches

  rg "KPC.*Effect.*dispatched|dispatched.*KPC" specs/vm/SPEC-008-rust-vm.md | rg -v "SUPERSEDED"
  # Expected: 0 matches
  ```

  **Commit**: YES
  - Message: `docs(specs): add R15-A to SPEC-008 — supersede KPC handler content`
  - Files: `specs/vm/SPEC-008-rust-vm.md`

---

### Task 4: Update SPEC-009 — Clean KPC Handler/Presets/Imports

- [x] 4. Update `specs/vm/SPEC-009-rust-vm-migration.md`

  **What to do**:

  Add R9-A revision tag and clean all 29 KPC handler references.

  **Edit plan:**

  **Add R9-A** (after latest revision):
  `| **R9-A** | KPC model | **KPC is a call-time macro, not a runtime effect (doeff-13).** KPC handler removed from handlers, presets, imports. See SPEC-KPC-001. |`

  **R6-C (line 32)**: REVERSED — KPC not routed through handler pipeline
  **R6-D (line 33)**: SUPERSEDED — KPC no longer extends PyEffectBase
  **R7-C (line 22)**: SUPERSEDED — KPC handler removed from default_handlers

  **Section 3 Program (lines 381-412)**:
  - Lines 386-390: UPDATE comment — "Calling the factory returns a Call DoCtrl (a Program[T]) via macro expansion" [replaces "creates a Program[T] (a KleisliProgramCall)"]
  - Lines 402-404: "VM dispatches KPC to kpc handler" — REPLACE with macro expansion
  - Lines 407-412: "KPC is an effect" — REPLACE with call-time macro description

  **Section 4 Effects (lines 455-467)**:
  - Remove `Perform(KPC(...))` from taxonomy table
  - Replace KPC prose with SUPERSEDED marker

  **kpc handler section (lines 812-858)**: Add SUPERSEDED block. Update presets and default_handlers to remove kpc.

  **Section 8 Imports (lines 862-893)**: Remove kpc from handler imports. Supersede KPC handler export note.

  **API-15 (line 1028)**: SUPERSEDED — kpc removed from default_handlers and presets.

  **NOTE**: RC-02 is in `specs/core/SPEC-TYPES-001-program-effect-separation.md` line 1445 (NOT in SPEC-009). It is handled by Task 2 (update section 11.5 RC-02 to remove +kpc from handler list).

  **Must NOT do**: Do NOT delete revision entries. Do NOT modify section 12 or non-KPC content.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high` — Many scattered edits across 1179 lines
  - **Skills**: [`python-coding-style`]

  **Parallelization**: Wave 2 (with Tasks 2, 3). Blocks Task 5. Blocked by Task 1.

  **Acceptance Criteria**:

  ```bash
  rg -c "R9-A" specs/vm/SPEC-009-rust-vm-migration.md
  # Expected: >= 1

  rg "sync_preset.*kpc|async_preset.*kpc" specs/vm/SPEC-009-rust-vm-migration.md | rg -v "removed|SUPERSEDED|R9-A"
  # Expected: 0 matches

  rg "default_handlers.*kpc" specs/vm/SPEC-009-rust-vm-migration.md | rg -v "removed|SUPERSEDED|R9-A"
  # Expected: 0 matches

  rg "API-15" specs/vm/SPEC-009-rust-vm-migration.md | rg -c "SUPERSEDED"
  # Expected: >= 1

  rg "KPC.*is an effect|KPC.*effect.*dispatched" specs/vm/SPEC-009-rust-vm-migration.md | rg -v "SUPERSEDED|REVERSED|R9-A"
  # Expected: 0 matches
  ```

  **Commit**: YES
  - Message: `docs(specs): add R9-A to SPEC-009 — remove KPC handler from handlers/presets/imports`
  - Files: `specs/vm/SPEC-009-rust-vm-migration.md`

---

### Task 5: Fix Cascade Specs + Final Cross-Spec Verification

- [x] 5. Fix cascade specs and run final verification

  **What to do**:

  **SPEC-SCHED-001 line 1549**:
  - "ConcurrentKpcHandler via Spawn/Gather" — Add SUPERSEDED marker

  **`specs/effects/SPEC-EFF-012-safe.md` line 18**: KEEP AS-IS. KleisliProgramCall is still a valid type name in the context of what Safe accepts.

  **SPEC-EFF-004 line 19**:
  - "lowered to Perform(KPC(...))" — REPLACE: "macro-expanded to Call DoCtrl [Rev 12, SPEC-KPC-001]"

  **Final Cross-Spec Verification** — run comprehensive rg checks:

  **NOTE on verification scope**: Final grep checks MUST exclude out-of-scope files:
  - `specs/features/program-architecture.md` — out of scope (deferred)
  - `specs/audits/` — out of scope
  These files will still contain stale KPC references. That is expected and acceptable.

  ```bash
  # 1. No normative KPC handler references in IN-SCOPE specs (exclude program-architecture.md and audits/)
  rg "KpcHandlerFactory|KpcHandlerProgram|ConcurrentKpcHandler" specs/core/ specs/vm/ specs/effects/ --glob '!*KPC-001*' | rg -v "SUPERSEDED|removed|eliminated|Historical"
  # Expected: 0

  # 2. No normative extends=PyEffectBase for KPC in IN-SCOPE specs
  rg "extends=PyEffectBase" specs/core/ specs/vm/ specs/effects/ | rg -i "kpc|KleisliProgramCall" | rg -v "SPEC-KPC-001|SUPERSEDED|Historical"
  # Expected: 0

  # 3. No normative Perform(KPC) in IN-SCOPE specs
  rg "Perform.KPC" specs/core/ specs/vm/ specs/effects/ --glob '!*KPC-001*' | rg -v "SUPERSEDED|Historical|REVERSED"
  # Expected: 0

  # 4. No normative "dispatched to KPC handler" in IN-SCOPE specs
  rg "dispatched.*KPC handler|KPC handler.*dispatch" specs/core/ specs/vm/ specs/effects/ --glob '!*KPC-001*' | rg -v "SUPERSEDED|removed|REVERSED"
  # Expected: 0

  # 5. SPEC-EFF-004 no longer references Perform(KPC)
  rg "Perform.KPC" specs/effects/SPEC-EFF-004-control.md
  # Expected: 0

  # 6. ConcurrentKpcHandler fully superseded in IN-SCOPE specs
  rg "ConcurrentKpcHandler" specs/core/ specs/vm/ specs/effects/ --glob '!*KPC-001*' | rg -v "SUPERSEDED|removed"
  # Expected: 0

  # NOTE: specs/features/program-architecture.md and specs/audits/ will still have stale KPC refs.
  # This is expected — they are explicitly out of scope (deferred work).
  ```

  All checks should return 0 matches.

  **Recommended Agent Profile**:
  - **Category**: `quick` — 2 small edits + grep verification
  - **Skills**: [`python-coding-style`]

  **Parallelization**: Wave 3 (solo, final). Blocked by Tasks 2, 3, 4.

  **Commit**: YES
  - Message: `docs(specs): fix cascade KPC references in SCHED-001, EFF-004 + final verification`
  - Files: `specs/vm/SPEC-SCHED-001-cooperative-scheduling.md`, `specs/effects/SPEC-EFF-004-control.md`

---

## Commit Strategy

| After Task | Message | Files |
|------------|---------|-------|
| 1 | `docs(specs): create SPEC-KPC-001 — dedicated KPC macro expansion spec` | specs/core/SPEC-KPC-001-*.md |
| 2 | `docs(specs): clean stale KPC-as-effect content from SPEC-TYPES-001` | specs/core/SPEC-TYPES-001-*.md |
| 3 | `docs(specs): add R15-A to SPEC-008 — supersede KPC handler content` | specs/vm/SPEC-008-rust-vm.md |
| 4 | `docs(specs): add R9-A to SPEC-009 — remove KPC handler from handlers/presets/imports` | specs/vm/SPEC-009-*.md |
| 5 | `docs(specs): fix cascade KPC references in SCHED-001, EFF-004 + final verification` | specs/vm/SPEC-SCHED-001-*.md, specs/effects/SPEC-EFF-004-*.md |

---

## Success Criteria

### Final Verification

```bash
# All should return 0 normative matches:
rg "KpcHandlerFactory|KpcHandlerProgram|ConcurrentKpcHandler" specs/ --glob '!*KPC-001*' | rg -v "SUPERSEDED|removed|eliminated|Historical"
rg "extends=PyEffectBase" specs/ | rg -i "kpc|KleisliProgramCall" | rg -v "SPEC-KPC-001|SUPERSEDED|Historical"
rg "Perform.KPC" specs/ --glob '!*KPC-001*' | rg -v "SUPERSEDED|Historical|REVERSED"

# SPEC-KPC-001 exists and is substantive:
wc -l specs/core/SPEC-KPC-001-kleisli-program-call-macro.md
# Expected: >= 150 lines
```

### Final Checklist

- [x] SPEC-KPC-001 exists as authoritative KPC spec
- [x] SPEC-KPC-001 describes __call__() returning Call DoCtrl directly
- [x] All stale KPC-as-effect content superseded (not deleted)
- [x] No internal contradictions in any spec
- [x] Section numbering preserved in SPEC-TYPES-001
- [x] Historical changelogs preserved with supersession markers
- [x] Cross-references updated to point to SPEC-KPC-001
- [x] KPC removed from EffectValue taxonomy
- [x] program-architecture.md documented as deferred (not touched)
