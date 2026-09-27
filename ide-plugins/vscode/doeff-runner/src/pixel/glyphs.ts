// pixel art の icon の元の定義(resources/pixel/glyphs.json)の型と、読み込みの唯一の検査、家族の枠と中の絵の重ね合わせ。
// 色は元の定義の色の組(palette)の表だけ — 格子は色を番号の文字で参照するので、表を差し替えれば全部の sprite の色が変わる。
// 格子の 1 文字 = 1 点(`.` = 透明、`0`〜`9`・`a`〜`v` = 色の組の番号、枠の `:` = 中の地、`A` / `B` = 家族や
// 語ごとの色の差し替え、`L` = 物に付いた小さな灯 — 消えている時は元の定義の lamps.off の色、違反の重さで灯す)。VS Code には触らない(生成の script・拡張の実行時・検の 3 か所が同じ関数を使う)。

/** 元の定義の版。 */
export const GLYPHS_VERSION = 3;

/** 色の組 — 番号の順の `#RRGGBB`(元の定義の palette の表。sprite の格子は番号の文字で参照する)。 */
export type Palette = readonly string[];

/** 色の明るさ(0〜1・sRGB の重みの和)— 旗の 2 色が見分けられるかを測るため。 */
export function lightness(palette: Palette, color: ColorIndex): number {
  const hex = palette[color];
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** 色の番号の文字(`0`〜`9`・`a`〜`v` — 色の組は 32 色まで)。 */
const COLOR_CHARS = '0123456789abcdefghijklmnopqrstuv';

/** 色の番号(色の組の表の位置)。 */
export type ColorIndex = number;

/** 重ね合わせた後の格子 — 行ごとの点(null = 透明)。 */
export type Pixels = ReadonlyArray<ReadonlyArray<ColorIndex | null>>;

/**
 * icon の 2 つの大きさ(点の数)— 大きい sprite(木・gutter・hover・字体)と小さい sprite(右下の印・文字の中の画)。
 * 版 2 で縦横とも 2 倍の点で描き直した(拡大ではなく、増えた点を陰影・縁の光・小さな部品に使う)。
 */
export const SIZES = [32, 16] as const;
export type GlyphSize = (typeof SIZES)[number];
/** 大きい sprite の点の数。 */
export const LARGE: GlyphSize = 32;
/** 小さい sprite の点の数。 */
export const SMALL: GlyphSize = 16;
/**
 * 1 css px に並ぶ点の数(縦横それぞれ)— 画面に出す大きさは版 1 のまま(大きい sprite は 16 css px・小さい sprite は
 * 8 css px)にし、点の密度だけを上げる。Retina の画面では 1 点がちょうど 1 画素になる。
 */
export const DENSITY = 2;
/** activity bar の単色の輪郭の点の数(24 css px に DENSITY 倍)。 */
export const ACTIVITY_BAR_SIZE = 24 * DENSITY;

/** 色の差し替えの名前(枠の `A` / `B` と、物に付いた灯の `L`)。 */
export const TINT_SLOTS = ['A', 'B', 'L'] as const;

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

/** icon 1 つ(元の定義のまま・絵の参照は解いた後)。 */
export interface GlyphSource {
  readonly name: string;
  readonly family: string;
  /** 語の一言(hover・見本・文書で同じ文) */
  readonly summary: string;
  readonly tint: Tint;
  /** 中の絵の格子(`picture` で別の icon の絵を使う時は、その icon の格子) */
  readonly grids: Readonly<Record<GlyphSize, readonly string[]>>;
  /** 別の icon の絵を使う時のその名前(同じ語は同じ絵 — 絵を写さずに枠だけ変える)。自分で描いた絵は null */
  readonly picture: string | null;
  /** 字体(単色)の格子 — 無ければ大きい sprite の黒と紺(輪郭の色)の点から作る */
  readonly mono: readonly string[] | null;
}

/** 読んだだけの icon — 絵は自分の格子か、別の icon の名前(checkSet の前に解く)。 */
type RawGlyph = Omit<GlyphSource, 'grids' | 'picture'> & {
  readonly drawing: { readonly tag: 'grids'; readonly grids: Readonly<Record<GlyphSize, readonly string[]>> } | { readonly tag: 'picture'; readonly name: string };
};

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

/** 拡張の icon — 色つきの icon の名前と、activity bar の単色の輪郭(48×48 の点を 24 css px で出す)。 */
export interface ExtensionIcon {
  /** 小さく出る所(16・32)で使う語の icon の名前 */
  readonly glyph: string;
  /**
   * 拡張の一覧の icon(128×128)の格子 — 32×32 を拡大すると細部が足りないので別に描く(64×64 を 2 倍、または
   * 128×128 をそのまま)。格子の点の数 × scale が EXTENSION_ICON_PX
   */
  readonly icon: readonly string[];
  /** 拡張の一覧の icon の格子の拡大の倍率(整数) */
  readonly scale: number;
  readonly activityBar: readonly string[];
}

/** 拡張の一覧の icon の大きさ(画素)。 */
export const EXTENSION_ICON_PX = 128;

/** 元の定義の全体。 */
export interface GlyphSet {
  readonly version: number;
  readonly families: readonly Family[];
  readonly glyphs: readonly GlyphSource[];
  readonly serviceFlags: ServiceFlags;
  readonly extension: ExtensionIcon;
  /** 色の組(番号の順の `#RRGGBB`)— 生成物・拡張・見本の画はすべてこの表で色を引く */
  readonly palette: Palette;
  /** 灯(`L`)の色 — 消えている時と違反の重さごと */
  readonly lamps: Lamps;
  /** 輪郭の色 — 字体(単色)に数える色。先頭は右下の印の周りに足す縁の色 */
  readonly outline: readonly ColorIndex[];
}

/** 灯の色 — 消えている時(off)と違反の重さ(error・warning・info)。 */
export interface Lamps {
  readonly off: ColorIndex;
  readonly error: ColorIndex;
  readonly warning: ColorIndex;
  readonly info: ColorIndex;
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
    fail(`${where}: 色の番号(0〜9・a〜v の 1 文字)ではない: ${JSON.stringify(value)}`);
  }
  return COLOR_CHARS.indexOf(value);
}

/** 色の組の中の番号を読む(色の組に無い番号は断る)。 */
function paletteColorOf(value: unknown, palette: Palette, where: string): ColorIndex {
  const color = colorOf(value, where);
  if (color >= palette.length) {
    fail(`${where}: 色の組(${palette.length} 色)に無い番号: ${JSON.stringify(value)}`);
  }
  return color;
}

/**
 * 色の組の表を読む — 1 行 = `{ "char": 番号の文字, "color": "#RRGGBB", "role": 役の一言 }`。番号の文字は 0 から順に
 * 並べる(表の位置が番号 — 抜けや入れ替わりは断る)。2 色以上 32 色まで。
 */
function paletteOf(value: unknown, where: string): Palette {
  if (!Array.isArray(value) || value.length < 2 || value.length > COLOR_CHARS.length) {
    fail(`${where}: 2〜${COLOR_CHARS.length} 行の配列ではない`);
  }
  return value.map((row: unknown, i) => {
    const r = record(row, `${where}[${i}]`);
    const ch = text(r, 'char', `${where}[${i}]`);
    if (ch !== COLOR_CHARS[i]) {
      fail(`${where}[${i}].char: 番号の順の文字 ${COLOR_CHARS[i]} ではない: ${JSON.stringify(ch)}`);
    }
    const color = text(r, 'color', `${where}[${i}]`);
    if (!/^#[0-9A-F]{6}$/.test(color)) {
      fail(`${where}[${i}].color: 大文字の #RRGGBB ではない: ${JSON.stringify(color)}`);
    }
    text(r, 'role', `${where}[${i}]`);
    return color;
  });
}

/** 灯の色(off・error・warning・info)を読む。 */
function lampsOf(value: unknown, palette: Palette, where: string): Lamps {
  const r = record(value, where);
  return {
    off: paletteColorOf(r.off, palette, `${where}.off`),
    error: paletteColorOf(r.error, palette, `${where}.error`),
    warning: paletteColorOf(r.warning, palette, `${where}.warning`),
    info: paletteColorOf(r.info, palette, `${where}.info`)
  };
}

/** 輪郭の色(1 色以上)を読む。 */
function outlineOf(value: unknown, palette: Palette, where: string): readonly ColorIndex[] {
  if (!Array.isArray(value) || value.length === 0) {
    fail(`${where}: 1 色以上の配列ではない`);
  }
  return value.map((c: unknown, i) => paletteColorOf(c, palette, `${where}[${i}]`));
}

/** 文字が色の差し替えの名前(A / B / L)であるかを見る。 */
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
const FRAME_CHARS = `.:${COLOR_CHARS}ABL`;
/** 絵の格子の文字 — 透明(枠を見せる)・色・差し替え。 */
const PICTURE_CHARS = `.${COLOR_CHARS}ABL`;
/** 拡張の一覧の icon の格子の文字 — 透明・色・灯(差し替えの A / B は使わない)。 */
const ICON_CHARS = `.${COLOR_CHARS}L`;
/** 旗の模様の文字。 */
const PATTERN_CHARS = '.AB';
/** 単色の格子の文字(`#` = 点)。 */
const MONO_CHARS = '.#';

/** 2 つの大きさ(px32・px16)の格子を検める。 */
function gridsOf(
  value: JsonObject,
  allowed: string,
  where: string
): Readonly<Record<GlyphSize, readonly string[]>> {
  return {
    32: gridOf(value.px32, 32, allowed, `${where}.px32`),
    16: gridOf(value.px16, 16, allowed, `${where}.px16`)
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

/** icon 1 つを検める — 絵は px32・px16 の格子か、`picture`(別の icon の名前)のどちらか一方。 */
function glyphOf(value: unknown, where: string): RawGlyph {
  const r = record(value, where);
  const name = text(r, 'name', where);
  const at = `${where}(${name})`;
  const hasGrids = r.px32 !== undefined || r.px16 !== undefined;
  if (hasGrids && r.picture !== undefined) {
    fail(`${at}: 絵の格子(px32・px16)と picture を両方は書かない`);
  }
  return {
    name,
    family: text(r, 'family', at),
    summary: text(r, 'summary', at),
    tint: tintOf(r.tint, `${at}.tint`),
    drawing: r.picture === undefined ? { tag: 'grids', grids: gridsOf(r, PICTURE_CHARS, at) } : { tag: 'picture', name: text(r, 'picture', at) },
    mono: r.mono === undefined ? null : gridOf(r.mono, LARGE, MONO_CHARS, `${at}.mono`)
  };
}

/** 絵の参照を解く — 参照先は自分で絵を描いた icon だけ(参照の参照は断る・知らない名前も断る)。 */
function resolvePictures(raw: readonly RawGlyph[]): GlyphSource[] {
  const drawn = new Map<string, Readonly<Record<GlyphSize, readonly string[]>>>();
  for (const glyph of raw) {
    if (glyph.drawing.tag === 'grids') {
      drawn.set(glyph.name, glyph.drawing.grids);
    }
  }
  return raw.map(({ drawing, ...rest }): GlyphSource => {
    switch (drawing.tag) {
      case 'grids':
        return { ...rest, grids: drawing.grids, picture: null };
      case 'picture': {
        const grids = drawn.get(drawing.name);
        if (grids === undefined) {
          fail(`glyphs(${rest.name}).picture: 自分で絵を描いた icon ではない: ${drawing.name}`);
        }
        return { ...rest, grids, picture: drawing.name };
      }
      default: {
        const unreachable: never = drawing;
        throw new Error(`網羅されていない絵の形: ${JSON.stringify(unreachable)}`);
      }
    }
  });
}

/** service の旗の決まり(色・模様・検算する名前)を検める。 */
function serviceFlagsOf(value: unknown, palette: Palette, where: string): ServiceFlags {
  const r = record(value, where);
  const colors = list(r, 'colors', where).map((c, i) => paletteColorOf(c, palette, `${where}.colors[${i}]`));
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
    if (!colors.some((c) => c !== color && Math.abs(lightness(palette, c) - lightness(palette, color)) >= minContrast)) {
      fail(`${where}.colors: 色 ${COLOR_CHARS[color]} と明るさの差が ${minContrast} 以上の色が無い`);
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
  if (EXTENSION_ICON_PX % scale !== 0) {
    fail(`${where}.scale: ${EXTENSION_ICON_PX} を割り切る整数ではない: ${scale}`);
  }
  return {
    glyph: text(r, 'glyph', where),
    icon: gridOf(r.icon, EXTENSION_ICON_PX / scale, ICON_CHARS, `${where}.icon`),
    scale,
    activityBar: gridOf(r.activityBar, ACTIVITY_BAR_SIZE, MONO_CHARS, `${where}.activityBar`)
  };
}

/** 絵が枠の中の地(`:`)の外に点を置いていないかを確かめる(家族の文法 — 枠は家族、中は語)。 */
function checkInside(glyph: GlyphSource, frame: Frame): void {
  for (const size of SIZES) {
    const picture = glyph.grids[size];
    const shape = frame.grids[size];
    picture.forEach((row, y) => {
      [...row].forEach((ch, x) => {
        if (ch !== '.' && shape[y][x] !== ':') {
          const borrowed = glyph.picture === null ? '' : `(絵は ${glyph.picture} から)`;
          fail(`glyphs(${glyph.name}).px${size}[${y}][${x}]: 枠の中の地の外に点がある${borrowed}(家族の枠は変えない)`);
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

/** 格子と差し替えの色の番号が全部、色の組の中にあるかを確かめる(表に無い番号の点は描けない)。 */
function checkColors(set: GlyphSet): void {
  const known = new Set(COLOR_CHARS.slice(0, set.palette.length));
  const checkGrid = (grid: readonly string[], where: string): void => {
    grid.forEach((row, y) => {
      for (const ch of row) {
        if (COLOR_CHARS.includes(ch) && !known.has(ch)) {
          fail(`${where}[${y}]: 色の組(${set.palette.length} 色)に無い番号 ${JSON.stringify(ch)}`);
        }
      }
    });
  };
  const checkTint = (tint: Tint, where: string): void => {
    for (const [slot, color] of Object.entries(tint)) {
      if (color !== undefined && color >= set.palette.length) {
        fail(`${where}.${slot}: 色の組(${set.palette.length} 色)に無い番号`);
      }
    }
  };
  for (const family of set.families) {
    if (family.frame !== null) {
      checkTint(family.frame.tint, `families(${family.name}).frame.tint`);
      if (typeof family.frame.fill === 'number' && family.frame.fill >= set.palette.length) {
        fail(`families(${family.name}).frame.fill: 色の組に無い番号`);
      }
      for (const size of SIZES) {
        checkGrid(family.frame.grids[size], `families(${family.name}).frame.px${size}`);
      }
    }
  }
  for (const glyph of set.glyphs) {
    checkTint(glyph.tint, `glyphs(${glyph.name}).tint`);
    for (const size of SIZES) {
      checkGrid(glyph.grids[size], `glyphs(${glyph.name}).px${size}`);
    }
  }
  checkGrid(set.extension.icon, 'extension.icon');
}

/** 全体の整合 — 色の組の外の番号・名前の重なり・知らない家族・枠の外の点・差し替えの不足。 */
function checkSet(set: GlyphSet): void {
  checkColors(set);
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
    // 差し替えの名前を使うなら、語か家族の枠がその色を決めている(灯 L は元の定義の lamp が決める)
    for (const size of SIZES) {
      for (const slot of TINT_SLOTS.filter((t) => t !== 'L')) {
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
    const palette = paletteOf(root.palette, 'palette');
    const set: GlyphSet = {
      version: GLYPHS_VERSION,
      families: list(root, 'families', 'glyphs.json').map((f, i) => familyOf(f, `families[${i}]`)),
      glyphs: resolvePictures(list(root, 'glyphs', 'glyphs.json').map((g, i) => glyphOf(g, `glyphs[${i}]`))),
      serviceFlags: serviceFlagsOf(root.serviceFlags, palette, 'serviceFlags'),
      extension: extensionOf(root.extension, 'extension'),
      palette,
      lamps: lampsOf(root.lamps, palette, 'lamps'),
      outline: outlineOf(root.outline, palette, 'outline')
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
  /** 字体の単色の点(大きい sprite と同じ 32×32) */
  readonly mono: ReadonlyArray<ReadonlyArray<boolean>>;
}

/** 単色の点 — 明示の格子か、大きい sprite の輪郭の色(黒と紺)の点。 */
function monoOf(source: GlyphSource, large: Pixels, outline: readonly ColorIndex[]): boolean[][] {
  if (source.mono !== null) {
    return source.mono.map((row) => [...row].map((ch) => ch === '#'));
  }
  return large.map((row) => row.map((c) => c !== null && outline.includes(c)));
}

/**
 * icon 1 つを差し替えの色を上書きして重ね合わせる(規則の家族の縁を違反の重さの色にするため)。上書きは語と家族の
 * 既定より先に効く。知らない名前は undefined。
 */
export function composeTinted(set: GlyphSet, name: string, override: Tint): Readonly<Record<GlyphSize, Pixels>> | undefined {
  const source = set.glyphs.find((g) => g.name === name);
  if (source === undefined) {
    return undefined;
  }
  const frame = set.families.find((f) => f.name === source.family)?.frame ?? null;
  const tint = { L: set.lamps.off, ...source.tint, ...override };
  return { 32: composePixels(frame, source.grids[32], tint, 32), 16: composePixels(frame, source.grids[16], tint, 16) };
}

/** 元の定義の icon を全部重ね合わせる(service の旗は flags.ts が足す)。 */
export function composeGlyphs(set: GlyphSet): Glyph[] {
  const families = new Map(set.families.map((f) => [f.name, f]));
  return set.glyphs.map((source) => {
    const frame = families.get(source.family)?.frame ?? null;
    const tint = { L: set.lamps.off, ...source.tint };
    const large = composePixels(frame, source.grids[32], tint, 32);
    return {
      name: source.name,
      family: source.family,
      summary: source.summary,
      pixels: { 32: large, 16: composePixels(frame, source.grids[16], tint, 16) },
      mono: monoOf(source, large, set.outline)
    };
  });
}

/**
 * 大きい sprite(32×32)の右下に小さい sprite(16×16)の印を重ねる(gutter と木で「種類の icon + 状態の印」を 1 つの画に
 * するため)。印の点の周りの透明な点には縁の色 edge(元の定義の outline の先頭)を 1 点足して、土台の絵と混ざらない
 * ようにする。土台が無ければ印だけ。
 */
export function overlayBadge(base: Pixels | null, badge: Pixels | null, edge: ColorIndex): Pixels {
  const grid: Array<Array<ColorIndex | null>> = Array.from({ length: LARGE }, (_, y) =>
    Array.from({ length: LARGE }, (_, x) => (base === null ? null : base[y][x]))
  );
  if (badge === null) {
    return grid;
  }
  const at = (x: number, y: number): ColorIndex | null => (x >= 0 && x < SMALL && y >= 0 && y < SMALL ? badge[y][x] : null);
  for (let y = -1; y < SMALL; y++) {
    for (let x = -1; x < SMALL; x++) {
      const gx = LARGE - SMALL + x;
      const gy = LARGE - SMALL + y;
      const own = at(x, y);
      if (own !== null) {
        grid[gy][gx] = own;
        continue;
      }
      const touches = [[1, 0], [-1, 0], [0, 1], [0, -1]].some(([dx, dy]) => at(x + dx, y + dy) !== null);
      if (touches && base !== null) {
        grid[gy][gx] = edge;
      }
    }
  }
  return grid;
}

/** 拡張の一覧の icon(128×128 の元の格子)の点 — 生成の script が icon.png・icon.svg を作り、見本が並べるため(灯は消えた色)。 */
export function extensionIconPixels(set: GlyphSet): Pixels {
  return set.extension.icon.map((row) => [...row].map((ch) => resolveChar(ch, { L: set.lamps.off }, {})));
}
