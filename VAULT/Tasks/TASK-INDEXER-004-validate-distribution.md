---
id: TASK-INDEXER-004
title: Validate End-to-End Distribution Flow
module: INDEXER
status: todo
priority: medium
due-date: 
related-project: 
related-spec: SPEC-INDEXER-001
related-feature: 
code_path: packages/doeff-indexer/
created: 2024-12-08
updated: 2024-12-08
tags: [task, indexer, testing, distribution]
---

# TASK-INDEXER-004 — Validate End-to-End Distribution Flow

## Description

バンドル配布の全体フローをテストし、ユーザー体験が期待通りであることを確認する。

## Acceptance Criteria

- [ ] クリーンな Python 環境で `pip install doeff` が成功
- [ ] `doeff-indexer --version` が動作
- [ ] `doeff run --program ...` が動作（Python API 経由）
- [ ] VSCode プラグインがバイナリを自動検出
- [ ] PyCharm プラグインがバイナリを自動検出
- [ ] Rust ツールチェーンなしで全て動作

## Implementation Notes

### テストシナリオ

#### 1. 基本インストールテスト

```bash
# クリーンな仮想環境
python -m venv /tmp/test-doeff
source /tmp/test-doeff/bin/activate

# インストール
pip install doeff

# CLI 動作確認
doeff-indexer --version
doeff-indexer index --root .

# Python API 確認
python -c "from doeff_indexer import Indexer; print('OK')"

# doeff run 確認
python -m doeff run --program some.module.program
```

#### 2. IDE プラグインテスト

##### VSCode
1. 新しいワークスペースを開く
2. Python 拡張でインタープリターを選択（doeff インストール済み環境）
3. `.py` ファイルを開く
4. Output パネルで "doeff-runner" を確認
5. "Using indexer from Python env: ..." のログを確認

##### PyCharm
1. 新しいプロジェクトを開く
2. Project SDK に doeff インストール済み環境を設定
3. `.py` ファイルを開く
4. Gutter アイコンが表示されることを確認
5. Diagnostics ログでバイナリパスを確認

#### 3. フォールバックテスト

```bash
# doeff-indexer がインストールされていない環境
python -m venv /tmp/test-fallback
source /tmp/test-fallback/bin/activate

# doeff なしで IDE を開く
# → フォールバックパスが使われることを確認
# → または適切なエラーメッセージが表示される
```

#### 4. パフォーマンステスト

```bash
# CLI 起動時間
time doeff-indexer --version

# 複数回呼び出し
for i in {1..10}; do
  time doeff-indexer index --root . > /dev/null
done

# 期待: 起動時間 < 10ms
```

### テスト用チェックリスト

| プラットフォーム | pip install | CLI | Python API | VSCode | PyCharm |
|-----------------|-------------|-----|------------|--------|---------|
| macOS arm64     | [ ]         | [ ] | [ ]        | [ ]    | [ ]     |
| macOS x86_64    | [ ]         | [ ] | [ ]        | [ ]    | [ ]     |
| Linux x86_64    | [ ]         | [ ] | [ ]        | [ ]    | [ ]     |
| Linux aarch64   | [ ]         | [ ] | [ ]        | [ ]    | [ ]     |
| Windows x86_64  | [ ]         | [ ] | [ ]        | [ ]    | [ ]     |

## Subtasks

- [ ] テストスクリプト作成
- [ ] macOS arm64 でテスト
- [ ] macOS x86_64 でテスト（CI または手動）
- [ ] Linux x86_64 でテスト（CI または Docker）
- [ ] Linux aarch64 でテスト（CI または QEMU）
- [ ] Windows でテスト（CI または手動）
- [ ] パフォーマンス計測と報告
- [ ] README 更新（新しいインストール方法）

## Related

- Spec: [[SPEC-INDEXER-001-bundled-binary-distribution]]
- Depends: [[TASK-INDEXER-001-python-package-structure]]
- Depends: [[TASK-INDEXER-002-github-actions-multiplatform-build]]
- Depends: [[TASK-INDEXER-003-update-ide-plugins-binary-discovery]]
- PR: 

## Progress Log

### 2024-12-08
- タスク作成


