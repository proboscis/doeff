// file:line から定義のカードへ(v1 制約 3「agent の報告・git の差分・traceback は source の file と行で来る。
// conversation_input.hy:85 から該当のカードの該当の行へ行ける」・#910 V6)— 位置の文字を読み、索引の定義を引く純粋な関数。
// file を自分で歩かない(v1 制約 5)— 定義は索引の置き場から引く口(definitionsOf)を渡してもらう。

import * as path from 'path';
import type { HyDefinition, HyPosition } from '../hy/contract';

/** 面で見せる先 — 定義と、光らせる source の行(0 始まり・無ければ undefined)と、source の箱も開くか(違反の項目から — v10)。 */
export interface RevealTarget {
  readonly qualifiedName: string;
  readonly line: number | undefined;
  readonly showSource: boolean;
}

/** 読んだ位置 — file の path(書かれたまま)と 1 始まりの行。読めなければ undefined。 */
export type ParsedLocation = { readonly file: string; readonly line: number } | undefined;

/**
 * 位置の文字を読む — `path:line`・`path:line:col`(報告・差分・linter)と、Python の traceback の `File "path", line N`。
 * 前後の空白・`file://` は外す。
 */
export function parseLocation(text: string): ParsedLocation {
  const trimmed = text.trim().replace(/^file:\/\//, '');
  const traceback = /File "([^"]+)", line (\d+)/.exec(trimmed);
  if (traceback !== null) {
    return { file: traceback[1], line: Number(traceback[2]) };
  }
  const plain = /^(.+?\.(?:hy|hyk|hyp)):(\d+)(?::\d+)?/.exec(trimmed);
  return plain === null ? undefined : { file: plain[1], line: Number(plain[2]) };
}

/** 位置を引いた答え。 */
export type Located =
  | { readonly tag: 'found'; readonly path: string; readonly definition: HyDefinition; readonly line: number }
  | { readonly tag: 'unreadable'; readonly message: string }
  | { readonly tag: 'not-indexed'; readonly message: string }
  | { readonly tag: 'no-definition'; readonly message: string };

/**
 * 位置の file を workspace の root から探し(絶対の path はそのまま)、その行を含む入れ子でない定義を索引から引く。
 * 答えの line は 0 始まり(VS Code と索引の行)。
 */
export function locate(
  parsed: ParsedLocation,
  roots: readonly string[],
  definitionsOf: (filePath: string) => readonly HyDefinition[] | undefined
): Located {
  if (parsed === undefined) {
    return { tag: 'unreadable', message: 'file:line の形で読めない(例 controllers/messaging/core/conversation_input.hy:85)' };
  }
  const candidates = path.isAbsolute(parsed.file) ? [parsed.file] : roots.map((root) => path.join(root, parsed.file));
  for (const candidate of candidates) {
    const definitions = definitionsOf(candidate);
    if (definitions === undefined) {
      continue;
    }
    const line = parsed.line - 1;
    const owner = definitions.find((d) => d.container === null && d.fullRange.start.line <= line && line <= d.fullRange.end.line);
    return owner === undefined
      ? { tag: 'no-definition', message: `${parsed.file}:${parsed.line} を含む定義が索引に無い(定義の外の行)` }
      : { tag: 'found', path: candidate, definition: owner, line };
  }
  return { tag: 'not-indexed', message: `${parsed.file} が Hy の索引(hy-index)に無い` };
}

/** 違反(linter)の一覧の項目を押した時の命令 — 読む面の内部の命令(引数は ViolationPlace 1 つ・v10)。 */
export const REVEAL_VIOLATION_COMMAND = 'doeff-runner.read.revealViolation';

/** 違反の位置(linter の path と 0 始まりの範囲 — editor で開く時は範囲を選ぶ)。 */
export interface ViolationPlace {
  readonly path: string;
  readonly start: HyPosition;
  readonly end: HyPosition;
}

/** 違反の項目を押した時にすること。 */
export type ViolationAction =
  | { readonly tag: 'card'; readonly path: string; readonly target: RevealTarget }
  | { readonly tag: 'plane-top'; readonly path: string; readonly message: string }
  | { readonly tag: 'editor'; readonly place: ViolationPlace };

/** 読む面が開ける Hy の file か。 */
const HY_FILE = /\.(?:hy|hyk|hyp)$/;

/**
 * 違反の項目から読む面へ行くため(v10・operator "clicking linter issue browser's item must open the corresponding hy files'
 * reading view with source")— 違反の行を含む定義のカードを開いて source の箱も開く。定義の外の行は面の先頭と理由の 1 行。
 * 読む面を設定で切っている時・Hy でない file・索引に無い file は今までどおり editor で開く。定義の解決は file:line と同じ locate。
 */
export function violationAction(
  place: ViolationPlace,
  planeEnabled: boolean,
  roots: readonly string[],
  definitionsOf: (filePath: string) => readonly HyDefinition[] | undefined
): ViolationAction {
  if (!planeEnabled || !HY_FILE.test(place.path)) {
    return { tag: 'editor', place };
  }
  const located = locate(parseLocation(`${place.path}:${place.start.line + 1}`), roots, definitionsOf);
  switch (located.tag) {
    case 'found':
      return { tag: 'card', path: located.path, target: { qualifiedName: located.definition.qualifiedName, line: located.line, showSource: true } };
    case 'no-definition':
      return { tag: 'plane-top', path: place.path, message: located.message };
    case 'unreadable':
    case 'not-indexed':
      return { tag: 'editor', place };
    default: {
      const unreachable: never = located;
      throw new Error(`網羅されていない答え: ${JSON.stringify(unreachable)}`);
    }
  }
}
