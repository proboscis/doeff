// linter の表示の composition root — 置き場・子 process の handler・係・パネル・命令を組み、VS Code に登録する。
// extension.ts の activate からこの 1 関数だけを呼ぶ。

import * as vscode from 'vscode';
import { LintDecorations } from './decorations';
import { LayerFileDecorations, LayerHover, LayerStatusBar, showLayerTable } from './layerViews';
import { LintMapTree, LintViolationsTree, type TreePixels } from './panel';
import type { IconSource } from '../pixel/icons';
import { TREE_ICONS_SETTING } from '../pixel/editor';
import { ChildProcessLinter } from './runner';
import { pauseDelayMs, semanticStatus } from './semantic';
import { LintService } from './service';
import { LintStore } from './store';

/** 設定の名前(workspace ごとに決められる)。 */
const LINT_COMMAND_SETTING = 'doeff-runner.hy.lintCommand';
/** linter 1 回の上限。 */
const LINT_TIMEOUT_MS = 120_000;
/** Jev の判定(保存した時・編集中)1 回の上限。 */
const SEMANTIC_TIMEOUT_MS = 30_000;
/** 保存した時に Jev に問うかの設定。 */
const SEMANTIC_ON_SAVE_SETTING = 'doeff-runner.hy.semanticOnSave';
/** 編集中に打つのが止まったら Jev に問うかの設定と、止まってから問うまでの秒。 */
const SEMANTIC_ON_CHANGE_SETTING = 'doeff-runner.hy.semanticOnChange';
const SEMANTIC_ON_CHANGE_DELAY_SETTING = 'doeff-runner.hy.semanticOnChangeDelaySeconds';

/** workspace の root の設定から linter の命令を読む(空なら無効)。 */
function lintCommandFor(root: string): string {
  const value = vscode.workspace.getConfiguration(undefined, vscode.Uri.file(root)).get<string>(LINT_COMMAND_SETTING);
  return typeof value === 'string' ? value : '';
}

/** (結果の置き場を返す — 「タグで閲覧」が読む)linter の波線・「違反(linter)」・「層の地図(linter)」を登録し、linter に聞き始める。 */
export function registerLint(
  context: vscode.ExtensionContext,
  output: vscode.OutputChannel,
  pixel: { readonly tree: TreePixels; readonly ownsGutter: (document: vscode.TextDocument) => boolean; readonly icons: IconSource }
): LintStore {
  const store = new LintStore();
  const linter = new ChildProcessLinter(lintCommandFor, LINT_TIMEOUT_MS);
  const diagnostics = vscode.languages.createDiagnosticCollection('doeff-linter');
  // Jev の判定は別の子 process の口(同時に 1 本・決定的な実行を待たせない)
  const semanticLinter = new ChildProcessLinter(lintCommandFor, SEMANTIC_TIMEOUT_MS);
  const jevStatus = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 39);
  let notifiedOnce = false;
  const service = new LintService(store, linter, output, diagnostics, {
    linter: semanticLinter,
    triggers: () => {
      const config = vscode.workspace.getConfiguration();
      return {
        onSave: config.get<boolean>(SEMANTIC_ON_SAVE_SETTING) !== false,
        onChange: config.get<boolean>(SEMANTIC_ON_CHANGE_SETTING) !== false,
        pauseMs: pauseDelayMs(config.get<number>(SEMANTIC_ON_CHANGE_DELAY_SETTING))
      };
    },
    onState: (state) => {
      const status = semanticStatus(state);
      if (status === undefined) {
        jevStatus.hide();
        return;
      }
      jevStatus.text = status.text;
      jevStatus.tooltip = status.tooltip;
      jevStatus.backgroundColor = status.warning ? new vscode.ThemeColor('statusBarItem.warningBackground') : undefined;
      jevStatus.show();
    },
    notify: (message) => {
      // キーが無いことは session で一度だけ知らせる(キーの値は扱わない)
      if (!notifiedOnce) {
        notifiedOnce = true;
        void vscode.window.showWarningMessage(message);
      }
    }
  });
  context.subscriptions.push(jevStatus);
  // linter の出力に拡張の知らない語がある(linter の方が新しい)時の知らせ — その項目だけ既定の見た目にして描き続ける(#848)
  const staleStatus = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 38);
  staleStatus.text = '$(warning) doeff: 拡張が古い';
  staleStatus.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
  const offStale = store.onDidChange(() => {
    const unknown = store.unknownVocabulary();
    if (unknown.length === 0) {
      staleStatus.hide();
      return;
    }
    staleStatus.tooltip = `linter の出力に、この拡張の知らない語がある(その項目だけ一般の見た目にした)。拡張を入れ直すと直る:\n${unknown.slice(0, 10).join('\n')}`;
    staleStatus.show();
  });
  context.subscriptions.push(staleStatus, { dispose: offStale });
  const violations = new LintViolationsTree(store, pixel.tree);
  const map = new LintMapTree(store, pixel.tree);
  // 行末の文・行の左端の印・右端のスクロールバーの印(細い info の波線は色付けの上で見えないため)。
  // pixel art の gutter が出ている Hy の file では、左端の丸は出さない(1 行に画は 1 つ)
  const decorations = new LintDecorations(store, output, pixel.ownsGutter);
  const violationsView = vscode.window.createTreeView('doeff-lint-violations', { treeDataProvider: violations, showCollapseAll: true });
  const mapView = vscode.window.createTreeView('doeff-lint-map', { treeDataProvider: map, showCollapseAll: true });
  // 置き場が変わったら木を出し直す(波線は係が出し直す)
  const unsubscribe = store.onDidChange(() => {
    violations.refresh();
    map.refresh();
  });
  // 木の pixel art の icon の入り切りで出し直す
  const treeSetting = vscode.workspace.onDidChangeConfiguration((event) => {
    if (event.affectsConfiguration(TREE_ICONS_SETTING)) {
      violations.refresh();
      map.refresh();
    }
  });
  context.subscriptions.push(treeSetting);
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
      new LayerHover(store, pixel.icons)
    ),
    vscode.commands.registerCommand('doeff-runner.lint.showLayers', () => showLayerTable(store))
  );
  decorations.start();
  service.start();
  return store;
}
