// source の色付けの VS Code の層 — editor の `.hy` と同じ文法・同じ theme・同じ記号ごとの色で、file 全体の source を塗る。
// 文法と theme は入っている拡張の宣言から引き(locate.ts)、記号ごとの色は python-semantic-highlighter の API に聞く
// (semantic.ts)。theme・関係する設定・拡張が変わったら作り直して onDidChange を鳴らす(面が描き直すため)。

import * as fs from 'fs';
import * as path from 'path';
import * as vscode from 'vscode';
import { locateGrammar, locateTheme } from './locate';
import { HY_LANGUAGE_ID, SEMANTIC_HIGHLIGHTER_ID, asSemanticApi, parseSemanticSpans } from './semantic';
import { PLAIN, overlaySemantic, type HighlightedLines, type SemanticSpan, type SourceColoring } from './spans';
import { createTokenizer, type LineTokenizer } from './textmate';
import { resolveThemeRules, type ReadThemeFile } from './theme';

/** 塗り直しが要る設定(theme・token の色の上書き・記号ごとの色の設定)。 */
const WATCHED_SETTINGS = ['workbench.colorTheme', 'editor.tokenColorCustomizations', 'pythonSemanticHighlighter'] as const;

/** 下の層の tokenizer の用意の結果(文法が見つからなければ理由)。 */
type TokenizerState = { readonly tag: 'ready'; readonly tokenizer: LineTokenizer } | { readonly tag: 'missing'; readonly reason: string };

/** theme の file を読む口(VS Code の外の file を読むだけ)。 */
const readThemeFile: ReadThemeFile = async (file) => {
  try {
    return { tag: 'ok', text: await fs.promises.readFile(file, 'utf8') };
  } catch (error) {
    return { tag: 'error', reason: `読めない: ${String(error)}` };
  }
};

/** editor と同じ色で `.hy` の source を塗る係(拡張に 1 つ)。 */
export class SourceHighlighter implements vscode.Disposable {
  private readonly emitter = new vscode.EventEmitter<void>();
  /** 塗り直しが要った時に鳴る。 */
  readonly onDidChange = this.emitter.event;
  private tokenizer: Promise<TokenizerState> | undefined;
  private readonly disposables: vscode.Disposable[] = [];
  private readonly reported = new Set<string>();

  constructor(private readonly output: vscode.OutputChannel) {
    this.disposables.push(
      this.emitter,
      vscode.window.onDidChangeActiveColorTheme(() => this.reset()),
      vscode.extensions.onDidChange(() => this.reset()),
      vscode.workspace.onDidChangeConfiguration((event) => {
        if (WATCHED_SETTINGS.some((key) => event.affectsConfiguration(key))) {
          this.reset();
        }
      })
    );
  }

  /** file 全体の source を塗る(上の層は file 全体の記号の順で色が決まるので、切り出す前に全体で塗る)。 */
  async highlight(source: string): Promise<SourceColoring> {
    const lines = source.split(/\r?\n/);
    const state = await this.tokenizerState();
    const base: HighlightedLines =
      state.tag === 'ready' ? state.tokenizer.tokenize(lines) : lines.map((text) => [{ start: 0, end: text.length, color: null, fontStyle: PLAIN }]);
    const semantic = await this.semanticSpans(source);
    return { lines, spans: overlaySemantic(base, semantic) };
  }

  /** 作ってある tokenizer を捨てて、面に塗り直しを知らせる。 */
  private reset(): void {
    this.tokenizer = undefined;
    this.emitter.fire();
  }

  /** 同じ問題を Output へ 1 度だけ書く(描き直しのたびに積もらないため)。 */
  private report(message: string): void {
    if (!this.reported.has(message)) {
      this.reported.add(message);
      this.output.appendLine(`[read] ${message}`);
    }
  }

  /** editor と同じ文法と theme の tokenizer(1 度だけ作る)。 */
  private tokenizerState(): Promise<TokenizerState> {
    if (this.tokenizer === undefined) {
      this.tokenizer = this.buildTokenizer();
    }
    return this.tokenizer;
  }

  /** 文法と theme を引いて tokenizer を作る。 */
  private async buildTokenizer(): Promise<TokenizerState> {
    const extensions = vscode.extensions.all;
    const grammar = locateGrammar(extensions, HY_LANGUAGE_ID);
    if (grammar === null) {
      const reason = `言語 ${HY_LANGUAGE_ID} の文法を出している拡張が無い(source は色なしで描く)`;
      this.report(reason);
      return { tag: 'missing', reason };
    }
    const themeName = vscode.workspace.getConfiguration('workbench').get<string>('colorTheme') ?? '';
    const themePath = locateTheme(extensions, themeName);
    if (themePath === null) {
      this.report(`theme "${themeName}" の file が見つからない(token の色の上書きの設定だけで塗る)`);
    }
    const customizations: unknown = vscode.workspace.getConfiguration('editor').get('tokenColorCustomizations');
    const theme = await resolveThemeRules(themePath, themeName, customizations, readThemeFile, (from, relative) => path.join(path.dirname(from), relative));
    for (const problem of theme.problems) {
      this.report(`theme: ${problem}`);
    }
    let text: string;
    try {
      text = await fs.promises.readFile(grammar.path, 'utf8');
    } catch (error) {
      const reason = `文法の file を読めない(${grammar.path}): ${String(error)}`;
      this.report(reason);
      return { tag: 'missing', reason };
    }
    let tokenizer: LineTokenizer | null;
    try {
      tokenizer = await createTokenizer({ scopeName: grammar.scopeName, path: grammar.path, text }, theme.rules);
    } catch (error) {
      // 文法の JSON の誤り・oniguruma の wasm の読み込みの失敗 — 面は色なしの source で描き続ける
      const reason = `文法 ${grammar.scopeName}(${grammar.path})で tokenizer を作れない: ${String(error)}`;
      this.report(reason);
      return { tag: 'missing', reason };
    }
    if (tokenizer === null) {
      const reason = `文法 ${grammar.scopeName}(${grammar.extensionId})を読み込めない`;
      this.report(reason);
      return { tag: 'missing', reason };
    }
    return { tag: 'ready', tokenizer };
  }

  /** 記号ごとの色(API が無い・切ってある・答えの形が違う時は空 — 下の層だけで描く)。 */
  private async semanticSpans(source: string): Promise<readonly SemanticSpan[]> {
    const extension = vscode.extensions.getExtension(SEMANTIC_HIGHLIGHTER_ID);
    if (extension === undefined) {
      return [];
    }
    let offered: unknown;
    try {
      offered = extension.isActive ? extension.exports : await extension.activate();
    } catch (error) {
      this.report(`${SEMANTIC_HIGHLIGHTER_ID} を起こせない: ${String(error)}`);
      return [];
    }
    const api = asSemanticApi(offered);
    if (api === undefined) {
      this.report(`${SEMANTIC_HIGHLIGHTER_ID} が色の API(版 1)を出していない — 1.8.0 以上で記号ごとの色が付く`);
      return [];
    }
    let answer: unknown;
    try {
      answer = await api.colorize(source, HY_LANGUAGE_ID);
    } catch (error) {
      this.report(`${SEMANTIC_HIGHLIGHTER_ID} の colorize が失敗した: ${String(error)}`);
      return [];
    }
    const parsed = parseSemanticSpans(answer);
    switch (parsed.tag) {
      case 'ok':
        return parsed.spans;
      case 'none':
        return [];
      case 'rejected':
        this.report(parsed.reason);
        return [];
      default: {
        const unreachable: never = parsed;
        throw new Error(`網羅されていない答え: ${JSON.stringify(unreachable)}`);
      }
    }
  }

  /** 購読を止める。 */
  dispose(): void {
    for (const d of this.disposables) {
      d.dispose();
    }
    this.disposables.length = 0;
  }
}
