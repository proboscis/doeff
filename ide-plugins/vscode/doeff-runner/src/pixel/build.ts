// 元の定義(glyphs.json)から生成物の全部を作る純粋な関数 — icon の全部(語の icon + service の旗)・SVG・PNG・拡張の
// icon(128×128 の色つき — 32×32 の拡大ではなく別に描いた格子から)・activity bar の輪郭(48×48 の点を 24 css px で出す単色)。file に書くか食い違いを検めるかは生成の script が決める。

import { chooseFlag, flagCollisions, flagGlyph, type FlagChoice, type FlagCollision } from './flags';
import { composeGlyphs, DENSITY, EXTENSION_ICON_PX, extensionIconPixels, SIZES, type Glyph, type GlyphSet, type GlyphSize } from './glyphs';
import { colorSvg, monoSvg, png } from './render';

/** pixel art の icon の置き場(拡張の root からの相対)。 */
export const PIXEL_DIR = 'resources/pixel';
/** PNG の拡大の倍率(32×32 → 64×64、16×16 → 32×32)。整数倍なので縮めて表示してもぼけない。 */
export const PNG_SCALE = 2;

/** icon の全部 — 語の icon の後に service の旗(元の定義に並べた順)。 */
export function allGlyphs(set: GlyphSet): Glyph[] {
  return [...composeGlyphs(set), ...set.serviceFlags.services.map((service) => flagGlyph(set, service))];
}

/** service の旗の検算の結果(見本と生成の script が表示する)。 */
export interface FlagReport {
  readonly choices: readonly FlagChoice[];
  readonly collisions: readonly FlagCollision[];
}

/** 並べた service の旗の選びと重複を数える。 */
export function flagReport(set: GlyphSet): FlagReport {
  const choices = set.serviceFlags.services.map((service) => chooseFlag(set, service));
  return { choices, collisions: flagCollisions(choices) };
}

/** icon の SVG の置き場(拡張の root からの相対)。 */
export function svgPath(name: string, size: GlyphSize): string {
  return `${PIXEL_DIR}/svg/${size}/${name}.svg`;
}

/** icon の PNG の置き場(拡張の root からの相対)。 */
export function pngPath(name: string, size: GlyphSize): string {
  return `${PIXEL_DIR}/png/${size}/${name}.png`;
}

/** 拡張の icon の格子(parseGlyphSet が名前の実在を先に確かめている)。 */
export function extensionGlyph(set: GlyphSet, glyphs: readonly Glyph[]): Glyph {
  const glyph = glyphs.find((g) => g.name === set.extension.glyph);
  if (glyph === undefined) {
    throw new Error(`拡張の icon ${set.extension.glyph} が無い`);
  }
  return glyph;
}

/** activity bar の輪郭(48×48 の単色)の点。 */
export function activityBarMono(set: GlyphSet): boolean[][] {
  return set.extension.activityBar.map((row) => [...row].map((ch) => ch === '#'));
}

/**
 * 字体を除く生成物の全部(拡張の root からの相対の path → 中身)。
 * 字体(woff)は svg2ttf が要るので生成の script が足す。
 */
export function assetFiles(set: GlyphSet): Map<string, string | Buffer> {
  const glyphs = allGlyphs(set);
  const files = new Map<string, string | Buffer>();
  for (const glyph of glyphs) {
    for (const size of SIZES) {
      files.set(svgPath(glyph.name, size), colorSvg(glyph.pixels[size], set.palette, size / DENSITY, glyph.summary));
      files.set(pngPath(glyph.name, size), png(glyph.pixels[size], set.palette, PNG_SCALE));
    }
  }
  const doe = extensionGlyph(set, glyphs);
  const icon = extensionIconPixels(set);
  files.set('icon.png', png(icon, set.palette, set.extension.scale));
  files.set('icon.svg', colorSvg(icon, set.palette, EXTENSION_ICON_PX, doe.summary));
  files.set(`${PIXEL_DIR}/activitybar.svg`, monoSvg(activityBarMono(set), set.extension.activityBar.length / DENSITY));
  return files;
}
