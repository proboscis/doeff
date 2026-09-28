// defk の見出しと束縛の型を editor に描く係と、その hover・定義へ飛ぶ命令。描く物と場所は model.ts / svg.ts の純粋な関数が決め、
// ここは VS Code の飾りへ写すだけ。file の文字は変えない(保存・検索・コピーは元の文字のまま)。材料は linter の editor-json
// (契約 版 2)で、linter に渡した document の版と今の版が違う間は描かない(位置が古いため)。

import * as vscode from 'vscode';
import type { LintBinding, LintLocation, LintSignature } from '../lint/contract';
import type { LintStore } from '../lint/store';
import { dataUri } from '../pixel/render';
import {
  bindingPlan,
  bindingRevealed,
  headerPlan,
  headerRevealed,
  multiBindingForms,
  parseRevealMode,
  rangeKey,
  targetsAt,
  type At,
  type BindingPlan,
  type HeaderPlan,
  type LineSource,
  type LineSpan,
  type Piece,
  type RevealMode,
  type Span
} from './model';
import { bindingHover, headerHover, OPEN_LOCATION_COMMAND } from './hover';
import { effectChipSvg, tagsSvg, typeColors, type Drawn, type Metrics, type SpritePixels } from './svg';

/** 見出しを出すかの設定。 */
export const HEADER_SETTING = 'doeff-runner.defk.header';
/** 束縛の型を出すかの設定。 */
export const BINDING_SETTING = 'doeff-runner.defk.bindingTypes';
/** カーソルが入った時に元の文字を見せる範囲の設定。 */
export const REVEAL_SETTING = 'doeff-runner.defk.revealOnCursor';

/** 見えている範囲・カーソルの変化から描き直すまで待つ時間。 */
const REFRESH_DELAY_MS = 30;

/** 今の設定。 */
interface DefkSettings {
  readonly header: boolean;
  readonly bindings: boolean;
  readonly reveal: RevealMode;
}

/** 設定を読む(読めない値は理由を 1 度だけ出す)。 */
function readSettings(log: { appendLine(line: string): void }, reported: Set<string>): DefkSettings {
  const config = vscode.workspace.getConfiguration();
  const { mode, problem } = parseRevealMode(config.get(REVEAL_SETTING));
  if (problem !== undefined && !reported.has(problem)) {
    reported.add(problem);
    log.appendLine(`[defk] 設定 ${REVEAL_SETTING}: ${problem}`);
  }
  return { header: config.get<boolean>(HEADER_SETTING) !== false, bindings: config.get<boolean>(BINDING_SETTING) !== false, reveal: mode };
}

/** editor の文字の大きさ・行の高さ・書体(行の高さの 0 は VS Code と同じく文字の大きさから決め、8 未満は倍率として読む)。 */
function readMetrics(): Metrics {
  const editor = vscode.workspace.getConfiguration('editor');
  const fontSize = editor.get<number>('fontSize') ?? 14;
  const configured = editor.get<number>('lineHeight') ?? 0;
  const lineHeight =
    configured === 0
      ? Math.round(fontSize * (process.platform === 'darwin' ? 1.5 : 1.35))
      : configured < 8
        ? Math.round(fontSize * configured)
        : configured;
  return { fontSize, lineHeight, fontFamily: editor.get<string>('fontFamily') ?? 'monospace' };
}

/** Hy の file の文書か。 */
function isHyDocument(document: vscode.TextDocument): boolean {
  return document.uri.scheme === 'file' && (document.languageId === 'hy' || /\.(hy|hyk|hyp)$/.test(document.uri.fsPath));
}

/** document を行の口にする。 */
function lineSource(document: vscode.TextDocument): LineSource {
  return { lineCount: document.lineCount, lineText: (line) => document.lineAt(line).text };
}

/** カーソルと選んだ範囲の行。 */
function cursorLines(editor: vscode.TextEditor): LineSpan[] {
  return editor.selections.map((s) => ({ start: s.start.line, end: s.end.line }));
}

/** 範囲を VS Code の範囲にする。 */
function toRange(span: Span): vscode.Range {
  return new vscode.Range(span.line, span.start, span.line, span.end);
}

/** 描いた SVG を飾りの画にする。 */
function imageOf(drawn: Drawn): vscode.ThemableDecorationAttachmentRenderOptions {
  return {
    contentIconPath: vscode.Uri.parse(dataUri('image/svg+xml', drawn.svg)),
    width: `${drawn.width}px`,
    height: `${drawn.height}px`,
    // 行の真ん中に置く(textDecoration は css の宣言を足す唯一の口)
    textDecoration: 'none; vertical-align: middle'
  };
}

/** document 1 つの描く物(版の合う結果から作る)。 */
interface DocumentPlans {
  readonly version: number;
  readonly headers: ReadonlyArray<{ readonly signature: LintSignature; readonly plan: HeaderPlan }>;
  readonly bindings: ReadonlyArray<{ readonly binding: LintBinding; readonly plan: BindingPlan }>;
}

/** editor 1 つに今描いている物(#841 の置き換えの除外と「定義へ移動」が読む)。 */
interface Shown {
  readonly hidden: readonly Span[];
  readonly headers: readonly HeaderPlan[];
  readonly bindings: readonly BindingPlan[];
}

/** 部品の中身を editor の文字の飾りにする(型の名は型ごとの色と薄い枠・区切りは普通の文字・見出しの語は小さく淡い)。 */
function textContent(text: string, style: 'type' | 'punct' | 'label', colorKey = ''): vscode.ThemableDecorationAttachmentRenderOptions {
  switch (style) {
    case 'type': {
      const colors = typeColors(colorKey);
      return {
        contentText: text,
        color: colors.text,
        fontWeight: '600',
        border: `1px solid ${colors.border}`,
        // 角の小さい薄い枠と、枠の内側の余白(textDecoration は css の宣言を足す唯一の口)
        textDecoration: 'none; border-radius: 2px; padding: 0 2px'
      };
    }
    case 'punct':
      return { contentText: text, color: new vscode.ThemeColor('editor.foreground') };
    case 'label':
      return { contentText: text, color: '#8a96a3', margin: '0 0.6em 0 0', textDecoration: 'none; font-size: 0.85em' };
    default: {
      const unreachable: never = style;
      throw new Error(`網羅されていない書き方: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 位置を VS Code の 1 文字の範囲にする(部品を付ける隠した文字)。 */
function charRange(at: At): vscode.Range {
  return new vscode.Range(at.line, at.character, at.line, at.character + 1);
}

/** 定義の位置を VS Code の場所にする。 */
function toLocation(location: LintLocation): vscode.Location {
  const { start, end } = location.range;
  return new vscode.Location(vscode.Uri.file(location.path), new vscode.Range(start.line, start.character, end.line, end.character));
}

/** defk の見出しと束縛の型を描く係。 */
export class DefkView implements vscode.Disposable {
  private readonly hidden: vscode.TextEditorDecorationType;
  private readonly headLine: vscode.TextEditorDecorationType;
  private readonly name: vscode.TextEditorDecorationType;
  private readonly piece: vscode.TextEditorDecorationType;
  private readonly tags: vscode.TextEditorDecorationType;
  private readonly operator: vscode.TextEditorDecorationType;
  private readonly disposables: vscode.Disposable[] = [];
  private readonly reported = new Set<string>();
  private readonly shown = new WeakMap<vscode.TextEditor, Shown>();
  private readonly listeners = new Set<() => void>();
  private settings: DefkSettings;
  private metrics = readMetrics();
  private pending: NodeJS.Timeout | undefined;

  constructor(
    private readonly store: LintStore,
    private readonly sprites: SpritePixels,
    private readonly log: { appendLine(line: string): void }
  ) {
    this.settings = readSettings(log, this.reported);
    // 元の文字は表示だけ消す(文書の文字・選択・コピーは元のまま)
    this.hidden = vscode.window.createTextEditorDecorationType({ textDecoration: 'none; display: none' });
    this.headLine = vscode.window.createTextEditorDecorationType({
      isWholeLine: true,
      backgroundColor: 'rgba(90, 120, 160, 0.14)',
      borderWidth: '0 0 1px 0',
      borderStyle: 'solid',
      borderColor: 'rgba(140, 160, 190, 0.35)'
    });
    this.name = vscode.window.createTextEditorDecorationType({ fontWeight: 'bold', dark: { color: '#ffd97a' }, light: { color: '#8a5a00' } });
    this.piece = vscode.window.createTextEditorDecorationType({});
    this.tags = vscode.window.createTextEditorDecorationType({});
    this.operator = vscode.window.createTextEditorDecorationType({});
    const offStore = store.onDidChange(() => this.schedule());
    this.disposables.push(
      { dispose: offStore },
      vscode.window.onDidChangeVisibleTextEditors(() => this.refreshAll()),
      vscode.window.onDidChangeTextEditorSelection((event) => this.refresh(event.textEditor)),
      vscode.workspace.onDidChangeTextDocument(() => this.schedule()),
      vscode.workspace.onDidChangeConfiguration((event) => {
        if (event.affectsConfiguration('editor.fontSize') || event.affectsConfiguration('editor.lineHeight') || event.affectsConfiguration('editor.fontFamily')) {
          this.metrics = readMetrics();
          this.refreshAll();
        }
        if (event.affectsConfiguration(HEADER_SETTING) || event.affectsConfiguration(BINDING_SETTING) || event.affectsConfiguration(REVEAL_SETTING)) {
          this.settings = readSettings(this.log, this.reported);
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

  /** editor で今文字を隠している範囲(文字の置き換え #841 がそこへ画を描かないため)。 */
  hiddenSpans(editor: vscode.TextEditor): readonly Span[] {
    return this.shown.get(editor)?.hidden ?? [];
  }

  /** 押された位置の部品(型の名・effect の札・束縛の型の札)の定義の場所 — 部品の無い位置は undefined(他の「定義へ移動」に任せる)。 */
  definitionsAt(document: vscode.TextDocument, position: vscode.Position): vscode.Location[] | undefined {
    const editor = vscode.window.visibleTextEditors.find((e) => e.document === document);
    const drawn = editor === undefined ? undefined : this.shown.get(editor);
    if (drawn === undefined) {
      return undefined;
    }
    return targetsAt(drawn.headers, drawn.bindings, { line: position.line, character: position.character })?.map(toLocation);
  }

  /** 位置にかかる見出しか束縛(hover のため — 描いている物だけ)。 */
  itemAt(
    document: vscode.TextDocument,
    position: vscode.Position
  ): { readonly tag: 'header'; readonly signature: LintSignature } | { readonly tag: 'binding'; readonly binding: LintBinding } | undefined {
    const plans = this.plansFor(document);
    if (plans === undefined) {
      return undefined;
    }
    for (const { signature, plan } of plans.headers) {
      const lines = [plan.headLine, ...plan.hidden.map((h) => h.line)];
      if (lines.includes(position.line)) {
        return { tag: 'header', signature };
      }
    }
    for (const { binding, plan } of plans.bindings) {
      const spans = [...plan.hidden, plan.name];
      if (spans.some((s) => s.line === position.line && s.start <= position.character && position.character <= s.end)) {
        return { tag: 'binding', binding };
      }
    }
    return undefined;
  }

  /** 版の合う結果から document の描く物を作る(版が違う・結果が無ければ undefined)。 */
  private plansFor(document: vscode.TextDocument): DocumentPlans | undefined {
    if (!isHyDocument(document)) {
      return undefined;
    }
    const found = this.store.signaturesFor(document.uri.fsPath);
    if (found === undefined || found.version !== document.version) {
      return undefined;
    }
    const lines = lineSource(document);
    const multi = multiBindingForms(found.bindings);
    return {
      version: found.version,
      headers: found.signatures.flatMap((signature) => {
        const plan = headerPlan(signature, lines);
        return plan === undefined ? [] : [{ signature, plan }];
      }),
      bindings: found.bindings.flatMap((binding) => {
        const plan = multi.has(rangeKey(binding.formRange)) ? undefined : bindingPlan(binding, lines);
        return plan === undefined ? [] : [{ binding, plan }];
      })
    };
  }

  /** 少し待ってから見えている editor を全部描き直す。 */
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

  /** 部品 1 つを飾りにする(型の名と区切りは editor の文字、effect の札は画像)。 */
  private pieceDecoration(piece: Piece): vscode.DecorationOptions {
    const content = piece.before;
    let before: vscode.ThemableDecorationAttachmentRenderOptions;
    switch (content.kind) {
      case 'type':
        before = textContent(content.text, 'type', content.colorKey);
        break;
      case 'punct':
        before = textContent(content.text, 'punct');
        break;
      case 'label':
        before = { ...textContent(content.text, 'label'), margin: '0 0.6em 0 1.2em' };
        break;
      case 'effect':
      case 'raise':
        before = { ...imageOf(effectChipSvg(content.kind, content.name, this.metrics, this.sprites)), margin: '0 4px 0 0' };
        break;
      default: {
        const unreachable: never = content;
        throw new Error(`網羅されていない部品: ${JSON.stringify(unreachable)}`);
      }
    }
    const after = piece.after === undefined ? undefined : textContent(piece.after, 'punct');
    return { range: charRange(piece.at), renderOptions: after === undefined ? { before } : { before, after } };
  }

  /** editor 1 つを描き直す。 */
  private refresh(editor: vscode.TextEditor): void {
    const plans = this.plansFor(editor.document);
    const cursors = cursorLines(editor);
    const hidden: vscode.DecorationOptions[] = [];
    const heads: vscode.Range[] = [];
    const names: vscode.Range[] = [];
    const pieces: vscode.DecorationOptions[] = [];
    const tags: vscode.DecorationOptions[] = [];
    const operators: vscode.DecorationOptions[] = [];
    const hiddenSpans: Span[] = [];
    const drawnHeaders: HeaderPlan[] = [];
    const drawnBindings: BindingPlan[] = [];
    if (plans !== undefined && this.settings.header) {
      for (const { signature, plan } of plans.headers) {
        if (headerRevealed(plan, cursors, this.settings.reveal)) {
          continue;
        }
        drawnHeaders.push(plan);
        heads.push(new vscode.Range(plan.headLine, 0, plan.headLine, 0));
        names.push(toRange(plan.name));
        for (const span of plan.hidden) {
          hidden.push({ range: toRange(span) });
          hiddenSpans.push(span);
        }
        pieces.push(...plan.pieces.map((p) => this.pieceDecoration(p)));
        const end = new vscode.Range(plan.tagsAt.line, plan.tagsAt.character, plan.tagsAt.line, plan.tagsAt.character);
        const drawnTags = tagsSvg(signature.tags, this.metrics, this.sprites);
        const fallback = plan.fallback === undefined ? undefined : { ...textContent(plan.fallback, 'label'), margin: '0 0 0 1.2em' };
        if (drawnTags !== undefined || fallback !== undefined) {
          const after = drawnTags !== undefined ? { ...imageOf(drawnTags), margin: '0 0 0 1.5em' } : fallback;
          tags.push({ range: end, renderOptions: { after } });
        }
      }
    }
    if (plans !== undefined && this.settings.bindings) {
      for (const { plan } of plans.bindings) {
        if (bindingRevealed(plan, cursors)) {
          continue;
        }
        drawnBindings.push(plan);
        for (const span of plan.hidden) {
          hidden.push({ range: toRange(span) });
          hiddenSpans.push(span);
        }
        if (plan.chipAt !== undefined && plan.chip !== undefined) {
          const before =
            plan.chip.tag === 'unknown'
              ? { ...textContent('?', 'type', '?'), color: '#8b949e', border: '1px dashed #5a6270' }
              : textContent(plan.chip.absent ? `Maybe[${plan.chip.text}]` : plan.chip.text, 'type', plan.chip.colorKey);
          pieces.push({ range: charRange(plan.chipAt), renderOptions: { before: { ...before, margin: '0 0.5em 0 0.3em' } } });
        }
        operators.push({ range: toRange(plan.name), renderOptions: { after: { contentText: ` ${plan.operator}`, color: '#8fb3d9' } } });
      }
    }
    editor.setDecorations(this.hidden, hidden);
    editor.setDecorations(this.headLine, heads);
    editor.setDecorations(this.name, names);
    editor.setDecorations(this.piece, pieces);
    editor.setDecorations(this.tags, tags);
    editor.setDecorations(this.operator, operators);
    this.shown.set(editor, { hidden: hiddenSpans, headers: drawnHeaders, bindings: drawnBindings });
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
    for (const type of [this.hidden, this.headLine, this.name, this.piece, this.tags, this.operator]) {
      type.dispose();
    }
  }
}

/** 見出しと束縛の型の部品の「定義へ移動」(Cmd+クリック・F12)— 部品の無い位置は何も返さない。 */
export class DefkDefinitions implements vscode.DefinitionProvider {
  constructor(private readonly view: DefkView) {}

  /** 押された位置の部品の定義を返す(組み込みの型など定義の無い部品は空 — 飛ばない)。 */
  provideDefinition(document: vscode.TextDocument, position: vscode.Position): vscode.Location[] {
    return this.view.definitionsAt(document, position) ?? [];
  }
}

/** 見出しと束縛の hover。 */
export class DefkHover implements vscode.HoverProvider {
  constructor(private readonly view: DefkView) {}

  /** 位置が描いている見出しか束縛なら hover を出す。 */
  provideHover(document: vscode.TextDocument, position: vscode.Position): vscode.Hover | undefined {
    const item = this.view.itemAt(document, position);
    if (item === undefined) {
      return undefined;
    }
    const markdown = new vscode.MarkdownString(item.tag === 'header' ? headerHover(item.signature) : bindingHover(item.binding));
    markdown.isTrusted = { enabledCommands: [OPEN_LOCATION_COMMAND] };
    return new vscode.Hover(markdown);
  }
}

/** 見出しと束縛の型の係・hover・定義へ飛ぶ命令を登録する(文字の置き換え #841 が隠した範囲を避けるため、係を返す)。 */
export function registerDefkView(
  context: vscode.ExtensionContext,
  store: LintStore,
  sprites: SpritePixels,
  output: vscode.OutputChannel
): DefkView {
  const view = new DefkView(store, sprites, output);
  context.subscriptions.push(
    view,
    vscode.languages.registerHoverProvider([{ language: 'hy', scheme: 'file' }, { pattern: '**/*.{hy,hyk,hyp}', scheme: 'file' }], new DefkHover(view)),
    // 見出しの型の名・effect の札・束縛の型の札の上の Cmd+クリックと F12(隠した文字の位置で引く — 元の文字の上の Hy の口と重ならない)
    vscode.languages.registerDefinitionProvider([{ language: 'hy', scheme: 'file' }, { pattern: '**/*.{hy,hyk,hyp}', scheme: 'file' }], new DefkDefinitions(view)),
    vscode.commands.registerCommand(OPEN_LOCATION_COMMAND, async (path: unknown, line: unknown, character: unknown) => {
      if (typeof path !== 'string' || typeof line !== 'number' || typeof character !== 'number') {
        output.appendLine(`[defk] 定義へ飛ぶ命令の引数が違う: ${JSON.stringify([path, line, character])}`);
        return;
      }
      const position = new vscode.Position(line, character);
      await vscode.window.showTextDocument(vscode.Uri.file(path), { selection: new vscode.Range(position, position) });
    }),
    vscode.commands.registerCommand('doeff-runner.defk.toggleHeader', async () => {
      const config = vscode.workspace.getConfiguration();
      await config.update(HEADER_SETTING, config.get<boolean>(HEADER_SETTING) === false, vscode.ConfigurationTarget.Global);
    })
  );
  return view;
}
