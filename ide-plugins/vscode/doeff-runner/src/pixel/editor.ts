// エディタの上の pixel art — gutter(定義の行に種類の icon + 右下に状態の印)・決まった語の文字の置き換えと hover
// (replaceEditor.ts)・状態バーの doe。何を出すかは vocabulary.ts と replace.ts の純粋な関数が hy-index と linter の
// 出力から決め、ここは VS Code の飾り・hover・状態バーへ写すだけ。判定はしない。

import type { DefkView } from '../defk/view';
import * as vscode from 'vscode';
import type { EffectGraphSource } from '../hy/effects';
import { isHyPath } from '../hy/indexService';
import type { HyIndexStore } from '../hy/store';
import type { LintStore } from '../lint/store';
import { atLeast, parseMinSeverity } from '../lint/view';
import { INLINE_MIN_SEVERITY_SETTING } from '../lint/decorations';
import type { IconSource } from './icons';
import { PixelTextReplacer, PixelWordHover, ReplacementCache, replaceCommands } from './replaceEditor';
import {
  doeStatus,
  GUTTER_SETTING,
  gutterLines,
  parseGutterMode,
  litKey,
  tallyViolations,
  type LitIcon,
  type GutterMode
} from './vocabulary';

/** 木の項目を pixel art の icon にするかの設定。 */
export const TREE_ICONS_SETTING = 'doeff-runner.pixel.treeIcons';
/** 状態バーに doe を出すかの設定。 */
export const STATUS_BAR_SETTING = 'doeff-runner.pixel.statusBar';

/** 設定の gutter の出し方(知らない値は理由を 1 度出して既定の kind)。 */
export function gutterMode(log: { appendLine(line: string): void }, reported: Set<string>): GutterMode {
  const value: unknown = vscode.workspace.getConfiguration().get(GUTTER_SETTING);
  const mode = parseGutterMode(value);
  if (mode === undefined) {
    const key = JSON.stringify(value);
    if (!reported.has(key)) {
      reported.add(key);
      log.appendLine(`[pixel] 設定 ${GUTTER_SETTING} の値 ${key} は kind / layer / off のどれでもない — kind で出す`);
    }
    return 'kind';
  }
  return mode;
}

/** Hy の file の editor か(pixel art の gutter と状態バーの doe を出す file)。 */
function isHyDocument(document: vscode.TextDocument): boolean {
  return document.uri.scheme === 'file' && (document.languageId === 'hy' || isHyPath(document.uri.fsPath));
}

/** その file の gutter を pixel art が持つか — linter の左端の丸を同じ行に重ねないため(読めない値は既定の kind)。 */
export function pixelOwnsGutter(document: vscode.TextDocument): boolean {
  const mode = parseGutterMode(vscode.workspace.getConfiguration().get(GUTTER_SETTING)) ?? 'kind';
  return mode !== 'off' && isHyDocument(document);
}

/** 木に pixel art の icon を使うか(切れば codicon の表示に戻る)。 */
export function treeIconsEnabled(): boolean {
  return vscode.workspace.getConfiguration().get<boolean>(TREE_ICONS_SETTING) !== false;
}

/** 見えている Hy の editor の gutter に定義の icon と状態の印を出す係。 */
export class PixelGutter implements vscode.Disposable {
  private readonly types = new Map<string, vscode.TextEditorDecorationType>();
  private readonly disposables: vscode.Disposable[] = [];
  private readonly reported = new Set<string>();

  constructor(
    private readonly hy: HyIndexStore,
    private readonly lint: LintStore,
    private readonly icons: IconSource,
    private readonly log: { appendLine(line: string): void }
  ) {}

  /** 置き場・見えている editor・設定の変化で出し直し始める。 */
  start(): void {
    const offHy = this.hy.onDidChange(() => this.refresh());
    const offLint = this.lint.onDidChange(() => this.refresh());
    this.disposables.push(
      { dispose: offHy },
      { dispose: offLint },
      vscode.window.onDidChangeVisibleTextEditors(() => this.refresh()),
      vscode.workspace.onDidChangeConfiguration((event) => {
        if (event.affectsConfiguration(GUTTER_SETTING) || event.affectsConfiguration(INLINE_MIN_SEVERITY_SETTING)) {
          this.refresh();
        }
      })
    );
    this.refresh();
  }

  /** 画 1 つの飾りの型(同じ画は使い回す)。画が作れなければ undefined。 */
  private typeFor(icon: LitIcon): vscode.TextEditorDecorationType | undefined {
    const key = litKey(icon);
    const existing = this.types.get(key);
    if (existing !== undefined) {
      return existing;
    }
    const uri = this.icons.lit(icon);
    if (uri === undefined) {
      return undefined;
    }
    const type = vscode.window.createTextEditorDecorationType({ gutterIconPath: uri, gutterIconSize: 'contain' });
    this.types.set(key, type);
    return type;
  }

  /** 見えている editor 全部の gutter を出し直す。 */
  refresh(): void {
    const mode = gutterMode(this.log, this.reported);
    const minimum = parseMinSeverity(vscode.workspace.getConfiguration().get(INLINE_MIN_SEVERITY_SETTING)) ?? 'warning';
    for (const editor of vscode.window.visibleTextEditors) {
      const filePath = editor.document.uri.fsPath;
      const byType = new Map<vscode.TextEditorDecorationType, vscode.Range[]>();
      if (isHyDocument(editor.document)) {
        const definitions = this.hy.get(filePath)?.file.definitions ?? [];
        const violations = atLeast(this.lint.violationsIn(filePath), minimum);
        for (const { line, icon } of gutterLines(definitions, violations, this.lint.moduleFor(filePath), mode)) {
          const type = this.typeFor(icon);
          if (type === undefined || line >= editor.document.lineCount) {
            continue;
          }
          byType.set(type, [...(byType.get(type) ?? []), new vscode.Range(line, 0, line, 0)]);
        }
      }
      for (const type of this.types.values()) {
        editor.setDecorations(type, byType.get(type) ?? []);
      }
    }
  }

  /** 購読と飾りの型を片づける。 */
  dispose(): void {
    for (const d of this.disposables) {
      d.dispose();
    }
    for (const type of this.types.values()) {
      type.dispose();
    }
  }
}

/** 状態バーの doe — 今の file の違反で表情が変わる(違反 0 は落ち着き、error があれば驚き)。 */
export class DoeStatusBar implements vscode.Disposable {
  private readonly item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 41);
  private readonly disposables: vscode.Disposable[] = [];

  constructor(
    private readonly lint: LintStore,
    private readonly icons: IconSource
  ) {
    this.item.command = 'doeff-lint-violations.focus';
    const off = lint.onDidChange(() => this.refresh());
    this.disposables.push(
      { dispose: off },
      vscode.window.onDidChangeActiveTextEditor(() => this.refresh()),
      vscode.workspace.onDidChangeConfiguration((event) => {
        if (event.affectsConfiguration(STATUS_BAR_SETTING)) {
          this.refresh();
        }
      })
    );
    this.refresh();
  }

  /** 今の file の表情と数を出し直す(linter の結果の無い file・設定で切った時は隠す)。 */
  refresh(): void {
    const document = vscode.window.activeTextEditor?.document;
    const enabled = vscode.workspace.getConfiguration().get<boolean>(STATUS_BAR_SETTING) !== false;
    const filePath = document?.uri.fsPath;
    const covered = filePath !== undefined && this.lint.rootPaths().some((root) => filePath.startsWith(root));
    if (!enabled || document === undefined || !covered || !isHyDocument(document)) {
      this.item.hide();
      return;
    }
    const status = doeStatus(tallyViolations(this.lint.violationsIn(document.uri.fsPath)));
    this.item.text = status.text;
    const tooltip = new vscode.MarkdownString(
      [`${this.icons.hoverImage(status.mood, 48)}`, '', ...status.lines.map((l) => `${this.icons.hoverImage(l.glyph, 16)} ${l.label}`), '', '押すと「違反(linter)」の欄を開く'].join(
        '\n\n'
      )
    );
    tooltip.supportHtml = true;
    this.item.tooltip = tooltip;
    this.item.show();
  }

  /** 表示と購読を片づける。 */
  dispose(): void {
    for (const d of this.disposables) {
      d.dispose();
    }
    this.item.dispose();
  }
}

/** エディタの上の pixel art(gutter・文字の置き換えと hover・状態バー)と、出し方を選ぶ命令を登録する。 */
export function registerPixelEditor(
  context: vscode.ExtensionContext,
  hy: HyIndexStore,
  effects: EffectGraphSource,
  lint: LintStore,
  icons: IconSource,
  output: vscode.OutputChannel,
  /** 文字を隠している係(defk の見出しと束縛・呼びの形)— そこへは画を描かない */
  defk: Pick<DefkView, 'hiddenSpans' | 'onDidRedraw'>
): void {
  const gutter = new PixelGutter(hy, lint, icons, output);
  const replacements = new ReplacementCache(effects);
  // 索引が変わると effect の表が変わる(宣言した effect の頭の印)ので出し直す。defk の見出しと束縛が隠した文字の上には描かない
  const replacer = new PixelTextReplacer(replacements, icons, output, (listener) => hy.onDidChange(listener), (editor) => defk.hiddenSpans(editor));
  const offDefk = defk.onDidRedraw(() => replacer.redraw());
  context.subscriptions.push({ dispose: offDefk });
  context.subscriptions.push(
    gutter,
    replacer,
    ...replaceCommands(),
    new DoeStatusBar(lint, icons),
    vscode.languages.registerHoverProvider(
      [{ language: 'hy', scheme: 'file' }, { pattern: '**/*.{hy,hyk,hyp}', scheme: 'file' }],
      new PixelWordHover(replacements, replacer, icons)
    ),
    vscode.commands.registerCommand('doeff-runner.pixel.chooseGutter', async () => {
      const picked = await vscode.window.showQuickPick(
        [
          { label: '種類と違反', description: '定義の行に kind の icon、右下に違反の印', mode: 'kind' as const },
          { label: '層と service', description: '定義の行に層のタイル、右下に service の旗', mode: 'layer' as const },
          { label: '出さない', description: 'gutter の pixel art を出さない(違反の行の丸に戻る)', mode: 'off' as const }
        ],
        { title: 'gutter の pixel art の出し方' }
      );
      if (picked !== undefined) {
        await vscode.workspace.getConfiguration().update(GUTTER_SETTING, picked.mode, vscode.ConfigurationTarget.Global);
      }
    })
  );
  gutter.start();
}
