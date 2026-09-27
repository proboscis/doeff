// linter の結果のパネル — 「違反(linter)」(law → file → 違反、または規則の一覧)と「層の地図(linter)」。
// 木の中身は view.ts の純粋な関数が作り、ここは VS Code の TreeItem へ写すだけ。色は違反の有無だけで決める。

import * as vscode from 'vscode';
import type { LintLayer, LintViolation } from './contract';
import { layerSummary, violationExplanationLines } from './layers';
import { displayRange, lintChildren, mapRoots, ruleNodes, violationCount, violationRoots, type LintNode } from './view';
import type { LintStore } from './store';

/** 違反の位置へ移動し、その範囲を選ぶ命令(空の範囲は行全体)。 */
function openViolation(violation: LintViolation): vscode.Command {
  const r = displayRange(violation.range, undefined);
  const selection = new vscode.Range(r.start.line, r.start.character, r.end.line, r.end.character);
  return { title: '開く', command: 'vscode.open', arguments: [vscode.Uri.file(violation.path), { selection }] };
}

/** 位置へ移動する命令。 */
function openAt(filePath: string, line: number, character: number): vscode.Command {
  const at = new vscode.Position(line, character);
  return { title: '開く', command: 'vscode.open', arguments: [vscode.Uri.file(filePath), { selection: new vscode.Range(at, at) }] };
}

/** 違反の有無で色を付けた codicon(違反あり = 問題の一覧の赤、無し = 通った印)。 */
function countIcon(count: number, base: string): vscode.ThemeIcon {
  return count > 0
    ? new vscode.ThemeIcon(base, new vscode.ThemeColor('problemsErrorIcon.foreground'))
    : new vscode.ThemeIcon(base, new vscode.ThemeColor('testing.iconPassed'));
}

/** 節を VS Code の TreeItem にする。 */
export function lintTreeItem(node: LintNode, layers: readonly LintLayer[]): vscode.TreeItem {
  const collapsed = vscode.TreeItemCollapsibleState.Collapsed;
  const none = vscode.TreeItemCollapsibleState.None;
  switch (node.tag) {
    case 'law': {
      const item = new vscode.TreeItem(node.label, collapsed);
      item.description = `${node.violations.length} 件`;
      item.iconPath = new vscode.ThemeIcon('law');
      return item;
    }
    case 'file': {
      const item = new vscode.TreeItem(node.label, collapsed);
      item.description = `${node.violations.length} 件 · ${vscode.workspace.asRelativePath(node.path)}`;
      item.resourceUri = vscode.Uri.file(node.path);
      item.iconPath = vscode.ThemeIcon.File;
      return item;
    }
    case 'violation': {
      const v = node.violation;
      const item = new vscode.TreeItem(v.message, none);
      item.description = `${v.range.start.line + 1} 行 · ${v.rule}${v.registered ? ' · 登録簿' : ''}`;
      item.tooltip = new vscode.MarkdownString(
        [v.message, ...violationExplanationLines(v), `規則 \`${v.rule}\`${v.law === null ? '' : ` · law \`${v.law}\``}`].join('\n\n')
      );
      item.command = openViolation(v);
      item.iconPath = new vscode.ThemeIcon(v.severity === 'error' ? 'error' : v.severity === 'warning' ? 'warning' : 'info');
      return item;
    }
    case 'rule': {
      const r = node.rule;
      const item = new vscode.TreeItem(r.rule, none);
      item.description = r.wired ? r.adr ?? '' : '(針なし — linter はこの規則をまだ見ていない)';
      item.tooltip = r.statement;
      item.iconPath = r.wired
        ? new vscode.ThemeIcon('pass')
        : new vscode.ThemeIcon('circle-slash', new vscode.ThemeColor('disabledForeground'));
      return item;
    }
    case 'layer': {
      const item = new vscode.TreeItem(node.label, collapsed);
      const count = violationCount(node);
      // 層の一行の説明は linter の layers から(出していなければ数だけ)
      const summary = layerSummary(node.label, layers);
      item.description = `${summary === '' ? '' : `${summary} · `}${node.entries.length} file · 違反 ${count}`;
      item.iconPath = countIcon(count, 'layers');
      return item;
    }
    case 'dir': {
      const item = new vscode.TreeItem(node.label, collapsed);
      const count = violationCount(node);
      item.description = `${node.entries.length} file · 違反 ${count}`;
      item.iconPath = countIcon(count, 'folder');
      return item;
    }
    case 'module': {
      const m = node.entry.module;
      // 違反のある file は展開するとその違反(行・規則・文)が出る
      const state = node.entry.violations.length > 0 ? collapsed : none;
      const item = new vscode.TreeItem(node.entry.relative.split(/[\\/]/).pop() ?? node.entry.relative, state);
      const parts = [m.context, m.role].filter((p): p is string => p !== null);
      item.description = `${parts.join(' · ')}${parts.length > 0 ? ' · ' : ''}違反 ${m.violations}`;
      item.tooltip = m.layerReason === null ? node.entry.relative : `${node.entry.relative}\n${m.layerReason}`;
      item.command = openAt(m.path, 0, 0);
      item.iconPath = countIcon(m.violations, 'file-code');
      return item;
    }
    case 'message': {
      const item = new vscode.TreeItem(node.label, none);
      item.iconPath = new vscode.ThemeIcon('info');
      return item;
    }
    default: {
      const unreachable: never = node;
      throw new Error(`網羅されていない節: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 「違反(linter)」の木 — 違反の一覧と規則の一覧を切り替える。 */
export class LintViolationsTree implements vscode.TreeDataProvider<LintNode>, vscode.Disposable {
  private readonly changed = new vscode.EventEmitter<LintNode | undefined>();
  readonly onDidChangeTreeData = this.changed.event;
  private mode: 'violations' | 'rules' = 'violations';

  constructor(private readonly store: LintStore) {}

  /** 今の出し方(違反か規則の一覧)。 */
  get showing(): 'violations' | 'rules' {
    return this.mode;
  }

  /** 違反の一覧と規則の一覧を切り替える。 */
  toggleRules(): void {
    this.mode = this.mode === 'violations' ? 'rules' : 'violations';
    this.refresh();
  }

  /** 出し直す。 */
  refresh(): void {
    this.changed.fire(undefined);
  }

  /** 購読を止める。 */
  dispose(): void {
    this.changed.dispose();
  }

  /** 節の表示。 */
  getTreeItem(node: LintNode): vscode.TreeItem {
    return lintTreeItem(node, this.store.layers());
  }

  /** 節の子(最上段は law の束か規則の一覧)。 */
  getChildren(node?: LintNode): LintNode[] {
    if (node !== undefined) {
      return lintChildren(node);
    }
    return this.mode === 'violations' ? violationRoots(this.store.violations()) : ruleNodes(this.store.rules());
  }
}

/** 「層の地図(linter)」の木 — linter の modules の層ごと → dir → file。 */
export class LintMapTree implements vscode.TreeDataProvider<LintNode>, vscode.Disposable {
  private readonly changed = new vscode.EventEmitter<LintNode | undefined>();
  readonly onDidChangeTreeData = this.changed.event;

  constructor(private readonly store: LintStore) {}

  /** 出し直す。 */
  refresh(): void {
    this.changed.fire(undefined);
  }

  /** 購読を止める。 */
  dispose(): void {
    this.changed.dispose();
  }

  /** 節の表示。 */
  getTreeItem(node: LintNode): vscode.TreeItem {
    return lintTreeItem(node, this.store.layers());
  }

  /** 節の子(最上段は層の束)。 */
  getChildren(node?: LintNode): LintNode[] {
    return node === undefined ? mapRoots(this.store.modules(), this.store.violations()) : lintChildren(node);
  }
}
