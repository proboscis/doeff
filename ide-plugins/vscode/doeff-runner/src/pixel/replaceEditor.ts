// 決まった語の文字を表示の上でだけ icon に置き換える係と、その hover。何を置き換えるかは replace.ts の純粋な関数が
// 決め、ここは VS Code の飾りへ写すだけ。file の文字は変えないので、保存・検索・コピー・画面読み上げは元の文字のまま
// (画面読み上げは本文を読み、飾りの画は読まない)。カーソルの行と選んだ範囲の行は元の文字で見せ、見えている範囲にだけ付ける。

import * as vscode from 'vscode';
import type { EffectGraphSource } from '../hy/effects';
import { isHyPath } from '../hy/indexService';
import { mangle } from '../hy/mangle';
import type { IconSource } from './icons';
import {
  findReplacements,
  isShownAsIcon,
  parseReplaceKinds,
  REPLACE_KIND_LABELS,
  REPLACE_KINDS,
  replacementAt,
  replacementHover,
  shownReplacements,
  type LineSpan,
  type Replacement,
  type ReplaceKind
} from './replace';

/** 置き換えの入り切りの設定(全体)。 */
export const REPLACE_SETTING = 'doeff-runner.pixel.replaceText';
/** 置き換えの種類ごとの入り切りの設定。 */
export const REPLACE_KINDS_SETTING = 'doeff-runner.pixel.replaceKinds';

/** 見えている範囲の上下に余分に付ける行の数(少しの scroll で icon が遅れて出ないように)。 */
const VISIBLE_MARGIN = 40;
/** 見えている範囲・編集の変化から出し直すまで待つ時間。 */
const REFRESH_DELAY_MS = 30;

/** Hy の file の文書か(置き換えは Hy の file だけ)。 */
function isHyDocument(document: vscode.TextDocument): boolean {
  return document.uri.scheme === 'file' && (document.languageId === 'hy' || isHyPath(document.uri.fsPath));
}

/** 頭の語が宣言された effect か — effect の表(hy-index から作る)に聞く。`.method` の呼び出しは effect ではない。 */
function effectTest(effects: EffectGraphSource): (name: string) => boolean {
  const graph = effects.current();
  return (name) => {
    if (name.startsWith('.')) {
      return false;
    }
    const last = name.split('.').pop() ?? name;
    return last !== '' && graph.isEffect(mangle(last));
  };
}

/** 文書の置き換えの場所を、文書の版と effect の表が同じ間は使い回す係(飾りと hover が共有する)。 */
export class ReplacementCache {
  private readonly cached = new Map<string, { readonly version: number; readonly graph: unknown; readonly found: Replacement[] }>();

  constructor(private readonly effects: EffectGraphSource) {}

  /** 文書の置き換えの場所(本文の順)。 */
  of(document: vscode.TextDocument): Replacement[] {
    const key = document.uri.toString();
    const graph = this.effects.current();
    const hit = this.cached.get(key);
    if (hit !== undefined && hit.version === document.version && hit.graph === graph) {
      return hit.found;
    }
    const found = findReplacements(document.getText(), effectTest(this.effects));
    this.cached.set(key, { version: document.version, graph, found });
    return found;
  }

  /** 閉じた文書の分を捨てる。 */
  forget(document: vscode.TextDocument): void {
    this.cached.delete(document.uri.toString());
  }
}

/** 今の設定 — 全体の入り切りと、入れた種類。 */
interface ReplaceSettings {
  readonly enabled: ReadonlySet<ReplaceKind>;
}

/** 設定を読む(全体を切っていれば種類は空。読めない値は理由を 1 度だけ出す)。 */
function readSettings(log: { appendLine(line: string): void }, reported: Set<string>): ReplaceSettings {
  const config = vscode.workspace.getConfiguration();
  if (config.get<boolean>(REPLACE_SETTING) === false) {
    return { enabled: new Set() };
  }
  const { enabled, problems } = parseReplaceKinds(config.get(REPLACE_KINDS_SETTING));
  for (const problem of problems) {
    if (!reported.has(problem)) {
      reported.add(problem);
      log.appendLine(`[pixel] 設定 ${REPLACE_KINDS_SETTING}: ${problem}`);
    }
  }
  return { enabled };
}

/** 元の文字で見せる行 — カーソルの行と、選んだ範囲の行(複数のカーソルも全部)。 */
function plainLines(editor: vscode.TextEditor): LineSpan[] {
  return editor.selections.map((s) => ({ start: s.start.line, end: s.end.line }));
}

/** 見えている範囲に余分の行を足した範囲。 */
function visibleLines(editor: vscode.TextEditor): LineSpan[] {
  return editor.visibleRanges.map((r) => ({ start: Math.max(0, r.start.line - VISIBLE_MARGIN), end: r.end.line + VISIBLE_MARGIN }));
}

/** 文字の中の icon の css の大きさ — 8×8 の画を整数倍にして、文字の大きさに近づける(12〜19px の文字なら 16px)。 */
function inlinePx(): number {
  const fontSize = vscode.workspace.getConfiguration('editor').get<number>('fontSize') ?? 14;
  return 8 * Math.max(1, Math.round(fontSize / 8));
}

/** 決まった語の文字を icon に置き換えて見せる係。 */
export class PixelTextReplacer implements vscode.Disposable {
  private readonly types = new Map<string, vscode.TextEditorDecorationType>();
  private readonly disposables: vscode.Disposable[] = [];
  private readonly reported = new Set<string>();
  /** editor ごとの直前に出した中身の鍵(カーソルが同じ行の中で動いただけなら出し直さない) */
  private readonly shown = new WeakMap<vscode.TextEditor, string>();
  private settings: ReplaceSettings;
  private px = inlinePx();
  private pending: NodeJS.Timeout | undefined;

  constructor(
    private readonly cache: ReplacementCache,
    private readonly icons: IconSource,
    private readonly log: { appendLine(line: string): void },
    onIndexChange: (listener: () => void) => () => void
  ) {
    this.settings = readSettings(this.log, this.reported);
    const offIndex = onIndexChange(() => this.schedule());
    this.disposables.push(
      { dispose: offIndex },
      vscode.window.onDidChangeVisibleTextEditors(() => this.refreshAll()),
      vscode.window.onDidChangeTextEditorSelection((event) => this.refresh(event.textEditor)),
      vscode.window.onDidChangeTextEditorVisibleRanges(() => this.schedule()),
      vscode.workspace.onDidChangeTextDocument(() => this.schedule()),
      vscode.workspace.onDidCloseTextDocument((document) => this.cache.forget(document)),
      vscode.workspace.onDidChangeConfiguration((event) => {
        if (event.affectsConfiguration('editor.fontSize')) {
          this.px = inlinePx();
          this.disposeTypes();
        }
        if (event.affectsConfiguration(REPLACE_SETTING) || event.affectsConfiguration(REPLACE_KINDS_SETTING) || event.affectsConfiguration('editor.fontSize')) {
          this.settings = readSettings(this.log, this.reported);
          this.refreshAll(true);
        }
      })
    );
    this.refreshAll();
  }

  /** 置き換えの 1 つが今 icon で見えているか(hover が元の文字を出すかを決める)。 */
  isShownAsIcon(document: vscode.TextDocument, replacement: Replacement): boolean {
    const editor =
      vscode.window.activeTextEditor?.document === document
        ? vscode.window.activeTextEditor
        : vscode.window.visibleTextEditors.find((e) => e.document === document);
    return editor !== undefined && isShownAsIcon(replacement, this.settings.enabled, plainLines(editor));
  }

  /** 少し待ってから見えている editor を全部出し直す(scroll・編集の連打をまとめる)。 */
  private schedule(): void {
    if (this.pending !== undefined) {
      clearTimeout(this.pending);
    }
    this.pending = setTimeout(() => {
      this.pending = undefined;
      this.refreshAll();
    }, REFRESH_DELAY_MS);
  }

  /** 見えている editor を全部出し直す(force なら中身が同じでも)。 */
  private refreshAll(force = false): void {
    for (const editor of vscode.window.visibleTextEditors) {
      this.refresh(editor, force);
    }
  }

  /** 画 1 つ(icon と見せ方)の飾りの型。画が作れなければ undefined。 */
  private typeFor(glyph: string, display: 'replace' | 'mark'): vscode.TextEditorDecorationType | undefined {
    const key = `${glyph}|${display}`;
    const existing = this.types.get(key);
    if (existing !== undefined) {
      return existing;
    }
    const uri = this.icons.inline(glyph, this.px);
    if (uri === undefined) {
      return undefined;
    }
    const before: vscode.ThemableDecorationAttachmentRenderOptions = {
      contentIconPath: uri,
      width: `${this.px}px`,
      height: `${this.px}px`,
      margin: display === 'mark' ? '0 2px 0 0' : '0 1px',
      // 文字の高さの真ん中に置く(textDecoration は css の宣言を足す唯一の口)
      textDecoration: 'none; vertical-align: middle'
    };
    const type = vscode.window.createTextEditorDecorationType(
      display === 'replace'
        ? // 元の文字は表示だけ消す(文書の文字・選択・コピー・画面読み上げは元のまま)
          { before, textDecoration: 'none; display: none' }
        : { before }
    );
    this.types.set(key, type);
    return type;
  }

  /** editor 1 つの置き換えを出し直す。 */
  private refresh(editor: vscode.TextEditor, force = false): void {
    const document = editor.document;
    const all = isHyDocument(document) && this.settings.enabled.size > 0 ? this.cache.of(document) : [];
    const plain = plainLines(editor);
    const visible = visibleLines(editor);
    const shown = shownReplacements(all, this.settings.enabled, plain, visible);
    const key = `${document.version}|${shown.map((r) => `${r.line}:${r.start}:${r.end}:${r.glyph}`).join(',')}`;
    if (!force && this.shown.get(editor) === key) {
      return;
    }
    this.shown.set(editor, key);
    const byType = new Map<vscode.TextEditorDecorationType, vscode.Range[]>();
    for (const r of shown) {
      const type = this.typeFor(r.glyph, r.display);
      if (type === undefined || r.line >= document.lineCount) {
        continue;
      }
      // replace は語を隠して前に icon、mark は語を残して前に icon(どちらも語の範囲に付ける)
      byType.set(type, [...(byType.get(type) ?? []), new vscode.Range(r.line, r.start, r.line, r.end)]);
    }
    for (const type of this.types.values()) {
      editor.setDecorations(type, byType.get(type) ?? []);
    }
  }

  /** 飾りの型を片づける(大きさが変わった時・終わる時)。 */
  private disposeTypes(): void {
    for (const type of this.types.values()) {
      type.dispose();
    }
    this.types.clear();
  }

  /** 購読と飾りの型を片づける。 */
  dispose(): void {
    if (this.pending !== undefined) {
      clearTimeout(this.pending);
    }
    for (const d of this.disposables) {
      d.dispose();
    }
    this.disposeTypes();
  }
}

/**
 * 決まった語の hover — icon に置き換えて見せている語は、置き換える前の文字を一字一句そのまま(コピーできる code block)と、
 * その下に語の icon と一言。文字のまま見せている語(カーソルの行・種類を切った時)は icon と一言だけ。
 */
export class PixelWordHover implements vscode.HoverProvider {
  constructor(
    private readonly cache: ReplacementCache,
    private readonly replacer: PixelTextReplacer,
    private readonly icons: IconSource
  ) {}

  /** 位置の語が置き換えの場所なら hover を出す。 */
  provideHover(document: vscode.TextDocument, position: vscode.Position): vscode.Hover | undefined {
    if (!isHyDocument(document)) {
      return undefined;
    }
    const found = replacementAt(this.cache.of(document), position.line, position.character);
    if (found === undefined) {
      return undefined;
    }
    const summary = this.icons.summary(found.glyph);
    if (summary === undefined) {
      return undefined;
    }
    const shown = this.replacer.isShownAsIcon(document, found);
    const markdown = new vscode.MarkdownString(replacementHover(found, shown, this.icons.hoverImage(found.glyph, 32), summary));
    // icon の <img>(data URI)を出すため。元の文字は code block の中なので HTML として読まれない
    markdown.supportHtml = true;
    return new vscode.Hover(markdown, new vscode.Range(position.line, found.start, position.line, found.end));
  }
}

/** 置き換えの入り切りの命令 2 つ — 全体の入り切りと、種類を選ぶ。 */
export function replaceCommands(): vscode.Disposable[] {
  return [
    vscode.commands.registerCommand('doeff-runner.pixel.toggleReplaceText', async () => {
      const config = vscode.workspace.getConfiguration();
      await config.update(REPLACE_SETTING, config.get<boolean>(REPLACE_SETTING) === false, vscode.ConfigurationTarget.Global);
    }),
    vscode.commands.registerCommand('doeff-runner.pixel.chooseReplaceKinds', async () => {
      const config = vscode.workspace.getConfiguration();
      const { enabled } = parseReplaceKinds(config.get(REPLACE_KINDS_SETTING));
      const picked = await vscode.window.showQuickPick(
        REPLACE_KINDS.map((kind) => ({ label: kind, description: REPLACE_KIND_LABELS[kind], picked: enabled.has(kind), replaceKind: kind })),
        { title: 'icon に置き換える語の種類(選ばない種類は元の文字のまま)', canPickMany: true }
      );
      if (picked === undefined) {
        return;
      }
      const chosen = new Set(picked.map((p) => p.replaceKind));
      const value = Object.fromEntries(REPLACE_KINDS.map((kind) => [kind, chosen.has(kind)]));
      await config.update(REPLACE_KINDS_SETTING, value, vscode.ConfigurationTarget.Global);
    })
  ];
}
