// linter の結果のパネル — 「違反(linter)」(規則 → file → 違反、または規則の一覧)と「層の地図(linter)」。
// 木の中身は view.ts の純粋な関数が作り、ここは VS Code の TreeItem へ写すだけ。規則の名と家族は linter の出力から。

import * as vscode from 'vscode';
import type { LintLayer, LintLevel, LintSeverity, LintViolation } from './contract';
import { layerSummary, violationExplanationLines } from './layers';
import {
  displayRange,
  groupHeading,
  groupStanding,
  groupTooltipLines,
  lintChildren,
  mapRoots,
  nodeId,
  panelViolationRoots,
  ruleNodes,
  violationCount,
  violationTrail,
  worstSeverity,
  type LintNode
} from './view';
import type { LintStore } from './store';
import { ALL_VIOLATIONS, summaryDescription, summaryLabel, type PanelFilter, type SavedTally } from './severity';
import type { IconSource } from '../pixel/icons';
import { layerGlyph, ruleIcon, serviceGlyph, violationMark } from '../pixel/vocabulary';
import { mentionLink, REVEAL_ENTITY_COMMAND, REVEAL_VIOLATION_COMMAND, type MentionsOf, type ViolationPlace, type ViolationRef } from '../read/locate';

/**
 * 違反の項目を押した時の命令 — その .hy の読む面で、違反の行を含む定義のカードと source の箱の該当の行へ(v10・#910 U18)。
 * 読む面を切っている時・Hy でない file は、読む面の命令が今までどおり editor で範囲を選んで開く(空の範囲は行全体)。
 */
function openViolation(violation: LintViolation): vscode.Command {
  const r = displayRange(violation.range, undefined);
  const place: ViolationPlace = {
    path: violation.path,
    start: { line: r.start.line, character: r.start.character },
    end: { line: r.end.line, character: r.end.character }
  };
  return { title: '開く', command: REVEAL_VIOLATION_COMMAND, arguments: [place] };
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

/** 重さの codicon(束の最も重い重さ。重さが無ければ law の codicon)。 */
function severityIcon(severity: LintSeverity | undefined): vscode.ThemeIcon {
  switch (severity) {
    case 'error':
      return new vscode.ThemeIcon('error', new vscode.ThemeColor('problemsErrorIcon.foreground'));
    case 'warning':
      return new vscode.ThemeIcon('warning', new vscode.ThemeColor('problemsWarningIcon.foreground'));
    case 'info':
      return new vscode.ThemeIcon('info', new vscode.ThemeColor('problemsInfoIcon.foreground'));
    case undefined:
      return new vscode.ThemeIcon('law');
    default: {
      const unreachable: never = severity;
      throw new Error(`網羅されていない重さ: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 重大さを灯の重さへ写す — critical = 赤・major = 琥珀・info = 青、minor は灯を消す(pixel art の灯は 3 色だけのため)。 */
function levelLamp(level: LintLevel): LintSeverity | null {
  switch (level) {
    case 'critical':
      return 'error';
    case 'major':
      return 'warning';
    case 'minor':
      return null;
    case 'info':
      return 'info';
    default: {
      const unreachable: never = level;
      throw new Error(`網羅されていない重大さ: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 重大さの codicon(minor は灯の消えた丸)。 */
function levelIcon(level: LintLevel): vscode.ThemeIcon {
  const lamp = levelLamp(level);
  return lamp === null ? new vscode.ThemeIcon('circle-outline', new vscode.ThemeColor('descriptionForeground')) : severityIcon(lamp);
}

/** hover の Markdown に linter の文をそのまま出す(`*`・`_`・`<` などを文字として読ませる)。 */
function escapeMarkdown(line: string): string {
  return line.replace(/[\\`*_{}[\]<>()#+!|]/g, (ch) => `\\${ch}`);
}

/** 木の pixel art の icon の口 — 切っている時は undefined(codicon のまま)。 */
export type TreePixels = () => IconSource | undefined;

/** 節の pixel art の icon(当てる icon が無い節・切っている時は undefined — codicon のまま)。 */
function pixelIcon(node: LintNode, icons: IconSource): vscode.Uri | undefined {
  switch (node.tag) {
    case 'summary': {
      // 要約の行 = 重大さの色の印(火・琥珀の旗・青い旗)。minor は印が無いので codicon のまま
      const lamp = levelLamp(node.level);
      return lamp === null ? undefined : icons.icon(lamp === 'error' ? 'lint-error' : lamp === 'warning' ? 'lint-warning' : 'lint-info');
    }
    case 'group':
      // sprite = 規則の家族、灯の色 = 束の重大さ(登録簿に載った分も重大さの色で灯す)
      return icons.lit(ruleIcon(node.summary.family, levelLamp(node.level), node.violations));
    case 'violation':
      return icons.icon(violationMark(node.violation));
    case 'rule':
      // 針のつながっていない規則は linter がまだ見ていない = 霧。つながった規則は家族の sprite(灯は消えたまま)
      return node.rule.wired ? icons.lit(ruleIcon(node.rule.family, null, [])) : icons.icon('jev-unjudged');
    case 'layer': {
      const base = layerGlyph(node.label);
      return base === undefined ? undefined : icons.lit({ glyph: base, severity: worstSeverity(node.entries.flatMap((e) => e.violations)) ?? null, flag: null });
    }
    case 'module': {
      const service = node.entry.module.service;
      return service === null ? undefined : icons.lit({ glyph: serviceGlyph(service), severity: worstSeverity(node.entry.violations) ?? null, flag: null });
    }
    case 'file':
    case 'dir':
    case 'message':
      return undefined;
    default: {
      const unreachable: never = node;
      throw new Error(`網羅されていない節: ${JSON.stringify(unreachable)}`);
    }
  }
}

/**
 * 節を VS Code の TreeItem にする(pixel art の icon があればそれ、無ければ codicon)。id は view.ts の nodeId の写し — 出し直しても
 * 同じ節は同じ id なので、VS Code が展開と選択を保つ(#2162)。
 */
export function lintTreeItem(node: LintNode, layers: readonly LintLayer[], icons?: IconSource, mentions?: MentionsOf): vscode.TreeItem {
  const item = codiconTreeItem(node, layers, mentions);
  item.id = nodeId(node);
  const pixel = icons === undefined ? undefined : pixelIcon(node, icons);
  if (pixel !== undefined) {
    item.iconPath = pixel;
  }
  return item;
}

/** 節を codicon の TreeItem にする。 */
function codiconTreeItem(node: LintNode, layers: readonly LintLayer[], mentions?: MentionsOf): vscode.TreeItem {
  const collapsed = vscode.TreeItemCollapsibleState.Collapsed;
  const none = vscode.TreeItemCollapsibleState.None;
  switch (node.tag) {
    case 'summary': {
      const item = new vscode.TreeItem(summaryLabel(node.level, node.counts), none);
      item.description = summaryDescription(node.counts, node.delta);
      item.tooltip = [
        `${summaryLabel(node.level, node.counts)} — 重大さが ${node.level} の規則の違反(重大さは repo の設定 rules.<ID>.level が決め、登録簿で下げない)`,
        `新しい ${node.counts.new}(登録簿に無い)`,
        `既知 ${node.counts.registered}(登録簿に載った分 — 波線の色は下げてある)`,
        `照合中 ${node.counts.reconciling}(照合中の規則 — info に下げてある)`,
        ...(node.delta === undefined ? [] : ['前回 = この workspace で前に VS Code を開いていた時の最後の数'])
      ].join('\n');
      item.iconPath = levelIcon(node.level);
      return item;
    }
    case 'group': {
      const item = new vscode.TreeItem(groupHeading(node.level, node.rule, node.summary, node.violations.length), collapsed);
      item.description = groupStanding(node.violations);
      // law の名・ADR・規則の文は hover に(見出しは規則の番号と短い名にそろえる)
      item.tooltip = new vscode.MarkdownString(groupTooltipLines(node.rule, node.summary, node.violations).map(escapeMarkdown).join('\n\n'));
      item.iconPath = levelIcon(node.level);
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
      const standing = v.standing === 'registered' ? ' · 既知(登録簿)' : v.standing === 'reconciling' ? ' · 照合中' : '';
      item.description = `${v.range.start.line + 1} 行 · ${v.rule}${standing}`;
      // 文の中の実体の名は、読む面のその定義のカードへの link(v12 — 違反の欄の名も押せる)
      const named = mentions === undefined ? [] : mentions(v.path, v.message);
      const definitions = named.length === 0 ? [] : [`定義: ${named.map(mentionLink).join('・')}`];
      const tooltip = new vscode.MarkdownString(
        [v.message, ...definitions, ...violationExplanationLines(v), `規則 \`${v.rule}\`${v.law === null ? '' : ` · law \`${v.law}\``}`].join('\n\n')
      );
      tooltip.isTrusted = { enabledCommands: [REVEAL_ENTITY_COMMAND] };
      item.tooltip = tooltip;
      item.command = openViolation(v);
      item.iconPath = new vscode.ThemeIcon(v.severity === 'error' ? 'error' : v.severity === 'warning' ? 'warning' : 'info');
      return item;
    }
    case 'rule': {
      const r = node.rule;
      const item = new vscode.TreeItem(r.title === null ? r.rule : `${r.rule} ${r.title}`, none);
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
  private filterState: PanelFilter = ALL_VIOLATIONS;
  /**
   * 作った節を出し直すまで使い回す — 読む面から表の項目を見せる時(reveal)に、VS Code に渡した節と同じ物を親から辿れるように
   * するため(節は値で、作り直すと別の物になる)。最上段は出し直す時に捨て、子と親の表は節の object に結ぶ
   */
  private rootNodes: LintNode[] | undefined;
  private readonly childNodes = new WeakMap<LintNode, LintNode[]>();
  private readonly parentNodes = new WeakMap<LintNode, LintNode>();

  constructor(
    private readonly store: LintStore,
    private readonly pixels: TreePixels,
    /** 前回の数え(増減の元 — 無ければ増減を出さない) */
    private readonly previous: () => SavedTally | undefined,
    /** 違反の文の中の実体の名を引く口(読む面の索引の表 — tooltip の link) */
    private readonly mentions?: MentionsOf
  ) {}

  /** 今の絞り込み。 */
  get filter(): PanelFilter {
    return this.filterState;
  }

  /** 絞り込みを替えて出し直す。 */
  setFilter(filter: PanelFilter): void {
    this.filterState = filter;
    this.refresh();
  }

  /** 今の出し方(違反か規則の一覧)。 */
  get showing(): 'violations' | 'rules' {
    return this.mode;
  }

  /** 違反の一覧と規則の一覧を切り替える。 */
  toggleRules(): void {
    this.mode = this.mode === 'violations' ? 'rules' : 'violations';
    this.refresh();
  }

  /** 出し直す(作った節を捨てる)。 */
  refresh(): void {
    this.rootNodes = undefined;
    this.changed.fire(undefined);
  }

  /** 購読を止める。 */
  dispose(): void {
    this.changed.dispose();
  }

  /** 節の表示。 */
  getTreeItem(node: LintNode): vscode.TreeItem {
    return lintTreeItem(node, this.store.layers(), this.pixels(), this.mentions);
  }

  /** 節の子(最上段は law の束か規則の一覧)。出し直すまで同じ節を返す。 */
  getChildren(node?: LintNode): LintNode[] {
    if (node === undefined) {
      if (this.rootNodes === undefined) {
        this.rootNodes =
          this.mode === 'violations'
            ? panelViolationRoots(this.store.rootRuns(), this.store.violations(), this.store.rules(), this.filterState, this.previous())
            : ruleNodes(this.store.rules());
      }
      return this.rootNodes;
    }
    const known = this.childNodes.get(node);
    if (known !== undefined) {
      return known;
    }
    const made = lintChildren(node);
    this.childNodes.set(node, made);
    for (const child of made) {
      this.parentNodes.set(child, node);
    }
    return made;
  }

  /** 節の親(最上段は undefined)— VS Code の reveal が親から辿るため。 */
  getParent(node: LintNode): LintNode | undefined {
    return this.parentNodes.get(node);
  }

  /** 目印の違反の節までの道(今の出し方と絞り込みの表に無ければ undefined)— 読む面の吹き出しから表の項目を見せるため。 */
  trailOf(ref: ViolationRef): readonly LintNode[] | undefined {
    return this.mode === 'violations' ? violationTrail(this.getChildren(), ref, (n) => this.getChildren(n)) : undefined;
  }
}

/** 「層の地図(linter)」の木 — linter の modules の層ごと → dir → file。 */
export class LintMapTree implements vscode.TreeDataProvider<LintNode>, vscode.Disposable {
  private readonly changed = new vscode.EventEmitter<LintNode | undefined>();
  readonly onDidChangeTreeData = this.changed.event;

  constructor(
    private readonly store: LintStore,
    private readonly pixels: TreePixels
  ) {}

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
    return lintTreeItem(node, this.store.layers(), this.pixels());
  }

  /** 節の子(最上段は層の束)。 */
  getChildren(node?: LintNode): LintNode[] {
    return node === undefined ? mapRoots(this.store.modules(), this.store.violations()) : lintChildren(node);
  }
}
