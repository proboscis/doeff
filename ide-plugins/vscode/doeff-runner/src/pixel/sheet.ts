// icon の見本の一覧 — 1 枚の自己完結 HTML(画は data URI・字体は base64 の woff を埋める)。全 icon を 16×16・8×8・
// 拡大で、暗い背景と明るい背景の両方に並べ、名前と一言を添える。service の旗の重複の検算もここに出す。

import { activityBarMono, allGlyphs, extensionGlyph, flagReport } from './build';
import { flagGlyphName } from './flags';
import { codepoints, FONT_FAMILY } from './font';
import { PICO8, type Glyph, type GlyphSet } from './glyphs';
import { dataUri, escapeXml, monoSvg, png } from './render';

/** 画 1 つの `<img>`(点を補間しない表示)。 */
function img(glyph: Glyph, size: 16 | 8, shown: number): string {
  const src = dataUri('image/png', png(glyph.pixels[size], Math.max(1, Math.round(shown / size))));
  return `<img src="${src}" width="${shown}" height="${shown}" alt="${escapeXml(glyph.name)}">`;
}

/** icon 1 行 — 拡大・実寸(16・8)・2 倍・字体(単色)を暗い背景と明るい背景に。 */
function glyphRow(glyph: Glyph, codepoint: number | undefined): string {
  const actual = `${img(glyph, 16, 16)} ${img(glyph, 8, 8)}`;
  const doubled = `${img(glyph, 16, 32)} ${img(glyph, 8, 16)}`;
  const font = codepoint === undefined ? '' : `<span class="icon-font">&#x${codepoint.toString(16)};</span>`;
  return [
    '<tr>',
    `<td class="big">${img(glyph, 16, 96)}</td>`,
    `<td class="big">${img(glyph, 8, 48)}</td>`,
    `<td class="dark">${actual}<br>${doubled}<br>${font}</td>`,
    `<td class="light">${actual}<br>${doubled}<br>${font}</td>`,
    `<td><code>${escapeXml(glyph.name)}</code><br><code class="id">$(doeff-${escapeXml(glyph.name)})</code></td>`,
    `<td>${escapeXml(glyph.summary)}</td>`,
    '</tr>'
  ].join('');
}

/** 見本の HTML の全体(woff は字体の byte 列 — 字体の欄の表示に埋める)。 */
export function previewHtml(set: GlyphSet, woff: Buffer): string {
  const glyphs = allGlyphs(set);
  const cps = new Map(codepoints(glyphs).map((c) => [c.name, c.codepoint]));
  const sections = set.families.map((family) => {
    const rows = glyphs
      .filter((g) => g.family === family.name)
      .map((g) => glyphRow(g, cps.get(g.name)))
      .join('\n');
    return [
      `<h2>${escapeXml(family.label)} <small>${escapeXml(family.name)}</small></h2>`,
      `<p>${escapeXml(family.summary)}</p>`,
      '<table><tr><th>拡大 16×16</th><th>拡大 8×8</th><th>暗い背景(実寸・2 倍・字体)</th><th>明るい背景</th><th>名前</th><th>一言</th></tr>',
      rows,
      '</table>'
    ].join('\n');
  });
  const report = flagReport(set);
  const byName = new Map(glyphs.map((g) => [g.name, g]));
  const flagRows = report.choices
    .map((c) => {
      const swatch = (color: number): string => `<span class="swatch" style="background:${PICO8[color]}"></span>${PICO8[color]}`;
      const flag = byName.get(flagGlyphName(c.service));
      const pictures = flag === undefined ? '' : `${img(flag, 16, 48)} ${img(flag, 16, 16)} ${img(flag, 8, 16)}`;
      return `<tr><td class="big">${pictures}</td><td><code>${escapeXml(c.service)}</code></td><td>${escapeXml(c.pattern)}</td><td>${swatch(c.primary)}</td><td>${swatch(c.secondary)}</td></tr>`;
    })
    .join('\n');
  const verdict =
    report.collisions.length === 0
      ? `<p class="ok">重複の検算: ${report.choices.length} の service の旗で、同じ模様と同じ色の組を持つ物は 0 組(重複ゼロ)。</p>`
      : `<p class="ng">重複の検算: ${report.collisions.map((c) => `${c.services.join(' と ')}(${c.key})`).join('・')} が重なる。</p>`;
  const doe = extensionGlyph(set, glyphs);
  const activity = monoSvg(activityBarMono(set), 24);
  const palette = PICO8.map((c, i) => `<span class="swatch big" style="background:${c}" title="${i.toString(16)} ${c}"></span>`).join('');
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
</style></head><body>
<h1>doeff-runner の pixel art の icon(見本)</h1>
<p>これは doeff の VS Code 拡張(doeff-runner)に入れる、pixel art の icon の一覧です。agora-redesign の issue 841(エディタと linter の欄を pixel art の icon で見せる)の最初の作業 = icon の組を作る作業の成果です。次の作業でこの icon を linter の欄・行の左端(gutter)・hover・状態バーに使い、その次の作業でエディタの決まった語(<code>&lt;-</code>・<code>defk</code> など)の表示をこの icon に置き換えます。</p>
<p>元の定義は <code>resources/pixel/glyphs.json</code> の 1 か所で、この一覧・SVG・PNG・icon 字体・拡張の icon はすべてそこから生成しています。色は PICO-8 の 16 色だけです。</p>
<div>${palette}</div>
<h2>拡張の icon</h2>
<div class="hero">
<div><img src="${dataUri('image/png', png(doe.pixels[16], set.extension.scale))}" width="128" height="128" alt="doe"><br>128×128(16×16 を ${set.extension.scale} 倍)</div>
<div class="activity"><span style="background:#333;color:#ccc">${activity}</span><span style="background:#2c2c2c;color:#fff">${activity}</span><span style="background:#f3f3f3;color:#424242">${activity}</span><br>activity bar 用の単色 24×24(テーマの文字色で塗られる)</div>
</div>
<p>${escapeXml(doe.summary)}</p>
<h2>文法</h2>
<p>家族ごとに枠の形が決まり、中の絵が語を表します。同じ語はエディタ・linter の欄・hover・文書で必ず同じ icon です。字体の欄は <code>$(doeff-名前)</code> で書ける単色の icon 字体です。</p>
${sections.join('\n')}
<h2>service の旗の選び</h2>
${verdict}
<table><tr><th>旗</th><th>service</th><th>模様</th><th>色 1</th><th>色 2</th></tr>
${flagRows}
</table>
</body></html>
`;
}
