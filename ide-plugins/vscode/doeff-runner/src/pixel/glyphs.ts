// pixel art の icon の元の定義(resources/pixel/glyphs.json)の型と、読み込みの唯一の検査、家族の枠と中の絵の重ね合わせ。
// 色は PICO-8 の 16 色だけ。格子の 1 文字 = 1 点(`.` = 透明、`0`〜`f` = 色の番号、枠の `:` = 中の地、`A` / `B` = 家族や
// 語ごとの色の差し替え)。VS Code には触らない(生成の script・拡張の実行時・検の 3 か所が同じ関数を使う)。

/** 元の定義の版。 */
export const GLYPHS_VERSION = 1;

/** PICO-8 の 16 色(番号の順)。これ以外の色は使わない。 */
export const PICO8 = [
  '#000000',
  '#1D2B53',
  '#7E2553',
  '#008751',
  '#AB5236',
  '#5F574F',
  '#C2C3C7',
  '#FFF1E8',
  '#FF004D',
  '#FFA300',
  '#FFEC27',
  '#00E436',
  '#29ADFF',
  '#83769C',
  '#FF77A8',
  '#FFCCAA'
] as const;

/** 色の明るさ(0〜1・sRGB の重みの和)— 旗の 2 色が見分けられるかを測るため。 */
export function lightness(color: ColorIndex): number {
  const hex = PICO8[color];
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** 色の番号の文字(`0`〜`f`)。 */
const COLOR_CHARS = '0123456789abcdef';

/** 色の番号(0〜15)。 */
export type ColorIndex = number;

/** 重ね合わせた後の格子 — 行ごとの点(null = 透明)。 */
export type Pixels = ReadonlyArray<ReadonlyArray<ColorIndex | null>>;

/** icon の 2 つの大きさ。 */
export const SIZES = [16, 8] as const;
export type GlyphSize = (typeof SIZES)[number];

/** 色の差し替えの名前(枠の `A` / `B`)。 */
export const TINT_SLOTS = ['A', 'B'] as const;
export type TintSlot = (typeof TINT_SLOTS)[number];
export type Tint = Readonly<Partial<Record<TintSlot, ColorIndex>>>;

/** 家族の枠 — 形の格子(大きさごと)・中の地の色・既定の差し替え。 */
export interface Frame {
  /** 中の地(`:`)の色 — 色の番号か差し替えの名前 */
  readonly fill: ColorIndex | TintSlot;
  readonly tint: Tint;
  readonly grids: Readonly<Record<GlyphSize, readonly string[]>>;
}

/** 家族 — 枠の形が家族を、中の絵が語を表す。 */
export interface Family {
  readonly name: string;
  readonly label: string;
  readonly summary: string;
  /** 枠の無い家族(linter の印・拡張の icon)は null */
  readonly frame: Frame | null;
}

/** icon 1 つ(元の定義のまま)。 */
export interface GlyphSource {
  readonly name: string;
  readonly family: string;
  /** 語の一言(hover・見本・文書で同じ文) */
  readonly summary: string;
  readonly tint: Tint;
  readonly grids: Readonly<Record<GlyphSize, readonly string[]>>;
  /** 字体(単色)の格子 — 無ければ 16×16 の黒(色 0)の点から作る */
  readonly mono: readonly string[] | null;
}

/** service の旗の模様 1 つ(布の中の `A` / `B`)。 */
export interface FlagPattern {
  readonly name: string;
  readonly grids: Readonly<Record<GlyphSize, readonly string[]>>;
}

/** service の旗の決まり — 同じ形の旗に、名前の hash から色と模様を選ぶ。 */
export interface ServiceFlags {
  readonly family: string;
  /** hash に混ぜる値(重複が出た時に変える) */
  readonly salt: string;
  readonly colors: readonly ColorIndex[];
  /** 2 色の明るさの差の下限(0〜1)— 見分けにくい組(白と薄い桃など)を選ばない */
  readonly minContrast: number;
  readonly patterns: readonly FlagPattern[];
  /** 生成の時に重複を検算する service の名前 */
  readonly services: readonly string[];
}

/** 拡張の icon — 色つきの icon の名前と、activity bar の単色の輪郭(24×24)。 */
export interface ExtensionIcon {
  readonly glyph: string;
  readonly scale: number;
  readonly activityBar: readonly string[];
}

/** 元の定義の全体。 */
export interface GlyphSet {
  readonly version: number;
  readonly families: readonly Family[];
  readonly glyphs: readonly GlyphSource[];
  readonly serviceFlags: ServiceFlags;
  readonly extension: ExtensionIcon;
}

/** 読み込みの結果 — 読めたか、理由つきで読めなかったか。 */
export type GlyphSetParseResult = { readonly tag: 'ok'; readonly set: GlyphSet } | { readonly tag: 'invalid'; readonly reason: string };

/** 契約に合わない定義(理由つき)。 */
class GlyphContractViolation extends Error {}

type JsonObject = { readonly [key: string]: unknown };

/** 契約に合わない所を理由つきで知らせる(parse の外へは出さない)。 */
function fail(reason: string): never {
  throw new GlyphContractViolation(reason);
}

/** 値が JSON の object(配列でない)であるかを見る。 */
function isObject(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** object であることを検める。 */
function record(value: unknown, where: string): JsonObject {
  if (!isObject(value)) {
    fail(`${where}: object ではない`);
  }
  return value;
}

/** 空でない文字列の欄を検める。 */
function text(value: JsonObject, key: string, where: string): string {
  const v = value[key];
  if (typeof v !== 'string' || v === '') {
    fail(`${where}.${key}: 空でない文字列ではない`);
  }
  return v;
}

/** 配列の欄を検める。 */
function list(value: JsonObject, key: string, where: string): readonly unknown[] {
  const v = value[key];
  if (!Array.isArray(v)) {
    fail(`${where}.${key}: 配列ではない`);
  }
  return v;
}

/** 色の番号の文字を読む。 */
function colorOf(value: unknown, where: string): ColorIndex {
  if (typeof value !== 'string' || value.length !== 1 || !COLOR_CHARS.includes(value)) {
    fail(`${where}: PICO-8 の色の番号(0〜f の 1 文字)ではない: ${JSON.stringify(value)}`);
  }
  return COLOR_CHARS.indexOf(value);
}

/** 文字が色の差し替えの名前(A / B)であるかを見る。 */
function isTintSlot(value: string): value is TintSlot {
  return (TINT_SLOTS as readonly string[]).includes(value);
}

/** 色の差し替えの表(名前 → 色)を検める。 */
function tintOf(value: unknown, where: string): Tint {
  if (value === undefined) {
    return {};
  }
  const r = record(value, where);
  const tint: Partial<Record<TintSlot, ColorIndex>> = {};
  for (const [slot, color] of Object.entries(r)) {
    if (!isTintSlot(slot)) {
      fail(`${where}: 差し替えの名前は A か B: ${slot}`);
    }
    tint[slot] = colorOf(color, `${where}.${slot}`);
  }
  return tint;
}

/** 格子を読む — 行の数と長さが大きさちょうどで、文字が許された集合の中。 */
function gridOf(value: unknown, size: number, allowed: string, where: string): readonly string[] {
  if (!Array.isArray(value) || value.length !== size) {
    fail(`${where}: ${size} 行の配列ではない`);
  }
  return value.map((row: unknown, y) => {
    if (typeof row !== 'string' || row.length !== size) {
      fail(`${where}[${y}]: 長さ ${size} の文字列ではない: ${JSON.stringify(row)}`);
    }
    for (const ch of row) {
      if (!allowed.includes(ch)) {
        fail(`${where}[${y}]: 許されない文字 ${JSON.stringify(ch)}(許す文字: ${allowed})`);
      }
    }
    return row;
  });
}

/** 枠の格子の文字 — 透明・色・中の地・差し替え。 */
const FRAME_CHARS = `.:${COLOR_CHARS}AB`;
/** 絵の格子の文字 — 透明(枠を見せる)・色・差し替え。 */
const PICTURE_CHARS = `.${COLOR_CHARS}AB`;
/** 旗の模様の文字。 */
const PATTERN_CHARS = '.AB';
/** 単色の格子の文字(`#` = 点)。 */
const MONO_CHARS = '.#';

/** 2 つの大きさ(px16・px8)の格子を検める。 */
function gridsOf(
  value: JsonObject,
  allowed: string,
  where: string
): Readonly<Record<GlyphSize, readonly string[]>> {
  return {
    16: gridOf(value.px16, 16, allowed, `${where}.px16`),
    8: gridOf(value.px8, 8, allowed, `${where}.px8`)
  };
}

/** 家族の枠を検める(枠の無い家族は null)。 */
function frameOf(value: unknown, where: string): Frame | null {
  if (value === null) {
    return null;
  }
  const r = record(value, where);
  const fillText = text(r, 'fill', where);
  const fill = isTintSlot(fillText) ? fillText : colorOf(fillText, `${where}.fill`);
  return { fill, tint: tintOf(r.tint, `${where}.tint`), grids: gridsOf(r, FRAME_CHARS, where) };
}

/** 家族 1 つを検める。 */
function familyOf(value: unknown, where: string): Family {
  const r = record(value, where);
  if (!('frame' in r)) {
    fail(`${where}.frame: 欄が無い(枠の無い家族は null)`);
  }
  return { name: text(r, 'name', where), label: text(r, 'label', where), summary: text(r, 'summary', where), frame: frameOf(r.frame, `${where}.frame`) };
}

/** icon 1 つを検める。 */
function glyphOf(value: unknown, where: string): GlyphSource {
  const r = record(value, where);
  const name = text(r, 'name', where);
  const at = `${where}(${name})`;
  return {
    name,
    family: text(r, 'family', at),
    summary: text(r, 'summary', at),
    tint: tintOf(r.tint, `${at}.tint`),
    grids: gridsOf(r, PICTURE_CHARS, at),
    mono: r.mono === undefined ? null : gridOf(r.mono, 16, MONO_CHARS, `${at}.mono`)
  };
}

/** service の旗の決まり(色・模様・検算する名前)を検める。 */
function serviceFlagsOf(value: unknown, where: string): ServiceFlags {
  const r = record(value, where);
  const colors = list(r, 'colors', where).map((c, i) => colorOf(c, `${where}.colors[${i}]`));
  if (new Set(colors).size !== colors.length || colors.length < 2) {
    fail(`${where}.colors: 重ならない 2 色以上ではない`);
  }
  const patterns = list(r, 'patterns', where).map((p, i) => {
    const pr = record(p, `${where}.patterns[${i}]`);
    const name = text(pr, 'name', `${where}.patterns[${i}]`);
    return { name, grids: gridsOf(pr, PATTERN_CHARS, `${where}.patterns(${name})`) };
  });
  const services = list(r, 'services', where).map((s, i) => {
    if (typeof s !== 'string' || !/^[a-z][a-z0-9-]*$/.test(s)) {
      fail(`${where}.services[${i}]: 小文字と - の名前ではない: ${JSON.stringify(s)}`);
    }
    return s;
  });
  if (typeof r.salt !== 'string') {
    fail(`${where}.salt: 文字列ではない`);
  }
  const minContrast = r.minContrast;
  if (typeof minContrast !== 'number' || minContrast < 0 || minContrast > 1) {
    fail(`${where}.minContrast: 0〜1 の数ではない`);
  }
  if (patterns.length === 0) {
    fail(`${where}.patterns: 模様が 1 つも無い`);
  }
  for (const color of colors) {
    if (!colors.some((c) => c !== color && Math.abs(lightness(c) - lightness(color)) >= minContrast)) {
      fail(`${where}.colors: 色 ${color.toString(16)} と明るさの差が ${minContrast} 以上の色が無い`);
    }
  }
  return { family: text(r, 'family', where), salt: r.salt, colors, minContrast, patterns, services };
}

/** 拡張の icon の決まりを検める。 */
function extensionOf(value: unknown, where: string): ExtensionIcon {
  const r = record(value, where);
  const scale = r.scale;
  if (typeof scale !== 'number' || !Number.isInteger(scale) || scale < 1) {
    fail(`${where}.scale: 1 以上の整数ではない`);
  }
  return { glyph: text(r, 'glyph', where), scale, activityBar: gridOf(r.activityBar, 24, MONO_CHARS, `${where}.activityBar`) };
}

/** 絵が枠の中の地(`:`)の外に点を置いていないかを確かめる(家族の文法 — 枠は家族、中は語)。 */
function checkInside(glyph: GlyphSource, frame: Frame): void {
  for (const size of SIZES) {
    const picture = glyph.grids[size];
    const shape = frame.grids[size];
    picture.forEach((row, y) => {
      [...row].forEach((ch, x) => {
        if (ch !== '.' && shape[y][x] !== ':') {
          fail(`glyphs(${glyph.name}).px${size}[${y}][${x}]: 枠の中の地の外に点がある(家族の枠は変えない)`);
        }
      });
    });
  }
}

/** 旗の模様が布(枠の中の地)をちょうど塗るかを確かめる。 */
function checkPattern(pattern: FlagPattern, frame: Frame): void {
  for (const size of SIZES) {
    pattern.grids[size].forEach((row, y) => {
      [...row].forEach((ch, x) => {
        const inside = frame.grids[size][y][x] === ':';
        if (inside !== (ch !== '.')) {
          fail(`serviceFlags.patterns(${pattern.name}).px${size}[${y}][${x}]: 布の点と模様の点が合わない`);
        }
      });
    });
  }
}

/** 全体の整合 — 名前の重なり・知らない家族・枠の外の点・差し替えの不足。 */
function checkSet(set: GlyphSet): void {
  const families = new Map(set.families.map((f) => [f.name, f]));
  if (families.size !== set.families.length) {
    fail('families: 同じ名前の家族が 2 つある');
  }
  const names = new Set<string>();
  for (const glyph of set.glyphs) {
    if (!/^[a-z][a-z0-9-]*$/.test(glyph.name)) {
      fail(`glyphs(${glyph.name}): 名前は小文字と - だけ`);
    }
    if (names.has(glyph.name)) {
      fail(`glyphs(${glyph.name}): 同じ名前が 2 つある`);
    }
    names.add(glyph.name);
    const family = families.get(glyph.family);
    if (family === undefined) {
      fail(`glyphs(${glyph.name}).family: 知らない家族 ${glyph.family}`);
    }
    if (family.frame !== null) {
      checkInside(glyph, family.frame);
    }
    // 差し替えの名前を使うなら、語か家族の枠がその色を決めている
    for (const size of SIZES) {
      for (const slot of TINT_SLOTS) {
        const used = glyph.grids[size].some((row) => row.includes(slot)) || (family.frame?.grids[size].some((row) => row.includes(slot)) ?? false);
        if (used && glyph.tint[slot] === undefined && family.frame?.tint[slot] === undefined) {
          fail(`glyphs(${glyph.name}): 差し替え ${slot} の色が決まっていない`);
        }
      }
    }
  }
  const flagFamily = families.get(set.serviceFlags.family);
  if (flagFamily?.frame === null || flagFamily === undefined) {
    fail(`serviceFlags.family: 枠のある家族ではない: ${set.serviceFlags.family}`);
  }
  for (const pattern of set.serviceFlags.patterns) {
    checkPattern(pattern, flagFamily.frame);
  }
  const doe = set.glyphs.find((g) => g.name === set.extension.glyph);
  if (doe === undefined) {
    fail(`extension.glyph: 知らない icon ${set.extension.glyph}`);
  }
}

/** glyphs.json を読む唯一の口 — 合わない定義は理由つきで断り、既定値で埋めない。 */
export function parseGlyphSet(source: string): GlyphSetParseResult {
  try {
    const root = record(JSON.parse(source), 'glyphs.json');
    if (root.version !== GLYPHS_VERSION) {
      fail(`version: ${GLYPHS_VERSION} ではない: ${JSON.stringify(root.version)}`);
    }
    const set: GlyphSet = {
      version: GLYPHS_VERSION,
      families: list(root, 'families', 'glyphs.json').map((f, i) => familyOf(f, `families[${i}]`)),
      glyphs: list(root, 'glyphs', 'glyphs.json').map((g, i) => glyphOf(g, `glyphs[${i}]`)),
      serviceFlags: serviceFlagsOf(root.serviceFlags, 'serviceFlags'),
      extension: extensionOf(root.extension, 'extension')
    };
    checkSet(set);
    return { tag: 'ok', set };
  } catch (error) {
    if (error instanceof GlyphContractViolation || error instanceof SyntaxError) {
      return { tag: 'invalid', reason: error.message };
    }
    throw error;
  }
}

/** 格子の 1 文字を色にする(差し替えは語 → 家族の枠の順で引く)。 */
function resolveChar(ch: string, tint: Tint, frameTint: Tint): ColorIndex | null {
  if (ch === '.') {
    return null;
  }
  if (isTintSlot(ch)) {
    const color = tint[ch] ?? frameTint[ch];
    if (color === undefined) {
      throw new Error(`差し替え ${ch} の色が無い(parseGlyphSet が先に断るはず)`);
    }
    return color;
  }
  return COLOR_CHARS.indexOf(ch);
}

/** 枠と中の絵を重ねた格子を作る(枠の無い家族は絵だけ)。 */
export function composePixels(
  frame: Frame | null,
  picture: readonly string[],
  tint: Tint,
  size: GlyphSize
): Pixels {
  const frameTint = frame?.tint ?? {};
  return picture.map((row, y) =>
    [...row].map((ch, x) => {
      const own = resolveChar(ch, tint, frameTint);
      if (own !== null || frame === null) {
        return own;
      }
      const f = frame.grids[size][y][x];
      if (f === ':') {
        return typeof frame.fill === 'number' ? frame.fill : resolveChar(frame.fill, tint, frameTint);
      }
      return resolveChar(f, tint, frameTint);
    })
  );
}

/** 重ね合わせの済んだ icon 1 つ(生成物と拡張が使う形)。 */
export interface Glyph {
  readonly name: string;
  readonly family: string;
  readonly summary: string;
  readonly pixels: Readonly<Record<GlyphSize, Pixels>>;
  /** 字体の単色の点(16×16) */
  readonly mono: ReadonlyArray<ReadonlyArray<boolean>>;
}

/** 単色の点 — 明示の格子か、16×16 の黒(色 0)の点。 */
function monoOf(source: GlyphSource, pixels16: Pixels): boolean[][] {
  if (source.mono !== null) {
    return source.mono.map((row) => [...row].map((ch) => ch === '#'));
  }
  return pixels16.map((row) => row.map((c) => c === 0));
}

/** 元の定義の icon を全部重ね合わせる(service の旗は flags.ts が足す)。 */
export function composeGlyphs(set: GlyphSet): Glyph[] {
  const families = new Map(set.families.map((f) => [f.name, f]));
  return set.glyphs.map((source) => {
    const frame = families.get(source.family)?.frame ?? null;
    const pixels16 = composePixels(frame, source.grids[16], source.tint, 16);
    return {
      name: source.name,
      family: source.family,
      summary: source.summary,
      pixels: { 16: pixels16, 8: composePixels(frame, source.grids[8], source.tint, 8) },
      mono: monoOf(source, pixels16)
    };
  });
}
