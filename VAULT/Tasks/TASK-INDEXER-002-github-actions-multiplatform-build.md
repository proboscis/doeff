---
id: TASK-INDEXER-002
title: Setup GitHub Actions for Multi-Platform Wheel Build
module: INDEXER
status: todo
priority: high
due-date: 
related-project: 
related-spec: SPEC-INDEXER-001
related-feature: 
code_path: .github/workflows/
created: 2024-12-08
updated: 2024-12-08
tags: [task, indexer, ci, distribution]
---

# TASK-INDEXER-002 — Setup GitHub Actions for Multi-Platform Wheel Build

## Description

GitHub Actions ワークフローを作成し、複数プラットフォーム向けの `doeff-indexer` ホイールを自動ビルド・公開する。

## Acceptance Criteria

- [ ] 5 プラットフォーム向けホイールをビルド:
  - Linux x86_64
  - Linux aarch64
  - macOS x86_64
  - macOS arm64
  - Windows x86_64
- [ ] タグプッシュ時に自動ビルド
- [ ] ビルドしたホイールを PyPI に公開
- [ ] ビルド時間 15 分以内

## Implementation Notes

### ワークフローファイル

`.github/workflows/build-indexer.yml`:

```yaml
name: Build doeff-indexer

on:
  push:
    tags: ['doeff-indexer-v*']
  workflow_dispatch:

jobs:
  build-linux:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        target: [x86_64, aarch64]
    steps:
      - uses: actions/checkout@v4
      - uses: PyO3/maturin-action@v1
        with:
          target: ${{ matrix.target }}
          args: --release --out dist
          working-directory: packages/doeff-indexer
          before-script-linux: |
            # Build native binary first
            cargo build --release
            mkdir -p python/doeff_indexer/bin
            cp target/release/doeff-indexer python/doeff_indexer/bin/
      - uses: actions/upload-artifact@v4
        with:
          name: wheels-linux-${{ matrix.target }}
          path: packages/doeff-indexer/dist

  build-macos:
    runs-on: macos-latest
    strategy:
      matrix:
        target: [x86_64, aarch64]
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
        with:
          targets: ${{ matrix.target }}-apple-darwin
      - run: |
          cargo build --release --target ${{ matrix.target }}-apple-darwin
          mkdir -p python/doeff_indexer/bin
          cp target/${{ matrix.target }}-apple-darwin/release/doeff-indexer python/doeff_indexer/bin/
        working-directory: packages/doeff-indexer
      - uses: PyO3/maturin-action@v1
        with:
          target: ${{ matrix.target }}
          args: --release --out dist
          working-directory: packages/doeff-indexer
      - uses: actions/upload-artifact@v4
        with:
          name: wheels-macos-${{ matrix.target }}
          path: packages/doeff-indexer/dist

  build-windows:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - run: |
          cargo build --release
          mkdir -p python/doeff_indexer/bin
          cp target/release/doeff-indexer.exe python/doeff_indexer/bin/
        working-directory: packages/doeff-indexer
      - uses: PyO3/maturin-action@v1
        with:
          args: --release --out dist
          working-directory: packages/doeff-indexer
      - uses: actions/upload-artifact@v4
        with:
          name: wheels-windows
          path: packages/doeff-indexer/dist

  publish:
    needs: [build-linux, build-macos, build-windows]
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write  # for trusted publishing
    steps:
      - uses: actions/download-artifact@v4
        with:
          pattern: wheels-*
          merge-multiple: true
          path: dist/
      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1
        with:
          packages-dir: dist/
```

### Linux aarch64 クロスコンパイル

maturin-action は内部で `cross` を使用。追加設定不要の予定だが、動作確認が必要。

### PyPI Trusted Publishing

GitHub Actions から PyPI への公開には Trusted Publishing を使用:
1. PyPI プロジェクト設定で GitHub リポジトリを信頼
2. `permissions: id-token: write` を設定

## Subtasks

- [ ] `.github/workflows/build-indexer.yml` 作成
- [ ] テスト用に `workflow_dispatch` で手動実行
- [ ] Linux x86_64 ビルド確認
- [ ] Linux aarch64 ビルド確認（クロスコンパイル）
- [ ] macOS x86_64 ビルド確認
- [ ] macOS arm64 ビルド確認
- [ ] Windows ビルド確認
- [ ] PyPI Trusted Publishing 設定
- [ ] タグプッシュでの自動公開テスト

## Related

- Spec: [[SPEC-INDEXER-001-bundled-binary-distribution]]
- Feature: 
- PR: 

## Progress Log

### 2024-12-08
- タスク作成

