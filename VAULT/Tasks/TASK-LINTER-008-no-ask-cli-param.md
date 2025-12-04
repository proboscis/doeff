---
id: TASK-LINTER-008
title: DOEFF023 no-ask-cli-param ルール実装
module: linter
status: todo
priority: medium
assignee: 
due-date: 
related-project: PROJECT-LINTER-001
related-spec: SPEC-LINTER-001
related-feature: 
code_path: packages/doeff-linter/src/rules/
created: 2025-12-04
updated: 2025-12-04
tags: [task, linter, doeff023]
---

# TASK-LINTER-008 — DOEFF023 no-ask-cli-param ルール実装

## Description

`ask` effect で CLI 引数のようなパラメータを取得することを禁止するルール。Program エントリーポイントは固定された振る舞いを持つべきで、`ask` でパラメータを取得して振る舞いを変えるべきではない。

## Acceptance Criteria

- [ ] `yield ask("param_*")` パターンを検出して警告
- [ ] `yield ask("arg_*")` パターンを検出して警告
- [ ] `yield ask("input_*")` パターンを検出して警告
- [ ] `yield ask("logger")` などのサービスは許可
- [ ] ユニットテスト 5 件以上

## Implementation Notes

### Detection Logic

```rust
// Yield で func が ask の場合、引数の文字列パターンを検査
const CLI_PARAM_PATTERNS: &[&str] = &[
    "param_", "arg_", "input_", "config_", "option_",
    "path_", "file_", "dir_", "url_", "host_", "port_",
];

fn is_cli_param_name(key: &str) -> bool {
    CLI_PARAM_PATTERNS.iter().any(|p| key.starts_with(p))
}
```

### Error Message

```
DOEFF023: 'ask("{key}")' looks like a CLI parameter injection.

Problem: Program entrypoints should have fixed behavior. Using ask() to
inject parameters that change behavior makes the Program non-reproducible.

Fix: Pass the parameter as a KleisliProgram argument instead:
  # Before
  @do
  def process():
      path = yield ask("param_input_path")
      ...
  p_process: Program = process()

  # After
  @do
  def process(input_path: Path):
      ...
  p_process: Program = process(input_path=Path("data/input.json"))
```

## Subtasks

- [ ] `doeff023_no_ask_cli_param.rs` 作成
- [ ] mod.rs に登録
- [ ] DOEFF023.md ドキュメント作成
- [ ] cargo test 実行

## Related

- Spec: [[SPEC-LINTER-001]]
- Project: [[PROJECT-LINTER-001]]

## Progress Log

### 2025-12-04
- タスク作成


