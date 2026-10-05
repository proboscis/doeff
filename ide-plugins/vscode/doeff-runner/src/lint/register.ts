// linter の表示の composition root — 置き場・子 process の handler・係・パネル・命令を組み、VS Code に登録する。
// extension.ts の activate からこの 1 関数だけを呼ぶ。

import * as vscode from 'vscode';
import { LintDecorations } from './decorations';
import { registerDocLint } from './docRegister';
import { LayerFileDecorations, LayerHover, LayerStatusBar, showLayerTable } from './layerViews';
import { LintMapTree, LintViolationsTree, type TreePixels } from './panel';
import type { IconSource } from '../pixel/icons';
import { TREE_ICONS_SETTING } from '../pixel/editor';
import { ChildProcessLinter } from './runner';
import { pauseDelayMs, semanticStatus } from './semantic';
import { LintService } from './service';
import { LintStore } from './store';
import { readViolationRef, REVEAL_IN_VIOLATIONS_COMMAND, type MentionsOf } from '../read/locate';
import {
  ALL_VIOLATIONS,
  filterText,
  levelStatus,
  levelTally,
  nextLevelFilter,
  readSavedTally,
  saveTally,
  type PanelFilter
} from './severity';

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
/** 重大さごとの数えを覚えておく workspace の状態の鍵(次に開いた時の「前回から」の元)。 */
const LEVEL_TALLY_KEY = 'doeff-runner.lint.levelTally';

/** workspace の root の設定から linter の命令を読む(空なら無効)。 */
function lintCommandFor(root: string): string {
  const value = vscode.workspace.getConfiguration(undefined, vscode.Uri.file(root)).get<string>(LINT_COMMAND_SETTING);
  return typeof value === 'string' ? value : '';
}

/** registerLint が返す口 — 結果の置き場と、text editor に出ていない document を linter に聞かせる口。 */
export interface LintRegistration {
  readonly store: LintStore;
  /** 定義を読む面で開いた document の見出しと束縛を聞かせる(agora-redesign #910) */
  readonly watch: (document: vscode.TextDocument) => void;
}

/** (結果の置き場を返す — 「タグで閲覧」と定義を読む面が読む)linter の波線・「違反(linter)」・「層の地図(linter)」を登録し、linter に聞き始める。 */
export function registerLint(
  context: vscode.ExtensionContext,
  output: vscode.OutputChannel,
  pixel: { readonly tree: TreePixels; readonly ownsGutter: (document: vscode.TextDocument) => boolean; readonly icons: IconSource },
  /** 違反の文の中の実体の名を引く口(読む面の索引の表 — v12) */
  mentions?: MentionsOf
): LintRegistration {
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
  // 「拡張が古い」の札を、今の知らない語の有無に合わせる(拡張を入れ直す時を知らせるため)
  const showStale = (): void => {
    const unknown = store.unknownVocabulary();
    if (unknown.length === 0) {
      staleStatus.hide();
      return;
    }
    staleStatus.tooltip = `linter の出力に、この拡張の知らない語がある(その項目だけ一般の見た目にした)。拡張を入れ直すと直る:\n${unknown.slice(0, 10).join('\n')}`;
    staleStatus.show();
  };
  // 知らない語は違反の結果からも見出しの結果からも来る — 両方の知らせを聞く
  const offStale = store.onDidChange(showStale);
  const offStaleSignatures = store.onDidChangeSignatures(showStale);
  context.subscriptions.push(staleStatus, { dispose: offStale }, { dispose: offStaleSignatures });
  // 前回 = この workspace で前に開いていた時の最後の数(起動の時に 1 度だけ読み、以後は今の数を書き続ける)
  const previous = readSavedTally(context.workspaceState.get(LEVEL_TALLY_KEY));
  const violations = new LintViolationsTree(store, pixel.tree, () => previous, mentions);
  const map = new LintMapTree(store, pixel.tree);
  // 行末の文・行の左端の印・右端のスクロールバーの印(細い info の波線は色付けの上で見えないため)。
  // pixel art の gutter が出ている Hy の file では、左端の丸は出さない(1 行に画は 1 つ)
  const decorations = new LintDecorations(store, output, pixel.ownsGutter);
  const violationsView = vscode.window.createTreeView('doeff-lint-violations', { treeDataProvider: violations, showCollapseAll: true });
  const mapView = vscode.window.createTreeView('doeff-lint-map', { treeDataProvider: map, showCollapseAll: true });
  // 手つかずの critical の数を状態バーに常に出す(押すと critical だけに絞った違反の欄)
  const levelStatusItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 37);
  levelStatusItem.command = 'doeff-runner.lint.showCritical';
  const showFilter = (filter: PanelFilter): void => {
    violations.setFilter(filter);
    violationsView.description = filter.level === 'all' && filter.standing === 'all' ? undefined : filterText(filter);
  };
  // 置き場の違反の側が変わったら木を出し直し、重大さの数え(状態バー・欄の badge・覚えておく値)を更新する(波線は係が出し直す)。
  // 木が読むのは違反・module・規則・層・root の実行の状態だけ — 見出しの知らせは聞かない(#2162)
  const unsubscribe = store.onDidChange(() => {
    violations.refresh();
    map.refresh();
    if (store.rootPaths().length === 0) {
      levelStatusItem.hide();
      violationsView.badge = undefined;
      return;
    }
    const tally = levelTally(store.violations());
    const status = levelStatus(tally, previous);
    levelStatusItem.text = status.text;
    levelStatusItem.tooltip = status.tooltip;
    levelStatusItem.backgroundColor = status.alarming ? new vscode.ThemeColor('statusBarItem.errorBackground') : undefined;
    levelStatusItem.show();
    violationsView.badge = { value: tally.critical.total, tooltip: `critical ${tally.critical.total} 件(新しい ${tally.critical.new})` };
    void context.workspaceState.update(LEVEL_TALLY_KEY, saveTally(tally));
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
    service,
    violations,
    map,
    violationsView,
    mapView,
    { dispose: unsubscribe },
    levelStatusItem,
    vscode.commands.registerCommand('doeff-runner.lint.rerun', () => service.lintAll()),
    vscode.commands.registerCommand('doeff-runner.lint.cycleLevel', () =>
      showFilter({ ...violations.filter, level: nextLevelFilter(violations.filter.level) })
    ),
    vscode.commands.registerCommand('doeff-runner.lint.toggleNewOnly', () =>
      showFilter({ ...violations.filter, standing: violations.filter.standing === 'new' ? 'all' : 'new' })
    ),
    vscode.commands.registerCommand('doeff-runner.lint.showAll', () => showFilter(ALL_VIOLATIONS)),
    vscode.commands.registerCommand('doeff-runner.lint.showCritical', () => {
      showFilter({ level: 'critical', standing: 'all' });
      void vscode.commands.executeCommand('doeff-lint-violations.focus');
    }),
    vscode.commands.registerCommand('doeff-runner.lint.toggleRules', () => {
      violations.toggleRules();
      violationsView.message = violations.showing === 'rules' ? 'linter の規則の一覧(灰色 = 針なしで見ていない規則)' : undefined;
    }),
    // 読む面の違反の吹き出しの「show in violations」— 違反の表の該当の項目を見せる(REVEAL_VIOLATION_COMMAND の逆向き・#1685)。
    // 規則の一覧を出していれば違反の一覧へ戻し、絞り込みで隠れていれば絞り込みを外してから探す
    vscode.commands.registerCommand(REVEAL_IN_VIOLATIONS_COMMAND, async (raw: unknown) => {
      const ref = readViolationRef(raw);
      if (ref === undefined) {
        return;
      }
      if (violations.showing === 'rules') {
        violations.toggleRules();
        violationsView.message = undefined;
      }
      const narrowed = violations.filter.level !== 'all' || violations.filter.standing !== 'all';
      const direct = violations.trailOf(ref);
      if (direct === undefined && narrowed) {
        showFilter(ALL_VIOLATIONS);
      }
      const trail = direct ?? violations.trailOf(ref);
      if (trail === undefined) {
        void vscode.window.showInformationMessage(`違反の表に ${ref.rule}(${ref.place.path}:${ref.place.start.line + 1})の項目がありません — linter の結果が変わった可能性があります`);
        return;
      }
      await vscode.commands.executeCommand('doeff-lint-violations.focus');
      await violationsView.reveal(trail[trail.length - 1], { select: true, focus: true, expand: true });
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
  const watchDocs = registerDocLint(context,store,output);
  return { store, watch: (document) => {service.watch(document);watchDocs(document);} };
}
