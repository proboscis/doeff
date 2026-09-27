// defk の見出しと束縛の型を editor に描く係と、その hover・定義へ飛ぶ命令。描く物と場所は model.ts / svg.ts の純粋な関数が決め、
// ここは VS Code の飾りへ写すだけ。file の文字は変えない(保存・検索・コピーは元の文字のまま)。材料は linter の editor-json
// (契約 版 2)で、linter に渡した document の版と今の版が違う間は描かない(位置が古いため)。

import * as vscode from 'vscode';
import type { LintBinding, LintSignature } from '../lint/contract';
import type { LintStore } from '../lint/store';
import { dataUri } from '../pixel/render';
import {
  bindingPlan,
  bindingRevealed,
  effectAgreement,
  headerPlan,
  headerRevealed,
  multiBindingForms,
  parseRevealMode,
  rangeKey,
  type BindingPlan,
  type HeaderPlan,
  type LineSource,
  type LineSpan,
  type RevealMode,
  type Span
} from './model';
import { bindingHover, headerHover, OPEN_LOCATION_COMMAND } from './hover';
import { bindingChipSvg, flowSvg, tagsSvg, type Drawn, type Metrics, type SpritePixels } from './svg';

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

/** editor 1 つに今描いている物(hover と #841 の置き換えの除外が読む)。 */
interface Shown {
  readonly hidden: readonly Span[];
}

/** defk の見出しと束縛の型を描く係。 */
export class DefkView implements vscode.Disposable {
  private readonly hidden: vscode.TextEditorDecorationType;
  private readonly headLine: vscode.TextEditorDecorationType;
  private readonly name: vscode.TextEditorDecorationType;
  private readonly anchorFirst: vscode.TextEditorDecorationType;
  private readonly anchorSecond: vscode.TextEditorDecorationType;
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
    this.name = vscode.window.createTextEditorDecorationType({
      fontWeight: 'bold',
      dark: { color: '#ffd97a' },
      light: { color: '#8a5a00' }
    });
    this.anchorFirst = vscode.window.createTextEditorDecorationType({});
    this.anchorSecond = vscode.window.createTextEditorDecorationType({});
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

  /** editor 1 つを描き直す。 */
  private refresh(editor: vscode.TextEditor): void {
    const plans = this.plansFor(editor.document);
    const cursors = cursorLines(editor);
    const hidden: vscode.DecorationOptions[] = [];
    const heads: vscode.Range[] = [];
    const names: vscode.Range[] = [];
    const first: vscode.DecorationOptions[] = [];
    const second: vscode.DecorationOptions[] = [];
    const operators: vscode.DecorationOptions[] = [];
    const hiddenSpans: Span[] = [];
    if (plans !== undefined && this.settings.header) {
      for (const { signature, plan } of plans.headers) {
        if (headerRevealed(plan, cursors, this.settings.reveal)) {
          continue;
        }
        heads.push(new vscode.Range(plan.headLine, 0, plan.headLine, 0));
        names.push(toRange(plan.name));
        const flow = imageOf(flowSvg(signature, this.metrics, this.sprites));
        const violations = this.store
          .violationsIn(editor.document.uri.fsPath)
          .filter((v) => v.range.start.line >= plan.lines.start && v.range.start.line <= plan.lines.end).length;
        const tags = imageOf(tagsSvg(signature.tags, effectAgreement(signature), violations, this.metrics, this.sprites));
        const flowOnHidden = plan.flowAt.placement === 'before';
        const tagsOnHidden = plan.tagsAt.placement === 'before' && !(plan.tagsAt.line === plan.flowAt.line && plan.tagsAt.character === plan.flowAt.character);
        for (const span of plan.hidden) {
          const at = (p: { line: number; character: number }): boolean => p.line === span.line && p.character === span.start;
          const before = flowOnHidden && at(plan.flowAt) ? flow : tagsOnHidden && at(plan.tagsAt) ? tags : undefined;
          hidden.push(before === undefined ? { range: toRange(span) } : { range: toRange(span), renderOptions: { before } });
          hiddenSpans.push(span);
        }
        const endOf = (line: number): vscode.Range => {
          const length = editor.document.lineAt(line).text.length;
          return new vscode.Range(line, length, line, length);
        };
        if (!flowOnHidden) {
          first.push({ range: endOf(plan.flowAt.line), renderOptions: { after: { ...flow, margin: '0 0 0 1.2em' } } });
        }
        if (!tagsOnHidden) {
          const lastHidden = plan.hidden[plan.hidden.length - 1];
          const range = plan.tagsAt.placement === 'before' && lastHidden !== undefined ? toRange({ ...lastHidden, start: lastHidden.end }) : endOf(plan.tagsAt.line);
          second.push({ range, renderOptions: { after: { ...tags, margin: '0 0 0 1.2em' } } });
        }
      }
    }
    if (plans !== undefined && this.settings.bindings) {
      for (const { plan } of plans.bindings) {
        if (bindingRevealed(plan, cursors)) {
          continue;
        }
        plan.hidden.forEach((span, i) => {
          const chip = i === 0 && plan.chip !== undefined ? imageOf(bindingChipSvg(plan.chip, plan.prefix, this.metrics, this.sprites)) : undefined;
          hidden.push(chip === undefined ? { range: toRange(span) } : { range: toRange(span), renderOptions: { before: { ...chip, margin: '0 4px 0 0' } } });
          hiddenSpans.push(span);
        });
        operators.push({
          range: toRange(plan.name),
          renderOptions: { after: { contentText: ` ${plan.operator}`, color: '#8fb3d9' } }
        });
      }
    }
    editor.setDecorations(this.hidden, hidden);
    editor.setDecorations(this.headLine, heads);
    editor.setDecorations(this.name, names);
    editor.setDecorations(this.anchorFirst, first);
    editor.setDecorations(this.anchorSecond, second);
    editor.setDecorations(this.operator, operators);
    this.shown.set(editor, { hidden: hiddenSpans });
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
    for (const type of [this.hidden, this.headLine, this.name, this.anchorFirst, this.anchorSecond, this.operator]) {
      type.dispose();
    }
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
