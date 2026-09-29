// 定義を読む面の VS Code の層 — `.hy` を webview(custom text editor)で開き、model.ts が組むカードと軸を render.ts の
// HTML で描く。読むためだけの面で、document を書き換えない(operator は source を手で編集しない)。
// 定義の一覧は hy-index の置き場だけから読み、面が自分で file を歩かない。型・effect・違反は linter の置き場から添える。
// `.hy` を開いた時の既定はこの面(v5・operator 2026-09-29 "when opening hy file the default should be reading view")。

import * as crypto from 'crypto';
import * as path from 'path';
import * as vscode from 'vscode';
import { SourceHighlighter } from '../hy/highlight/host';
import type { SourceColoring } from '../hy/highlight/spans';
import type { HyIndexStatusView } from '../hy/indexService';
import { emptyIndexLines } from '../hy/indexStatus';
import type { HyIndexStore } from '../hy/store';
import type { LintStore } from '../lint/store';
import type { IconSource } from '../pixel/icons';
import { effectGlyph } from '../pixel/vocabulary';
import {
  cardKey,
  foldAll,
  loadFold,
  parseLineField,
  saveFold,
  toggleLineField,
  toggleOpen,
  unfoldAll,
  type FoldState
} from './fold';
import { followEntity } from './goto';
import { LABELS } from './labels';
import { locate, parseLocation, REVEAL_VIOLATION_COMMAND, violationAction, type RevealTarget, type ViolationPlace } from './locate';
import { buildCards, facets, parseAxisKey, SEARCH_KEY, setSearch, toggle, visibleCards, type Card, type Selection } from './model';
import type { Glyphs } from './html';
import { lineClasses, renderFacets, renderPage, renderTreePart, summaryText, type PlaneState } from './render';
import { buildCallGraph, buildCallTree, DEFAULT_TREE_DEPTH, relationOf, type CallGraph, type CallTree, type TreeDirection, type TreeQuery } from './tree';

/** custom editor の種類の名(package.json の customEditors と同じ)。 */
export const READING_PLANE_VIEW_TYPE = 'doeff-runner.readingPlane';
/** 面を使うかの設定(切ると今の装飾と「タグで閲覧」だけになる)。 */
export const READING_PLANE_SETTING = 'doeff-runner.hy.readingPlane.enabled';
/** 置き場が変わってから描き直すまでの間(続けて変わる時に 1 度で済ませるため)。 */
export const REDRAW_DELAY_MS = 200;
/** 頁の中身が変わったかを比べる時の nonce(実際に描く頁は毎回新しい nonce)。 */
export const COMPARE_NONCE = 'compare';
/** 1 行に出す欄の設定を覚える workspace の状態の鍵(全カード・全 file 共通)。 */
const LINE_STATE_KEY = 'doeff-runner.read.line';
/** 開いたカードを覚える workspace の状態の鍵の頭(file ごと)。 */
const OPEN_STATE_PREFIX = 'doeff-runner.read.open:';

/** webview から届く知らせ。 */
export type PlaneMessage =
  | { readonly type: 'toggle'; readonly axis: string; readonly value: string }
  | { readonly type: 'clear' }
  | { readonly type: 'open'; readonly line: number; readonly character: number; readonly qualifiedName: string }
  | { readonly type: 'hydrate'; readonly qualifiedName: string; readonly source: boolean }
  | { readonly type: 'search'; readonly text: string }
  | { readonly type: 'fold'; readonly key: string }
  | { readonly type: 'fold-all' }
  | { readonly type: 'unfold-all' }
  | { readonly type: 'line'; readonly field: string }
  | { readonly type: 'tree'; readonly root: string; readonly direction: TreeDirection }
  | { readonly type: 'tree-direction'; readonly direction: TreeDirection }
  | { readonly type: 'tree-more' }
  | { readonly type: 'tree-close' }
  | { readonly type: 'tree-tests' }
  /** 実体の名を押した(候補の完全修飾名 — 複数なら選ばせる。editor = Cmd / Ctrl を押していた — v12) */
  | { readonly type: 'reveal'; readonly qualifiedNames: readonly string[]; readonly editor: boolean }
  /** 頁が読み込めて知らせを受けられる(html を差し替えた後 — それまで送る知らせは溜める) */
  | { readonly type: 'ready' };

/** 木の向きの文字を読む(知らない値は undefined)。 */
function parseDirection(value: unknown): TreeDirection | undefined {
  return value === 'callees' || value === 'callers' ? value : undefined;
}

/** webview の知らせを形で確かめて読む(知らない形は undefined)。 */
export function readMessage(raw: unknown): PlaneMessage | undefined {
  if (typeof raw !== 'object' || raw === null) {
    return undefined;
  }
  const fields = new Map<string, unknown>(Object.entries(raw));
  const type = fields.get('type');
  const text = (name: string): string | undefined => {
    const value = fields.get(name);
    return typeof value === 'string' ? value : undefined;
  };
  switch (type) {
    case 'clear':
    case 'fold-all':
    case 'unfold-all':
    case 'tree-more':
    case 'tree-close':
    case 'tree-tests':
    case 'ready':
      return { type };
    case 'tree': {
      const root = text('root');
      const direction = parseDirection(fields.get('direction'));
      return root !== undefined && direction !== undefined ? { type, root, direction } : undefined;
    }
    case 'tree-direction': {
      const direction = parseDirection(fields.get('direction'));
      return direction !== undefined ? { type, direction } : undefined;
    }
    case 'reveal': {
      const names = fields.get('qualifiedNames');
      const editor = fields.get('editor');
      const qualifiedNames = Array.isArray(names) && names.every((n): n is string => typeof n === 'string') ? names : undefined;
      return qualifiedNames !== undefined && qualifiedNames.length > 0 && typeof editor === 'boolean' ? { type, qualifiedNames, editor } : undefined;
    }
    case 'toggle': {
      const axis = text('axis');
      const value = text('value');
      return axis !== undefined && value !== undefined ? { type, axis, value } : undefined;
    }
    case 'open': {
      const line = fields.get('line');
      const character = fields.get('character');
      const qualifiedName = text('qualifiedName') ?? '';
      return typeof line === 'number' && typeof character === 'number' ? { type, line, character, qualifiedName } : undefined;
    }
    case 'search': {
      const query = text('text');
      return query !== undefined ? { type, text: query } : undefined;
    }
    case 'hydrate': {
      const qualifiedName = text('qualifiedName');
      const source = fields.get('source');
      return qualifiedName !== undefined && typeof source === 'boolean' ? { type, qualifiedName, source } : undefined;
    }
    case 'fold': {
      const key = text('key');
      return key !== undefined ? { type, key } : undefined;
    }
    case 'line': {
      const field = text('field');
      return field !== undefined ? { type, field } : undefined;
    }
    default:
      return undefined;
  }
}

/** 面を使う設定か。 */
export function planeEnabled(): boolean {
  return vscode.workspace.getConfiguration().get<boolean>(READING_PLANE_SETTING) !== false;
}

/** 畳む状態を覚える口(VS Code の workspace の状態 — 開き直しても同じにするため)。 */
export interface FoldMemory {
  load(filePath: string): FoldState;
  save(filePath: string, state: FoldState): void;
}

/** workspace の状態に畳む状態を書く口を作る。 */
export function workspaceFoldMemory(state: vscode.Memento): FoldMemory {
  return {
    load: (filePath) => loadFold({ open: state.get(OPEN_STATE_PREFIX + filePath), line: state.get(LINE_STATE_KEY) }),
    save: (filePath, fold) => {
      const saved = saveFold(fold);
      void state.update(OPEN_STATE_PREFIX + filePath, saved.open);
      void state.update(LINE_STATE_KEY, saved.line);
    }
  };
}

/** 索引の全 file の呼び出しの表(索引の版が同じ間は作り直さない — カードの関係の数と木が同じ表を使う)。 */
export class GraphTable {
  private cached: { readonly version: number; readonly graph: CallGraph } | undefined;

  constructor(private readonly hy: HyIndexStore) {}

  /** 今の索引の呼び出しの表。 */
  get graph(): CallGraph {
    if (this.cached === undefined || this.cached.version !== this.hy.version) {
      this.cached = { version: this.hy.version, graph: buildCallGraph(this.hy.entries().map((e) => e.file)) };
    }
    return this.cached.graph;
  }
}

/** 面から webview へ送る知らせ。 */
type PlaneOutbound =
  | { readonly type: 'reveal'; readonly id: string; readonly line: number | null; readonly showSource: boolean }
  | { readonly type: 'top' }
  | { readonly type: 'tree'; readonly html: string }
  | { readonly type: 'filter'; readonly axes: string; readonly visible: readonly string[]; readonly summary: string }
  | { readonly type: 'fold'; readonly open: readonly string[]; readonly line: readonly string[]; readonly lineClasses: string };

/** 頁に描かれない知らせ(見せる先)か — 頁を差し替えても捨てずに、読み込めた頁へ送り直す物。 */
function isNavigation(message: PlaneOutbound): boolean {
  return message.type === 'reveal' || message.type === 'top';
}

/** 面どうしの移動 — 木の節の名から、その定義のカードへ(他の file なら、その file を読む面で開いてから見せる)。 */
class PlaneNavigator {
  private readonly panels = new Map<string, PlanePanel>();
  private readonly pending = new Map<string, RevealTarget>();

  /** 開いた面を覚える(file の path ごと)。 */
  register(filePath: string, panel: PlanePanel): void {
    this.panels.set(path.normalize(filePath), panel);
  }

  /** 閉じた面を忘れる。 */
  unregister(filePath: string, panel: PlanePanel): void {
    if (this.panels.get(path.normalize(filePath)) === panel) {
      this.panels.delete(path.normalize(filePath));
    }
  }

  /** この file の面が開いたら見せる定義(読んだら消す)。 */
  takePending(filePath: string): RevealTarget | undefined {
    const key = path.normalize(filePath);
    const found = this.pending.get(key);
    this.pending.delete(key);
    return found;
  }

  /** 他の file の定義を見せる — その file を読む面で開き(開いていれば前に出し)、カードへ。 */
  revealElsewhere(filePath: string, target: RevealTarget): void {
    const open = this.panels.get(path.normalize(filePath));
    if (open === undefined) {
      this.pending.set(path.normalize(filePath), target);
    }
    void vscode.commands.executeCommand('vscode.openWith', vscode.Uri.file(filePath), READING_PLANE_VIEW_TYPE).then(() => open?.reveal(target));
  }

  /** file の面を開いて先頭を見せる(違反の行が定義の外の時 — v10)。 */
  showTop(filePath: string): void {
    const open = this.panels.get(path.normalize(filePath));
    void vscode.commands.executeCommand('vscode.openWith', vscode.Uri.file(filePath), READING_PLANE_VIEW_TYPE).then(() => open?.showTop());
  }
}

/** 面 1 枚(開いた document 1 つ)の係 — 選択と畳む状態を持ち、置き場が変わったら描き直す。 */
class PlanePanel implements vscode.Disposable {
  private selection: Selection = new Map();
  private fold: FoldState;
  /** 開いている呼び出しの木(無ければ undefined) */
  private tree: TreeQuery | undefined;
  /** 開いたら見せる定義(索引がまだ無い時に待つ) */
  private revealWanted: RevealTarget | undefined;
  private cards: readonly Card[] = [];
  private lastHtml = '';
  private timer: NodeJS.Timeout | undefined;
  private readonly disposables: vscode.Disposable[] = [];
  /**
   * 頁が知らせを受けられるか — html を差し替えると、頁が読み込み直して ready を返すまでに送った知らせは捨てられる(U18 の
   * 実物の窓で見つけた: 開いた直後の reveal が届かず、source の箱が開かなかった)ので、それまでは queued に溜める
   */
  private ready = false;
  private queued: PlaneOutbound[] = [];
  /** file 全体の色と、塗った document の版(版が違えば使わない) */
  private coloring: { readonly version: number; readonly value: SourceColoring } | undefined;
  /** 塗っている最中の document の版(同じ版を 2 度塗らないため) */
  private coloringInFlight: number | undefined;

  constructor(
    private readonly document: vscode.TextDocument,
    private readonly panel: vscode.WebviewPanel,
    private readonly hy: HyIndexStore,
    private readonly status: HyIndexStatusView,
    private readonly lint: LintStore,
    private readonly glyphs: Glyphs,
    private readonly memory: FoldMemory,
    private readonly graphs: GraphTable,
    private readonly navigator: PlaneNavigator,
    private readonly highlighter: SourceHighlighter
  ) {
    this.fold = memory.load(document.uri.fsPath);
    this.revealWanted = navigator.takePending(document.uri.fsPath);
    navigator.register(document.uri.fsPath, this);
    panel.webview.options = { enableScripts: true };
    const offHy = hy.onDidChange(() => this.schedule());
    const offLint = lint.onDidChange(() => this.schedule());
    const offStatus = status.onDidChangeStatus(() => this.schedule());
    this.disposables.push(
      { dispose: offHy },
      { dispose: offLint },
      { dispose: offStatus },
      vscode.workspace.onDidChangeTextDocument((event) => {
        if (event.document === document) {
          this.schedule();
        }
      }),
      vscode.workspace.onDidChangeConfiguration((event) => {
        if (event.affectsConfiguration(READING_PLANE_SETTING)) {
          this.schedule();
        }
      }),
      // theme・token の色の設定・記号ごとの色の設定が変わったら塗り直す(editor と同時に面の色も変わるため)
      highlighter.onDidChange(() => {
        this.coloring = undefined;
        this.schedule();
      }),
      panel.webview.onDidReceiveMessage((raw: unknown) => this.receive(readMessage(raw)))
    );
    panel.onDidDispose(() => this.dispose());
    this.redraw();
  }

  /** 定義のカードを見せる(畳んでいれば開き、そこまで送る)。カードがまだ無ければ、次に描いた時に見せる。 */
  reveal(target: RevealTarget): void {
    const card = this.cards.find((c) => c.definition.qualifiedName === target.qualifiedName);
    if (card === undefined) {
      this.revealWanted = target;
      return;
    }
    this.revealWanted = undefined;
    const key = cardKey(card.definition);
    if (!this.fold.open.has(key)) {
      this.setFold(toggleOpen(this.fold, key));
    }
    // 行があれば本体(か source)のその行を光らせる — 行番号 = source の行(1 始まり)。showSource なら source の箱も開く
    this.post({ type: 'reveal', id: card.id, line: target.line === undefined ? null : target.line + 1, showSource: target.showSource });
  }

  /** 面の先頭へ送る(開いている面に、定義の外の行の違反から来た時)。 */
  showTop(): void {
    this.post({ type: 'top' });
  }

  /** 知らせを webview へ送る(頁が読み込み中なら、読み込めた知らせ ready まで溜める)。 */
  private post(message: PlaneOutbound): void {
    if (this.ready) {
      void this.panel.webview.postMessage(message);
    } else {
      this.queued.push(message);
    }
  }

  /** 頁が読み込めた — 溜めた知らせを送った順に送る。 */
  private flush(): void {
    this.ready = true;
    const queued = this.queued;
    this.queued = [];
    for (const message of queued) {
      void this.panel.webview.postMessage(message);
    }
  }

  /** 木の今の形(条件が無ければ undefined)。 */
  private treePart(): { readonly tree: CallTree; readonly showTests: boolean } | undefined {
    if (this.tree === undefined) {
      return undefined;
    }
    const tree = buildCallTree(this.graphs.graph, this.tree);
    return tree === undefined ? undefined : { tree, showTests: this.tree.showTests };
  }

  /** 木の条件を変え、木の欄だけを webview へ送る。 */
  private setTree(next: TreeQuery | undefined): void {
    this.tree = next;
    this.post({ type: 'tree', html: renderTreePart(this.cards, this.graphs.graph, this.glyphs, this.treePart()) });
    this.remember();
  }

  /** 今の置き場から面の状態を作る(索引にその file が無ければ理由の文)。 */
  private state(): PlaneState {
    if (!planeEnabled()) {
      return { tag: 'message', text: `${LABELS.disabled} (${READING_PLANE_SETTING})` };
    }
    const filePath = this.document.uri.fsPath;
    const entry = this.hy.get(filePath);
    if (entry === undefined) {
      const status = this.status.status;
      const text =
        status.tag === 'ready'
          ? LABELS.notIndexed
          : emptyIndexLines(status)
              .map((l) => l.label)
              .join(' / ');
      return { tag: 'message', text };
    }
    const seen = this.lint.signaturesFor(filePath);
    this.cards = buildCards({
      definitions: entry.file.definitions,
      signatures: seen !== undefined && seen.version === this.document.version ? seen.signatures : [],
      bodies: seen !== undefined && seen.version === this.document.version ? seen.bodies : [],
      bindings: seen !== undefined && seen.version === this.document.version ? seen.bindings : [],
      violations: this.lint.violationsIn(filePath),
      lines: this.document.getText().split(/\r?\n/),
      testsOf: (qn) => relationOf(this.graphs.graph, qn).tests,
      place: path.relative(entry.root, filePath)
    });
    return { tag: 'cards', cards: this.cards, selection: this.selection };
  }

  /** 続けて変わる時に 1 度だけ描き直す。 */
  private schedule(): void {
    if (this.timer !== undefined) {
      clearTimeout(this.timer);
    }
    this.timer = setTimeout(() => {
      this.timer = undefined;
      this.redraw();
    }, REDRAW_DELAY_MS);
  }

  /** 置き場の表示 — 索引の root から見た path(索引に無ければ file の名)。 */
  private place(): string {
    const filePath = this.document.uri.fsPath;
    const entry = this.hy.get(filePath);
    return entry === undefined ? path.basename(filePath) : path.relative(entry.root, filePath);
  }

  /** 状態から頁を組む(比べる用には決まった nonce を渡す)。 */
  private page(state: PlaneState, nonce: string): string {
    return renderPage({
      place: this.place(),
      state,
      glyphs: this.glyphs,
      fold: this.fold,
      graph: this.graphs.graph,
      tree: this.treePart(),
      coloring: this.currentColoring(),
      cspSource: this.panel.webview.cspSource,
      nonce
    });
  }

  /** 頁を描き直す(中身が同じなら描かない — 読んでいる位置を崩さないため)。 */
  private redraw(): void {
    const state = this.state();
    const stable = this.page(state, COMPARE_NONCE);
    if (stable === this.lastHtml) {
      return;
    }
    this.lastHtml = stable;
    // 新しい頁は今の畳む・選択・木を描くので、溜めた知らせのうち頁に描かれない見せる先だけを残す
    this.ready = false;
    this.queued = this.queued.filter(isNavigation);
    this.panel.webview.html = this.page(state, crypto.randomBytes(16).toString('hex'));
    if (this.revealWanted !== undefined) {
      this.reveal(this.revealWanted);
    }
    this.ensureColoring();
  }

  /** 今の document の版の色(版が違う・まだ塗れていなければ undefined — source は色なしで描く)。 */
  private currentColoring(): SourceColoring | undefined {
    return this.coloring !== undefined && this.coloring.version === this.document.version ? this.coloring.value : undefined;
  }

  /**
   * 今の document の版の色が無ければ、file 全体を editor と同じ色で塗り、塗れたら描き直す(source の箱が色つきになる)。
   * 塗っている間に document が変わった結果は捨てる。
   */
  private ensureColoring(): void {
    const version = this.document.version;
    if (this.currentColoring() !== undefined || this.coloringInFlight === version) {
      return;
    }
    this.coloringInFlight = version;
    void this.highlighter.highlight(this.document.getText()).then((value) => {
      if (this.coloringInFlight === version) {
        this.coloringInFlight = undefined;
      }
      if (this.document.version === version) {
        this.coloring = { version, value };
        this.schedule();
      }
    });
  }

  /** 送った変化の入った頁を覚え直す(次の描き直しで同じ頁を作り直さないため)。 */
  private remember(): void {
    this.lastHtml = this.page({ tag: 'cards', cards: this.cards, selection: this.selection }, COMPARE_NONCE);
  }

  /** 選択を変えた結果(札の並び・見せるカード)だけを webview へ送る(頁ごと描き直すと読んでいる位置が飛ぶため)。 */
  private postFilter(): void {
    const shown = visibleCards(this.cards, this.selection);
    const all = facets(this.cards, this.selection);
    this.post({
      type: 'filter',
      axes: renderFacets(all, Number.POSITIVE_INFINITY),
      visible: shown.map((c) => c.id),
      summary: summaryText(shown.length, this.cards.length, all)
    });
    this.remember();
  }

  /** 畳む状態を変え、覚えて、webview へ送る。 */
  private setFold(next: FoldState): void {
    this.fold = next;
    this.memory.save(this.document.uri.fsPath, next);
    this.post({ type: 'fold', open: [...next.open], line: [...next.line], lineClasses: lineClasses(next) });
    this.remember();
  }

  /** webview の知らせに応える。 */
  private receive(message: PlaneMessage | undefined): void {
    if (message === undefined) {
      return;
    }
    switch (message.type) {
      case 'toggle': {
        const axis = parseAxisKey(message.axis);
        if (axis !== undefined) {
          this.selection = toggle(this.selection, axis, message.value);
          this.postFilter();
        }
        return;
      }
      case 'clear': {
        // 名の検索は欄に文字が残るので、軸の選択だけを外す
        const search = this.selection.get(SEARCH_KEY);
        this.selection = search === undefined ? new Map() : new Map([[SEARCH_KEY, search]]);
        this.postFilter();
        return;
      }
      case 'search':
        this.selection = setSearch(this.selection, message.text);
        this.postFilter();
        return;
      case 'open': {
        const at = new vscode.Position(message.line, message.character);
        void vscode.window.showTextDocument(this.document, { selection: new vscode.Range(at, at), preview: false });
        return;
      }
      case 'hydrate':
        // この面のカードは開いた document から切り出した source を持つので、読み込む物は無い
        return;
      case 'ready':
        this.flush();
        return;
      case 'fold':
        this.setFold(toggleOpen(this.fold, message.key));
        return;
      case 'fold-all':
        this.setFold(foldAll(this.fold));
        return;
      case 'unfold-all':
        this.setFold(unfoldAll(this.fold, visibleCards(this.cards, this.selection).map((c) => cardKey(c.definition))));
        return;
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
        // 名を押した — そのカードへ(別の file はその file の面を開いてから)。Cmd / Ctrl なら editor へ(v12)
        void followEntity(this.graphs.graph, message.qualifiedNames, message.editor, (to) => {
          const target: RevealTarget = { qualifiedName: to.qualifiedName, line: to.line, showSource: false };
          if (path.normalize(to.path) === path.normalize(this.document.uri.fsPath)) {
            this.reveal(target);
          } else {
            this.navigator.revealElsewhere(to.path, target);
          }
        });
        return;
      default: {
        const unreachable: never = message;
        throw new Error(`網羅されていない知らせ: ${JSON.stringify(unreachable)}`);
      }
    }
  }

  /** 購読と待ちを止める。 */
  dispose(): void {
    this.navigator.unregister(this.document.uri.fsPath, this);
    if (this.timer !== undefined) {
      clearTimeout(this.timer);
    }
    for (const d of this.disposables) {
      d.dispose();
    }
    this.disposables.length = 0;
  }
}

/** `.hy` を読む面で開く custom editor(document は読むだけ)。 */
class ReadingPlaneProvider implements vscode.CustomTextEditorProvider {
  constructor(
    private readonly hy: HyIndexStore,
    private readonly status: HyIndexStatusView,
    private readonly lint: LintStore,
    private readonly glyphs: Glyphs,
    private readonly memory: FoldMemory,
    private readonly graphs: GraphTable,
    private readonly navigator: PlaneNavigator,
    private readonly highlighter: SourceHighlighter,
    private readonly watch: (document: vscode.TextDocument) => void
  ) {}

  /**
   * 面を開いた時 — 型と effect の材料を linter に聞かせ、面の係を立てる。面を設定で切ってある時は、既定で開かれても
   * 面を閉じて今の editor で開き直す(v5: 既定は読む面・切れば今の装飾と「タグで閲覧」だけ)。
   */
  resolveCustomTextEditor(document: vscode.TextDocument, panel: vscode.WebviewPanel): void {
    if (!planeEnabled()) {
      panel.dispose();
      void vscode.commands.executeCommand('vscode.openWith', document.uri, 'default');
      return;
    }
    this.watch(document);
    new PlanePanel(document, panel, this.hy, this.status, this.lint, this.glyphs, this.memory, this.graphs, this.navigator, this.highlighter);
  }
}

/** file の面と repo 全体の面が共有する部品(同じ絵・同じ畳む状態の覚え・同じ呼び出しの表・同じ色の係)。 */
export interface ReadingPlaneParts {
  readonly glyphs: Glyphs;
  readonly memory: FoldMemory;
  readonly graphs: GraphTable;
  readonly highlighter: SourceHighlighter;
}

/** 定義を読む面の custom editor と「定義を読む面で開く」の命令を登録し、repo 全体の面と共有する部品を返す。 */
export function registerReadingPlane(
  context: vscode.ExtensionContext,
  hy: HyIndexStore,
  status: HyIndexStatusView,
  lint: LintStore,
  icons: IconSource,
  watch: (document: vscode.TextDocument) => void,
  output: vscode.OutputChannel
): ReadingPlaneParts {
  // effect の絵は装飾 A と同じ pixel art を data URI で(webview の CSP は img-src data: だけを許す)
  const glyphs: Glyphs = { effect: (name) => icons.inline(effectGlyph(name), 14)?.toString(true) };
  // source の箱の色 — editor の `.hy` と同じ文法・theme・記号ごとの色(agora-redesign #910 U16)
  const highlighter = new SourceHighlighter(output);
  const memory = workspaceFoldMemory(context.workspaceState);
  const graphs = new GraphTable(hy);
  const navigator = new PlaneNavigator();
  const provider = new ReadingPlaneProvider(hy, status, lint, glyphs, memory, graphs, navigator, highlighter, watch);
  context.subscriptions.push(
    highlighter,
    vscode.window.registerCustomEditorProvider(READING_PLANE_VIEW_TYPE, provider, { webviewOptions: { retainContextWhenHidden: true } }),
    vscode.commands.registerCommand('doeff-runner.read.open', async (target?: vscode.Uri) => {
      if (!planeEnabled()) {
        void vscode.window.showInformationMessage(`${LABELS.disabled} (${READING_PLANE_SETTING})`);
        return;
      }
      const uri = target ?? vscode.window.activeTextEditor?.document.uri;
      if (uri === undefined) {
        void vscode.window.showInformationMessage('開く Hy の file がありません(Hy の file を開いてから呼んでください)');
        return;
      }
      await vscode.commands.executeCommand('vscode.openWith', uri, READING_PLANE_VIEW_TYPE);
    }),
    // file:line(agent の報告・差分・traceback)から、その行を含む定義のカードの該当の行へ(v1 制約 3・#910 V6)
    vscode.commands.registerCommand('doeff-runner.read.revealLocation', async () => {
      const editor = vscode.window.activeTextEditor;
      const typed =
        editor !== undefined && editor.document.uri.fsPath.endsWith('.hy')
          ? `${editor.document.uri.fsPath}:${editor.selection.active.line + 1}`
          : await vscode.window.showInputBox({ title: LABELS.revealLocationTitle, prompt: 'controllers/messaging/core/conversation_input.hy:85' });
      if (typed === undefined) {
        return;
      }
      const roots = (vscode.workspace.workspaceFolders ?? []).map((f) => f.uri.fsPath);
      const located = locate(parseLocation(typed), roots, (p) => hy.get(p)?.file.definitions);
      switch (located.tag) {
        case 'found':
          navigator.revealElsewhere(located.path, { qualifiedName: located.definition.qualifiedName, line: located.line, showSource: false });
          return;
        case 'unreadable':
        case 'not-indexed':
        case 'no-definition':
          void vscode.window.showInformationMessage(located.message);
          return;
        default: {
          const unreachable: never = located;
          throw new Error(`網羅されていない答え: ${JSON.stringify(unreachable)}`);
        }
      }
    }),
    // 違反(linter)の一覧の項目から、その .hy の読む面のカードと source の箱の該当の行へ(v10・#910 U18)
    vscode.commands.registerCommand(REVEAL_VIOLATION_COMMAND, (place: ViolationPlace) => {
      const roots = (vscode.workspace.workspaceFolders ?? []).map((f) => f.uri.fsPath);
      const action = violationAction(place, planeEnabled(), roots, (p) => hy.get(p)?.file.definitions);
      switch (action.tag) {
        case 'card':
          navigator.revealElsewhere(action.path, action.target);
          return;
        case 'plane-top':
          navigator.showTop(action.path);
          void vscode.window.showInformationMessage(action.message);
          return;
        case 'editor': {
          const { start, end } = action.place;
          const selection = new vscode.Range(start.line, start.character, end.line, end.character);
          void vscode.commands.executeCommand('vscode.open', vscode.Uri.file(action.place.path), { selection });
          return;
        }
        default: {
          const unreachable: never = action;
          throw new Error(`網羅されていない答え: ${JSON.stringify(unreachable)}`);
        }
      }
    })
  );
  return { glyphs, memory, graphs, highlighter };
}
