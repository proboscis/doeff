// icon の見本の一覧 — 1 枚の自己完結 HTML(画は data URI・字体は base64 の woff を埋める)。全 icon を 32×32・16×16・
// 拡大で、暗い背景と明るい背景の両方に並べ、名前と一言を添える。service の旗の重複の検算もここに出す。linter の出力を
// 渡せば「違反(linter)」の欄の見本を、いつも文字の置き換えの動く見本を足す(sheetDemos.ts)。

import { activityBarMono, allGlyphs, extensionGlyph, flagReport } from './build';
import { flagGlyphName } from './flags';
import { codepoints, FONT_FAMILY } from './font';
import { DENSITY, EXTENSION_ICON_PX, extensionIconPixels, LARGE, SMALL, type Glyph, type GlyphSet, type GlyphSize, type Palette } from './glyphs';
import { dataUri, escapeXml, monoSvg, png } from './render';
import type { LintReport } from '../lint/contract';
import { compareDemo, DEMO_CSS, panelDemo, replaceDemo } from './sheetDemos';

/** 並べ比べる元の定義(枠つきの版)と、その icon。 */
export interface PreviewCompare {
  readonly set: GlyphSet;
  readonly glyphs: readonly Glyph[];
}

/** 見本に載せる linter の実際の出力(違反の欄の見本の材料)と、どこを読んだ出力か。 */
export interface PreviewLint {
  readonly report: LintReport;
  readonly source: string;
}

/** 画 1 つの `<img>`(点を補間しない表示)。 */
function img(palette: Palette, glyph: Glyph, size: GlyphSize, shown: number): string {
  const src = dataUri('image/png', png(glyph.pixels[size], palette, Math.max(1, Math.round(shown / size))));
  return `<img src="${src}" width="${shown}" height="${shown}" alt="${escapeXml(glyph.name)}">`;
}

/** icon 1 行 — 拡大・実寸(16・8 css px)・2 倍・字体(単色)を暗い背景と明るい背景に。 */
function glyphRow(palette: Palette, glyph: Glyph, codepoint: number | undefined): string {
  const actual = `${img(palette, glyph, LARGE, LARGE / DENSITY)} ${img(palette, glyph, SMALL, SMALL / DENSITY)}`;
  const doubled = `${img(palette, glyph, LARGE, LARGE)} ${img(palette, glyph, SMALL, SMALL)}`;
  const font = codepoint === undefined ? '' : `<span class="icon-font">&#x${codepoint.toString(16)};</span>`;
  return [
    '<tr>',
    `<td class="big">${img(palette, glyph, LARGE, 96)}</td>`,
    `<td class="big">${img(palette, glyph, SMALL, 48)}</td>`,
    `<td class="dark">${actual}<br>${doubled}<br>${font}</td>`,
    `<td class="light">${actual}<br>${doubled}<br>${font}</td>`,
    `<td><code>${escapeXml(glyph.name)}</code><br><code class="id">$(doeff-${escapeXml(glyph.name)})</code></td>`,
    `<td>${escapeXml(glyph.summary)}</td>`,
    '</tr>'
  ].join('');
}

/** 見本の HTML の全体(woff は字体の byte 列 — 字体の欄の表示に埋める)。 */
export function previewHtml(set: GlyphSet, woff: Buffer, lint: PreviewLint | null, compare: PreviewCompare | null): string {
  const glyphs = allGlyphs(set);
  const cps = new Map(codepoints(glyphs).map((c) => [c.name, c.codepoint]));
  const sections = set.families.map((family) => {
    const rows = glyphs
      .filter((g) => g.family === family.name)
      .map((g) => glyphRow(set.palette, g, cps.get(g.name)))
      .join('\n');
    return [
      `<h2>${escapeXml(family.label)} <small>${escapeXml(family.name)}</small></h2>`,
      `<p>${escapeXml(family.summary)}</p>`,
      '<table><tr><th>拡大 32×32</th><th>拡大 16×16</th><th>暗い背景(実寸・2 倍・字体)</th><th>明るい背景</th><th>名前</th><th>一言</th></tr>',
      rows,
      '</table>'
    ].join('\n');
  });
  const report = flagReport(set);
  const byName = new Map(glyphs.map((g) => [g.name, g]));
  const flagRows = report.choices
    .map((c) => {
      const swatch = (color: number): string => `<span class="swatch" style="background:${set.palette[color]}"></span>${set.palette[color]}`;
      const flag = byName.get(flagGlyphName(c.service));
      const pictures = flag === undefined ? '' : `${img(set.palette, flag, LARGE, 48)} ${img(set.palette, flag, LARGE, LARGE / DENSITY)} ${img(set.palette, flag, SMALL, LARGE / DENSITY)}`;
      return `<tr><td class="big">${pictures}</td><td><code>${escapeXml(c.service)}</code></td><td>${escapeXml(c.pattern)}</td><td>${swatch(c.primary)}</td><td>${swatch(c.secondary)}</td></tr>`;
    })
    .join('\n');
  const verdict =
    report.collisions.length === 0
      ? `<p class="ok">重複の検算: ${report.choices.length} の service の旗で、同じ模様と同じ色の組を持つ物は 0 組(重複ゼロ)。</p>`
      : `<p class="ng">重複の検算: ${report.collisions.map((c) => `${c.services.join(' と ')}(${c.key})`).join('・')} が重なる。</p>`;
  const doe = extensionGlyph(set, glyphs);
  const activity = monoSvg(activityBarMono(set), set.extension.activityBar.length / DENSITY);
  const swatches = set.palette.map((c, i) => `<span class="swatch big" style="background:${c}" title="${i.toString(32)} ${c}"></span>`).join('');
  return `<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><title>doeff-runner の pixel art の icon(見本)</title>
<style>
@font-face { font-family: '${FONT_FAMILY}'; src: url(data:font/woff;base64,${woff.toString('base64')}) format('woff'); }
:root { --bg: #f4f1ea; --fg: #222222; --line: #cccccc; --head: #e8e4da; --muted: #777777; --ok: #0a6b2b; --ng: #b00020; }
@media (prefers-color-scheme: dark) { :root { --bg: #1b1b1f; --fg: #e6e6e6; --line: #444444; --head: #2a2a30; --muted: #a0a0a0; --ok: #5fd38a; --ng: #ff6b81; } }
html, body { background: var(--bg); color: var(--fg); }
body { font-family: -apple-system, 'Hiragino Sans', sans-serif; margin: 24px; }
h1 { font-size: 22px; } h2 { margin-top: 32px; font-size: 18px; } h2 small { color: var(--muted); font-weight: normal; }
table { border-collapse: collapse; margin: 8px 0; }
td, th { border: 1px solid var(--line); padding: 6px 10px; vertical-align: middle; font-size: 13px; }
th { background: var(--head); font-weight: normal; }
img { image-rendering: pixelated; vertical-align: middle; }
td.big { background: #2b2b2b; text-align: center; }
td.dark { background: #1e1e1e; color: #d4d4d4; }
td.light { background: #ffffff; color: #333; }
.icon-font { font-family: '${FONT_FAMILY}'; font-size: 16px; line-height: 16px; }
code.id { color: var(--muted); font-size: 11px; }
.swatch { display: inline-block; width: 14px; height: 14px; border: 1px solid #0003; vertical-align: middle; margin-right: 4px; }
.swatch.big { width: 22px; height: 22px; margin: 0; }
.ok { color: var(--ok); font-weight: bold; } .ng { color: var(--ng); font-weight: bold; }
.hero { display: flex; gap: 32px; align-items: center; }
.activity { display: inline-flex; gap: 12px; }
.activity span { display: inline-block; padding: 8px; }
nav a { margin-right: 16px; }
${DEMO_CSS}
</style></head><body>
<h1>doeff-runner の pixel art の icon(見本)</h1>
<p>これは doeff の VS Code 拡張(doeff-runner)に入れる pixel art の見本です。GitHub の issue proboscis/agora-redesign #841(エディタと linter の欄を pixel art の icon で見せる)の成果で、3 つの部分があります。</p>
<ol>
<li>icon の組 — 拡張の icon(手紙 = effect をくわえた雌鹿)と、語ごとの icon。1 つ 1 つが枠の無い物の sprite で、同じ語はどこでも同じ sprite です。</li>
<li>拡張の中で使う — 「違反(linter)」の欄・「タグで閲覧」の欄・行の左端(gutter)・hover・状態バー(違反で表情の変わる雌鹿)。</li>
<li>エディタの文字の置き換え — 決まった語(<code>&lt;-</code>・<code>defk</code> などの頭・<code>:tags</code>・effect の頭・<code>resume</code> / <code>finish</code>・<code>Absent</code> / <code>Raise</code>)の表示だけを icon に。</li>
</ol>
<p><b>絵柄を描き直しました</b>(2026-09-28 朝の感想「枠が格好よくない・居心地のよい SF のゲームのような pixel art に」を受けて)。家族ごとの枠を外し、1 つ 1 つを枠の無い物の sprite(小さなロボット・ドローン・貨物の木箱・データのカートリッジ・端末・宇宙港の建物・信号灯)にしました。違反の重さは、sprite に付いた小さな灯の色(赤 = error・琥珀 = warning・青 = info)で出します。</p>
<nav>${lint === null ? '' : '<a href="#panel">違反の欄の見本</a>'}${compare === null ? '' : '<a href="#compare">枠つきの版との並べ比べ</a>'}<a href="#replace">文字の置き換えの見本</a><a href="#icons">icon の一覧</a></nav>
<p>元の定義は <code>resources/pixel/glyphs.json</code> の 1 か所で、この一覧・SVG・PNG・icon 字体・拡張の icon はすべてそこから生成しています。色は元の定義の色の組(palette)の表だけで、格子は番号で色を参照します。</p>
<div>${swatches}</div>
${lint === null ? '' : panelDemo(set, glyphs, lint.report, lint.source, compare?.set ?? null)}
${compare === null ? '' : compareDemo(set, glyphs, compare.set, compare.glyphs)}
${replaceDemo(set.palette, glyphs)}
<h2 id="icons">拡張の icon</h2>
<div class="hero">
<div><img src="${dataUri('image/png', png(extensionIconPixels(set), set.palette, set.extension.scale))}" width="${EXTENSION_ICON_PX}" height="${EXTENSION_ICON_PX}" alt="doeff"><br>${EXTENSION_ICON_PX}×${EXTENSION_ICON_PX}(${set.extension.icon.length}×${set.extension.icon.length} の格子を ${set.extension.scale} 倍)</div>
<div>${img(set.palette, doe, LARGE, 64)} ${img(set.palette, doe, LARGE, LARGE / DENSITY)} ${img(set.palette, doe, SMALL, SMALL / DENSITY)}<br>小さく出る所(32×32・16×16 の格子)</div>
<div class="activity"><span style="background:#333;color:#ccc">${activity}</span><span style="background:#2c2c2c;color:#fff">${activity}</span><span style="background:#f3f3f3;color:#424242">${activity}</span><br>activity bar 用の単色 48×48 の点を 24 css px で(テーマの文字色で塗られる)</div>
</div>
<p>${escapeXml(doe.summary)}</p>
<h2>文法</h2>
<p>1 つ 1 つが枠の無い物の sprite で、家族は差し色と物の種類でそろえます(下の節ごとの説明)。外周だけを紺で縁取るので、暗いテーマでは縁が消えて見え、明るいテーマでは形が消えません。同じ語はエディタ・linter の欄・hover・文書で必ず同じ sprite です。灯(違反の重さで灯る点)を持つ sprite は、灯した版も並べています。字体の欄は <code>$(doeff-名前)</code> で書ける単色の icon 字体です(黒と紺の点から作ります)。</p>
${sections.join('\n')}
<h2>service の旗の選び</h2>
${verdict}
<table><tr><th>旗</th><th>service</th><th>模様</th><th>色 1</th><th>色 2</th></tr>
${flagRows}
</table>
</body></html>
`;
}
