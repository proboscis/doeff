// ナビゲーションパネルの TreeView — navTree の節を VS Code の TreeItem へ写し、絞り込み・更新・右クリックの命令を持つ。
// 木の中身は navTree の純粋な関数が作り、ここは表示と命令だけを持つ。

import * as vscode from 'vscode';
import type { Resolve } from './callGraph';
import type { HyRange } from './contract';
import type { DefRef, EffectGraphSource } from './effects';
import { childNodes, rootNodes, type NavNode, type NavSection, type NavView } from './navTree';
import { outlineKindOf } from './outline';
import { toRange, toSymbolKind } from './providers';

/** view の id と木の種類の対応(package.json の views と同じ id)。 */
export const NAV_VIEWS: ReadonlyArray<{ readonly id: string; readonly view: NavView; readonly label: string }> = [
  { id: 'doeff-hy-effects', view: 'effects', label: 'Effects' },
  { id: 'doeff-hy-handlers', view: 'handlers', label: 'Handlers' },
  { id: 'doeff-hy-programs', view: 'programs', label: 'Programs' },
  { id: 'doeff-hy-current-file', view: 'current-file', label: 'Current file' }
];

/** 束の見出し。 */
function sectionLabel(section: NavSection): string {
  switch (section) {
    case 'handlers':
      return 'Handlers';
    case 'performed-by':
      return 'Performed by';
    case 'performs':
      return 'Performs';
    case 'calls':
      return 'Calls';
    case 'called-by':
      return 'Called by';
    case 'other-handlers':
      return 'Other handlers';
    default: {
      const unreachable: never = section;
      throw new Error(`網羅されていない束: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 位置へ移動する命令(項目を押した時)。 */
function openCommand(filePath: string, range: HyRange): vscode.Command {
  return { title: '開く', command: 'vscode.open', arguments: [vscode.Uri.file(filePath), { selection: toRange(range) }] };
}

/** file の中の行の表示(`module:行`)。 */
function placeOf(module: string, range: HyRange): string {
  return `${module}:${range.start.line + 1}`;
}

/** 定義の項目の共通の形(押すと移動・右クリックで参照と呼び出し階層)。 */
function definitionItem(ref: DefRef, label: string, description: string, expandable: boolean): vscode.TreeItem {
  const item = new vscode.TreeItem(
    label,
    expandable ? vscode.TreeItemCollapsibleState.Collapsed : vscode.TreeItemCollapsibleState.None
  );
  item.description = description;
  item.tooltip = `${ref.definition.kind} ${ref.definition.name}\n${ref.path}:${ref.definition.range.start.line + 1}`;
  item.command = openCommand(ref.path, ref.definition.range);
  item.contextValue = 'hyDefinition';
  item.iconPath = new vscode.ThemeIcon(symbolIcon(ref));
  return item;
}

/** 定義の種類の codicon の名前。 */
function symbolIcon(ref: DefRef): string {
  switch (toSymbolKind(outlineKindOf(ref.definition.kind))) {
    case vscode.SymbolKind.Class:
      return 'symbol-class';
    case vscode.SymbolKind.Event:
      return 'symbol-event';
    case vscode.SymbolKind.Object:
      return 'symbol-interface';
    case vscode.SymbolKind.Method:
      return 'symbol-method';
    default:
      return 'symbol-function';
  }
}

/** 節を VS Code の TreeItem にする。 */
export function toTreeItem(node: NavNode): vscode.TreeItem {
  switch (node.tag) {
    case 'group': {
      const item = new vscode.TreeItem(node.label, vscode.TreeItemCollapsibleState.Collapsed);
      item.description = `${node.children.length}`;
      item.iconPath = new vscode.ThemeIcon('symbol-namespace');
      return item;
    }
    case 'effect': {
      const cls = node.entry.classes[0];
      const item = new vscode.TreeItem(node.entry.name, vscode.TreeItemCollapsibleState.Collapsed);
      item.iconPath = new vscode.ThemeIcon('symbol-event');
      item.description = cls === undefined ? 'effect(クラスは索引に無い)' : placeOf(cls.module, cls.definition.range);
      if (cls !== undefined) {
        item.command = openCommand(cls.path, cls.definition.range);
        item.contextValue = 'hyDefinition';
        item.tooltip = `effect ${node.entry.name}\n${cls.path}:${cls.definition.range.start.line + 1}`;
      }
      return item;
    }
    case 'handler':
      return definitionItem(node.ref, node.ref.definition.name, placeOf(node.ref.module, node.ref.definition.range), true);
    case 'clause':
      return definitionItem(
        node.ref,
        node.ref.definition.name,
        `${node.ref.definition.container ?? '?'} · ${placeOf(node.ref.module, node.ref.definition.range)}`,
        true
      );
    case 'program':
      return definitionItem(
        node.ref,
        node.ref.definition.name,
        `${node.ref.definition.kind} · ${placeOf(node.ref.module, node.ref.definition.range)}`,
        true
      );
    case 'section': {
      const item = new vscode.TreeItem(sectionLabel(node.section), vscode.TreeItemCollapsibleState.Collapsed);
      item.iconPath = new vscode.ThemeIcon('list-tree');
      return item;
    }
    case 'site': {
      const caller = node.site.caller;
      const item = new vscode.TreeItem(
        caller === null ? '(top level)' : caller.definition.name,
        vscode.TreeItemCollapsibleState.None
      );
      item.description = `${node.site.call.performed ? '撃つ' : '生成'} ${node.site.call.callee} · ${placeOf(node.site.module, node.site.call.range)}`;
      item.command = openCommand(node.site.path, node.site.call.range);
      item.iconPath = new vscode.ThemeIcon('debug-stackframe');
      return item;
    }
    case 'target': {
      const target = node.target;
      switch (target.tag) {
        case 'definition':
          return definitionItem(target.ref, target.ref.definition.name, placeOf(target.ref.module, target.ref.definition.range), false);
        case 'python': {
          const item = new vscode.TreeItem(target.name, vscode.TreeItemCollapsibleState.None);
          item.description = `python · ${placeOf(target.module, target.range)}`;
          item.command = openCommand(target.path, target.range);
          item.iconPath = new vscode.ThemeIcon('symbol-function');
          return item;
        }
        case 'effect-name': {
          const item = new vscode.TreeItem(target.name, vscode.TreeItemCollapsibleState.None);
          item.description = 'effect(クラスは索引に無い)';
          item.command = openCommand(target.site.path, target.site.call.range);
          item.iconPath = new vscode.ThemeIcon('symbol-event');
          return item;
        }
        default: {
          const unreachable: never = target;
          throw new Error(`網羅されていない行き先: ${JSON.stringify(unreachable)}`);
        }
      }
    }
    case 'top-level': {
      const first = node.ranges[0];
      const item = new vscode.TreeItem(`${node.module} の top level`, vscode.TreeItemCollapsibleState.None);
      item.description = `${node.ranges.length} 箇所`;
      if (first !== undefined) {
        item.command = openCommand(node.path, first);
      }
      item.iconPath = new vscode.ThemeIcon('file-code');
      return item;
    }
    case 'cycle': {
      const item = new vscode.TreeItem(node.label, vscode.TreeItemCollapsibleState.None);
      item.description = '(既に開いた経路 — ここで止める)';
      if (node.at !== undefined) {
        item.command = openCommand(node.at.path, node.at.definition.range);
      }
      item.iconPath = new vscode.ThemeIcon('debug-restart');
      return item;
    }
    case 'empty': {
      const item = new vscode.TreeItem(node.label, vscode.TreeItemCollapsibleState.None);
      item.iconPath = new vscode.ThemeIcon('info');
      return item;
    }
    default: {
      const unreachable: never = node;
      throw new Error(`網羅されていない節: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 節が指す定義(右クリックの命令用)。 */
export function nodeDefinition(node: NavNode): DefRef | undefined {
  switch (node.tag) {
    case 'handler':
    case 'clause':
    case 'program':
      return node.ref;
    case 'effect':
      return node.entry.classes[0];
    case 'target':
      return node.target.tag === 'definition' ? node.target.ref : undefined;
    case 'cycle':
      return node.at;
    default:
      return undefined;
  }
}

/** view 1 つの木 — 最上段は置き場の表から作り、子は展開した時に作る。 */
export class HyNavTreeProvider implements vscode.TreeDataProvider<NavNode>, vscode.Disposable {
  private readonly changed = new vscode.EventEmitter<NavNode | undefined>();
  readonly onDidChangeTreeData = this.changed.event;
  private filterText = '';

  constructor(
    private readonly view: NavView,
    private readonly graphs: EffectGraphSource,
    private readonly resolve: Resolve,
    private readonly currentFile: () => string | undefined
  ) {}

  /** 今の絞り込みの文字。 */
  get filter(): string {
    return this.filterText;
  }

  /** 絞り込みを変えて出し直す。 */
  setFilter(text: string): void {
    this.filterText = text;
    this.refresh();
  }

  /** 木を出し直す(置き場の変更・更新のボタン・開いている file の切り替え)。 */
  refresh(): void {
    this.changed.fire(undefined);
  }

  /** 購読を止める。 */
  dispose(): void {
    this.changed.dispose();
  }

  /** 節の表示。 */
  getTreeItem(node: NavNode): vscode.TreeItem {
    return toTreeItem(node);
  }

  /** 節の子(最上段は module の束)。 */
  async getChildren(node?: NavNode): Promise<NavNode[]> {
    const graph = this.graphs.current();
    if (node === undefined) {
      return rootNodes(graph, this.view, this.filterText, this.currentFile());
    }
    return childNodes({ graph, resolve: this.resolve }, node);
  }
}
