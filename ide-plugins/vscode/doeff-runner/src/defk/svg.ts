// defk の見出しの絵の部品(tags の札・effect の札)を、1 行の高さの SVG に描く純粋な関数(VS Code に触らない)と、型の色。
// 型の行は絵にしない — editor の文字で描く(operator 2026-09-28 "i want it to show more like (dict,str)->JsonAnswer")。
// editor の文字の大きさと行の高さは変えない。tags の札だけ小さい文字("tags can have small fonts though")。
// effect の札は 1 つずつ別の画像にする(押した札の effect の定義へ飛ぶため)。

import type { Palette, Pixels } from '../pixel/glyphs';
import { escapeXml, mergeRects } from '../pixel/render';
import { effectGlyph } from '../pixel/vocabulary';

/** 描く大きさ — editor の文字の大きさ・行の高さ・書体。 */
export interface Metrics {
  readonly fontSize: number;
  readonly lineHeight: number;
  readonly fontFamily: string;
}

/** sprite の格子と色の組を名前で引く口(知らない名前は undefined — 印を描かずに文字だけにする)。 */
export type SpritePixels = (name: string) => { readonly pixels: Pixels; readonly palette: Palette } | undefined;

/** 描いた SVG と、その css の大きさ。 */
export interface Drawn {
  readonly svg: string;
  readonly width: number;
  readonly height: number;
}

/** 組み込みの型(色を 1 つに揃える)。 */
const BUILTIN_TYPES = new Set(['int', 'str', 'bool', 'float', 'None', 'list', 'dict', 'tuple', 'set', 'frozenset', 'bytes', 'object', 'Any']);

/** 文字の幅の見積もり(等幅の半角 0.6・それ以外 1.0 文字分)— 最後は textLength で合わせる。 */
export function textWidth(value: string, fontSize: number): number {
  let width = 0;
  for (const ch of value) {
    width += ch.charCodeAt(0) < 0x2000 ? 0.6 : 1.0;
  }
  return Math.ceil(width * fontSize);
}

/** 文字列の決まった色相(同じ値はどこでも同じ色)。 */
export function hueOf(value: string): number {
  let h = 0;
  for (const ch of value) {
    h = (h * 131 + ch.charCodeAt(0)) >>> 0;
  }
  const hues = [205, 160, 30, 280, 340, 95, 50, 250, 185, 315, 15, 130];
  return hues[h % hues.length];
}

/** 型の札の色(組み込みは緑に揃え、それ以外は名ごと)。 */
export function typeColors(name: string): { readonly fill: string; readonly text: string; readonly border: string } {
  if (BUILTIN_TYPES.has(name)) {
    return { fill: '#26332a', text: '#b5cea8', border: '#4b6b4f' };
  }
  const h = hueOf(`type:${name}`);
  return { fill: `hsl(${h} 32% 22%)`, text: `hsl(${h} 70% 78%)`, border: `hsl(${h} 40% 45%)` };
}

/** 横に並べて描く道具。 */
class Row {
  private readonly parts: string[] = [];
  x = 0;

  constructor(
    readonly metrics: Metrics,
    private readonly sprites: SpritePixels
  ) {}

  get mid(): number {
    return this.metrics.lineHeight / 2;
  }

  /** 文字を置く(幅だけ進む)。 */
  text(value: string, style: { size?: number; color: string; bold?: boolean; mono?: boolean; gap?: number }): void {
    const size = style.size ?? this.metrics.fontSize;
    const width = textWidth(value, size);
    const family = style.mono === false ? '-apple-system, "Hiragino Sans", sans-serif' : this.metrics.fontFamily;
    this.parts.push(
      `<text x="${this.x}" y="${this.mid}" dominant-baseline="central" font-family="${escapeXml(family)}" font-size="${size}"` +
        `${style.bold === true ? ' font-weight="700"' : ''} fill="${style.color}" textLength="${width}" lengthAdjust="spacingAndGlyphs">${escapeXml(value)}</text>`
    );
    this.x += width + (style.gap ?? 0);
  }

  /** 空きを入れる。 */
  space(px: number): void {
    this.x += px;
  }

  /** sprite を置く(知らない名前は何も描かずに進まない)。 */
  sprite(name: string, size: number, opacity = 1): void {
    const found = this.sprites(name);
    if (found === undefined) {
      return;
    }
    const { pixels, palette } = found;
    const n = pixels.length;
    const y = this.mid - size / 2;
    const body: string[] = [];
    const colors = new Set<number>();
    pixels.forEach((row) => row.forEach((c) => c !== null && colors.add(c)));
    for (const color of colors) {
      const rects = mergeRects(n, n, (x, yy) => pixels[yy][x] === color);
      body.push(`<g fill="${palette[color]}">${rects.map((r) => `<rect x="${r.x}" y="${r.y}" width="${r.width}" height="${r.height}"/>`).join('')}</g>`);
    }
    this.parts.push(
      `<svg x="${this.x}" y="${y}" width="${size}" height="${size}" viewBox="0 0 ${n} ${n}" shape-rendering="crispEdges" opacity="${opacity}">${body.join('')}</svg>`
    );
    this.x += size;
  }

  /** 後から背景の枠を差し込む(中身を先に描いて幅を知ってから)。 */
  box(from: number, to: number, style: { fill: string; stroke: string; radius: number; dashed?: boolean; height: number; opacity?: number }): void {
    const y = this.mid - style.height / 2;
    this.parts.splice(
      0,
      0,
      `<rect x="${from + 0.5}" y="${y + 0.5}" width="${to - from - 1}" height="${style.height - 1}" rx="${style.radius}" fill="${style.fill}" stroke="${style.stroke}"` +
        `${style.dashed === true ? ' stroke-dasharray="3 2"' : ''}${style.opacity !== undefined ? ` opacity="${style.opacity}"` : ''}/>`
    );
  }

  /** 描き終える。 */
  done(): Drawn {
    const width = Math.ceil(this.x + 2);
    const height = this.metrics.lineHeight;
    return {
      svg: `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">${this.parts.join('')}</svg>`,
      width,
      height
    };
  }
}

/** 札の高さ(行の高さに収まる)。 */
function chipHeight(metrics: Metrics): number {
  return Math.min(metrics.lineHeight - 2, Math.round(metrics.fontSize * 1.45));
}

/** sprite の大きさ(文字の大きさに近い 8 の倍数・行に収まる)。 */
function spriteSize(metrics: Metrics): number {
  return Math.min(metrics.lineHeight - 4, Math.max(8, 8 * Math.round(metrics.fontSize / 8)));
}

/** effect の札 1 つ(装置の絵と名 — 型の文字と同じ大きさ)。Raise は警報灯と赤い文字。 */
export function effectChipSvg(kind: 'effect' | 'raise', name: string, metrics: Metrics, sprites: SpritePixels): Drawn {
  const row = new Row(metrics, sprites);
  const raise = kind === 'raise';
  const from = row.x;
  row.space(4);
  row.sprite(raise ? 'effect-raise' : effectGlyph(name), spriteSize(metrics));
  row.space(4);
  row.text(raise ? `Raise ${name}` : name, { color: raise ? '#ffb0b0' : '#cfe3ff', bold: true });
  row.space(5);
  row.box(from, row.x, {
    fill: raise ? '#3a1f1f' : '#1f2b3d',
    stroke: raise ? '#a04a4a' : '#4a6a95',
    radius: 3,
    height: chipHeight(metrics)
  });
  row.space(2);
  return row.done();
}

/** tags の札(丸い淡い札・小さな普通の書体・荷札の印)。tags が無ければ undefined。 */
export function tagsSvg(tags: ReadonlyMap<string, string>, metrics: Metrics, sprites: SpritePixels): Drawn | undefined {
  if (tags.size === 0) {
    return undefined;
  }
  const row = new Row(metrics, sprites);
  const small = Math.max(9, Math.round(metrics.fontSize * 0.78));
  const pillHeight = Math.min(metrics.lineHeight - 4, small + 6);
  for (const [key, value] of tags) {
    const h = hueOf(`${key}:${value}`);
    const from = row.x;
    row.space(4);
    row.sprite('tags', Math.min(pillHeight - 2, 8 * Math.max(1, Math.round(small / 8))));
    row.space(3);
    row.text(value, { color: `hsl(${h} 70% 74%)`, size: small, mono: false });
    row.space(7);
    row.box(from, row.x, { fill: `hsla(${h}, 55%, 55%, 0.16)`, stroke: `hsla(${h}, 55%, 55%, 0.35)`, radius: pillHeight / 2, height: pillHeight });
    row.space(4);
  }
  return row.done();
}
