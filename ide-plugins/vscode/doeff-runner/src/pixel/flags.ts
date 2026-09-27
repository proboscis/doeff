// service の旗 — 全 service が同じ形の旗で、色 2 つと模様を名前の hash から選ぶ。生成の時に、元の定義に並べた service
// の間で重複(同じ模様・同じ色の組)が 0 であることを検算する。拡張の実行時に知らない service の名前が来ても、同じ関数で
// 同じ旗を作る(旗の決まりの持ち主はこの 1 か所)。

import { composePixels, LARGE, lightness, SMALL, type ColorIndex, type Family, type Glyph, type GlyphSet, type GlyphSize, type Pixels } from './glyphs';

/** 旗 1 つの選び — 模様の番号と色 2 つ。 */
export interface FlagChoice {
  readonly service: string;
  readonly pattern: string;
  readonly primary: ColorIndex;
  readonly secondary: ColorIndex;
}

/** 名前を旗へ散らすための 32 bit の FNV-1a(UTF-8 の byte の上で — 実行する機体に依らず同じ値)。 */
export function fnv1a(value: string): number {
  let hash = 0x811c9dc5;
  for (const byte of Buffer.from(value, 'utf8')) {
    hash ^= byte;
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash >>> 0;
}

/** 1 色目と並べて見分けられる 2 色目の候補(明るさの差が決まりの値以上)。 */
export function secondaryCandidates(set: GlyphSet, primary: ColorIndex): ColorIndex[] {
  const { colors, minContrast } = set.serviceFlags;
  return colors.filter((c) => c !== primary && Math.abs(lightness(set.palette, c) - lightness(set.palette, primary)) >= minContrast);
}

/** service の名前から旗の模様と色 2 つを選ぶ(2 色は明るさの差が決まりの値以上)。 */
export function chooseFlag(set: GlyphSet, service: string): FlagChoice {
  const { colors, patterns, salt } = set.serviceFlags;
  const hash = fnv1a(`${salt}${service}`);
  const pattern = patterns[hash % patterns.length].name;
  const primary = colors[Math.floor(hash / patterns.length) % colors.length];
  const candidates = secondaryCandidates(set, primary);
  const secondary = candidates[Math.floor(hash / patterns.length / colors.length) % candidates.length];
  return { service, pattern, primary, secondary };
}

/** 旗の選びの重複 — 同じ模様・同じ色の組を持つ service の組。 */
export interface FlagCollision {
  readonly key: string;
  readonly services: readonly string[];
}

/** 並べた service の間の重複を数える(生成の時の検算。空なら重複ゼロ)。 */
export function flagCollisions(choices: readonly FlagChoice[]): FlagCollision[] {
  const byKey = new Map<string, string[]>();
  for (const c of choices) {
    const key = `${c.pattern}/${c.primary}/${c.secondary}`;
    byKey.set(key, [...(byKey.get(key) ?? []), c.service]);
  }
  return [...byKey.entries()].filter(([, services]) => services.length > 1).map(([key, services]) => ({ key, services }));
}

/** 旗の家族を引く(parseGlyphSet が枠のある家族であることを先に確かめている)。 */
function flagFamily(set: GlyphSet): Family {
  const family = set.families.find((f) => f.name === set.serviceFlags.family);
  if (family === undefined || family.frame === null) {
    throw new Error(`旗の家族 ${set.serviceFlags.family} が無い(parseGlyphSet が先に断るはず)`);
  }
  return family;
}

/** 旗の icon の名前(`service-<名>`)。 */
export function flagGlyphName(service: string): string {
  return `service-${service}`;
}

/** 選んだ旗の点を作る(枠 = 竿と布の形、模様 = 布の中の A / B、竿の先の灯 L は lamp の色 — 省けば消えた灯)。 */
export function flagPixels(set: GlyphSet, choice: FlagChoice, size: GlyphSize, lamp?: ColorIndex): Pixels {
  const family = flagFamily(set);
  const pattern = set.serviceFlags.patterns.find((p) => p.name === choice.pattern);
  if (pattern === undefined) {
    throw new Error(`旗の模様 ${choice.pattern} が無い`);
  }
  return composePixels(family.frame, pattern.grids[size], { A: choice.primary, B: choice.secondary, L: lamp ?? set.lamps.off }, size);
}

/** service の旗を icon にする(字体の単色は布の A の色と竿・縁の黒)。 */
export function flagGlyph(set: GlyphSet, service: string): Glyph {
  const choice = chooseFlag(set, service);
  const pixels: Record<GlyphSize, Pixels> = { 32: flagPixels(set, choice, LARGE), 16: flagPixels(set, choice, SMALL) };
  return {
    name: flagGlyphName(service),
    family: set.serviceFlags.family,
    summary: `service ${service} の旗`,
    pixels,
    mono: pixels[LARGE].map((row) => row.map((c) => c !== null && (set.outline.includes(c) || c === choice.primary)))
  };
}
