// 拡張の実行時の icon の置き場 — 同梱の元の定義(resources/pixel/glyphs.json)を起動時に 1 度読み、木・gutter・hover が
// 使う画(Uri と data URI)を渡す。生成済みの SVG がある icon はその file を、重ねた画(種類 + 印)と知らない service の旗は
// 同じ純粋な関数で作った data URI を返す。読めなければ理由を 1 度出し、呼ぶ側は codicon の表示に戻る。

import * as fs from 'fs';
import * as path from 'path';
import * as vscode from 'vscode';
import { allGlyphs, PIXEL_DIR, svgPath } from './build';
import { flagGlyph } from './flags';
import { parseGlyphSet, type Glyph, type GlyphSet } from './glyphs';
import { colorSvg, dataUri, png } from './render';
import { litKey, litPixels, type LitIcon } from './vocabulary';

/** 木・gutter・hover が icon を引く口(fake に差し替えられるよう interface にする)。 */
export interface IconSource {
  /** 16×16 の icon(知らない名前は undefined — 呼ぶ側が codicon に戻る) */
  icon(name: string): vscode.Uri | undefined;
  /** 灯を違反の重さの色に灯した 16×16 の sprite(層の gutter は右下に service の旗)。知らない名前は undefined */
  lit(icon: LitIcon): vscode.Uri | undefined;
  /** 文字の中に入れる 8×8 の画を css の大きさ px で(文字の置き換え。知らない名前は undefined) */
  inline(name: string, px: number): vscode.Uri | undefined;
  /** hover の Markdown に置く `<img>`(css の大きさ px。知らない名前は空文字) */
  hoverImage(name: string, cssPx: number): string;
  /** icon の一言(知らない名前は undefined) */
  summary(name: string): string | undefined;
}

/** 同梱の元の定義から作る icon の置き場。 */
export class PixelIcons implements IconSource {
  private readonly glyphs = new Map<string, Glyph>();
  private readonly composites = new Map<string, vscode.Uri | undefined>();
  private readonly images = new Map<string, string>();

  private constructor(
    private readonly extensionPath: string,
    private readonly set: GlyphSet | undefined
  ) {
    if (set !== undefined) {
      for (const glyph of allGlyphs(set)) {
        this.glyphs.set(glyph.name, glyph);
      }
    }
  }

  /** 同梱の glyphs.json を読む(読めなければ理由を出し、icon の無い置き場を返す)。 */
  static load(extensionPath: string, log: { appendLine(line: string): void }): PixelIcons {
    const file = path.join(extensionPath, PIXEL_DIR, 'glyphs.json');
    let source: string;
    try {
      source = fs.readFileSync(file, 'utf8');
    } catch (error) {
      log.appendLine(`[pixel] ${file} を読めない — icon は codicon のまま: ${String(error)}`);
      return new PixelIcons(extensionPath, undefined);
    }
    const parsed = parseGlyphSet(source);
    if (parsed.tag !== 'ok') {
      log.appendLine(`[pixel] ${file} が契約に合わない — icon は codicon のまま: ${parsed.reason}`);
      return new PixelIcons(extensionPath, undefined);
    }
    return new PixelIcons(extensionPath, parsed.set);
  }

  /** 名前の icon の重ね合わせ済みの格子(service の旗は知らない名前でも作る)。 */
  private glyph(name: string): Glyph | undefined {
    const known = this.glyphs.get(name);
    if (known !== undefined || this.set === undefined || !name.startsWith('service-')) {
      return known;
    }
    const made = flagGlyph(this.set, name.slice('service-'.length));
    this.glyphs.set(name, made);
    return made;
  }

  /** 16×16 の icon — 生成済みの SVG の file、無ければ(知らない service の旗)data URI。 */
  icon(name: string): vscode.Uri | undefined {
    const glyph = this.glyph(name);
    if (glyph === undefined) {
      return undefined;
    }
    const file = path.join(this.extensionPath, svgPath(name, 16));
    return fs.existsSync(file) ? vscode.Uri.file(file) : vscode.Uri.parse(dataUri('image/svg+xml', colorSvg(glyph.pixels[16])));
  }

  /** 灯を灯した sprite(同じ組は使い回す)。 */
  lit(icon: LitIcon): vscode.Uri | undefined {
    const key = `lit:${litKey(icon)}`;
    if (this.composites.has(key)) {
      return this.composites.get(key);
    }
    const pixels = this.set === undefined ? undefined : litPixels(this.set, icon);
    const uri = pixels === undefined ? undefined : vscode.Uri.parse(dataUri('image/svg+xml', colorSvg(pixels)));
    this.composites.set(key, uri);
    return uri;
  }

  /** 文字の中に入れる 8×8 の画(同じ名前と大きさは使い回す)。 */
  inline(name: string, px: number): vscode.Uri | undefined {
    const key = `inline:${name}@${px}`;
    if (this.composites.has(key)) {
      return this.composites.get(key);
    }
    const glyph = this.glyph(name);
    const uri = glyph === undefined ? undefined : vscode.Uri.parse(dataUri('image/svg+xml', colorSvg(glyph.pixels[8], px)));
    this.composites.set(key, uri);
    return uri;
  }

  /** hover の `<img>` — 4 倍の PNG を css の大きさで出す(整数倍なので縮めてもぼけない)。 */
  hoverImage(name: string, cssPx: number): string {
    const key = `${name}@${cssPx}`;
    const cached = this.images.get(key);
    if (cached !== undefined) {
      return cached;
    }
    const glyph = this.glyph(name);
    const html =
      glyph === undefined
        ? ''
        : `<img src="${dataUri('image/png', png(glyph.pixels[16], 4))}" width="${cssPx}" height="${cssPx}" alt="${name}">`;
    this.images.set(key, html);
    return html;
  }

  /** icon の一言。 */
  summary(name: string): string | undefined {
    return this.glyph(name)?.summary;
  }
}
