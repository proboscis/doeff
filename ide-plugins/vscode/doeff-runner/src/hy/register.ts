// Hy の行き来の機能の composition root — store・索引の handler・Python の source の handler・provider を組み、
// VS Code に登録する。extension.ts の activate からこの 1 関数だけを呼ぶ。

import * as vscode from 'vscode';
import { ChildProcessHyIndexer, type LocateIndexer } from './indexer';
import { HY_EXCLUDE_GLOB, HyIndexService } from './indexService';
import { ExternalModuleCache } from './externalCache';
import { HyNavigationProvider } from './providers';
import { FsPythonModuleSource } from './pythonSource';
import { HyIndexStore } from './store';
import { FS_CHANGE_STAMPS, UvModuleLocator } from './uvLocator';

/** 子 process 1 回の上限(大きな workspace の root 全体でも止まらない長さ)。 */
const INDEXER_TIMEOUT_MS = 120_000;
/** Python の module を glob で探す時の上限件数。 */
const PYTHON_GLOB_LIMIT = 20;

/** Hy の機能が拡張から受け取る物 — binary の探し方と Output channel。 */
export interface HyNavigationDeps {
  readonly locateIndexer: LocateIndexer;
  readonly output: vscode.OutputChannel;
}

/** Hy の定義へ移動・参照・目次・記号の検索・hover を登録し、索引の保持を始める。 */
export function registerHyNavigation(context: vscode.ExtensionContext, deps: HyNavigationDeps): void {
  const store = new HyIndexStore();
  const indexer = new ChildProcessHyIndexer(deps.locateIndexer, INDEXER_TIMEOUT_MS);
  const python = new FsPythonModuleSource(
    () => (vscode.workspace.workspaceFolders ?? []).map((folder) => folder.uri.fsPath),
    async (relativePath) =>
      (await vscode.workspace.findFiles(`**/${relativePath}`, HY_EXCLUDE_GLOB, PYTHON_GLOB_LIMIT)).map(
        (uri) => uri.fsPath
      )
  );
  const service = new HyIndexService(store, indexer, deps.output, (reason) => {
    void vscode.window.showWarningMessage(
      `doeff-runner: 使っている doeff-indexer が Hy の索引(hy-index)に対応していないため、` +
        `Hy の定義へ移動・参照・目次を止めました。doeff-indexer を新しい版にしてください。(${reason})`
    );
  });
  // workspace の外の module(uv の依存)は workspace の Python 環境に uv で聞き、索引は別の cache に持つ
  const external = new ExternalModuleCache(new UvModuleLocator(), indexer, FS_CHANGE_STAMPS, () => Date.now());
  const provider = new HyNavigationProvider(store, python, external, deps.output);
  const selector: vscode.DocumentSelector = [
    { language: 'hy', scheme: 'file' },
    { pattern: '**/*.{hy,hyk,hyp}', scheme: 'file' }
  ];
  context.subscriptions.push(
    service,
    vscode.languages.registerDefinitionProvider(selector, provider),
    vscode.languages.registerReferenceProvider(selector, provider),
    vscode.languages.registerDocumentSymbolProvider(selector, provider),
    vscode.languages.registerWorkspaceSymbolProvider(provider),
    vscode.languages.registerHoverProvider(selector, provider)
  );
  service.start();
}
