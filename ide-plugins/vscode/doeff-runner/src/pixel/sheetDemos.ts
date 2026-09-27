// 見本の HTML の動く見本と並べ比べ — 「違反(linter)」の欄(0.6.20・枠つきの直し・新しい版を linter の実際の出力で並べる)・
// icon の枠つきの版と新しい版の並べ比べ・エディタの
// 決まった語の文字の置き換え(種類ごとの入り切り・カーソルの行・icon を押すと元の文字)。拡張と同じ純粋な関数
// (view.ts の束ね方・vocabulary.ts の画・replace.ts の置き換え)で作るので、見本と拡張の見え方は食い違わない。

import type { LintReport, LintRuleFamily, LintSeverity, LintViolation } from '../lint/contract';
import { groupDescription, groupTooltipLines, violationRoots, worstSeverity } from '../lint/view';
import { composeTinted, overlayBadge, type Glyph, type GlyphSet, type Pixels } from './glyphs';
import { dataUri, escapeXml, png } from './render';
import { findReplacements, REPLACE_KIND_LABELS, REPLACE_KINDS, type Replacement } from './replace';
import { litPixels, ruleFamilyGlyph, ruleIcon, SEVERITY_LAMP, worstMark } from './vocabulary';

/** 格子を `<img>` にする(整数倍の PNG・補間なし)。 */
function pixelImg(pixels: Pixels, shown: number, alt: string): string {
  const scale = Math.max(1, Math.round(shown / pixels.length));
  return `<img src="${dataUri('image/png', png(pixels, scale))}" width="${shown}" height="${shown}" alt="${escapeXml(alt)}">`;
}

/** 0.6.20 の束の画 — 天秤の上に最も強い印を小さく重ねた物(どの行もほぼ同じ絵)。比べる元の定義の絵で作る。 */
function oldGroupPixels(old: GlyphSet, violations: readonly LintViolation[]): Pixels | undefined {
  const law = composeTinted(old, 'law', {});
  const mark = worstMark(violations);
  const badge = mark === undefined ? undefined : composeTinted(old, mark, {});
  return law === undefined ? undefined : overlayBadge(law[16], badge?.[8] ?? null);
}

/** 枠つきの直し(2026-09-28 の最初の直し)の束の画 — 家族の絵を看板の枠で囲み、枠の色で重さ、Jev の判定はふくろうを重ねる。 */
function framedGroupPixels(old: GlyphSet, family: LintRuleFamily | null, severity: LintSeverity | null, jev: boolean): Pixels | undefined {
  const framed = composeTinted(old, ruleFamilyGlyph(family), severity === null ? {} : { A: SEVERITY_LAMP[severity] });
  if (framed === undefined) {
    return undefined;
  }
  const owl = jev && family !== 'jev' ? composeTinted(old, 'jev', {}) : undefined;
  return owl === undefined ? framed[16] : overlayBadge(framed[16], owl[8]);
}

/** 木の 1 行(VS Code の暗いテーマの木を真似る)。 */
function treeRow(icon: string, label: string, description: string, tooltip: string | null): string {
  const tip = tooltip === null ? '' : ` data-tip="${escapeXml(tooltip)}" tabindex="0"`;
  return `<div class="tree-row"${tip}><span class="twisty">›</span>${icon}<span class="tree-label">${escapeXml(label)}</span><span class="tree-desc">${escapeXml(description)}</span></div>`;
}

/**
 * 「違反(linter)」の欄の見本 — linter の実際の出力(editor-json)から、0.6.20 の形(law の名か番号だけ・同じ天秤に小さな
 * 印)・枠つきの直し・新しい版(規則の番号と linter の短い名・家族ごとの sprite・付いた灯の色で重さ・law の名は hover)を
 * 並べる。old は比べる元の定義(枠つきの版)— 無ければ 0.6.20 と枠つきの列を出さない。
 */
export function panelDemo(set: GlyphSet, glyphs: readonly Glyph[], lint: LintReport, source: string, old: GlyphSet | null): string {
  const byName = new Map(glyphs.map((g) => [g.name, g]));
  const groups = violationRoots(lint.violations, lint.rules).filter((n) => n.tag === 'group');
  const before: string[] = [];
  const framed: string[] = [];
  const after: string[] = [];
  const zoomed: string[] = [];
  for (const node of groups) {
    if (node.tag !== 'group') {
      continue;
    }
    const severity = worstSeverity(node.violations) ?? null;
    const label = node.summary.title === null ? node.rule : `${node.rule} ${node.summary.title}`;
    const tooltip = groupTooltipLines(node.rule, node.summary, node.violations).join('\n');
    if (old !== null) {
      const oldPixels = oldGroupPixels(old, node.violations);
      const oldLabel = node.violations.find((v) => v.law !== null)?.law ?? node.rule;
      before.push(treeRow(oldPixels === undefined ? '' : pixelImg(oldPixels, 16, 'law'), oldLabel, `${node.violations.length} 件`, null));
      const framedPixels = framedGroupPixels(old, node.summary.family, severity, node.violations.some((v) => v.source === 'jev'));
      framed.push(treeRow(framedPixels === undefined ? '' : pixelImg(framedPixels, 16, 'framed'), label, groupDescription(node.violations), null));
    }
    const icon = ruleIcon(node.summary.family, severity, node.violations);
    const pixels = litPixels(set, icon);
    after.push(treeRow(pixels === undefined ? '' : pixelImg(pixels, 16, icon.glyph), label, groupDescription(node.violations), tooltip));
    zoomed.push(treeRow(pixels === undefined ? '' : pixelImg(pixels, 32, icon.glyph), label, groupDescription(node.violations), tooltip));
  }
  // 家族ごとの規則(linter の出力の rules から — 拡張は写しを持たない)
  const families = new Map<LintRuleFamily, Array<{ rule: string; title: string }>>();
  for (const rule of lint.rules) {
    if (rule.family === null || rule.title === null) {
      continue;
    }
    const list = families.get(rule.family) ?? [];
    if (!list.some((r) => r.rule === rule.rule)) {
      list.push({ rule: rule.rule, title: rule.title });
    }
    families.set(rule.family, list);
  }
  const familyRows = [...families.entries()]
    .map(([family, rules]) => {
      const glyph = byName.get(ruleFamilyGlyph(family));
      const pictures = ([null, 'error', 'warning', 'info'] as const)
        .map((sev) => {
          const p = litPixels(set, ruleIcon(family, sev, []));
          return p === undefined ? '' : pixelImg(p, 32, `${family} ${sev ?? 'off'}`);
        })
        .join(' ');
      const names = rules.map((r) => `<code>${escapeXml(r.rule)}</code> ${escapeXml(r.title)}`).join('<br>');
      return `<tr><td class="big">${pictures}</td><td><code>${escapeXml(family)}</code><br>${escapeXml(glyph?.summary ?? '')}</td><td>${names}</td></tr>`;
    })
    .join('\n');
  const pairs = (['smell', 'class'] as const)
    .map((family) => {
      const p = litPixels(set, { glyph: ruleFamilyGlyph(family, true), severity: 'warning', flag: null });
      return p === undefined ? '' : pixelImg(p, 48, family);
    })
    .join(' ');
  const oldColumns =
    old === null
      ? ''
      : `<div><h3>0.6.20</h3><div class="tree">${before.join('\n')}</div>
  <p class="note">どの行も同じ天秤の絵で、違いは右下の小さな印(火・旗・ふくろう)だけ。見出しは番号だけで、1 行だけ law の名。</p></div>
  <div><h3>枠つきの直し(今朝の最初の版)</h3><div class="tree">${framed.join('\n')}</div>
  <p class="note">見出しと家族ごとの絵はここで直した。重さは看板の枠の色。— 枠が格好よくない、という感想で次の版へ。</p></div>`;
  return `
<h2 id="panel">「違反(linter)」の欄 — 0.6.20・枠つきの直し・新しい版</h2>
<p>中身は <code>${escapeXml(source)}</code> を新しい doeff-linter で読んだ実際の出力です(違反 ${lint.violations.length} 件・規則の束 ${groups.length})。新しい版の行は、押す(またはキーボードで選ぶ)と VS Code の hover に出す文が下に出ます。</p>
<div class="panels">
  ${oldColumns}
  <div><h3>新しい版(等倍)</h3><div class="tree">${after.join('\n')}</div>
  <p class="note">見出しは「規則の番号 + linter が出す短い名」。sprite は規則の家族で変え、付いた小さな灯の色が重さ(赤 = error・琥珀 = warning・青 = info)。law の名は hover へ。</p></div>
  <div><h3>新しい版(2 倍)</h3><div class="tree zoom">${zoomed.join('\n')}</div></div>
</div>
<div id="tip" class="tip" hidden></div>
<p>Jev が判定した束は、組の sprite にします — 臭い(DOEFF205)= ふくろうの顔のドローン、class(DOEFF204)= ふくろうが乗った木箱: ${pairs}</p>
<h3>規則の家族と sprite</h3>
<p>どの規則がどの家族かは linter が出力の <code>rules</code> の <code>family</code> で決め、短い名も <code>title</code> で出します(拡張は写しを持ちません)。下の表は今回の出力から作りました。灯は左から 消えている・error・warning・info。</p>
<table><tr><th>sprite</th><th>家族</th><th>規則(linter の出力の短い名)</th></tr>
${familyRows}
</table>`;
}

/**
 * icon の並べ比べ — 同じ名前の icon を、比べる元の定義(枠つきの版)と今の元の定義で並べる(16×16 の 3 倍・実寸・8×8)。
 * 灯を持つ sprite は、灯した版(error)も添える。
 */
export function compareDemo(set: GlyphSet, glyphs: readonly Glyph[], old: GlyphSet, oldGlyphs: readonly Glyph[]): string {
  const before = new Map(oldGlyphs.map((g) => [g.name, g]));
  const rows = glyphs
    .filter((g) => !g.name.startsWith('service-'))
    .map((g) => {
      const o = before.get(g.name);
      const lit = litPixels(set, { glyph: g.name, severity: 'error', flag: null });
      const hasLamp = set.glyphs.find((x) => x.name === g.name)?.grids[16].some((row) => row.includes('L')) ?? false;
      const oldCell = o === undefined ? '(無し)' : `${pixelImg(o.pixels[16], 48, o.name)} ${pixelImg(o.pixels[16], 16, o.name)} ${pixelImg(o.pixels[8], 16, o.name)}`;
      const newCell = `${pixelImg(g.pixels[16], 48, g.name)} ${hasLamp && lit !== undefined ? pixelImg(lit, 48, 'lit') : ''} ${pixelImg(g.pixels[16], 16, g.name)} ${pixelImg(g.pixels[8], 16, g.name)}`;
      return `<tr><td class="dark">${oldCell}</td><td class="dark">${newCell}</td><td class="light">${pixelImg(g.pixels[16], 32, g.name)}</td><td><code>${escapeXml(g.name)}</code></td><td>${escapeXml(g.summary)}</td></tr>`;
    })
    .join('\n');
  return `
<h2 id="compare">icon の並べ比べ — 枠つきの版と新しい版</h2>
<p>左が枠つきの版(家族ごとに判子の枠・丸・赤い六角・床のタイル・看板の枠)、右が新しい版(枠の無い、居心地のよい SF のゲームの sprite)です。新しい版で灯を持つ物は、2 つ目に灯した版(error = 赤)を並べています。明るい背景の列は明るいテーマでの見え方です。</p>
<table><tr><th>枠つきの版(3 倍・実寸・8×8)</th><th>新しい版(3 倍・灯した版・実寸・8×8)</th><th>明るい背景</th><th>名前</th><th>一言(新しい版)</th></tr>
${rows}
</table>`;
}

/** 見本の Hy(置き換えの見本だけに使う。effect の宣言は WatchOnce)。 */
const HY_SAMPLE = `(defeffect WatchOnce [cursor]
  {:tags {:context "durable" :role "intent"}}
  "cursor から後の出来事を 1 度だけ見る。")

(defk poll-window [cursor kinds]
  {:pre [(: cursor int) (: kinds tuple)] :post [(: % tuple)] :tags {:context "durable" :role "foundation"}}
  "答え = #(新しい cursor 出来事の list)。"
  (<- signal dict (WatchOnce cursor))
  (<- limit int (Ask :window-limit))
  (if (empty? signal)
    (Absent "出来事が無い")
    #(cursor (cut (get signal "events") limit))))

(defhandler watch-by-table [table]
  {:tags {:context "durable" :role "translation"}}
  (WatchOnce [cursor]
    (if (in cursor table)
      (resume (get table cursor))
      (finish (Raise (Unreachable :reason "表に無い"))))))`;

/** 見本の Hy の 1 行を、置き換えの印つきの HTML にする(隠す範囲は span で包み、JS が種類ごとに入り切りする)。 */
function sampleLine(text: string, line: number, found: readonly Replacement[], images: ReadonlyMap<string, string>): string {
  const onLine = found.map((r, i) => ({ r, i })).filter(({ r }) => r.line === line);
  let at = 0;
  const parts: string[] = [];
  for (const { r, i } of onLine) {
    parts.push(escapeXml(text.slice(at, r.start)));
    const img = images.get(r.glyph) ?? '';
    const word = escapeXml(text.slice(r.start, r.end));
    parts.push(
      r.display === 'replace'
        ? `<span class="rp k-${r.kind}"><button class="ico" data-i="${i}" aria-label="${escapeXml(r.original)}">${img}</button><span class="orig">${word}</span></span>`
        : `<span class="rp mark k-${r.kind}"><button class="ico" data-i="${i}" aria-label="effect ${word}">${img}</button>${word}</span>`
    );
    at = r.end;
  }
  parts.push(escapeXml(text.slice(at)));
  return `<div class="code-line" data-line="${line}"><span class="ln">${line + 1}</span><span class="src">${parts.join('')}</span></div>`;
}

/**
 * 文字の置き換えの動く見本 — 見本の Hy を拡張と同じ関数で置き換え、種類ごとの入り切り・行を押すとその行が元の文字
 * (カーソルの行の真似)・icon を押すと元の文字をそのまま(コピーできる)と icon と一言を出す。
 */
export function replaceDemo(glyphs: readonly Glyph[]): string {
  const byName = new Map(glyphs.map((g) => [g.name, g]));
  const found = findReplacements(HY_SAMPLE, (name) => name === 'WatchOnce');
  const images = new Map<string, string>();
  for (const r of found) {
    const glyph = byName.get(r.glyph);
    if (glyph !== undefined && !images.has(r.glyph)) {
      images.set(r.glyph, pixelImg(glyph.pixels[8], 16, r.glyph));
    }
  }
  const lines = HY_SAMPLE.split('\n');
  const plain = lines.map((text, line) => `<div class="code-line"><span class="ln">${line + 1}</span><span class="src">${escapeXml(text)}</span></div>`).join('');
  const replaced = lines.map((text, line) => sampleLine(text, line, found, images)).join('');
  const toggles = REPLACE_KINDS.map(
    (kind) => `<label><input type="checkbox" data-kind="${kind}" checked> <code>${kind}</code> ${escapeXml(REPLACE_KIND_LABELS[kind])}</label>`
  ).join('<br>');
  const details = found.map((r) => {
    const glyph = byName.get(r.glyph);
    return { original: r.original, display: r.display, summary: glyph?.summary ?? '', image: glyph === undefined ? '' : pixelImg(glyph.pixels[16], 32, r.glyph) };
  });
  return `
<h2 id="replace">エディタの文字の置き換え(動く見本)</h2>
<p>決まった語だけを、表示の上でだけ icon に置き換えます。file の文字は変えないので、保存・検索・コピー・画面読み上げは元の文字のままです。右の見本は拡張と同じ関数で置き換えています。</p>
<ul>
<li>icon を押すと、VS Code の hover に出す物が下に出ます — 置き換える前の文字をそのまま(:tags の辞書は畳む前の全文・選んでコピーできる)と、その下に語の icon と一言。</li>
<li>行の番号を押すと、その行を「カーソルのある行」として元の文字に戻します(VS Code ではカーソルの行と選んだ範囲の行が元の文字)。</li>
<li>下の箱で種類ごとに入り切りできます(拡張では設定 <code>doeff-runner.pixel.replaceKinds</code> と命令「doeff: icon に置き換える語の種類を選ぶ」)。</li>
<li>利用者が付けた名前の effect(ここでは <code>WatchOnce</code>)は、名前を消さずに前に手紙の印を付けます。</li>
</ul>
<div class="kinds">${toggles}</div>
<div class="panels">
  <div><h3>元の文字</h3><pre class="code">${plain}</pre></div>
  <div><h3>置き換えた表示</h3><pre class="code" id="replaced">${replaced}</pre></div>
</div>
<div id="word" class="tip" hidden></div>
<script>
(() => {
  const details = ${JSON.stringify(details).replace(/</g, '\\u003c')};
  const box = document.getElementById('replaced');
  const word = document.getElementById('word');
  document.querySelectorAll('input[data-kind]').forEach((input) => {
    input.addEventListener('change', () => box.classList.toggle('off-' + input.dataset.kind, !input.checked));
  });
  box.addEventListener('click', (event) => {
    const button = event.target.closest('button.ico');
    if (button) {
      const d = details[Number(button.dataset.i)];
      word.hidden = false;
      word.textContent = '';
      if (d.display === 'replace') {
        const pre = document.createElement('pre');
        pre.textContent = d.original;
        word.appendChild(pre);
      }
      const line = document.createElement('div');
      line.innerHTML = d.image;
      line.appendChild(document.createTextNode(' ' + d.summary));
      word.appendChild(line);
      return;
    }
    const ln = event.target.closest('.ln');
    if (ln) {
      const row = ln.parentElement;
      const was = row.classList.contains('cursor');
      box.querySelectorAll('.code-line.cursor').forEach((r) => r.classList.remove('cursor'));
      row.classList.toggle('cursor', !was);
    }
  });
  const tip = document.getElementById('tip');
  document.querySelectorAll('[data-tip]').forEach((row) => {
    const show = () => { tip.hidden = false; tip.textContent = row.dataset.tip; row.after(tip); };
    row.addEventListener('click', show);
    row.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); show(); } });
  });
})();
</script>`;
}

/** 見本の 2 つの動く見本の css。 */
export const DEMO_CSS = `
.panels { display: flex; gap: 24px; flex-wrap: wrap; align-items: flex-start; }
.panels > div { min-width: 320px; }
.tree { background: #252526; color: #cccccc; font: 13px -apple-system, 'Hiragino Sans', sans-serif; padding: 6px 0; width: max-content; min-width: 340px; }
.tree-row { display: flex; align-items: center; gap: 6px; height: 22px; padding: 0 12px 0 8px; cursor: pointer; }
.tree-row:hover, .tree-row:focus { background: #2a2d2e; outline: none; }
.tree-row .twisty { color: #c5c5c5; width: 10px; }
.tree-row .tree-desc { color: #9d9d9d; font-size: 12px; margin-left: 6px; }
.tree.zoom { font-size: 20px; }
.tree.zoom .tree-row { height: 38px; }
.tree.zoom .tree-desc { font-size: 18px; }
.note { color: var(--muted); font-size: 12px; max-width: 360px; }
.tip { background: #252526; color: #cccccc; border: 1px solid #454545; padding: 8px 12px; white-space: pre-wrap; font-size: 12px; max-width: 720px; margin: 6px 0; }
.tip pre { background: #1e1e1e; color: #d4d4d4; padding: 6px 8px; margin: 0 0 8px; user-select: text; white-space: pre-wrap; }
.kinds { font-size: 12px; margin: 8px 0; }
pre.code { background: #1e1e1e; color: #d4d4d4; font: 13px/20px Menlo, Consolas, monospace; padding: 8px 0; margin: 0; overflow-x: auto; }
.code-line { white-space: pre; padding-right: 12px; }
.code-line .ln { display: inline-block; width: 28px; color: #858585; text-align: right; margin-right: 12px; cursor: pointer; user-select: none; }
.code-line.cursor { background: #2a2d2e; }
.rp .orig { display: none; }
.rp button.ico { background: none; border: 0; padding: 0 1px; margin: 0; cursor: pointer; vertical-align: middle; line-height: 0; }
.rp button.ico img { vertical-align: middle; }
.code-line.cursor .rp:not(.mark) .ico { display: none; }
.code-line.cursor .rp .orig { display: inline; }
.code-line.cursor .rp.mark .ico { display: none; }
${REPLACE_KINDS.map((k) => `.off-${k} .k-${k}:not(.mark) .ico { display: none; } .off-${k} .k-${k} .orig { display: inline; } .off-${k} .k-${k}.mark .ico { display: none; }`).join('\n')}
`;
