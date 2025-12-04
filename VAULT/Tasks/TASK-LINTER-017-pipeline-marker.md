---
id: TASK-LINTER-017
title: DOEFF023 pipeline-marker ルール実装
module: linter
status: done
priority: medium
assignee: 
due-date: 
related-project: PROJECT-LINTER-001
related-spec: SPEC-LINTER-001
related-feature: 
code_path: packages/doeff-linter/src/rules/
created: 2025-12-04
updated: 2025-12-04
tags: [task, linter, doeff023, pipeline, entrypoint]
---

# TASK-LINTER-017 — DOEFF023 pipeline-marker ルール実装

## Description

`@do` 関数がモジュールレベルの Program 変数作成に使用される場合、`# doeff: pipeline` マーカーが必須。マーカーがない場合、ユーザーがパイプラインスタイルを認識していないとみなし違反としてフラグ。

## Motivation

doeff プロジェクトでは、Pipeline Oriented Programming パラダイムに従い、中間生成物を Program グローバル変数として定義し、それらを合成して entrypoint を構築すべき。

このルールにより：

1. **明示的な認識**: ユーザーはパイプラインスタイルを意識的に採用
2. **教育効果**: マーカーを書くことでパイプラインパターンを学習
3. **コードレビュー**: マーカーの有無でパイプライン準拠を確認可能
4. **一貫性**: プロジェクト全体でパイプラインスタイルを強制

## Acceptance Criteria

- [ ] モジュールレベルの `Program[T]` 型変数への代入を検出
- [ ] 右辺が `@do` 関数の呼び出しかチェック
- [ ] 該当 `@do` 関数に `# doeff: pipeline` マーカーがあるか確認
- [ ] マーカー配置場所を3箇所サポート:
  - `@do` 行の末尾: `@do  # doeff: pipeline`
  - `def` 行の末尾: `def func():  # doeff: pipeline`
  - docstring 内: `"""doeff: pipeline"""`
- [ ] マーカーがなければ違反としてフラグ
- [ ] テストファイル (`test_*.py`, `*_test.py`) は除外
- [ ] Severity は Warning
- [ ] ユニットテスト 5 件以上

## Implementation Notes

### Detection Logic

```rust
// 1. Collect all @do decorated functions with their marker status
struct DoFunction {
    name: String,
    has_pipeline_marker: bool,
    line: usize,
}

// 2. Find module-level Program[T] assignments
// Pattern: var_name: Program[T] = func_name(...)

// 3. Check if the called function is a @do function without marker
fn check_program_assignment(assignment: &AnnAssign, do_functions: &HashMap<String, DoFunction>) {
    if is_program_type(&assignment.annotation) {
        if let Some(call) = get_call_expr(&assignment.value) {
            if let Some(do_func) = do_functions.get(&call.func_name) {
                if !do_func.has_pipeline_marker {
                    // Violation!
                }
            }
        }
    }
}

// 4. Check for marker in three locations
fn has_pipeline_marker(func: &FunctionDef, source: &str) -> bool {
    // Check @do line comment
    // Check def line comment  
    // Check docstring
}
```

### Error Message

```
DOEFF023: @do function '{}' is used to create entrypoint Program '{}' but lacks pipeline marker.

Pipeline-oriented programming requires explicit acknowledgment when creating Program entrypoints.

Fix: Add '# doeff: pipeline' marker to the function:

  # Option 1: After @do decorator
  @do  # doeff: pipeline
  def {}(...):
      ...

  # Option 2: After def line
  @do
  def {}(...):  # doeff: pipeline
      ...

  # Option 3: In docstring
  @do
  def {}(...):
      \"\"\"doeff: pipeline\"\"\"
      ...
```

## Subtasks

- [x] `doeff023_pipeline_marker.rs` 作成
- [x] mod.rs に登録
- [x] main.rs に RuleInfo 追加
- [x] DOEFF023.md ドキュメント作成
- [x] cargo test 実行 (14 tests passed)

## Related

- Spec: [[SPEC-LINTER-001]]
- Project: [[PROJECT-LINTER-001]]
- DOEFF020: Program naming convention (関連)
- DOEFF022: Prefer @do function (関連)

## Progress Log

### 2025-12-04
- タスク作成
- 実装開始
- GitHub issue #106 作成
- doeff023_pipeline_marker.rs 実装完了
- mod.rs, main.rs に登録
- DOEFF023.md ドキュメント作成
- 14件のユニットテスト全て通過
- タスク完了


