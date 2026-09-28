// file:line から定義のカードへ(v1 制約 3「agent の報告・git の差分・traceback は source の file と行で来る。
// conversation_input.hy:85 から該当のカードの該当の行へ行ける」・#910 V6)— 位置の文字を読み、索引の定義を引く純粋な関数。
// file を自分で歩かない(v1 制約 5)— 定義は索引の置き場から引く口(definitionsOf)を渡してもらう。

import * as path from 'path';
import type { HyDefinition } from '../hy/contract';

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
