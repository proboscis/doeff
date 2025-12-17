---
id: TASK-IDEA-002
title: Make PyCharm plugin Gradle build succeed
module: idea
status: completed
priority: medium
due-date:
related-project:
related-spec:
related-feature:
code_path: ide-plugins/pycharm/
created: 2025-12-16
updated: 2025-12-16
tags: [task, ide, intellij, pycharm, gradle, tests]
---

# TASK-IDEA-002 — Make PyCharm plugin Gradle build succeed

## Description

Make `./gradlew build` succeed for `ide-plugins/pycharm/` by default, while leaving IDE test
execution behind an explicit flag until the platform test harness issue is resolved.

## Acceptance Criteria

- [x] `cd ide-plugins/pycharm && ./gradlew build` succeeds.

## Implementation Notes

- `BasePlatformTestCase` requires JUnit classes on the test classpath.
- IDE tests currently fail during platform bootstrap (`SettingsController` missing), so `:test` is
  disabled unless `-PrunIdeTests=true` is provided.

## Subtasks

- [x] Add missing test dependencies (`junit`, `opentest4j`)
- [x] Gate `:test` behind `-PrunIdeTests=true`
- [x] Run `./gradlew build` to verify

## Progress Log

### 2025-12-16
- `./gradlew build` now succeeds; IDE tests can be enabled via `./gradlew test -PrunIdeTests=true`.
