// defk の見出し(型の流れ・tags と状態)と束縛の型の札を、1 行の高さの SVG に描く純粋な関数(VS Code に触らない)。
// editor の文字の大きさと行の高さは変えない — 型の札と名は editor の文字と同じ大きさ、tags の札だけ小さい文字
// (operator 2026-09-28 "it's not i wanat the font size changed" / "tags can have small fonts though")。
// tags と型は形・書体・塗りの 3 つとも違える: tags = 丸い淡い札・普通の書体・荷札の印 / 型 = 角の小さい枠の札・等幅の太字・型ごとの色。

import type { LintTypeRef } from '../lint/contract';
import type { Palette, Pixels } from '../pixel/glyphs';
import { escapeXml, mergeRects } from '../pixel/render';
import { effectGlyph } from '../pixel/vocabulary';
import { shownEffects, typeText, type BindingChip, type EffectAgreement, type EffectState } from './model';
import type { LintSignature } from '../lint/contract';

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

/** 型の札 1 つ(角の小さい枠・等幅の太字・型ごとの色)。param は引数の名(淡く添える)。 */
function typeChip(row: Row, type: LintTypeRef | null, options: { param?: string; maybe?: boolean; raises?: boolean } = {}): void {
  const text = typeText(type);
  const colors = type === null || type.kind === 'unknown' ? { fill: 'transparent', text: '#8b949e', border: '#5a6270' } : typeColors(text.split(/[ |[]/)[0]);
  const from = row.x;
  row.space(5);
  row.text(text, { color: colors.text, bold: true });
  if (options.param !== undefined) {
    row.space(4);
    row.text(options.param, { color: colors.text, size: Math.round(row.metrics.fontSize * 0.78) });
  }
  if (options.raises === true) {
    row.space(3);
    row.sprite('lint-error', spriteSize(row.metrics));
  }
  row.space(5);
  row.box(from, row.x, {
    fill: options.maybe === true ? 'transparent' : colors.fill,
    stroke: colors.border,
    radius: 2,
    dashed: type === null || options.maybe === true,
    height: chipHeight(row.metrics),
    opacity: options.maybe === true ? 0.75 : undefined
  });
}

/** effect の装置の印 — 絵は effect の名 → 装置の絵の表(effectGlyph)から引く(宣言と推論の状態で見た目を変える)。 */
function effectDevice(row: Row, name: string, state: EffectState): void {
  const from = row.x;
  row.space(2);
  row.sprite(effectGlyph(name), spriteSize(row.metrics), state === 'unused' ? 0.35 : 1);
  row.space(2);
  row.text(name, { color: state === 'unused' ? '#6b7785' : state === 'undeclared' ? '#ffcc66' : '#cfe3ff', size: Math.round(row.metrics.fontSize * 0.85) });
  row.space(3);
  if (state === 'undeclared' || state === 'unused') {
    row.box(from, row.x, {
      fill: state === 'undeclared' ? 'rgba(255,163,0,0.12)' : 'transparent',
      stroke: state === 'undeclared' ? '#ffa300' : '#6b7785',
      radius: 3,
      dashed: true,
      height: chipHeight(row.metrics) - 2
    });
  }
}

/** 型の流れ `(X, Y) → Program[effect | B]`(deff は `(X, Y) → B`)。 */
export function flowSvg(signature: LintSignature, metrics: Metrics, sprites: SpritePixels): Drawn {
  const row = new Row(metrics, sprites);
  const punct = { color: '#8a96a3' };
  const argsFrom = row.x;
  row.space(3);
  row.text('(', punct);
  signature.params.forEach((param, i) => {
    if (i > 0) {
      row.text(',', { ...punct, gap: 4 });
    }
    row.space(2);
    typeChip(row, param.type, { param: param.name });
    row.space(2);
  });
  row.text(')', punct);
  row.space(3);
  row.box(argsFrom, row.x, { fill: 'transparent', stroke: '#3b4350', radius: 4, height: chipHeight(metrics) + 2 });
  row.space(6);
  row.text('→', { color: '#8fb3d9', bold: true, gap: 6 });
  const answer = (): void => {
    if (signature.absent) {
      const from = row.x;
      row.space(3);
      row.text('Maybe[', { color: '#c5cdd6' });
      typeChip(row, signature.answer, { maybe: true });
      row.text(']', { color: '#c5cdd6' });
      row.space(3);
      row.box(from, row.x, { fill: 'transparent', stroke: '#9aa7b5', radius: 3, dashed: true, height: chipHeight(metrics) + 1 });
    } else {
      typeChip(row, signature.answer);
    }
  };
  if (signature.kind === 'deff') {
    answer();
    return row.done();
  }
  const programFrom = row.x;
  row.space(3);
  row.sprite('program', spriteSize(metrics));
  row.space(3);
  row.text('Program', { color: '#9fc3ff', bold: true });
  row.text('[', { color: '#9fc3ff', gap: 2 });
  const effects = shownEffects(signature);
  if (effects.length === 0 && signature.raises.length === 0) {
    const from = row.x;
    row.space(4);
    row.text('effect なし', { color: '#6f7d8c', size: Math.round(metrics.fontSize * 0.8), mono: false });
    row.space(4);
    row.box(from, row.x, { fill: 'transparent', stroke: '#44505e', radius: 3, dashed: true, height: chipHeight(metrics) - 4 });
  }
  effects.forEach((e) => effectDevice(row, e.effect.name, e.state));
  for (const raise of signature.raises) {
    row.space(2);
    row.sprite('raise', spriteSize(metrics));
    row.space(2);
    row.text(`Raise ${typeText(raise)}`, { color: '#ff9a9a', size: Math.round(metrics.fontSize * 0.85), gap: 3 });
  }
  row.text('|', { color: '#4d6a91', gap: 4 });
  answer();
  row.text(']', { color: '#9fc3ff' });
  row.space(3);
  row.box(programFrom, row.x, { fill: '#1f2b3d', stroke: '#3a5a8a', radius: 4, height: chipHeight(metrics) + 2 });
  return row.done();
}

/** tags の札(丸い淡い札・小さな普通の書体・荷札の印)と状態の札(四角い小さな札・印つき)。 */
export function tagsSvg(
  tags: ReadonlyMap<string, string>,
  agreement: EffectAgreement,
  violations: number,
  metrics: Metrics,
  sprites: SpritePixels
): Drawn {
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
  row.space(10);
  const stat = (text: string, tone: 'ok' | 'warn' | 'plain', icon: 'check' | 'lamp' | 'none'): void => {
    const from = row.x;
    row.space(4);
    if (icon === 'check') {
      row.text('✓', { color: '#89d185', bold: true, size: small, mono: false, gap: 3 });
    } else if (icon === 'lamp') {
      row.sprite('lint-warning', Math.min(pillHeight - 2, 8));
      row.space(3);
    }
    row.text(text, { color: tone === 'ok' ? '#89d185' : tone === 'warn' ? '#ffcc66' : '#9aa4ae', size: small, mono: false });
    row.space(4);
    row.box(from, row.x, {
      fill: tone === 'warn' ? '#2a220e' : '#1b1b1b',
      stroke: tone === 'ok' ? '#2f4a2c' : tone === 'warn' ? '#5a4617' : '#3a3f45',
      radius: 2,
      height: pillHeight
    });
    row.space(4);
  };
  switch (agreement.tag) {
    case 'match':
      stat('宣言 = 推論', 'ok', 'check');
      break;
    case 'mismatch':
      stat(`effect の食い違い ${agreement.count}`, 'warn', 'lamp');
      break;
    case 'undeclared':
      stat(':effects の宣言なし', 'plain', 'none');
      break;
    default: {
      const unreachable: never = agreement;
      throw new Error(`網羅されていない状態: ${JSON.stringify(unreachable)}`);
    }
  }
  if (violations === 0) {
    stat('違反なし', 'ok', 'check');
  } else {
    stat(`違反 ${violations}`, 'warn', 'lamp');
  }
  return row.done();
}

/** 束縛の型の札(`var` の語を前に添えられる・分からない型は `?`)。 */
export function bindingChipSvg(chip: BindingChip, prefix: string | undefined, metrics: Metrics, sprites: SpritePixels): Drawn {
  const row = new Row(metrics, sprites);
  if (prefix !== undefined) {
    row.text(prefix, { color: '#c586c0', gap: 6 });
  }
  switch (chip.tag) {
    case 'unknown':
      typeChip(row, null);
      break;
    case 'type':
      typeChip(row, chip.type, { maybe: chip.absent, raises: chip.raises.length > 0 });
      break;
    default: {
      const unreachable: never = chip;
      throw new Error(`網羅されていない札: ${JSON.stringify(unreachable)}`);
    }
  }
  row.space(1);
  return row.done();
}
