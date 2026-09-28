// 下の層 — Hy の TextMate 文法と theme の規則で行を token に分け、色と字の形を付ける(VS Code に触らない。node で動く)。
//
// editor と同じ tokenizer(vscode-textmate + oniguruma — VS Code の本体が使う物)に、editor と同じ文法の file
// (`.hy` の文法を出している拡張の file — 呼ぶ側が渡す)を通す。token の文字色の番号 1 は theme の既定の色
// (vscode-textmate の ColorMap は既定の色を最初に採番する)なので、色なし = editor の既定の文字色として返す。

import * as fs from 'fs';
import * as oniguruma from 'vscode-oniguruma';
import { INITIAL, Registry, parseRawGrammar, type IGrammar, type StateStack } from 'vscode-textmate';
import { PLAIN, type FontStyle, type HighlightedLines, type StyledSpan } from './spans';
import type { ThemeRule } from './theme';

/** token の属性の bit(vscode-textmate の EncodedTokenAttributes — 公開されていないので同じ値を置く)。 */
const FONT_STYLE_MASK = 0b0000_0000_0000_0000_0111_1000_0000_0000;
const FONT_STYLE_OFFSET = 11;
const FOREGROUND_MASK = 0b0000_0000_1111_1111_1000_0000_0000_0000;
const FOREGROUND_OFFSET = 15;
/** ColorMap の既定の文字色の番号。 */
const DEFAULT_FOREGROUND_ID = 1;
/** 1 行の tokenize の時間の上限(ms)— 長い行で止まらないため(VS Code の editor と同じ考え)。 */
const LINE_TIME_LIMIT_MS = 500;

/** oniguruma の wasm を 1 度だけ読む(同じ process の全部の tokenizer で共有する)。 */
let onigLib: Promise<{ createOnigScanner: (sources: string[]) => oniguruma.OnigScanner; createOnigString: (text: string) => oniguruma.OnigString }> | undefined;

/** tokenizer が要る oniguruma を用意するため。 */
function loadOnigLib(): NonNullable<typeof onigLib> {
  if (onigLib === undefined) {
    const wasm = fs.readFileSync(require.resolve('vscode-oniguruma/release/onig.wasm'));
    onigLib = oniguruma.loadWASM(wasm).then(() => ({
      createOnigScanner: (sources: string[]) => new oniguruma.OnigScanner(sources),
      createOnigString: (text: string) => new oniguruma.OnigString(text)
    }));
  }
  return onigLib;
}

/** 文法の file 1 つ(editor が `.hy` に使っている物)。 */
export interface GrammarSource {
  readonly scopeName: string;
  readonly path: string;
  readonly text: string;
}

/** 文法と theme の組で行を塗る tokenizer。 */
export interface LineTokenizer {
  /** 行の列を塗る(行の間で文法の状態を引き継ぐ — 複数行の文字列・註のため)。 */
  tokenize(lines: readonly string[]): HighlightedLines;
}

/** 字の形の bit を字の形にする。 */
function fontStyleOf(bits: number): FontStyle {
  if (bits === 0) {
    return PLAIN;
  }
  return { italic: (bits & 1) !== 0, bold: (bits & 2) !== 0, underline: (bits & 4) !== 0, strikethrough: (bits & 8) !== 0 };
}

/** 1 行を塗る(token の頭の位置と属性の組の列 → 隙間の無い区切り)。 */
function tokenizeLine(grammar: IGrammar, colorMap: readonly string[], text: string, state: StateStack): { spans: StyledSpan[]; state: StateStack } {
  const result = grammar.tokenizeLine2(text, state, LINE_TIME_LIMIT_MS);
  const spans: StyledSpan[] = [];
  const tokens = result.tokens;
  const count = tokens.length / 2;
  for (let i = 0; i < count; i += 1) {
    const start = tokens[2 * i];
    const end = i + 1 < count ? tokens[2 * (i + 1)] : text.length;
    if (end <= start) {
      continue;
    }
    const metadata = tokens[2 * i + 1];
    const foreground = (metadata & FOREGROUND_MASK) >>> FOREGROUND_OFFSET;
    const color = foreground === DEFAULT_FOREGROUND_ID ? null : (colorMap[foreground] ?? null);
    spans.push({ start, end, color, fontStyle: fontStyleOf((metadata & FONT_STYLE_MASK) >>> FONT_STYLE_OFFSET) });
  }
  return { spans, state: result.ruleStack };
}

/**
 * 文法と theme の規則から tokenizer を作る(文法が読めなければ null)。theme を替えたら作り直す
 * (vscode-textmate の Registry は theme ごとに色の番号を振るため)。
 */
export async function createTokenizer(grammar: GrammarSource, rules: readonly ThemeRule[]): Promise<LineTokenizer | null> {
  const registry = new Registry({
    onigLib: loadOnigLib(),
    loadGrammar: async (scopeName: string) => (scopeName === grammar.scopeName ? parseRawGrammar(grammar.text, grammar.path) : null)
  });
  registry.setTheme({ settings: rules.map((r) => ({ scope: r.scope === undefined ? undefined : typeof r.scope === 'string' ? r.scope : [...r.scope], settings: { ...r.settings } })) });
  const loaded = await registry.loadGrammar(grammar.scopeName);
  if (loaded === null) {
    return null;
  }
  const colorMap = registry.getColorMap();
  return {
    tokenize(lines: readonly string[]): HighlightedLines {
      let state: StateStack = INITIAL;
      return lines.map((text) => {
        const line = tokenizeLine(loaded, colorMap, text, state);
        state = line.state;
        return line.spans;
      });
    }
  };
}
