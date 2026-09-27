// Hy の行き来の機能の composition root — store・索引の handler・Python の source の handler・provider・
// ナビゲーションパネルを組み、VS Code に登録する。extension.ts の activate からこの 1 関数だけを呼ぶ。

import * as vscode from 'vscode';
import type { Resolve } from './callGraph';
import { HyCallHierarchyProvider, HyEffectCodeLensProvider } from './effectProviders';
import { EffectGraphSource } from './effects';
import { ChildProcessHyIndexer, type LocateIndexer } from './indexer';
import { HY_EXCLUDE_GLOB, HyIndexService, isHyPath } from './indexService';
import { ExternalModuleCache } from './externalCache';
import { HyNavTreeProvider, NAV_VIEWS, nodeDefinition } from './navPanel';
import type { NavNode } from './navTree';
import { HyNavigationProvider, toRange } from './providers';
import { FsPythonModuleSource } from './pythonSource';
import { resolveDefinition } from './resolve';
import { HyIndexStore } from './store';
import { FS_CHANGE_STAMPS, UvModuleLocator } from './uvLocator';

/** 子 process 1 回の上限(大きな workspace の root 全体でも止まらない長さ)。 */
const INDEXER_TIMEOUT_MS = 120_000;
/** Python の module を glob で探す時の上限件数。 */
const PYTHON_GLOB_LIMIT = 20;
/** 置き場が変わってから注記と木を出し直すまで待つ時間(編集の debounce と重ねて連打を避ける)。 */
const VIEW_REFRESH_DEBOUNCE_MS = 400;

/** Hy の機能が拡張から受け取る物 — binary の探し方と Output channel。 */
export interface HyNavigationDeps {
  readonly locateIndexer: LocateIndexer;
  readonly output: vscode.OutputChannel;
}

/** 今開いている Hy の file の path(Current file の view 用)。 */
function activeHyFile(): string | undefined {
  const document = vscode.window.activeTextEditor?.document;
  if (document === undefined || document.uri.scheme !== 'file') {
    return undefined;
  }
  return document.languageId === 'hy' || isHyPath(document.uri.fsPath) ? document.uri.fsPath : undefined;
}

/** 定義の位置を editor で開き、選んだ状態にする(右クリックの命令の前段)。 */
async function revealNode(node: NavNode | undefined): Promise<vscode.TextEditor | undefined> {
  const ref = node === undefined ? undefined : nodeDefinition(node);
  if (ref === undefined) {
    return undefined;
  }
  const editor = await vscode.window.showTextDocument(vscode.Uri.file(ref.path));
  const range = toRange(ref.definition.range);
  editor.selection = new vscode.Selection(range.start, range.start);
  editor.revealRange(range);
  return editor;
}

/** Hy の定義へ移動・参照・目次・記号の検索・hover・実装・呼び出し階層・注記・パネルを登録し、索引の保持を始める。 */
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
  const graphs = new EffectGraphSource(store, external);
  // 呼び出し階層・注記・パネルの解決は、定義へ移動と同じ解決の論理を通す
  const resolve: Resolve = async (query) => {
    const resolution = await resolveDefinition(store, python, external, query);
    for (const problem of resolution.problems) {
      deps.output.appendLine(`[hy] ${problem}`);
    }
    return resolution;
  };
  const provider = new HyNavigationProvider(store, python, external, graphs, deps.output);
  const hierarchy = new HyCallHierarchyProvider(graphs, resolve);
  const lenses = new HyEffectCodeLensProvider(graphs, resolve);
  const trees = NAV_VIEWS.map((v) => ({ ...v, provider: new HyNavTreeProvider(v.view, graphs, resolve, activeHyFile) }));
  const selector: vscode.DocumentSelector = [
    { language: 'hy', scheme: 'file' },
    { pattern: '**/*.{hy,hyk,hyp}', scheme: 'file' }
  ];

  // 置き場か外の cache が変わったら、少し待って注記と木を出し直す
  let pending: NodeJS.Timeout | undefined;
  const scheduleRefresh = (): void => {
    if (pending !== undefined) {
      clearTimeout(pending);
    }
    pending = setTimeout(() => {
      pending = undefined;
      lenses.refresh();
      for (const tree of trees) {
        tree.provider.refresh();
      }
    }, VIEW_REFRESH_DEBOUNCE_MS);
  };
  const unsubscribeStore = store.onDidChange(scheduleRefresh);
  const unsubscribeExternal = external.onDidChange(scheduleRefresh);

  context.subscriptions.push(
    service,
    lenses,
    { dispose: unsubscribeStore },
    { dispose: unsubscribeExternal },
    { dispose: () => (pending === undefined ? undefined : clearTimeout(pending)) },
    vscode.languages.registerDefinitionProvider(selector, provider),
    vscode.languages.registerImplementationProvider(selector, provider),
    vscode.languages.registerReferenceProvider(selector, provider),
    vscode.languages.registerDocumentSymbolProvider(selector, provider),
    vscode.languages.registerWorkspaceSymbolProvider(provider),
    vscode.languages.registerHoverProvider(selector, provider),
    vscode.languages.registerCallHierarchyProvider(selector, hierarchy),
    vscode.languages.registerCodeLensProvider(selector, lenses),
    vscode.window.onDidChangeActiveTextEditor(() => {
      trees.find((t) => t.view === 'current-file')?.provider.refresh();
    }),
    vscode.commands.registerCommand('doeff-runner.hy.refreshNavigation', () => {
      for (const tree of trees) {
        tree.provider.refresh();
      }
    }),
    vscode.commands.registerCommand('doeff-runner.hy.showReferences', async (node?: NavNode) => {
      if ((await revealNode(node)) !== undefined) {
        await vscode.commands.executeCommand('editor.action.referenceSearch.trigger');
      }
    }),
    vscode.commands.registerCommand('doeff-runner.hy.showCallHierarchy', async (node?: NavNode) => {
      if ((await revealNode(node)) !== undefined) {
        await vscode.commands.executeCommand('editor.showCallHierarchy');
      }
    })
  );
  for (const tree of trees) {
    const treeView = vscode.window.createTreeView(tree.id, { treeDataProvider: tree.provider, showCollapseAll: true });
    context.subscriptions.push(
      tree.provider,
      treeView,
      vscode.commands.registerCommand(`doeff-runner.hy.filter.${tree.view}`, async () => {
        const text = await vscode.window.showInputBox({
          title: `${tree.label} を名前で絞り込む`,
          value: tree.provider.filter,
          prompt: '空にすると全部を出します'
        });
        if (text !== undefined) {
          tree.provider.setFilter(text);
          treeView.message = text === '' ? undefined : `絞り込み: "${text}"`;
        }
      })
    );
  }
  service.start();
}
