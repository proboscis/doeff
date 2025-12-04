---
id: TASK-LINTER-014
title: DOEFF029 doeff-kleisli-comment-placement ルール実装
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
tags: [task, linter, doeff029]
---

# TASK-LINTER-014 — DOEFF029 doeff-kleisli-comment-placement ルール実装

## Description

`# doeff: kleisli` コメントが正しい位置（関数名行の末尾）に配置されているかを検証するルール。IDE プラグインがこのコメントを認識するため、位置が重要。

## Acceptance Criteria

- [ ] `# doeff: kleisli` がデコレータ行にある場合に警告
- [ ] `# doeff: kleisli` が関数本体内にある場合に警告
- [ ] `def func_name(...): # doeff: kleisli` は許可
- [ ] ユニットテスト 5 件以上

## Implementation Notes

### Detection Logic

```rust
// ソースコードを行単位で解析
// "# doeff: kleisli" または "# doeff:kleisli" を含む行を検出
// その行が関数定義行 (def ...) かどうかを確認

fn check_kleisli_comment_placement(source: &str, func: &StmtFunctionDef) -> Option<Violation> {
    let func_line = func.range.start().to_row();
    
    for (line_num, line) in source.lines().enumerate() {
        if line.contains("# doeff: kleisli") || line.contains("# doeff:kleisli") {
            if line_num != func_line && is_near_function(line_num, func) {
                // Comment is not on the function definition line
                return Some(violation_wrong_placement(line_num, func_line));
            }
        }
    }
    None
}
```

### Error Message

```
DOEFF029: '# doeff: kleisli' comment is not on the function definition line.

The IDE plugin looks for '# doeff: kleisli' at the end of the function
name line to identify Kleisli tools.

Fix: Move the comment to the end of the function definition line:
  # Before (bad)
  @do # doeff:kleisli
  def visualize_something(tgt: T):
      pass

  # After (good)
  @do
  def visualize_something(tgt: T):  # doeff: kleisli
      pass
```

## Subtasks

- [ ] `doeff029_kleisli_comment_placement.rs` 作成
- [ ] mod.rs に登録
- [ ] DOEFF029.md ドキュメント作成
- [ ] cargo test 実行

## Notes

- AST だけでなくソースコードの行解析が必要
- RuleContext に source が含まれているので利用可能

## Related

- Spec: [[SPEC-LINTER-001]]
- Project: [[PROJECT-LINTER-001]]

## Progress Log

### 2025-12-04
- タスク作成


