// 点の格子を画にする — SVG(`<rect>` と crispEdges で点をぼかさない)と PNG(整数倍の拡大・補間なし)。
// 同じ格子から、拡張の木・gutter・hover・見本の HTML・生成の script が同じ画を得る。VS Code には触らない。

import * as zlib from 'zlib';
import type { ColorIndex, Palette, Pixels } from './glyphs';

/** 同じ色の点をまとめた長方形(点の単位)。 */
export interface PixelRect {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

/**
 * 格子の中で `hit` が真の点を長方形にまとめる — 行ごとの連なりを取り、真下の行に同じ幅の連なりがあれば縦に伸ばす。
 * SVG の `<rect>` と字体の輪郭の数を減らすため(点ごとに rect を置くと file も字体も重い)。
 */
export function mergeRects(width: number, height: number, hit: (x: number, y: number) => boolean): PixelRect[] {
  const open = new Map<string, { x: number; y: number; width: number; height: number }>();
  const done: PixelRect[] = [];
  for (let y = 0; y < height; y++) {
    const runs: Array<{ x: number; width: number }> = [];
    for (let x = 0; x < width; ) {
      if (!hit(x, y)) {
        x++;
        continue;
      }
      const start = x;
      while (x < width && hit(x, y)) {
        x++;
      }
      runs.push({ x: start, width: x - start });
    }
    const next = new Map<string, { x: number; y: number; width: number; height: number }>();
    for (const run of runs) {
      const key = `${run.x}:${run.width}`;
      const growing = open.get(key);
      if (growing !== undefined) {
        growing.height += 1;
        next.set(key, growing);
        open.delete(key);
      } else {
        next.set(key, { x: run.x, y, width: run.width, height: 1 });
      }
    }
    done.push(...open.values());
    open.clear();
    for (const [key, rect] of next) {
      open.set(key, rect);
    }
  }
  done.push(...open.values());
  return done.sort((a, b) => a.y - b.y || a.x - b.x);
}

/** 格子に出てくる色(番号の順)。 */
function colorsIn(pixels: Pixels): ColorIndex[] {
  const seen = new Set<ColorIndex>();
  for (const row of pixels) {
    for (const c of row) {
      if (c !== null) {
        seen.add(c);
      }
    }
  }
  return [...seen].sort((a, b) => a - b);
}

/** 色つきの SVG — 色の番号を色の組で引き、色ごとに長方形をまとめ、`shape-rendering="crispEdges"` で点の縁をぼかさない。 */
export function colorSvg(pixels: Pixels, palette: Palette, cssSize?: number, title?: string): string {
  const size = pixels.length;
  const shown = cssSize ?? size;
  const parts: string[] = [];
  for (const color of colorsIn(pixels)) {
    const rects = mergeRects(size, size, (x, y) => pixels[y][x] === color);
    const body = rects.map((r) => `<rect x="${r.x}" y="${r.y}" width="${r.width}" height="${r.height}"/>`).join('');
    parts.push(`<g fill="${palette[color]}">${body}</g>`);
  }
  const label = title === undefined ? '' : `<title>${escapeXml(title)}</title>`;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${shown}" height="${shown}" viewBox="0 0 ${size} ${size}" shape-rendering="crispEdges">${label}${parts.join('')}</svg>\n`;
}

/** 単色の SVG(`currentColor`)— activity bar の輪郭と字体の見本。点の無い所は透明。 */
export function monoSvg(mono: ReadonlyArray<ReadonlyArray<boolean>>, cssSize?: number): string {
  const size = mono.length;
  const shown = cssSize ?? size;
  const rects = mergeRects(size, size, (x, y) => mono[y][x]);
  const body = rects.map((r) => `<rect x="${r.x}" y="${r.y}" width="${r.width}" height="${r.height}"/>`).join('');
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${shown}" height="${shown}" viewBox="0 0 ${size} ${size}" shape-rendering="crispEdges"><g fill="currentColor">${body}</g></svg>\n`;
}

/** XML の本文に置けるように特殊な文字を逃がす。 */
export function escapeXml(value: string): string {
  return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/** PNG の chunk の CRC32 の表。 */
const CRC_TABLE: readonly number[] = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) {
    c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  }
  return c >>> 0;
});

/** PNG の chunk の CRC32(型の名前 + 中身)。 */
function crc32(bytes: Uint8Array): number {
  let c = 0xffffffff;
  for (const b of bytes) {
    c = CRC_TABLE[(c ^ b) & 0xff] ^ (c >>> 8);
  }
  return (c ^ 0xffffffff) >>> 0;
}

/** PNG の chunk 1 つ(長さ・型・中身・CRC)。 */
function chunk(type: string, data: Buffer): Buffer {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const typed = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(typed));
  return Buffer.concat([length, typed, crc]);
}

/** 色の番号を色の組で引き、16 進の色(`#RRGGBB`)を 3 つの byte にする。 */
function rgb(palette: Palette, color: ColorIndex): [number, number, number] {
  const hex = palette[color];
  return [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)];
}

/**
 * PNG — 1 点を scale × scale の正方形にそのまま拡大する(補間しない・透明は alpha 0)。
 * 画素の値と圧縮の設定が決まっているので、同じ格子からは同じ byte 列になる(生成物の食い違いの検に使う)。
 */
export function png(pixels: Pixels, palette: Palette, scale: number): Buffer {
  const size = pixels.length;
  const side = size * scale;
  const raw = Buffer.alloc(side * (side * 4 + 1));
  for (let y = 0; y < side; y++) {
    const rowStart = y * (side * 4 + 1);
    raw[rowStart] = 0;
    for (let x = 0; x < side; x++) {
      const c = pixels[Math.floor(y / scale)][Math.floor(x / scale)];
      const at = rowStart + 1 + x * 4;
      if (c !== null) {
        const [r, g, b] = rgb(palette, c);
        raw[at] = r;
        raw[at + 1] = g;
        raw[at + 2] = b;
        raw[at + 3] = 255;
      }
    }
  }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(side, 0);
  header.writeUInt32BE(side, 4);
  header[8] = 8;
  header[9] = 6;
  const signature = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  return Buffer.concat([
    signature,
    chunk('IHDR', header),
    chunk('IDAT', zlib.deflateSync(raw, { level: 9 })),
    chunk('IEND', Buffer.alloc(0))
  ]);
}

/** 画の data URI(拡張の gutter・hover・見本の HTML が file を置かずに使う)。 */
export function dataUri(mime: 'image/svg+xml' | 'image/png', body: string | Buffer): string {
  const bytes = typeof body === 'string' ? Buffer.from(body, 'utf8') : body;
  return `data:${mime};base64,${bytes.toString('base64')}`;
}
