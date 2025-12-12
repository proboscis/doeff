---
id: SPEC-INDEXER-001
title: Bundled Binary Distribution for doeff-indexer
module: INDEXER
status: draft
code_path: packages/doeff-indexer/
version: 0.1.0
related-feature: 
created: 2024-12-08
updated: 2024-12-08
tags: [spec, indexer, distribution]
---

# SPEC-INDEXER-001 — Bundled Binary Distribution for doeff-indexer

## 1. Overview

`doeff-indexer` のRustバイナリをPython wheelに含め、`pip install doeff` のみで全てのツールが利用可能になる仕組みを定義する。

現状:
- `doeff` コア → `doeff-indexer` (Python API) を依存として要求
- IDE プラグイン → `doeff-indexer` (CLI バイナリ) を別途インストール必要
- ユーザーは Rust ツールチェーンが必要

目標:
- `pip install doeff` で Python API と CLI バイナリの両方が利用可能
- IDE プラグインは Python 環境内のバイナリを使用可能
- Rust ツールチェーン不要（プリビルトホイール配布）

## 2. Background / Motivation

### 現在の問題

1. **インストールの複雑さ**: doeff を使うには Rust ツールチェーンが必要
2. **バイナリの分離**: IDE プラグインは CLI バイナリを別途探す必要がある
3. **バージョン不整合リスク**: Python API と CLI バイナリが異なるバージョンになりうる

### 利用パターン

| 利用者 | インターフェース | 現状 |
|--------|-----------------|------|
| `doeff run` CLI | Python API (PyO3) | maturin ビルド必要 |
| VSCode Plugin | CLI バイナリ | cargo install 必要 |
| PyCharm Plugin | CLI バイナリ | cargo install 必要 |

### 解決方針

maturin でビルドする wheel にプリビルトバイナリを含め、Python の `[project.scripts]` でエントリーポイントを提供する。

## 3. Requirements

### 3.1 Functional Requirements

- FR-1: `pip install doeff-indexer` で CLI バイナリ (`doeff-indexer`) が利用可能になる
- FR-2: バイナリは Python 環境の bin ディレクトリに配置される（例: `.venv/bin/doeff-indexer`）
- FR-3: Python API (`from doeff_indexer import Indexer`) も従来通り利用可能
- FR-4: 以下のプラットフォームをサポート:
  - Linux x86_64 (manylinux)
  - Linux aarch64 (manylinux)
  - macOS x86_64
  - macOS arm64 (Apple Silicon)
  - Windows x86_64

### 3.2 Non-Functional Requirements

- NFR-1: CLI バイナリの起動時間は 10ms 以下を維持（Python ラッパーオーバーヘッドなし）
- NFR-2: wheel サイズは各プラットフォーム 15MB 以下
- NFR-3: GitHub Actions でのビルド時間は 15 分以下

## 4. Detailed Specification

### 4.1 パッケージ構造

```
packages/doeff-indexer/
├── Cargo.toml
├── pyproject.toml
├── src/
│   ├── main.rs          # CLI バイナリ
│   ├── lib.rs           # コアロジック
│   └── python_api.rs    # PyO3 バインディング
└── python/
    └── doeff_indexer/
        ├── __init__.py  # re-export (optional)
        └── _cli.py      # バイナリラッパー
```

### 4.2 pyproject.toml 設定

```toml
[build-system]
requires = ["maturin>=1.0,<2.0"]
build-backend = "maturin"

[project]
name = "doeff-indexer"
version = "0.1.2"

[project.scripts]
doeff-indexer = "doeff_indexer._cli:main"

[tool.maturin]
features = ["python"]
module-name = "doeff_indexer"
include = [
    { path = "bin/*", format = "wheel" }
]
```

### 4.3 CLI ラッパー (_cli.py)

```python
"""CLI wrapper that executes the bundled binary."""
import os
import subprocess
import sys
from pathlib import Path

def _get_binary_path() -> Path:
    """Locate the bundled binary."""
    # Wheel 内のバイナリパス
    package_dir = Path(__file__).parent
    bin_name = "doeff-indexer.exe" if sys.platform == "win32" else "doeff-indexer"
    binary = package_dir / "bin" / bin_name
    
    if binary.exists() and os.access(binary, os.X_OK):
        return binary
    
    raise FileNotFoundError(
        f"doeff-indexer binary not found at {binary}. "
        "This may indicate a corrupted installation."
    )

def main() -> int:
    """Execute the bundled doeff-indexer binary."""
    binary = _get_binary_path()
    return subprocess.call([str(binary)] + sys.argv[1:])

if __name__ == "__main__":
    sys.exit(main())
```

### 4.4 GitHub Actions ワークフロー

```yaml
name: Build and Publish doeff-indexer

on:
  push:
    tags: ['doeff-indexer-v*']
  workflow_dispatch:

jobs:
  build:
    strategy:
      matrix:
        include:
          - os: ubuntu-latest
            target: x86_64-unknown-linux-gnu
            maturin-target: x86_64
          - os: ubuntu-latest
            target: aarch64-unknown-linux-gnu
            maturin-target: aarch64
          - os: macos-latest
            target: x86_64-apple-darwin
            maturin-target: x86_64
          - os: macos-latest
            target: aarch64-apple-darwin
            maturin-target: aarch64
          - os: windows-latest
            target: x86_64-pc-windows-msvc
            maturin-target: x64
    
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      
      - name: Setup Rust
        uses: dtolnay/rust-toolchain@stable
        with:
          targets: ${{ matrix.target }}
      
      - name: Build binary
        run: cargo build --release --target ${{ matrix.target }}
        working-directory: packages/doeff-indexer
      
      - name: Copy binary to package
        run: |
          mkdir -p packages/doeff-indexer/python/doeff_indexer/bin
          cp target/${{ matrix.target }}/release/doeff-indexer* \
             packages/doeff-indexer/python/doeff_indexer/bin/
      
      - name: Build wheel with maturin
        uses: PyO3/maturin-action@v1
        with:
          command: build
          args: --release --target ${{ matrix.maturin-target }}
          working-directory: packages/doeff-indexer
      
      - uses: actions/upload-artifact@v4
        with:
          name: wheel-${{ matrix.target }}
          path: packages/doeff-indexer/target/wheels/*.whl

  publish:
    needs: build
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
        with:
          pattern: wheel-*
          merge-multiple: true
          path: dist/
      
      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@v1
        with:
          packages-dir: dist/
```

### 4.5 IDE プラグインの変更

IDE プラグインは以下の順序でバイナリを探索:

1. Python 環境内のバイナリ（推奨）
2. 環境変数 `DOEFF_INDEXER_PATH`
3. システムパス候補（フォールバック）

```typescript
// VSCode extension.ts
async function locateIndexer(): Promise<string> {
  // 1. Python 環境のバイナリを探す
  const pythonPath = await getPythonInterpreter();
  if (pythonPath) {
    const binDir = path.dirname(pythonPath);
    const indexerInEnv = path.join(binDir, 'doeff-indexer');
    if (isExecutable(indexerInEnv)) {
      return indexerInEnv;
    }
  }
  
  // 2. 環境変数
  const envPath = process.env.DOEFF_INDEXER_PATH;
  if (envPath && isExecutable(envPath)) {
    return envPath;
  }
  
  // 3. システムパス候補
  for (const candidate of INDEXER_CANDIDATES) {
    if (isExecutable(candidate)) {
      return candidate;
    }
  }
  
  throw new Error('doeff-indexer not found');
}
```

## 5. Examples

### インストールと使用

```bash
# インストール
pip install doeff

# CLI 使用
doeff-indexer index --root /path/to/project

# Python API 使用
python -c "from doeff_indexer import Indexer; print(Indexer)"
```

### IDE プラグインからの使用

```bash
# Python 環境をアクティベート後
which doeff-indexer
# => /path/to/.venv/bin/doeff-indexer
```

## 6. Open Questions

- Q1: Linux aarch64 のクロスコンパイル設定（cross または QEMU）
- Q2: Windows ARM64 サポートの必要性
- Q3: ソースビルドのフォールバック（Rust ツールチェーンがある場合）

## 7. References

- maturin bindings documentation: https://www.maturin.rs/bindings.html
- PyO3 guide: https://pyo3.rs/
- GitHub Actions maturin-action: https://github.com/PyO3/maturin-action


