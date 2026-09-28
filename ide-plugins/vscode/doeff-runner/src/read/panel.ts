// 定義を読む面の VS Code の層 — `.hy` を webview(custom text editor)で開き、model.ts が組むカードと軸を render.ts の
// HTML で描く。読むためだけの面で、document を書き換えない(operator は source を手で編集しない)。
// 定義の一覧は hy-index の置き場だけから読み、面が自分で file を歩かない。型・effect・違反は linter の置き場から添える。

import * as crypto from 'crypto';
import * as path from 'path';
import * as vscode from 'vscode';
import type { HyIndexStatusView } from '../hy/indexService';
import { emptyIndexLines } from '../hy/indexStatus';
import type { HyIndexStore } from '../hy/store';
import type { LintStore } from '../lint/store';
import type { IconSource } from '../pixel/icons';
import { effectGlyph } from '../pixel/vocabulary';
import { buildCards, facets, parseAxisKey, toggle, visibleCards, type Card, type Selection } from './model';
import { renderFacets, renderPage, summaryText, type Glyphs, type PlaneState } from './render';

/** custom editor の種類の名(package.json の customEditors と同じ)。 */
export const READING_PLANE_VIEW_TYPE = 'doeff-runner.readingPlane';
/** 面を使うかの設定(切ると今の装飾と「タグで閲覧」だけになる)。 */
export const READING_PLANE_SETTING = 'doeff-runner.hy.readingPlane.enabled';
/** 置き場が変わってから描き直すまでの間(続けて変わる時に 1 度で済ませるため)。 */
const REDRAW_DELAY_MS = 200;
/** 頁の中身が変わったかを比べる時の nonce(実際に描く頁は毎回新しい nonce)。 */
const COMPARE_NONCE = 'compare';

/** webview から届く知らせ。 */
type PlaneMessage =
  | { readonly type: 'toggle'; readonly axis: string; readonly value: string }
  | { readonly type: 'clear' }
  | { readonly type: 'open'; readonly line: number; readonly character: number };

/** webview の知らせを形で確かめて読む(知らない形は undefined)。 */
function readMessage(raw: unknown): PlaneMessage | undefined {
  if (typeof raw !== 'object' || raw === null) {
    return undefined;
  }
  const fields = new Map<string, unknown>(Object.entries(raw));
  const type = fields.get('type');
  if (type === 'clear') {
    return { type };
  }
  if (type === 'toggle') {
    const axis = fields.get('axis');
    const value = fields.get('value');
    return typeof axis === 'string' && typeof value === 'string' ? { type, axis, value } : undefined;
  }
  if (type === 'open') {
    const line = fields.get('line');
    const character = fields.get('character');
    return typeof line === 'number' && typeof character === 'number' ? { type, line, character } : undefined;
  }
  return undefined;
}

/** 面を使う設定か。 */
function planeEnabled(): boolean {
  return vscode.workspace.getConfiguration().get<boolean>(READING_PLANE_SETTING) !== false;
}

/** 面 1 枚(開いた document 1 つ)の係 — 選択を持ち、置き場が変わったら描き直す。 */
class PlanePanel implements vscode.Disposable {
  private selection: Selection = new Map();
  private cards: readonly Card[] = [];
  private lastHtml = '';
  private timer: NodeJS.Timeout | undefined;
  private readonly disposables: vscode.Disposable[] = [];

  constructor(
    private readonly document: vscode.TextDocument,
    private readonly panel: vscode.WebviewPanel,
    private readonly hy: HyIndexStore,
    private readonly status: HyIndexStatusView,
    private readonly lint: LintStore,
    private readonly glyphs: Glyphs
  ) {
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
      panel.webview.onDidReceiveMessage((raw: unknown) => this.receive(readMessage(raw)))
    );
    panel.onDidDispose(() => this.dispose());
    this.redraw();
  }

  /** 今の置き場から面の状態を作る(索引にその file が無ければ理由の文)。 */
  private state(): PlaneState {
    if (!planeEnabled()) {
      return { tag: 'message', text: `定義を読む面は設定 ${READING_PLANE_SETTING} で切ってあります` };
    }
    const filePath = this.document.uri.fsPath;
    const entry = this.hy.get(filePath);
    if (entry === undefined) {
      const status = this.status.status;
      const text =
        status.tag === 'ready'
          ? 'この file はまだ Hy の索引(hy-index)に入っていません'
          : emptyIndexLines(status)
              .map((l) => l.label)
              .join(' / ');
      return { tag: 'message', text };
    }
    const seen = this.lint.signaturesFor(filePath);
    this.cards = buildCards({
      definitions: entry.file.definitions,
      signatures: seen !== undefined && seen.version === this.document.version ? seen.signatures : [],
      violations: this.lint.violationsIn(filePath),
      lines: this.document.getText().split(/\r?\n/)
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
    return renderPage({ place: this.place(), state, glyphs: this.glyphs, cspSource: this.panel.webview.cspSource, nonce });
  }

  /** 頁を描き直す(中身が同じなら描かない — 読んでいる位置を崩さないため)。 */
  private redraw(): void {
    const state = this.state();
    const stable = this.page(state, COMPARE_NONCE);
    if (stable === this.lastHtml) {
      return;
    }
    this.lastHtml = stable;
    this.panel.webview.html = this.page(state, crypto.randomBytes(16).toString('hex'));
  }

  /** 選択を変えた結果(札の並び・見せるカード)だけを webview へ送る(頁ごと描き直すと読んでいる位置が飛ぶため)。 */
  private postFilter(): void {
    const shown = visibleCards(this.cards, this.selection);
    const all = facets(this.cards, this.selection);
    void this.panel.webview.postMessage({
      type: 'filter',
      axes: renderFacets(all),
      visible: shown.map((c) => c.id),
      summary: summaryText(shown.length, this.cards.length, all)
    });
    // 次の描き直しで同じ頁を作り直さないよう、選択の入った頁を覚え直す
    this.lastHtml = this.page({ tag: 'cards', cards: this.cards, selection: this.selection }, COMPARE_NONCE);
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
      case 'clear':
        this.selection = new Map();
        this.postFilter();
        return;
      case 'open': {
        const at = new vscode.Position(message.line, message.character);
        void vscode.window.showTextDocument(this.document, { selection: new vscode.Range(at, at), preview: false });
        return;
      }
      default: {
        const unreachable: never = message;
        throw new Error(`網羅されていない知らせ: ${JSON.stringify(unreachable)}`);
      }
    }
  }

  /** 購読と待ちを止める。 */
  dispose(): void {
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
    private readonly watch: (document: vscode.TextDocument) => void
  ) {}

  /** 面を開いた時 — 型と effect の材料を linter に聞かせ、面の係を立てる。 */
  resolveCustomTextEditor(document: vscode.TextDocument, panel: vscode.WebviewPanel): void {
    this.watch(document);
    new PlanePanel(document, panel, this.hy, this.status, this.lint, this.glyphs);
  }
}

/** 定義を読む面の custom editor と「定義を読む面で開く」の命令を登録する。 */
export function registerReadingPlane(
  context: vscode.ExtensionContext,
  hy: HyIndexStore,
  status: HyIndexStatusView,
  lint: LintStore,
  icons: IconSource,
  watch: (document: vscode.TextDocument) => void
): void {
  // effect の絵は装飾 A と同じ pixel art を data URI で(webview の CSP は img-src data: だけを許す)
  const glyphs: Glyphs = { effect: (name) => icons.inline(effectGlyph(name), 14)?.toString(true) };
  context.subscriptions.push(
    vscode.window.registerCustomEditorProvider(READING_PLANE_VIEW_TYPE, new ReadingPlaneProvider(hy, status, lint, glyphs, watch), {
      webviewOptions: { retainContextWhenHidden: true }
    }),
    vscode.commands.registerCommand('doeff-runner.read.open', async (target?: vscode.Uri) => {
      if (!planeEnabled()) {
        void vscode.window.showInformationMessage(`定義を読む面は設定 ${READING_PLANE_SETTING} で切ってあります`);
        return;
      }
      const uri = target ?? vscode.window.activeTextEditor?.document.uri;
      if (uri === undefined) {
        void vscode.window.showInformationMessage('開く Hy の file がありません(Hy の file を開いてから呼んでください)');
        return;
      }
      await vscode.commands.executeCommand('vscode.openWith', uri, READING_PLANE_VIEW_TYPE);
    })
  );
}
