---
id: TASK-CORE-006
title: Update DOEFF014 Linter Rule for Native try-except
status: done
project: "[[PROJECT-CORE-001]]"
created: 2025-12-07
completed: 2025-12-07
tags: [task, core, linter]
---

# TASK-CORE-006 — Update DOEFF014 Linter Rule for Native try-except

## Overview

Update the DOEFF014 linter rule to reflect that native try-except now works in @do functions. Change from a warning against try-except to an informational suggestion about effect-based alternatives for complex cases.

## Changes Made

### 1. Rule Implementation (`packages/doeff-linter/src/rules/doeff014_no_try_except.rs`)

- Changed severity from `Warning` to `Info`
- Updated message:
  - **Before**: "Avoid using try-except in @do functions..."
  - **After**: "Native try-except works in @do functions. For complex error handling, consider effect-based alternatives..."
- Updated description to reflect new behavior

### 2. Documentation (`packages/doeff-linter/docs/rules/DOEFF014.md`)

Complete rewrite of the rule documentation:

- Updated title to "Consider Effect-Based Error Handling"
- Added examples of native try-except usage (now supported)
- Added examples of effect-based alternatives for complex cases
- Added "When to Use Each Approach" decision table
- Updated suppression instructions

## Related

- [[PROJECT-CORE-001]] — Native try-except Support
- [[SPEC-CORE-001]] — Specification
- [GitHub Issue #2](https://github.com/CyberAgentAILab/doeff/issues/2)

## Verification

- Linter builds successfully
- Rule triggers as Info level (not Warning)
- Documentation renders correctly
