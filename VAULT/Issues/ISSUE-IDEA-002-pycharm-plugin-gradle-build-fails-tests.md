---
id: ISSUE-IDEA-002
title: PyCharm plugin IDE tests fail under Gradle (SettingsController missing)
module: idea
status: open
severity: medium
related-project:
related-spec:
related-task: TASK-IDEA-002
related-feature:
created: 2025-12-16
updated: 2025-12-16
tags: [issue, ide, intellij, pycharm, gradle, tests]
---

# ISSUE-IDEA-002 — PyCharm plugin IDE tests fail under Gradle (SettingsController missing)

## Summary

Running IDE tests for `ide-plugins/pycharm/` fails during IntelliJ test application bootstrap with:

- `InstanceNotRegisteredException: com.intellij.platform.settings.SettingsController`

`./gradlew build` should succeed by default; until the upstream/platform test harness issue is
resolved, IDE tests are gated behind `-PrunIdeTests=true`.

## Steps to Reproduce

1. `cd ide-plugins/pycharm`
2. `./gradlew test -PrunIdeTests=true`

## Expected Behavior

- IDE tests pass.

## Actual Behavior

- Tests fail before execution while loading the test application.

## Investigation

- An earlier failure (missing `junit.framework.TestCase`) was resolved by adding JUnit 4 to the test
  classpath.
- Current failure originates from `ApplicationLoader.initConfigurationStore` attempting to resolve
  `com.intellij.platform.settings.SettingsController`, which is not registered in the unit test app
  container.

## Workaround

- `ide-plugins/pycharm/build.gradle.kts` disables `:test` unless `-PrunIdeTests=true` is provided.

## Related

- Task: [[TASK-IDEA-002-pycharm-plugin-fix-gradle-build]]
