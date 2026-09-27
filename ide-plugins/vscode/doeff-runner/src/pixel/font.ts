// 拡張の icon 字体(contributes.icons の `$(doeff-<名>)`)の元 — icon ごとの文字の番号、SVG 字体の本文、package.json の
// contributes.icons の欄。SVG 字体から woff への変換は生成の script(svg2ttf・ttf2woff)が持ち、ここは文字列だけを作る。

import type { Glyph } from './glyphs';
import { escapeXml, mergeRects } from './render';

/** 字体の 1 点の大きさ(字体の単位)。16 点 × 64 = 1024 が em の高さ。 */
const UNIT = 64;
/** em の高さ(字体の単位)。 */
export const UNITS_PER_EM = 16 * UNIT;
/** 私用領域の最初の文字(U+E000 から icon の並びの順に当てる)。 */
const FIRST_CODEPOINT = 0xe000;
/** 字体の名前(package.json の fontPath と見本の @font-face が同じ名前を使う)。 */
export const FONT_FAMILY = 'doeff-pixel-icons';
/** 拡張の中の字体の置き場(package.json の fontPath)。 */
export const FONT_PATH = 'resources/pixel/doeff-icons.woff';

/** icon の名前と文字の番号(並びの順)。 */
export interface Codepoint {
  readonly name: string;
  readonly codepoint: number;
}

/** icon の並びの順に私用領域の文字を当てる。 */
export function codepoints(glyphs: readonly Glyph[]): Codepoint[] {
  return glyphs.map((g, i) => ({ name: g.name, codepoint: FIRST_CODEPOINT + i }));
}

/** 字体の glyph 1 つの輪郭(y は上向き・基準線 0 から em の高さまで)。 */
function glyphPath(mono: ReadonlyArray<ReadonlyArray<boolean>>): string {
  const rects = mergeRects(16, 16, (x, y) => mono[y][x]);
  return rects
    .map((r) => {
      const left = r.x * UNIT;
      const top = (16 - r.y) * UNIT;
      const bottom = (16 - r.y - r.height) * UNIT;
      const right = (r.x + r.width) * UNIT;
      // 外周は時計回りで揃える(重ならない長方形なので塗りの規則に依らない)
      return `M${left} ${bottom}L${left} ${top}L${right} ${top}L${right} ${bottom}Z`;
    })
    .join('');
}

/** SVG 字体の本文 — 生成の script がこれを TrueType → woff にする。 */
export function svgFont(glyphs: readonly Glyph[]): string {
  const byName = new Map(glyphs.map((g) => [g.name, g]));
  const body = codepoints(glyphs)
    .map(({ name, codepoint }) => {
      const glyph = byName.get(name);
      if (glyph === undefined) {
        throw new Error(`icon ${name} が無い`);
      }
      return `<glyph glyph-name="${escapeXml(name)}" unicode="&#x${codepoint.toString(16)};" horiz-adv-x="${UNITS_PER_EM}" d="${glyphPath(glyph.mono)}"/>`;
    })
    .join('\n');
  return [
    '<?xml version="1.0" standalone="no"?>',
    '<svg xmlns="http://www.w3.org/2000/svg"><defs>',
    `<font id="${FONT_FAMILY}" horiz-adv-x="${UNITS_PER_EM}">`,
    `<font-face font-family="${FONT_FAMILY}" units-per-em="${UNITS_PER_EM}" ascent="${UNITS_PER_EM}" descent="0"/>`,
    '<missing-glyph horiz-adv-x="0"/>',
    body,
    '</font></defs></svg>',
    ''
  ].join('\n');
}

/** package.json の contributes.icons の 1 項目。 */
export interface IconContribution {
  readonly description: string;
  readonly default: { readonly fontPath: string; readonly fontCharacter: string };
}

/** package.json の contributes.icons(`doeff-<名>` → 字体の文字)を作る。 */
export function iconContributions(glyphs: readonly Glyph[]): Record<string, IconContribution> {
  const byName = new Map(glyphs.map((g) => [g.name, g]));
  const entries = codepoints(glyphs).map(({ name, codepoint }): [string, IconContribution] => [
    `doeff-${name}`,
    {
      description: byName.get(name)?.summary ?? name,
      default: { fontPath: FONT_PATH, fontCharacter: `\\${codepoint.toString(16).toUpperCase()}` }
    }
  ]);
  return Object.fromEntries(entries);
}
