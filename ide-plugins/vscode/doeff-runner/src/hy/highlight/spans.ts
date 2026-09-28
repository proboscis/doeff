// editor の `.hy` と同じ色で source を描くための、行ごとの色つきの区切り(純粋・VS Code に触らない)。
//
// editor の色は 2 層で決まる(agora-redesign #910 U16):
// - 下の層 = Hy の TextMate 文法の token に、有効な theme の tokenColors を当てた色(keyword・文字列・数・註など)
// - 上の層 = python-semantic-highlighter が記号ごとに計算した色(装飾として下の層の色を上書きする。字の形は下の層のまま)
// 上の層は file 全体の記号の順で色が決まる(round-robin)ので、file 全体で重ねてから定義の範囲を切り出す。

import type { HyRange } from '../contract';

/** 字の形(theme の fontStyle)。 */
export interface FontStyle {
  readonly italic: boolean;
  readonly bold: boolean;
  readonly underline: boolean;
  readonly strikethrough: boolean;
}

export const PLAIN: FontStyle = { italic: false, bold: false, underline: false, strikethrough: false };

/** 行の中の 1 区切り(UTF-16 の列で [start, end))。color が null なら editor の既定の文字色。 */
export interface StyledSpan {
  readonly start: number;
  readonly end: number;
  readonly color: string | null;
  readonly fontStyle: FontStyle;
}

/** 行ごとの区切り(区切りは行の頭から終わりまで隙間なく並ぶ)。 */
export type HighlightedLines = readonly (readonly StyledSpan[])[];

/** 上の層の色 1 つ(python-semantic-highlighter の colorize の答えの 1 件)。 */
export interface SemanticSpan {
  readonly line: number;
  readonly column: number;
  readonly length: number;
  readonly color: string;
}

/** 描く 1 片(文字と色と字の形)。 */
export interface Piece {
  readonly text: string;
  readonly color: string | null;
  readonly fontStyle: FontStyle;
}

/** 1 行の区切りの [from, to) に色を上書きする(区切りを境目で割る)。 */
function paint(spans: readonly StyledSpan[], from: number, to: number, color: string): StyledSpan[] {
  const out: StyledSpan[] = [];
  for (const span of spans) {
    if (span.end <= from || span.start >= to) {
      out.push(span);
      continue;
    }
    if (span.start < from) {
      out.push({ ...span, end: from });
    }
    out.push({ ...span, start: Math.max(span.start, from), end: Math.min(span.end, to), color });
    if (span.end > to) {
      out.push({ ...span, start: to });
    }
  }
  return out;
}

/**
 * 下の層の区切りに上の層の色を重ねる(editor の装飾と同じく、色だけを上書きして字の形は下の層のまま)。
 * 行の外・行の長さを超える上の層は、その行の中に収まる分だけ当てる。
 */
export function overlaySemantic(base: HighlightedLines, semantic: readonly SemanticSpan[]): HighlightedLines {
  const lines = base.map((spans) => spans.slice());
  for (const s of semantic) {
    const spans = lines[s.line];
    if (spans === undefined || s.length <= 0) {
      continue;
    }
    lines[s.line] = paint(spans, s.column, s.column + s.length, s.color);
  }
  return lines;
}

/** 範囲の各行を、文字と色の片に切り出す(行の文字は元の行から取る)。 */
export function sliceHighlight(lines: readonly string[], highlighted: HighlightedLines, range: HyRange): Piece[][] {
  const out: Piece[][] = [];
  for (let line = range.start.line; line <= range.end.line && line < lines.length; line += 1) {
    const text = lines[line];
    const from = line === range.start.line ? range.start.character : 0;
    const to = line === range.end.line ? range.end.character : text.length;
    const spans = highlighted[line] ?? [{ start: 0, end: text.length, color: null, fontStyle: PLAIN }];
    const pieces: Piece[] = [];
    for (const span of spans) {
      const start = Math.max(span.start, from);
      const end = Math.min(span.end, to);
      if (end > start) {
        pieces.push({ text: text.slice(start, end), color: span.color, fontStyle: span.fontStyle });
      }
    }
    out.push(pieces);
  }
  return out;
}

/** 塗った file(行と、行ごとの色つきの区切り)— 面が document の版と一緒に持ち、カードごとに切り出す。 */
export interface SourceColoring {
  readonly lines: readonly string[];
  readonly spans: HighlightedLines;
}

/** CSS の色として安全な `#` の 16 進か(webview の style へそのまま置くため)。 */
export function isHexColor(value: unknown): value is string {
  return typeof value === 'string' && /^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/.test(value);
}

/**
 * 1 片の inline の style(色なし・字の形なしなら空 — editor の既定の文字色のまま)。source の箱と本体の文字(U5)が
 * 同じ色で描くための 1 か所。色は `#` の 16 進だけを置く(theme の file や拡張の答えの値を style へ流さないため)。
 */
export function pieceStyle(piece: Piece): string {
  const parts: string[] = [];
  if (piece.color !== null && isHexColor(piece.color)) {
    parts.push(`color:${piece.color}`);
  }
  if (piece.fontStyle.italic) {
    parts.push('font-style:italic');
  }
  if (piece.fontStyle.bold) {
    parts.push('font-weight:bold');
  }
  const lines = [piece.fontStyle.underline ? 'underline' : '', piece.fontStyle.strikethrough ? 'line-through' : ''].filter((l) => l !== '');
  if (lines.length > 0) {
    parts.push(`text-decoration:${lines.join(' ')}`);
  }
  return parts.join(';');
}
