// 「タグで閲覧」の view — browse.ts の純粋な関数が作る木を VS Code の TreeItem へ写し、並べ方・絞り込み・保存した見方の
// 命令を持つ。定義は hy-index の置き場から、service・層・違反は linter の置き場から読むだけ(判定しない)。

import * as vscode from 'vscode';
import {
  availableAxes,
  axisId,
  axisLabel,
  browseChildren,
  browseRoots,
  DEFAULT_BROWSE_VIEWS,
  describeView,
  groupViolationCount,
  itemPlace,
  parseBrowseViews,
  upsertView,
  valueCounts,
  violationsOf,
  type Axis,
  type BrowseContext,
  type BrowseItem,
  type BrowseNode,
  type BrowseView,
  type BrowseViewSetting
} from './browse';
import type { HyIndexStore } from './store';
import type { LintStore } from '../lint/store';
import type { TreePixels } from '../lint/panel';
import { TREE_ICONS_SETTING } from '../pixel/editor';
import { worstSeverity } from '../lint/view';
import { axisValueGlyph, kindGlyph } from '../pixel/vocabulary';

/** 保存した見方の設定の名前(workspace の設定に置けば repo で共有できる)。 */
export const BROWSE_VIEWS_SETTING = 'doeff-runner.hy.browseViews';

/** 「タグで閲覧」の木。 */
export class BrowseTree implements vscode.TreeDataProvider<BrowseNode>, vscode.Disposable {
  private readonly changed = new vscode.EventEmitter<BrowseNode | undefined>();
  readonly onDidChangeTreeData = this.changed.event;
  private view: BrowseView = DEFAULT_BROWSE_VIEWS[0];
  private cachedItems: { readonly version: number; readonly items: BrowseItem[] } | undefined;

  constructor(
    private readonly hy: HyIndexStore,
    private readonly lint: LintStore,
    /** 木の pixel art の icon の口(切っている時は undefined — codicon のまま) */
    private readonly pixels: TreePixels
  ) {}

  /** linter の置き場から軸の値を引く口。 */
  get context(): BrowseContext {
    return { lintModule: (p) => this.lint.moduleFor(p), lintViolations: (p) => this.lint.violationsIn(p) };
  }

  /** 今の見方。 */
  get current(): BrowseView {
    return this.view;
  }

  /** 見方を変えて出し直す。 */
  setView(view: BrowseView): void {
    this.view = view;
    this.refresh();
  }

  /** 定義の一覧(hy-index の置き場の版が同じ間は作り直さない)。 */
  items(): BrowseItem[] {
    if (this.cachedItems === undefined || this.cachedItems.version !== this.hy.version) {
      const items: BrowseItem[] = [];
      for (const entry of this.hy.entries()) {
        for (const definition of entry.file.definitions) {
          items.push({ path: entry.file.path, module: entry.file.module, definition });
        }
      }
      this.cachedItems = { version: this.hy.version, items };
    }
    return this.cachedItems.items;
  }

  /** 出し直す。 */
  refresh(): void {
    this.changed.fire(undefined);
  }

  /** 購読を止める。 */
  dispose(): void {
    this.changed.dispose();
  }

  /** 節の表示 — 束は件数と違反の数、定義は押すと移動して範囲を選ぶ。 */
  getTreeItem(node: BrowseNode): vscode.TreeItem {
    switch (node.tag) {
      case 'group': {
        const item = new vscode.TreeItem(node.value, vscode.TreeItemCollapsibleState.Collapsed);
        const violations = groupViolationCount(node.items, this.context);
        item.description = `${axisLabel(node.axis)} · ${node.items.length} 件${violations > 0 ? ` · 違反 ${violations}` : ''}`;
        const icons = this.pixels();
        const severity = worstSeverity(node.items.flatMap((i) => violationsOf(i, this.context))) ?? null;
        const base = axisValueGlyph(node.axis, node.value) ?? null;
        const pixel = icons === undefined || base === null ? undefined : icons.lit({ glyph: base, severity, flag: null });
        item.iconPath = pixel ?? new vscode.ThemeIcon(violations > 0 ? 'warning' : 'symbol-namespace');
        return item;
      }
      case 'item': {
        const d = node.item.definition;
        const item = new vscode.TreeItem(d.name, vscode.TreeItemCollapsibleState.None);
        const own = violationsOf(node.item, this.context);
        item.description = `${d.kind} · ${itemPlace(node.item)}${own.length > 0 ? ` · 違反 ${own.length}` : ''}`;
        const r = d.fullRange;
        const selection = new vscode.Range(r.start.line, r.start.character, r.end.line, r.end.character);
        item.command = { title: '開く', command: 'vscode.open', arguments: [vscode.Uri.file(node.item.path), { selection }] };
        const icons = this.pixels();
        const base = kindGlyph(d.kind) ?? null;
        const pixel = icons === undefined || base === null ? undefined : icons.lit({ glyph: base, severity: worstSeverity(own) ?? null, flag: null });
        item.iconPath = pixel ?? new vscode.ThemeIcon(own.length > 0 ? 'error' : 'symbol-function');
        return item;
      }
      case 'message':
        return new vscode.TreeItem(node.label, vscode.TreeItemCollapsibleState.None);
      default: {
        const unreachable: never = node;
        throw new Error(`網羅されていない節: ${JSON.stringify(unreachable)}`);
      }
    }
  }

  /** 節の子(最上段は 1 段目の軸の束)。 */
  getChildren(node?: BrowseNode): BrowseNode[] {
    if (node === undefined) {
      if (this.items().length === 0) {
        return [{ tag: 'message', label: 'Hy の索引がまだありません' }];
      }
      return browseRoots(this.items(), this.view, this.context);
    }
    return browseChildren(node, this.view, this.context);
  }
}

/** 軸を QuickPick で選ぶ(終わりの項目を足すこともできる)。 */
async function pickAxis(axes: readonly Axis[], title: string, allowDone: boolean): Promise<Axis | 'done' | undefined> {
  const items: Array<vscode.QuickPickItem & { readonly axis: Axis | 'done' }> = [
    ...(allowDone ? [{ label: '$(check) ここまでにする', axis: 'done' as const }] : []),
    ...axes.map((axis) => ({ label: axisLabel(axis), description: axisId(axis), axis }))
  ];
  const picked = await vscode.window.showQuickPick(items, { title });
  return picked?.axis;
}

/** 設定の保存した見方(同梱の見方と合わせた一覧)を読む。読めない見方は Output に理由。 */
function savedViews(output: vscode.OutputChannel): BrowseView[] {
  const parsed = parseBrowseViews(vscode.workspace.getConfiguration().get(BROWSE_VIEWS_SETTING));
  for (const problem of parsed.problems) {
    output.appendLine(`[browse] ${problem}`);
  }
  const names = new Set(parsed.views.map((v) => v.name));
  return [...parsed.views, ...DEFAULT_BROWSE_VIEWS.filter((v) => !names.has(v.name))];
}

/** 「タグで閲覧」の view と命令を登録する。 */
export function registerBrowse(
  context: vscode.ExtensionContext,
  hy: HyIndexStore,
  lint: LintStore,
  output: vscode.OutputChannel,
  pixels: TreePixels
): void {
  const tree = new BrowseTree(hy, lint, pixels);
  const view = vscode.window.createTreeView('doeff-hy-browse', { treeDataProvider: tree, showCollapseAll: true });
  // 今の見方を view の説明欄に出す
  const describe = (): void => {
    view.description = describeView(tree.current);
  };
  describe();
  const unsubscribeHy = hy.onDidChange(() => tree.refresh());
  const unsubscribeLint = lint.onDidChange(() => tree.refresh());
  // 木の pixel art の icon の入り切りで出し直す
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration(TREE_ICONS_SETTING)) {
        tree.refresh();
      }
    })
  );
  context.subscriptions.push(
    tree,
    view,
    { dispose: unsubscribeHy },
    { dispose: unsubscribeLint },
    vscode.commands.registerCommand('doeff-runner.browse.chooseOrder', async () => {
      const axes = availableAxes(tree.items());
      const order: Axis[] = [];
      while (order.length < 3) {
        const remaining = axes.filter((a) => !order.some((o) => axisId(o) === axisId(a)));
        const picked = await pickAxis(remaining, `並べ方 ${order.length + 1} 段目`, order.length > 0);
        if (picked === undefined) {
          return;
        }
        if (picked === 'done') {
          break;
        }
        order.push(picked);
      }
      tree.setView({ name: '(今の見方)', order, filters: tree.current.filters });
      describe();
    }),
    vscode.commands.registerCommand('doeff-runner.browse.addFilter', async () => {
      const axis = await pickAxis(availableAxes(tree.items()), '絞り込む軸', false);
      if (axis === undefined || axis === 'done') {
        return;
      }
      const counts = valueCounts(tree.items(), axis, tree.context);
      const picked = await vscode.window.showQuickPick(
        counts.map((c) => ({ label: c.value, description: `${c.count} 件` })),
        { title: `${axisLabel(axis)} の値(複数可)`, canPickMany: true }
      );
      if (picked === undefined || picked.length === 0) {
        return;
      }
      const added = picked.map((p) => ({ axis, value: p.label }));
      tree.setView({ ...tree.current, name: '(今の見方)', filters: [...tree.current.filters, ...added] });
      describe();
    }),
    vscode.commands.registerCommand('doeff-runner.browse.clearFilters', () => {
      tree.setView({ ...tree.current, filters: [] });
      describe();
    }),
    vscode.commands.registerCommand('doeff-runner.browse.pickView', async () => {
      const views = savedViews(output);
      const picked = await vscode.window.showQuickPick(
        views.map((v) => ({ label: v.name, description: describeView(v), view: v })),
        { title: '保存した見方' }
      );
      if (picked !== undefined) {
        tree.setView(picked.view);
        describe();
      }
    }),
    vscode.commands.registerCommand('doeff-runner.browse.saveView', async () => {
      const name = await vscode.window.showInputBox({ title: '今の見方に名前を付けて保存', prompt: '同じ名前の見方は置き換えます' });
      if (name === undefined || name.trim() === '') {
        return;
      }
      const config = vscode.workspace.getConfiguration();
      const current = parseBrowseViews(config.get(BROWSE_VIEWS_SETTING)).views.map((v): BrowseViewSetting => ({
        name: v.name,
        order: v.order.map(axisId),
        filters: v.filters.map((f) => ({ axis: axisId(f.axis), value: f.value }))
      }));
      const target = vscode.workspace.workspaceFolders === undefined ? vscode.ConfigurationTarget.Global : vscode.ConfigurationTarget.Workspace;
      await config.update(BROWSE_VIEWS_SETTING, upsertView(current, { ...tree.current, name: name.trim() }), target);
      tree.setView({ ...tree.current, name: name.trim() });
      describe();
    })
  );
}
