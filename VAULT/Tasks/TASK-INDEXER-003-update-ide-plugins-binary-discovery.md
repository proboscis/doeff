---
id: TASK-INDEXER-003
title: Update IDE Plugins to Discover Binary in Python Environment
module: INDEXER
status: todo
priority: medium
due-date: 
related-project: 
related-spec: SPEC-INDEXER-001
related-feature: 
code_path: ide-plugins/
created: 2024-12-08
updated: 2024-12-08
tags: [task, indexer, vscode, pycharm]
---

# TASK-INDEXER-003 — Update IDE Plugins to Discover Binary in Python Environment

## Description

VSCode と PyCharm プラグインのバイナリ探索ロジックを更新し、Python 環境内のバイナリを優先的に使用するようにする。

## Acceptance Criteria

- [ ] VSCode プラグイン: Python 環境の bin ディレクトリを最初に探索
- [ ] PyCharm プラグイン: 同様の探索ロジックを追加
- [ ] 既存のフォールバックパス（`~/.cargo/bin` 等）は維持
- [ ] Python インタープリターの検出方法を実装

## Implementation Notes

### VSCode (extension.ts)

現在の探索パス（lines 12-18）:
```typescript
const INDEXER_CANDIDATES = [
  '/usr/local/bin/doeff-indexer',
  '/usr/bin/doeff-indexer',
  `${process.env.HOME}/.cargo/bin/doeff-indexer`,
  `${process.env.HOME}/.local/bin/doeff-indexer`,
  '/opt/homebrew/bin/doeff-indexer'
];
```

更新後:
```typescript
async function locateIndexer(): Promise<string> {
  // 1. Python 環境のバイナリを探す
  const pythonPath = await getPythonInterpreter();
  if (pythonPath) {
    const binDir = path.dirname(pythonPath);
    const indexerPath = path.join(binDir, 'doeff-indexer');
    if (isExecutable(indexerPath)) {
      output.appendLine(`[info] Using indexer from Python env: ${indexerPath}`);
      return indexerPath;
    }
  }
  
  // 2. 環境変数
  const envPath = process.env.DOEFF_INDEXER_PATH;
  if (envPath && isExecutable(envPath)) {
    return envPath;
  }
  
  // 3. 既存のフォールバック
  for (const candidate of INDEXER_CANDIDATES) {
    if (candidate && isExecutable(candidate)) {
      return candidate;
    }
  }
  
  throw new Error('doeff-indexer not found');
}

async function getPythonInterpreter(): Promise<string | undefined> {
  // VSCode Python 拡張から取得
  const pythonExt = vscode.extensions.getExtension('ms-python.python');
  if (pythonExt?.isActive) {
    const pythonPath = pythonExt.exports.settings.getExecutionDetails?.()?.execCommand?.[0];
    if (pythonPath) return pythonPath;
  }
  
  // ワークスペース設定から取得
  const config = vscode.workspace.getConfiguration('python');
  const pythonPath = config.get<string>('defaultInterpreterPath');
  if (pythonPath && fs.existsSync(pythonPath)) {
    return pythonPath;
  }
  
  return undefined;
}
```

### PyCharm (IndexerClient.kt)

現在の探索パス（lines 422-428）:
```kotlin
val candidates = listOf(
    "/usr/local/bin/doeff-indexer",
    "/usr/bin/doeff-indexer",
    "${System.getProperty("user.home")}/.cargo/bin/doeff-indexer",
    "${System.getProperty("user.home")}/.local/bin/doeff-indexer",
    "/opt/homebrew/bin/doeff-indexer"
)
```

更新後:
```kotlin
private fun locateIndexerInternal(): String? {
    // 1. プロジェクトの Python SDK からバイナリを探す
    val pythonSdk = ProjectRootManager.getInstance(project).projectSdk
    if (pythonSdk != null) {
        val pythonHome = pythonSdk.homePath?.let { File(it).parent }
        if (pythonHome != null) {
            val indexerInEnv = File(pythonHome, "doeff-indexer")
            if (indexerInEnv.canExecute()) {
                return indexerInEnv.absolutePath
            }
        }
    }
    
    // 2. 環境変数
    System.getenv("DOEFF_INDEXER_PATH")?.takeIf { it.isNotBlank() }?.let { path ->
        val file = File(path)
        if (file.canExecute()) {
            return file.absolutePath
        }
    }
    
    // 3. 既存のフォールバック
    return candidates.firstOrNull { File(it).canExecute() }
}
```

## Subtasks

- [ ] VSCode: `getPythonInterpreter()` 関数を実装
- [ ] VSCode: `locateIndexer()` を更新
- [ ] VSCode: ログ出力を追加（どのパスが使われたか）
- [ ] PyCharm: Python SDK からのパス取得を実装
- [ ] PyCharm: `locateIndexerInternal()` を更新
- [ ] 両プラグインでテスト:
  - [ ] Python 環境にインストール済みの場合
  - [ ] インストールされていない場合（フォールバック）

## Related

- Spec: [[SPEC-INDEXER-001-bundled-binary-distribution]]
- Feature: 
- PR: 

## Progress Log

### 2024-12-08
- タスク作成

