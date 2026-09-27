// linter の表示の composition root — 置き場・子 process の handler・係・パネル・命令を組み、VS Code に登録する。
// extension.ts の activate からこの 1 関数だけを呼ぶ。

import * as vscode from 'vscode';
import { LintDecorations } from './decorations';
import { LayerFileDecorations, LayerHover, LayerStatusBar, showLayerTable } from './layerViews';
import { LintMapTree, LintViolationsTree } from './panel';
import { ChildProcessLinter } from './runner';
import { LintService } from './service';
import { LintStore } from './store';

/** 設定の名前(workspace ごとに決められる)。 */
const LINT_COMMAND_SETTING = 'doeff-runner.hy.lintCommand';
/** linter 1 回の上限。 */
const LINT_TIMEOUT_MS = 120_000;

/** workspace の root の設定から linter の命令を読む(空なら無効)。 */
function lintCommandFor(root: string): string {
  const value = vscode.workspace.getConfiguration(undefined, vscode.Uri.file(root)).get<string>(LINT_COMMAND_SETTING);
  return typeof value === 'string' ? value : '';
}

/** (結果の置き場を返す — 「タグで閲覧」が読む)linter の波線・「違反(linter)」・「層の地図(linter)」を登録し、linter に聞き始める。 */
export function registerLint(context: vscode.ExtensionContext, output: vscode.OutputChannel): LintStore {
  const store = new LintStore();
  const linter = new ChildProcessLinter(lintCommandFor, LINT_TIMEOUT_MS);
  const diagnostics = vscode.languages.createDiagnosticCollection('doeff-linter');
  const service = new LintService(store, linter, output, diagnostics);
  const violations = new LintViolationsTree(store);
  const map = new LintMapTree(store);
  // 行末の文・行の左端の印・右端のスクロールバーの印(細い info の波線は色付けの上で見えないため)
  const decorations = new LintDecorations(store, output);
  const violationsView = vscode.window.createTreeView('doeff-lint-violations', { treeDataProvider: violations, showCollapseAll: true });
  const mapView = vscode.window.createTreeView('doeff-lint-map', { treeDataProvider: map, showCollapseAll: true });
  // 置き場が変わったら木を出し直す(波線は係が出し直す)
  const unsubscribe = store.onDidChange(() => {
    violations.refresh();
    map.refresh();
  });
  context.subscriptions.push(
    diagnostics,
    service,
    violations,
    map,
    violationsView,
    mapView,
    { dispose: unsubscribe },
    vscode.commands.registerCommand('doeff-runner.lint.rerun', () => service.lintAll()),
    vscode.commands.registerCommand('doeff-runner.lint.toggleRules', () => {
      violations.toggleRules();
      violationsView.message = violations.showing === 'rules' ? 'linter の規則の一覧(灰色 = 針なしで見ていない規則)' : undefined;
    }),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration(LINT_COMMAND_SETTING)) {
        service.lintAll();
      }
    })
  );
  context.subscriptions.push(decorations);
  // 層を見分ける表示(エクスプローラーの印・ステータスバー・タグと違反の hover)— 文は linter の出力から
  const layerDecorations = new LayerFileDecorations(store);
  context.subscriptions.push(
    layerDecorations,
    vscode.window.registerFileDecorationProvider(layerDecorations),
    new LayerStatusBar(store),
    vscode.languages.registerHoverProvider(
      [{ language: 'hy', scheme: 'file' }, { language: 'python', scheme: 'file' }, { pattern: '**/*.{hy,hyk,hyp}', scheme: 'file' }],
      new LayerHover(store)
    ),
    vscode.commands.registerCommand('doeff-runner.lint.showLayers', () => showLayerTable(store))
  );
  decorations.start();
  service.start();
  return store;
}
