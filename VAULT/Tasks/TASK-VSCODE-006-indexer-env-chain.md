---
id: TASK-VSCODE-006
title: Indexer find-env-chain コマンド実装
module: indexer
status: completed
priority: high
due-date:
related-project: PROJECT-VSCODE-001
related-spec: SPEC-VSCODE-001
related-feature:
code_path: packages/doeff-indexer/src/
created: 2025-12-11
updated: 2025-12-11
tags: [task, indexer, env, rust]
---

# TASK-VSCODE-006 — Indexer find-env-chain コマンド実装

## Description

doeff-indexer に `find-env-chain` コマンドを追加し、指定された Program エントリーポイントに対して、どの暗黙的環境 (implicit env) がロードされるかを JSON 形式で出力する。

## Acceptance Criteria

- [x] `doeff-indexer find-env-chain --root <path> --program <qualified_name>` コマンドが動作する
- [x] ~/.doeff.py (ユーザーレベル設定) の検出と解析
- [x] `# doeff: default` マーカー付き env のモジュール階層に従った収集
- [ ] `Program.pure({...})` パターンからの静的値抽出 (Deferred)
- [x] JSON 形式での出力（EnvChainEntry 配列）

## Implementation Notes

### コマンド仕様

```bash
doeff-indexer find-env-chain \
  --root /path/to/project \
  --program src.features.auth.login_program
```

### 出力フォーマット

```json
{
  "program": "src.features.auth.login_program",
  "envChain": [
    {
      "qualified_name": "~/.doeff",
      "file_path": "/Users/john/.doeff.py",
      "line": 1,
      "is_user_config": true,
      "keys": ["log_level", "user"],
      "static_values": {
        "log_level": "DEBUG",
        "user": "john"
      }
    },
    {
      "qualified_name": "src.base_env",
      "file_path": "/path/to/src/__init__.py",
      "line": 15,
      "keys": ["db_host", "timeout", "debug"],
      "static_values": {
        "db_host": "localhost",
        "timeout": 10,
        "debug": false
      }
    }
  ]
}
```

### 静的値抽出パターン

```python
# 抽出可能
# doeff: default
base_env: Program[dict] = Program.pure({
    'db_host': 'localhost',
    'timeout': 10
})

# 部分的に抽出可能
# doeff: default
dynamic_env: Program[dict] = Program.pure({
    'static_key': 'value',
    'dynamic_key': os.environ.get('KEY')  # → null
})

# 抽出不可（keys: [], static_values: null）
# doeff: default
computed_env: Program[dict] = load_config_from_file()
```

## Subtasks

- [x] `find-env-chain` サブコマンドを CLI に追加
- [x] モジュール階層を辿って env を収集するロジック実装
- [x] ~/.doeff.py の検出と解析
- [ ] `Program.pure({...})` パターンの AST 解析による静的値抽出 (Deferred)
- [x] EnvChainEntry 構造体の定義と JSON シリアライズ
- [ ] ユニットテスト

## Related

- Spec: [[SPEC-VSCODE-001-implicit-env-inspector]]
- Existing: `index` コマンド（env marker 検出の参考）

## Progress Log

### 2025-12-11
- タスク作成
