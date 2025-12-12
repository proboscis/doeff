---
id: FEAT-VSCODE-002
title: Entrypoint Explorer TreeView
module: vscode
status: done
created: 2025-12-05
updated: 2025-12-05
priority: medium
tags: [feature, vscode, ide, treeview, explorer]
---

# FEAT-VSCODE-002 — Entrypoint Explorer TreeView

## 1. Summary

VSCode の doeff-runner プラグインにサイドバーペインを追加し、doeff-indexer が返す実行可能な Program エントリーポイントを階層的に表示する。各エントリーポイントに対して run/run_with_options/kleisli/transform を選択でき、選択したアクションをエディタの CodeLens に反映する。

現在はエディタ内の Program 行にのみ CodeLens ボタンが表示されるが、この機能によりワークスペース全体のエントリーポイントを俯瞰・管理できるようになる。

## 2. Goals / Non-Goals

**Goals:**
- doeff-indexer を使用してワークスペース全体の Program エントリーポイントを取得
- VSCode サイドバーに TreeView で階層表示（モジュール/ファイル > エントリーポイント）
- 各エントリーポイントに対して run/run_with_options/kleisli/transform のアクションを選択可能
- 選択した設定をエディタの CodeLens に反映
- エントリーポイントをクリックして該当ファイル/行にジャンプ
- ファイル変更時の自動リフレッシュ

**Non-Goals:**
- エントリーポイントの編集・作成機能
- 複数ワークスペースの同時管理
- リモート開発環境での動作保証（初期実装では対象外）

## 3. Linked Specs

- N/A

## 4. Linked Designs

- N/A

## 5. Tasks

- [[TASK-VSCODE-002-treeview-provider]]
- [[TASK-VSCODE-003-entrypoint-indexing]]
- [[TASK-VSCODE-004-action-selector]]
- [[TASK-VSCODE-005-codelens-integration]]

## 6. Related Decisions

- N/A

## 7. Related Issues

- N/A

## 8. Acceptance Criteria

- [ ] VSCode サイドバーに "doeff Programs" ペインが表示される
- [ ] ワークスペース内の全 Program エントリーポイントが階層的に表示される
- [ ] エントリーポイントをクリックすると該当ファイルの該当行にジャンプする
- [ ] 各エントリーポイントに対して run/run_with_options/kleisli/transform を選択できる
- [ ] 選択したアクションがエディタの CodeLens に反映される
- [ ] ファイル保存時にエントリーポイント一覧が自動更新される
- [ ] 手動リフレッシュボタンが機能する

## 9. Notes / References

### TreeView 階層構造

```
doeff Programs
├── src/module_a
│   ├── my_program: Program[int]
│   │   ├── ▶ Run (default)
│   │   ├── ▶⚙ Run with options
│   │   ├── 🔗 with_logging (kleisli)
│   │   └── 🔀 traced (transform)
│   └── another_program: Program[str]
└── src/module_b
    └── ...
```

### 関連する既存実装

**extension.ts:**
- `IndexEntry` インターフェース: indexer 出力の型定義
- `fetchEntries()`: indexer コマンド実行
- `ProgramCodeLensProvider`: CodeLens 提供

**doeff-indexer:**
- `index` コマンド: ワークスペース全体をスキャン
- `find-kleisli`, `find-transforms`, `find-interpreters`: 各種ツールの検索

### VSCode TreeView API

- `vscode.TreeDataProvider<T>`: ツリーデータの提供
- `vscode.TreeItem`: ツリー項目
- `vscode.window.createTreeView()`: TreeView の作成
- `package.json` の `contributes.views` と `contributes.viewsContainers`

