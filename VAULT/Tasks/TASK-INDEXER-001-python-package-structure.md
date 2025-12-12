---
id: TASK-INDEXER-001
title: Add Python Package Structure for Binary Bundling
module: INDEXER
status: todo
priority: high
due-date: 
related-project: 
related-spec: SPEC-INDEXER-001
related-feature: 
code_path: packages/doeff-indexer/
created: 2024-12-08
updated: 2024-12-08
tags: [task, indexer, distribution]
---

# TASK-INDEXER-001 — Add Python Package Structure for Binary Bundling

## Description

`doeff-indexer` パッケージに Python ラッパー構造を追加し、プリビルトバイナリをホイールに含められるようにする。

## Acceptance Criteria

- [ ] `python/doeff_indexer/` ディレクトリ構造を作成
- [ ] `_cli.py` でバイナリラッパーを実装
- [ ] `pyproject.toml` に `[project.scripts]` エントリーポイントを追加
- [ ] `[tool.maturin]` の `include` 設定を追加
- [ ] ローカルで `maturin develop` が動作することを確認

## Implementation Notes

### ディレクトリ構造

```
packages/doeff-indexer/
├── Cargo.toml
├── pyproject.toml
├── src/
│   └── ...
└── python/
    └── doeff_indexer/
        ├── __init__.py
        ├── _cli.py
        └── bin/           # ビルド時に生成
            └── doeff-indexer
```

### _cli.py の実装

プラットフォーム判定とバイナリ実行のラッパー:

```python
import os
import subprocess
import sys
from pathlib import Path

def _get_binary_path() -> Path:
    package_dir = Path(__file__).parent
    bin_name = "doeff-indexer.exe" if sys.platform == "win32" else "doeff-indexer"
    return package_dir / "bin" / bin_name

def main() -> int:
    binary = _get_binary_path()
    if not binary.exists():
        print(f"Error: Binary not found at {binary}", file=sys.stderr)
        return 1
    return subprocess.call([str(binary)] + sys.argv[1:])
```

### pyproject.toml 変更

```toml
[project.scripts]
doeff-indexer = "doeff_indexer._cli:main"

[tool.maturin]
features = ["python"]
module-name = "doeff_indexer"
python-source = "python"
include = [
    { path = "python/doeff_indexer/bin/*", format = "wheel" }
]
```

## Subtasks

- [ ] `python/doeff_indexer/__init__.py` 作成（既存エクスポートを維持）
- [ ] `python/doeff_indexer/_cli.py` 作成
- [ ] `pyproject.toml` 更新
- [ ] ローカルテスト: バイナリを手動コピーして動作確認

## Related

- Spec: [[SPEC-INDEXER-001-bundled-binary-distribution]]
- Feature: 
- PR: 

## Progress Log

### 2024-12-08
- タスク作成


