// 上の層 — python-semantic-highlighter の公開 API(記号ごとの色)の口の形と検め(純粋 — 拡張の取り出しは host.ts)。
//
// editor の記号ごとの色は、その拡張が「Rust の解析 → 設定の色相の範囲 → file 全体の記号の順の round-robin」で決める。
// 同じ色を出すには同じ計算を呼ぶしかない(面に色の計算を写すと、色の決め方が 2 つになる)ので、拡張の API を呼ぶ。
// API が無い(古い版・入っていない・切ってある)時は上の層なしで描く — editor でも装飾が無ければ下の層だけになる。

import { isHexColor, type SemanticSpan } from './spans';

/** 拡張の id。 */
export const SEMANTIC_HIGHLIGHTER_ID = 'Proboscis.python-semantic-highlighter';
/** 面が読む API の版。 */
export const SEMANTIC_API_VERSION = 1;
/** 面が色を聞く言語。 */
export const HY_LANGUAGE_ID = 'hy';

/** 拡張が activate で返す API(版 1)。 */
export interface SemanticHighlighterApi {
  readonly apiVersion: 1;
  /** source 全体の記号ごとの色(editor の装飾と同じ計算)。切ってある・解析できない時は null。 */
  colorize(source: string, languageId: string): Promise<unknown>;
}

/** 値が object かを見る(API と答えの形を確かめるため)。 */
function isRecord(value: unknown): value is { readonly [key: string]: unknown } {
  return typeof value === 'object' && value !== null;
}

/** 拡張の exports が版 1 の API かを確かめる(形が違えば undefined — 上の層なしで描く)。 */
export function asSemanticApi(candidate: unknown): SemanticHighlighterApi | undefined {
  if (!isRecord(candidate) || candidate.apiVersion !== SEMANTIC_API_VERSION) {
    return undefined;
  }
  const colorize = candidate.colorize;
  if (typeof colorize !== 'function') {
    return undefined;
  }
  return { apiVersion: 1, colorize: (source, languageId) => Promise.resolve(colorize.call(candidate, source, languageId)) };
}

/** 0 以上の整数か。 */
function isNat(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0;
}

/**
 * colorize の答えを検める。null は「上の層なし」、配列でない・形の違う要素は理由つきで捨てる
 * (1 つでも違えば全部を捨てる — 形の違う版の答えを部分的に信じないため)。
 */
export function parseSemanticSpans(value: unknown): { readonly tag: 'ok'; readonly spans: readonly SemanticSpan[] } | { readonly tag: 'none' } | { readonly tag: 'rejected'; readonly reason: string } {
  if (value === null || value === undefined) {
    return { tag: 'none' };
  }
  if (!Array.isArray(value)) {
    return { tag: 'rejected', reason: 'colorize の答えが配列でない' };
  }
  const spans: SemanticSpan[] = [];
  for (const [i, item] of value.entries()) {
    if (!isRecord(item) || !isNat(item.line) || !isNat(item.column) || !isNat(item.length) || !isHexColor(item.color)) {
      return { tag: 'rejected', reason: `colorize の答えの ${i} 番目の形が違う: ${JSON.stringify(item)}` };
    }
    spans.push({ line: item.line, column: item.column, length: item.length, color: item.color });
  }
  return { tag: 'ok', spans };
}
