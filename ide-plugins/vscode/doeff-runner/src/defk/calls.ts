// 呼びを `f(a, b)` の形で見せる表示の置き換え — どの置き換えを描くか・どこに何を描くか・hover の文を決める純粋な関数(VS Code に触らない)。
// 式の形を読むのは linter の editor-json の `rewrites`(src/project/call_view.rs)の 1 か所で、ここは Hy を読み直さずに描く場所を決めるだけ。
// 読むだけの表示 — file の文字は変えない(agora-redesign #849・operator 2026-09-28 "also, maybe we could make the func call look like f(a,b)
// instead of (f a b)?"・式の途中の effect は `!` の印を残す案 A "lets try A")。

import type { LintRange, LintRewrite, LintRewritePart, LintTypeRef } from '../lint/contract';
import { typeText, type LineSource, type LineSpan, type Span } from './model';

/** 描く物 1 つ — 元の文字の範囲(空なら挿すだけ)を隠し、text を見せる(effect があれば text の前に装置の絵)。 */
export interface CallMark {
  readonly span: Span;
  readonly text: string;
  readonly effect: string | null;
}

/** 範囲が document の中の 1 行に収まって正しいか(版の食い違いで外れた位置を描かない)。 */
function oneLine(range: LintRange, lines: LineSource): boolean {
  return (
    range.start.line === range.end.line &&
    range.end.line < lines.lineCount &&
    range.start.character <= range.end.character &&
    range.end.character <= lines.lineText(range.end.line).length
  );
}

/** 範囲が document の中で正しいか。 */
function inside(range: LintRange, lines: LineSource): boolean {
  return range.start.line <= range.end.line && range.end.line < lines.lineCount && range.end.character <= lines.lineText(range.end.line).length;
}

/**
 * 描く置き換え(番号の列)。元の lisp で見せる物 = カーソル(と選んだ範囲)の行にかかる置き換えと、その中の置き換え全部
 * (外を lisp で見せる時は、括弧の要否が外に依るので中も lisp)。位置が document に合わない置き換えも描かない(中も)。
 */
export function shownRewrites(rewrites: readonly LintRewrite[], cursors: readonly LineSpan[], lines: LineSource): number[] {
  const plain = new Map<number, boolean>();
  const isPlain = (i: number, depth = 0): boolean => {
    const known = plain.get(i);
    if (known !== undefined) {
      return known;
    }
    const r = rewrites[i];
    const own =
      !inside(r.range, lines) ||
      r.edits.some((e) => !oneLine(e.range, lines)) ||
      cursors.some((c) => c.start <= r.range.end.line && c.end >= r.range.start.line);
    // 番号の輪(壊れた出力)で回り続けないよう、深さで打ち切る
    const parent = r.parent;
    const result = own || (parent !== null && parent !== i && parent < rewrites.length && depth < rewrites.length && isPlain(parent, depth + 1));
    plain.set(i, result);
    return result;
  };
  return rewrites.map((_, i) => i).filter((i) => !isPlain(i));
}

/** 描く置き換えの edit を、描く物の列にする(本文の順)。 */
export function callMarks(rewrites: readonly LintRewrite[], shown: readonly number[]): CallMark[] {
  return shown
    .flatMap((i) => rewrites[i].edits)
    .map((e) => ({ span: { line: e.range.start.line, start: e.range.start.character, end: e.range.end.character }, text: e.text, effect: e.effect }))
    .sort((a, b) => a.span.line - b.span.line || a.span.start - b.span.start || a.span.end - b.span.end);
}

/** 描く置き換えの中で、effect の頭の範囲(文字の置き換え #841 が同じ所に effect の印を二重に描かないため)。 */
export function effectHeadSpans(rewrites: readonly LintRewrite[], shown: readonly number[]): Span[] {
  return shown
    .flatMap((i) => rewrites[i].parts)
    .filter((p) => p.role === 'effect' && p.range.start.line === p.range.end.line)
    .map((p) => ({ line: p.range.start.line, start: p.range.start.character, end: p.range.end.character }));
}

/** 位置を含む、描いている置き換えのうち一番内側の物(hover のため)。 */
export function rewriteAt(rewrites: readonly LintRewrite[], shown: readonly number[], line: number, character: number): LintRewrite | undefined {
  const contains = (r: LintRewrite): boolean => {
    const { start, end } = r.range;
    const afterStart = line > start.line || (line === start.line && character >= start.character);
    const beforeEnd = line < end.line || (line === end.line && character <= end.character);
    return afterStart && beforeEnd;
  };
  const size = (r: LintRewrite): number => r.original.length;
  return shown
    .map((i) => rewrites[i])
    .filter(contains)
    .sort((a, b) => size(a) - size(b))[0];
}

/** 部品の種類の言葉(hover)。 */
const ROLE_WORDS: Readonly<Record<string, string>> = {
  effect: 'effect',
  defk: 'defk',
  deff: 'deff',
  type: '型',
  function: '関数',
  builtin: '組み込み',
  local: '局所の名',
  method: 'method'
};

/** 定義へ飛ぶ link(定義の無い部品は名の文字だけ)。 */
function partLink(part: LintRewritePart, command: string): string {
  if (part.definition === null) {
    return `\`${part.name}\``;
  }
  const args = encodeURIComponent(JSON.stringify([part.definition.path, part.definition.range.start.line, part.definition.range.start.character]));
  return `[\`${part.name}\`](command:${command}?${args} "${part.definition.path}")`;
}

/** 答えの型の文字(無ければ ?)。 */
function answerText(answer: LintTypeRef | null): string {
  return answer === null ? '?' : typeText(answer);
}

/** 置き換えの hover の Markdown — 元の lisp(一字一句)・見せている形・部品ごとの定義への link と答えの型。 */
export function rewriteHover(rewrite: LintRewrite, command: string): string {
  const lines = ['**元の lisp**', '```hy', rewrite.original, '```', '表示', '```', rewrite.text, '```'];
  if (rewrite.parts.length > 0) {
    lines.push('');
    for (const part of rewrite.parts) {
      const role = part.role === null ? '' : `${ROLE_WORDS[part.role] ?? part.role} `;
      lines.push(`- ${role}${partLink(part, command)} → \`${answerText(part.answer)}\``);
    }
  }
  lines.push('', '_表示だけの置き換え(doeff-linter の editor-json `rewrites`)。file の中身は元の lisp のまま — カーソルの行は元の文字で見せる。_');
  return lines.join('\n');
}
