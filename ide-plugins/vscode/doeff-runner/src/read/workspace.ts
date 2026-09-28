// 定義を読む面の repo 全体の入口(agora-redesign #910 U9)— file を開かずに、索引の全 file の定義を 1 つの面で軸から絞る。
// operator の裁定「単位は定義。入口は木ではなく tag の複数の軸」「file を跨ぐという概念は面に無い」(v1)の本来の形。
// 全カードは索引だけから作り(source を持たない = 軽い)、カードを開く・source を押した時にその file だけを読み込む
// (文字・editor と同じ色・linter の見出しと本体)。木や帯から寄せたカードは「積んだカード」として上に残す(v4 2 節)。

import * as crypto from 'crypto';
import * as path from 'path';
import * as vscode from 'vscode';
import type { SourceHighlighter } from '../hy/highlight/host';
import type { SourceColoring } from '../hy/highlight/spans';
import type { HyIndexStore } from '../hy/store';
import type { LintStore } from '../lint/store';
import { cardKey, foldAll, parseLineField, toggleLineField, toggleOpen, unfoldAll, type FoldState } from './fold';
import type { Glyphs } from './html';
import { LABELS } from './labels';
import { buildCards, parseAxisKey, toggle, visibleCards, type Card, type Selection } from './model';
import { COMPARE_NONCE, planeEnabled, READING_PLANE_SETTING, readMessage, REDRAW_DELAY_MS, type FoldMemory, type GraphTable, type PlaneMessage, type ReadingPlaneParts } from './panel';
import { lineClasses, renderCard, renderPage, renderTreePart, renderWorkspaceCards, type CardContext, type WorkspaceState } from './render';
import { buildCallTree, DEFAULT_TREE_DEPTH, relationOf, type CallTree, type TreeQuery } from './tree';

/** 絞った先のカードを描く上限(repo の定義は 1 万近い — 全部描くと頁が重くなって読めない)。 */
const WORKSPACE_CARD_LIMIT = 200;
/** 左の欄の軸ごとに見せる値の数の上限(型と置き場の軸は値が数百になる)。 */
const WORKSPACE_FACET_LIMIT = 40;
/** 畳む状態を覚える時の、この面の名(file の面は file の path)。 */
const WORKSPACE_MEMORY_KEY = '(workspace)';

/** 読み込んだ file — その file の document(文字と linter の版)と、editor と同じ色。 */
interface HydratedFile {
  readonly document: vscode.TextDocument;
  readonly coloring: SourceColoring | undefined;
}

/** repo 全体の面(webview 1 枚)の係。 */
export class WorkspacePlane implements vscode.Disposable {
  private panel: vscode.WebviewPanel | undefined;
  private selection: Selection = new Map();
  private fold: FoldState;
  private tree: TreeQuery | undefined;
  /** 積んだカードの完全修飾名(新しい物が上) */
  private pinned: string[] = [];
  private readonly hydrated = new Map<string, HydratedFile>();
  private lastHtml = '';
  private timer: NodeJS.Timeout | undefined;
  private readonly disposables: vscode.Disposable[] = [];

  constructor(
    private readonly hy: HyIndexStore,
    private readonly lint: LintStore,
    private readonly glyphs: Glyphs,
    private readonly memory: FoldMemory,
    private readonly graphs: GraphTable,
    private readonly highlighter: SourceHighlighter,
    private readonly watch: (document: vscode.TextDocument) => void
  ) {
    this.fold = memory.load(WORKSPACE_MEMORY_KEY);
  }

  /** 面を開く(開いていれば前に出す)。 */
  show(): void {
    if (this.panel !== undefined) {
      this.panel.reveal();
      return;
    }
    const panel = vscode.window.createWebviewPanel('doeff-runner.readingPlaneAll', `${LABELS.allDefinitions} (doeff)`, vscode.ViewColumn.Active, {
      enableScripts: true,
      retainContextWhenHidden: true
    });
    this.panel = panel;
    const offHy = this.hy.onDidChange(() => this.schedule(false));
    const offLint = this.lint.onDidChange(() => {
      if (this.hydrated.size > 0) {
        this.schedule(true);
      }
    });
    this.disposables.push(
      { dispose: offHy },
      { dispose: offLint },
      this.highlighter.onDidChange(() => {
        this.hydrated.clear();
        this.schedule(false);
      }),
      panel.webview.onDidReceiveMessage((raw: unknown) => void this.receive(readMessage(raw)))
    );
    panel.onDidDispose(() => this.close());
    this.lastHtml = '';
    this.redraw();
  }

  /** 定義 1 つを積んで見せる(木・帯・他の面から寄せる口)。 */
  async stack(qualifiedName: string): Promise<void> {
    this.show();
    await this.pin(qualifiedName);
  }

  /** 索引の全 file のカード(読み込んだ file は文字と linter の見出しつき)。id は file の順で重ならないようにする。 */
  private cards(): Card[] {
    const graph = this.graphs.graph;
    return this.hy.entries().flatMap((entry, index) => {
      const hydrated = this.hydrated.get(path.normalize(entry.file.path));
      const seen = hydrated === undefined ? undefined : this.lint.signaturesFor(entry.file.path);
      const fresh = seen !== undefined && hydrated !== undefined && seen.version === hydrated.document.version ? seen : undefined;
      return buildCards({
        definitions: entry.file.definitions,
        signatures: fresh?.signatures ?? [],
        bodies: fresh?.bodies ?? [],
        violations: hydrated === undefined ? [] : this.lint.violationsIn(entry.file.path),
        lines: hydrated === undefined ? [] : hydrated.document.getText().split(/\r?\n/),
        testsOf: (qn) => relationOf(graph, qn).tests,
        place: path.relative(entry.root, entry.file.path)
      }).map((card) => ({ ...card, id: `f${index}-${card.id}` }));
    });
  }

  /** カードの file 全体の色(読み込んだ file の分だけ)。 */
  private coloringOf(card: Card): SourceColoring | undefined {
    const found = this.graphs.graph.definitions.get(card.definition.qualifiedName);
    return found === undefined ? undefined : this.hydrated.get(path.normalize(found.path))?.coloring;
  }

  /** 今の状態。 */
  private state(cards: readonly Card[]): WorkspaceState {
    return {
      tag: 'workspace',
      cards,
      selection: this.selection,
      pinned: this.pinned,
      limit: WORKSPACE_CARD_LIMIT,
      facetLimit: WORKSPACE_FACET_LIMIT,
      coloringOf: (card) => this.coloringOf(card)
    };
  }

  /** カードを描く材料。 */
  private context(): CardContext {
    return { glyphs: this.glyphs, fold: this.fold, graph: this.graphs.graph, coloringOf: (card) => this.coloringOf(card) };
  }

  /** 木の今の形。 */
  private treePart(): { readonly tree: CallTree; readonly showTests: boolean } | undefined {
    if (this.tree === undefined) {
      return undefined;
    }
    const tree = buildCallTree(this.graphs.graph, this.tree);
    return tree === undefined ? undefined : { tree, showTests: this.tree.showTests };
  }

  /** 頁の全体。 */
  private page(cards: readonly Card[], nonce: string): string {
    if (this.panel === undefined) {
      return '';
    }
    return renderPage({
      place: LABELS.allDefinitions,
      state: this.state(cards),
      glyphs: this.glyphs,
      fold: this.fold,
      graph: this.graphs.graph,
      tree: this.treePart(),
      coloring: undefined,
      cspSource: this.panel.webview.cspSource,
      nonce
    });
  }

  /** 続けて変わる時に 1 度だけ描き直す(listOnly = 右の列と軸だけ送り、読んでいる位置を保つ)。 */
  private schedule(listOnly: boolean): void {
    if (this.timer !== undefined) {
      clearTimeout(this.timer);
    }
    this.timer = setTimeout(() => {
      this.timer = undefined;
      if (listOnly) {
        this.postCards(false);
      } else {
        this.redraw();
      }
    }, REDRAW_DELAY_MS);
  }

  /** 頁を描き直す(中身が同じなら描かない)。 */
  private redraw(): void {
    if (this.panel === undefined) {
      return;
    }
    const cards = this.cards();
    const stable = this.page(cards, COMPARE_NONCE);
    if (stable === this.lastHtml) {
      return;
    }
    this.lastHtml = stable;
    this.panel.webview.html = this.page(cards, crypto.randomBytes(16).toString('hex'));
  }

  /** 右の列(積んだカードと絞った先)と軸と件数だけを送る。 */
  private postCards(scrollTop: boolean): void {
    if (this.panel === undefined) {
      return;
    }
    const cards = this.cards();
    const listed = renderWorkspaceCards(this.state(cards), this.context());
    void this.panel.webview.postMessage({ type: 'cards', html: listed.html, axes: listed.axes, summary: listed.summary, scrollTop });
    this.lastHtml = this.page(cards, COMPARE_NONCE);
  }

  /** カード 1 枚だけを描き直して送る(開いた・読み込んだ時)。 */
  private postCard(qualifiedName: string, showSource: boolean): void {
    const card = this.cards().find((c) => c.definition.qualifiedName === qualifiedName);
    if (this.panel === undefined || card === undefined) {
      return;
    }
    void this.panel.webview.postMessage({ type: 'card', id: card.id, html: renderCard(card, this.context(), false), showSource });
  }

  /** 畳む状態を変え、覚えて、送る。 */
  private setFold(next: FoldState): void {
    this.fold = next;
    this.memory.save(WORKSPACE_MEMORY_KEY, next);
    void this.panel?.webview.postMessage({ type: 'fold', open: [...next.open], line: [...next.line], lineClasses: lineClasses(next) });
  }

  /** 定義の file を読み込む(文字・editor と同じ色・linter の見出しと本体)。読み込み済みなら何もしない。 */
  private async hydrate(qualifiedName: string): Promise<void> {
    const found = this.graphs.graph.definitions.get(qualifiedName);
    if (found === undefined || this.hydrated.has(path.normalize(found.path))) {
      return;
    }
    const document = await vscode.workspace.openTextDocument(vscode.Uri.file(found.path));
    this.watch(document);
    const coloring = await this.highlighter.highlight(document.getText());
    this.hydrated.set(path.normalize(found.path), { document, coloring });
  }

  /** 定義のカードを開いた形にする(畳む状態に足す)。 */
  private openCard(qualifiedName: string): void {
    const found = this.graphs.graph.definitions.get(qualifiedName);
    if (found !== undefined && !this.fold.open.has(cardKey(found.definition))) {
      this.setFold(toggleOpen(this.fold, cardKey(found.definition)));
    }
  }

  /** 定義を積む — 上に置き、開き、その file を読み込む。 */
  private async pin(qualifiedName: string): Promise<void> {
    this.pinned = [qualifiedName, ...this.pinned.filter((qn) => qn !== qualifiedName)];
    this.openCard(qualifiedName);
    await this.hydrate(qualifiedName);
    this.postCards(true);
  }

  /** webview の知らせに応える。 */
  private async receive(message: PlaneMessage | undefined): Promise<void> {
    if (message === undefined) {
      return;
    }
    switch (message.type) {
      case 'toggle': {
        const axis = parseAxisKey(message.axis);
        if (axis !== undefined) {
          this.selection = toggle(this.selection, axis, message.value);
          this.postCards(true);
        }
        return;
      }
      case 'clear':
        this.selection = new Map();
        this.postCards(true);
        return;
      case 'open': {
        const found = this.graphs.graph.definitions.get(message.qualifiedName);
        if (found !== undefined) {
          const at = new vscode.Position(message.line, message.character);
          void vscode.window.showTextDocument(vscode.Uri.file(found.path), { selection: new vscode.Range(at, at), preview: false });
        }
        return;
      }
      case 'hydrate':
        this.openCard(message.qualifiedName);
        await this.hydrate(message.qualifiedName);
        this.postCard(message.qualifiedName, message.source);
        return;
      case 'fold': {
        const opening = !this.fold.open.has(message.key);
        this.setFold(toggleOpen(this.fold, message.key));
        const card = opening ? this.cards().find((c) => cardKey(c.definition) === message.key) : undefined;
        if (card !== undefined && card.source === '') {
          await this.hydrate(card.definition.qualifiedName);
          this.postCard(card.definition.qualifiedName, false);
        }
        return;
      }
      case 'fold-all':
        this.setFold(foldAll(this.fold));
        return;
      case 'unfold-all': {
        const cards = this.cards();
        const shown = visibleCards(cards, this.selection).slice(0, WORKSPACE_CARD_LIMIT);
        this.setFold(unfoldAll(this.fold, shown.map((c) => cardKey(c.definition))));
        return;
      }
      case 'line': {
        const field = parseLineField(message.field);
        if (field !== undefined) {
          this.setFold(toggleLineField(this.fold, field));
        }
        return;
      }
      case 'tree':
        this.setTree({ root: message.root, direction: message.direction, depth: DEFAULT_TREE_DEPTH, showTests: false });
        return;
      case 'tree-direction':
        if (this.tree !== undefined) {
          this.setTree({ ...this.tree, direction: message.direction });
        }
        return;
      case 'tree-more':
        if (this.tree !== undefined) {
          this.setTree({ ...this.tree, depth: this.tree.depth + 1 });
        }
        return;
      case 'tree-tests':
        if (this.tree !== undefined) {
          this.setTree({ ...this.tree, showTests: !this.tree.showTests });
        }
        return;
      case 'tree-close':
        this.setTree(undefined);
        return;
      case 'reveal':
        await this.pin(message.qualifiedName);
        return;
      default: {
        const unreachable: never = message;
        throw new Error(`網羅されていない知らせ: ${JSON.stringify(unreachable)}`);
      }
    }
  }

  /** 木の条件を変え、木の欄だけを送る。 */
  private setTree(next: TreeQuery | undefined): void {
    this.tree = next;
    void this.panel?.webview.postMessage({ type: 'tree', html: renderTreePart(this.cards(), this.graphs.graph, this.glyphs, this.treePart()) });
  }

  /** 面を閉じた時 — 購読を止め、読み込んだ file を忘れる。 */
  private close(): void {
    for (const d of this.disposables) {
      d.dispose();
    }
    this.disposables.length = 0;
    this.hydrated.clear();
    this.panel = undefined;
    if (this.timer !== undefined) {
      clearTimeout(this.timer);
      this.timer = undefined;
    }
  }

  /** 拡張を止める時。 */
  dispose(): void {
    this.panel?.dispose();
    this.close();
  }
}

/** repo 全体の面の命令を登録する(file の面と同じ部品 — 絵・畳む状態の覚え・呼び出しの表・色の係 — を使う)。 */
export function registerWorkspacePlane(
  context: vscode.ExtensionContext,
  hy: HyIndexStore,
  lint: LintStore,
  parts: ReadingPlaneParts,
  watch: (document: vscode.TextDocument) => void
): void {
  const plane = new WorkspacePlane(hy, lint, parts.glyphs, parts.memory, parts.graphs, parts.highlighter, watch);
  context.subscriptions.push(
    plane,
    vscode.commands.registerCommand('doeff-runner.read.openAll', () => {
      if (!planeEnabled()) {
        void vscode.window.showInformationMessage(`${LABELS.disabled} (${READING_PLANE_SETTING})`);
        return;
      }
      plane.show();
    })
  );
}
