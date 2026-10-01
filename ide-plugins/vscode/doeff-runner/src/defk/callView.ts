// 呼びを `f(a, b)` の形で見せる係と、その hover。描く物と場所は calls.ts の純粋な関数が決め、ここは VS Code の飾りへ写すだけ。
// file の文字は変えない(保存・検索・コピーは元の文字のまま)。名・引数は元の文字を残すので、定義へ飛ぶ機能はそのまま効く。
// 材料は linter の editor-json の `rewrites` で、linter に渡した document の版と今の版が違う間は描かない(位置が古いため)。

import * as vscode from 'vscode';
import type { LintRewrite } from '../lint/contract';
import { stampOf, type LintStore } from '../lint/store';
import { effectGlyph } from '../pixel/vocabulary';
import { callMarks, effectHeadSpans, rewriteAt, rewriteHover, shownRewrites } from './calls';
import { OPEN_LOCATION_COMMAND } from './hover';
import type { LineSource, LineSpan, Span } from './model';

/** 呼びの形の置き換えを出すかの設定。 */
export const CALL_SYNTAX_SETTING = 'doeff-runner.defk.callSyntax';

/** 見えている範囲・カーソルの変化から描き直すまで待つ時間。 */
const REFRESH_DELAY_MS = 30;

/** effect の装置の小さな絵(名と css の大きさ → 画。作れなければ undefined)。 */
export type InlineGlyph = (name: string, px: number) => vscode.Uri | undefined;

/** Hy の file の文書か。 */
function isHyDocument(document: vscode.TextDocument): boolean {
  return document.uri.scheme === 'file' && (document.languageId === 'hy' || /\.(hy|hyk|hyp)$/.test(document.uri.fsPath));
}

/** document を行の口にする(純粋な関数に渡すため)。 */
function lineSource(document: vscode.TextDocument): LineSource {
  return { lineCount: document.lineCount, lineText: (line) => document.lineAt(line).text };
}

/** カーソルと選んだ範囲の行(そこは元の lisp で見せるため)。 */
function cursorLines(editor: vscode.TextEditor): LineSpan[] {
  return editor.selections.map((s) => ({ start: s.start.line, end: s.end.line }));
}

/** 文字の中の絵の css の大きさ(文字の大きさに近い 8 の倍数 — #841 の置き換えと同じ決め方)。 */
function inlinePx(): number {
  const fontSize = vscode.workspace.getConfiguration('editor').get<number>('fontSize') ?? 14;
  return 8 * Math.max(1, Math.round(fontSize / 8));
}

/** editor 1 つに今描いている物(hover と #841 の置き換えの除外が読む)。 */
interface Shown {
  readonly version: number;
  readonly rewrites: readonly LintRewrite[];
  readonly indices: readonly number[];
  readonly excluded: readonly Span[];
}

/** 呼びを `f(a, b)` の形で見せる係。 */
export class CallSyntaxView implements vscode.Disposable {
  private readonly hidden: vscode.TextEditorDecorationType;
  private readonly inserted: vscode.TextEditorDecorationType;
  private readonly disposables: vscode.Disposable[] = [];
  private readonly shown = new WeakMap<vscode.TextEditor, Shown>();
  private readonly listeners = new Set<() => void>();
  private enabled: boolean;
  private px = inlinePx();
  private pending: NodeJS.Timeout | undefined;

  constructor(
    private readonly store: LintStore,
    private readonly glyph: InlineGlyph
  ) {
    this.enabled = vscode.workspace.getConfiguration().get<boolean>(CALL_SYNTAX_SETTING) !== false;
    // 元の文字は表示だけ消す(文書の文字・選択・コピーは元のまま)。見せる文字は置いた範囲ごとの before / after
    this.hidden = vscode.window.createTextEditorDecorationType({ textDecoration: 'none; display: none' });
    this.inserted = vscode.window.createTextEditorDecorationType({});
    // 描くのは見出しの置き換えだけ — linter の置き場の見出しの知らせだけを聞く(違反の変化では描き直さない)
    const offStore = store.onDidChangeSignatures(() => this.schedule());
    this.disposables.push(
      { dispose: offStore },
      vscode.window.onDidChangeVisibleTextEditors(() => this.refreshAll()),
      vscode.window.onDidChangeTextEditorSelection((event) => this.refresh(event.textEditor)),
      vscode.workspace.onDidChangeTextDocument(() => this.schedule()),
      vscode.workspace.onDidChangeConfiguration((event) => {
        if (event.affectsConfiguration('editor.fontSize')) {
          this.px = inlinePx();
        }
        if (event.affectsConfiguration(CALL_SYNTAX_SETTING) || event.affectsConfiguration('editor.fontSize')) {
          this.enabled = vscode.workspace.getConfiguration().get<boolean>(CALL_SYNTAX_SETTING) !== false;
          this.refreshAll();
        }
      })
    );
    this.refreshAll();
  }

  /** 描き直しの知らせを購読する(文字の置き換え #841 が、隠した範囲を避けて描き直すため)。 */
  onDidRedraw(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** editor で今文字を隠している範囲と effect の頭(文字の置き換え #841 がそこへ画を描かないため)。 */
  hiddenSpans(editor: vscode.TextEditor): readonly Span[] {
    return this.shown.get(editor)?.excluded ?? [];
  }

  /** 位置にかかる、描いている置き換え(hover のため)。 */
  rewriteAt(document: vscode.TextDocument, position: vscode.Position): LintRewrite | undefined {
    const editor = vscode.window.visibleTextEditors.find((e) => e.document === document);
    const shown = editor === undefined ? undefined : this.shown.get(editor);
    if (shown === undefined || shown.version !== document.version) {
      return undefined;
    }
    return rewriteAt(shown.rewrites, shown.indices, position.line, position.character);
  }

  /** 少し待ってから見えている editor を全部描き直す(編集の連打をまとめる)。 */
  private schedule(): void {
    if (this.pending !== undefined) {
      clearTimeout(this.pending);
    }
    this.pending = setTimeout(() => {
      this.pending = undefined;
      this.refreshAll();
    }, REFRESH_DELAY_MS);
  }

  /** 見えている editor を全部描き直す。 */
  private refreshAll(): void {
    for (const editor of vscode.window.visibleTextEditors) {
      this.refresh(editor);
    }
  }

  /** effect の名の絵の飾り(作れなければ undefined)。 */
  private glyphOf(effect: string): vscode.ThemableDecorationAttachmentRenderOptions | undefined {
    const uri = this.glyph(effectGlyph(effect), this.px);
    return uri === undefined
      ? undefined
      : { contentIconPath: uri, width: `${this.px}px`, height: `${this.px}px`, margin: '0 1px 0 0', textDecoration: 'none; vertical-align: middle' };
  }

  /** editor 1 つを描き直す。 */
  private refresh(editor: vscode.TextEditor): void {
    const document = editor.document;
    // 印(版と中身の hash)が今の document と同じ見出しだけ — 閉じて開き直した document の版 1 を前の版 1 と取り違えない
    const found = this.enabled && isHyDocument(document) ? this.store.currentSignatures(document.uri.fsPath, stampOf(document)) : undefined;
    const hidden: vscode.DecorationOptions[] = [];
    const inserted: vscode.DecorationOptions[] = [];
    let shown: Shown | undefined;
    if (found !== undefined) {
      const indices = shownRewrites(found.rewrites, cursorLines(editor), lineSource(document));
      const color = new vscode.ThemeColor('editor.foreground');
      for (const mark of callMarks(found.rewrites, indices)) {
        const range = new vscode.Range(mark.span.line, mark.span.start, mark.span.line, mark.span.end);
        const glyph = mark.effect === null ? undefined : this.glyphOf(mark.effect);
        const text: vscode.ThemableDecorationAttachmentRenderOptions | undefined = mark.text === '' ? undefined : { contentText: mark.text, color };
        if (mark.span.start === mark.span.end) {
          // 挿すだけ(絵と文字は 1 つの前置きにまとめられないので、絵があれば絵を前に、文字を後ろに)
          inserted.push({ range, renderOptions: glyph === undefined ? { before: text } : { before: glyph, after: text } });
        } else {
          hidden.push({ range, renderOptions: { before: glyph ?? text, after: glyph === undefined ? undefined : text } });
        }
      }
      const excluded = [
        ...callMarks(found.rewrites, indices)
          .filter((m) => m.span.start < m.span.end)
          .map((m) => m.span),
        ...effectHeadSpans(found.rewrites, indices)
      ];
      shown = { version: found.version, rewrites: found.rewrites, indices, excluded };
    }
    editor.setDecorations(this.hidden, hidden);
    editor.setDecorations(this.inserted, inserted);
    if (shown === undefined) {
      this.shown.delete(editor);
    } else {
      this.shown.set(editor, shown);
    }
    for (const listener of this.listeners) {
      listener();
    }
  }

  /** 購読と飾りの型を片づける。 */
  dispose(): void {
    if (this.pending !== undefined) {
      clearTimeout(this.pending);
    }
    for (const d of this.disposables) {
      d.dispose();
    }
    this.hidden.dispose();
    this.inserted.dispose();
  }
}

/** 置き換えた式の hover — 元の lisp と、部品ごとの定義への link と答えの型を見せる。 */
export class CallSyntaxHover implements vscode.HoverProvider {
  constructor(private readonly view: CallSyntaxView) {}

  /** 位置が描いている置き換えの中なら hover を出す。 */
  provideHover(document: vscode.TextDocument, position: vscode.Position): vscode.Hover | undefined {
    const found = this.view.rewriteAt(document, position);
    if (found === undefined) {
      return undefined;
    }
    const markdown = new vscode.MarkdownString(rewriteHover(found, OPEN_LOCATION_COMMAND));
    markdown.isTrusted = { enabledCommands: [OPEN_LOCATION_COMMAND] };
    const { start, end } = found.range;
    return new vscode.Hover(markdown, new vscode.Range(start.line, start.character, end.line, end.character));
  }
}

/** 呼びの形の係・hover・入り切りの命令を登録する(定義へ飛ぶ命令は見出しの係が登録した物を使う)。 */
export function registerCallSyntaxView(context: vscode.ExtensionContext, store: LintStore, glyph: InlineGlyph): CallSyntaxView {
  const view = new CallSyntaxView(store, glyph);
  context.subscriptions.push(
    view,
    vscode.languages.registerHoverProvider([{ language: 'hy', scheme: 'file' }, { pattern: '**/*.{hy,hyk,hyp}', scheme: 'file' }], new CallSyntaxHover(view)),
    vscode.commands.registerCommand('doeff-runner.defk.toggleCallSyntax', async () => {
      const config = vscode.workspace.getConfiguration();
      await config.update(CALL_SYNTAX_SETTING, config.get<boolean>(CALL_SYNTAX_SETTING) === false, vscode.ConfigurationTarget.Global);
    })
  );
  return view;
}
