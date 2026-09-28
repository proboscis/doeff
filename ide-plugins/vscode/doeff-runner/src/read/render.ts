// 定義を読む面の HTML を組む純粋な関数(VS Code に触らない)。カード = 定義などの実体 1 つの部品を HTML で見せる物で、
// 構文はなぞらない(v2・operator 2026-09-28 "so we are not to follow syntax like using. we want better html visualization of
// entity like defk")。見本 = docs/design/hy-reading-plane/artifacts/v3/entity.html(左に軸・右に実体のカード)。
// 本体の文字(val / var の表)は linter の印字が入るまで出さず、元の Hy は実体ごとの `source` ボタンで開閉する(v3 3 節)。

import type { HyDefinition } from '../hy/contract';
import type { LintSignature } from '../lint/contract';
import { answerText, headerEffects, typeText } from '../defk/model';
import { axisKey, axisTitle, facets, visibleCards, worstLevel, type Card, type Facet, type Selection } from './model';

/** HTML の特別な文字を逃がす。 */
export function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/** tags の key ごとの色(context と role は見本と同じ決まった色、他の key は名前から選ぶ — 同じ key は同じ色に見せるため)。 */
const TAG_PALETTE: readonly string[] = ['tag-c0', 'tag-c1', 'tag-c2', 'tag-c3', 'tag-c4', 'tag-c5'];

/** tags の key の色の class。 */
export function tagClass(key: string): string {
  if (key === 'context') {
    return 'tag-context';
  }
  if (key === 'role') {
    return 'tag-role';
  }
  let hash = 0;
  for (const ch of key) {
    hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  }
  return TAG_PALETTE[hash % TAG_PALETTE.length];
}

/** 絵の口 — effect の名から pixel art の data URI(#849 の装飾 A と同じ絵。無ければ undefined)。 */
export interface Glyphs {
  readonly effect: (name: string) => string | undefined;
}

/** 左の軸の欄(軸ごとに値の札)。 */
export function renderFacets(all: readonly Facet[]): string {
  return all
    .map((facet) => {
      const key = axisKey(facet.axis);
      const chips = facet.values
        .map((v) => {
          const classes = ['facet', v.selected ? 'on' : '', v.count === 0 && !v.selected ? 'empty' : ''].filter((c) => c !== '').join(' ');
          return `<button class="${classes}" data-axis="${escapeHtml(key)}" data-value="${escapeHtml(v.value)}">${escapeHtml(v.value)}<small>${v.count}</small></button>`;
        })
        .join('');
      return `<h2>軸: ${escapeHtml(axisTitle(facet.axis))}</h2><div class="fac">${chips}</div>`;
    })
    .join('');
}

/** 絞り込みの結果の 1 行(選んだ軸の積と件数)。 */
export function summaryText(shown: number, total: number, all: readonly Facet[] = []): string {
  const chosen = all
    .map((f) => ({ title: axisTitle(f.axis), values: f.values.filter((v) => v.selected).map((v) => v.value) }))
    .filter((c) => c.values.length > 0)
    .map((c) => `${c.title} = ${c.values.join(' か ')}`);
  const count = shown === total ? `定義 ${total}` : `定義 ${shown} / ${total}`;
  return chosen.length === 0 ? count : `${chosen.join(' × ')} → ${count}`;
}

/** 引数と答えのチップ(defk / deff の見出しから)。 */
function signatureStrip(signature: LintSignature): string {
  const params = signature.params
    .map((p) => `<span class="p"><span class="n">${escapeHtml(p.name)}</span><span class="t">${escapeHtml(typeText(p.type))}</span></span>`)
    .join('');
  return `<div class="sig">${params === '' ? '<span class="none">引数なし</span>' : params}<span class="arrow">→</span><span class="ret">${escapeHtml(answerText(signature))}</span></div>`;
}

/** 使う effect の欄(絵つきのチップ・Raise は赤い札)。effect が無ければ欄ごと出さない。 */
function effectRow(signature: LintSignature, glyphs: Glyphs): string {
  const items = headerEffects(signature);
  if (items.length === 0) {
    return signature.inferenceComplete ? '' : '<div class="row"><span class="k">使う effect</span><span class="none">見えた分は無し(推論は途中まで)</span></div>';
  }
  const chips = items
    .map((e) => {
      if (e.kind === 'raise') {
        return `<span class="eff raise">Raise ${escapeHtml(e.name)}</span>`;
      }
      const src = glyphs.effect(e.name);
      const img = src === undefined ? '' : `<img src="${escapeHtml(src)}" alt="">`;
      return `<span class="eff">${img}${escapeHtml(e.name)}</span>`;
    })
    .join('');
  const partial = signature.inferenceComplete ? '' : '<span class="none">(推論は途中まで)</span>';
  return `<div class="row"><span class="k">使う effect</span><div>${chips}${partial}</div></div>`;
}

/** 入れ子の定義の欄の見出し(kind ごと)。 */
function memberTitle(kind: HyDefinition['kind']): string {
  switch (kind) {
    case 'field':
      return '欄';
    case 'enum-member':
      return '値';
    case 'effect-clause':
      return '解く effect';
    case 'method':
      return 'method';
    default:
      return kind;
  }
}

/** 見出しの無い実体の欄(引数の名・基底・入れ子の定義・:check)。 */
function attributeRows(card: Card): string {
  const d = card.definition;
  const rows: string[] = [];
  if (d.params.length > 0) {
    rows.push(`<div class="row"><span class="k">引数</span><div>${d.params.map((p) => `<span class="p"><span class="n">${escapeHtml(p)}</span></span>`).join('')}</div></div>`);
  }
  if (d.bases.length > 0) {
    rows.push(`<div class="row"><span class="k">基底</span><div>${d.bases.map((b) => `<span class="p"><span class="t">${escapeHtml(b)}</span></span>`).join('')}</div></div>`);
  }
  const groups = new Map<string, HyDefinition[]>();
  for (const member of card.members) {
    const title = memberTitle(member.kind);
    groups.set(title, [...(groups.get(title) ?? []), member]);
  }
  for (const [title, members] of groups) {
    rows.push(`<div class="row"><span class="k">${escapeHtml(title)}</span><div>${members.map((m) => `<span class="p"><span class="n">${escapeHtml(m.name)}</span></span>`).join('')}</div></div>`);
  }
  if (d.checks !== null && d.checks.length > 0) {
    rows.push(`<div class="row"><span class="k">契約</span><div>${d.checks.map((c) => `<code>${escapeHtml(c)}</code>`).join('')}</div></div>`);
  }
  return rows.join('');
}

/** 元の Hy(source の行番号つき・読むだけ)。 */
function sourceBox(card: Card, fileLabel: string): string {
  const lines = card.source.split('\n');
  const last = card.firstLine + lines.length - 1;
  const numbered = lines.map((text, i) => `<div><span class="ln">${card.firstLine + i}</span>${escapeHtml(text)}</div>`).join('');
  return `<div class="srcbox" id="src-${card.id}" hidden><div class="h">元の Hy(読むだけ)· ${escapeHtml(fileLabel)}:${card.firstLine}–${last}</div><div class="code">${numbered}</div></div>`;
}

/** カード 1 枚 — 頭(種類・名・source と editor のボタン・tags のチップ)・引数と答え・使う effect・説明・元の Hy・置き場。 */
export function renderCard(card: Card, place: string, glyphs: Glyphs, hidden: boolean): string {
  const d = card.definition;
  const tags = Object.entries(d.tags ?? {})
    .map(
      ([key, value]) =>
        `<button class="chip ${tagClass(key)}" data-axis="${escapeHtml(`tag:${key}`)}" data-value="${escapeHtml(value)}" title="この値で絞る">${escapeHtml(key)}: ${escapeHtml(value)}</button>`
    )
    .join('');
  const start = d.fullRange.start;
  const buttons = `<span class="srcbar"><button class="btn" data-src="${card.id}">source</button><button class="btn" data-line="${start.line}" data-character="${start.character}">editor で開く</button></span>`;
  const head = `<div class="hd"><span class="kind k-${escapeHtml(d.kind)}">${escapeHtml(d.kind)}</span><span class="name">${escapeHtml(d.name)}</span>${buttons}<span class="chips">${tags}</span></div>`;
  const typed = d.kind === 'defk' || d.kind === 'deff';
  const middle =
    card.signature !== undefined
      ? signatureStrip(card.signature) + effectRow(card.signature, glyphs)
      : typed
        ? '<div class="row"><span class="k">型と effect</span><span class="none">linter の答え待ち</span></div>'
        : attributeRows(card);
  const doc = d.docstring === null ? '' : `<div class="doc">${escapeHtml(d.docstring)}</div>`;
  const level = worstLevel(card.violations);
  const violations =
    level === undefined
      ? ''
      : `<span class="viol viol-${level}" title="${escapeHtml(card.violations.map((v) => `${v.rule}: ${v.message}`).join('\n'))}">違反 ${card.violations.length}</span>`;
  const foot = `<div class="ft">${violations}<span class="loc">${escapeHtml(place)}:${card.firstLine}</span></div>`;
  const fileLabel = place.split('/').pop() ?? place;
  return `<section class="card" id="${card.id}"${hidden ? ' hidden' : ''}>${head}${middle}${doc}${sourceBox(card, fileLabel)}${foot}</section>`;
}

/** 面の状態 — 索引にその file が無い時・設定で切った時は理由を出す。 */
export type PlaneState =
  | { readonly tag: 'cards'; readonly cards: readonly Card[]; readonly selection: Selection }
  | { readonly tag: 'message'; readonly text: string };

/** 面の頁の材料。 */
export interface PageInput {
  /** workspace の root から見た file の path(置き場の表示) */
  readonly place: string;
  readonly state: PlaneState;
  readonly glyphs: Glyphs;
  /** webview の CSP の出どころ(`webview.cspSource`) */
  readonly cspSource: string;
  /** script に付ける 1 回限りの数 */
  readonly nonce: string;
}

/** 頁の全体(左に軸・右に実体のカード)。 */
export function renderPage(input: PageInput): string {
  const content = (() => {
    switch (input.state.tag) {
      case 'message':
        return { axes: '', summary: '', cards: `<p class="message">${escapeHtml(input.state.text)}</p>` };
      case 'cards': {
        const { cards, selection } = input.state;
        const shown = new Set(visibleCards(cards, selection).map((c) => c.id));
        const all = facets(cards, selection);
        return {
          axes: renderFacets(all),
          summary: summaryText(shown.size, cards.length, all),
          cards: cards.map((c) => renderCard(c, input.place, input.glyphs, !shown.has(c.id))).join('')
        };
      }
      default: {
        const unreachable: never = input.state;
        throw new Error(`網羅されていない状態: ${JSON.stringify(unreachable)}`);
      }
    }
  })();
  return `<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src ${input.cspSource} 'unsafe-inline'; script-src 'nonce-${input.nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>${PAGE_STYLE}</style>
</head>
<body>
<aside class="axes"><div id="axes">${content.axes}</div><div class="hint">左の軸はどれからでも入れて、交差できる。数は索引(hy-index)から。カードの tags のチップを押してもその値で絞る。</div></aside>
<main class="main">
<div class="crumb"><b>${escapeHtml(input.place)}</b><span id="summary">${escapeHtml(content.summary)}</span><button class="btn" id="clear">絞り込みを外す</button></div>
<div id="cards">${content.cards}</div>
</main>
<script nonce="${input.nonce}">${PAGE_SCRIPT}</script>
</body>
</html>`;
}

/** 頁の見た目(見本 artifacts/v3/entity.html の色と部品に合わせる)。 */
const PAGE_STYLE = `
html,body{background:#1b1d21;color:#d6d8dc}
body{font-family:-apple-system,"Hiragino Sans",sans-serif;margin:0;display:grid;grid-template-columns:240px minmax(0,1fr);min-height:100vh}
button{font:inherit;cursor:pointer}
.axes{background:#15171a;border-right:1px solid #2c3036;padding:16px 14px;font-size:12.5px;position:sticky;top:0;height:100vh;overflow-y:auto;box-sizing:border-box}
.axes h2{font-size:11px;letter-spacing:.08em;color:#8a9099;margin:14px 0 6px}
.axes h2:first-child{margin-top:0}
.fac{display:flex;flex-wrap:wrap;gap:5px}
.facet{border:1px solid #3a3f47;border-radius:12px;padding:1px 8px;color:#b8bec7;background:#1f2227;font-size:12px}
.facet small{color:#7d858f;margin-left:4px}
.facet.on{background:#2f4a66;border-color:#4a76a8;color:#dfeeff}
.facet.on small{color:#bcd8ff}
.facet.empty{opacity:.4}
.hint{color:#7d858f;font-size:11px;line-height:1.6;margin-top:18px}
.main{padding:14px 22px 40px;min-width:0}
.crumb{display:flex;gap:12px;align-items:center;font-size:12px;color:#8a9099;margin-bottom:12px;flex-wrap:wrap}
.crumb b{color:#c9ced5;font-weight:600;font-family:Menlo,monospace}
.card{background:#22252a;border:1px solid #33383f;border-radius:10px;margin:0 0 16px;box-shadow:0 1px 0 #000}
.card[hidden]{display:none}
.hd{display:flex;align-items:center;gap:10px;padding:10px 16px;border-bottom:1px solid #33383f;flex-wrap:wrap}
.kind{font-size:10.5px;font-weight:700;letter-spacing:.06em;color:#1b1d21;background:#e2c46a;border-radius:4px;padding:2px 6px}
.k-deff{background:#d8cf8a}.k-defn{background:#a8a8a8}.k-defeffect,.k-effect-clause{background:#8fd3ff}.k-defrecord,.k-deftype{background:#b9e39a}
.k-defhandler{background:#f0a8a8}.k-deftest{background:#c8a8f0}.k-defenum{background:#f0b890}.k-defclass{background:#a0a8ff}.k-variable{background:#bfc5cc}
.name{font:600 16px Menlo,monospace;color:#f2e6a8}
.srcbar{display:flex;gap:6px}
.btn{font-size:11px;border:1px solid #4a76a8;border-radius:5px;padding:2px 8px;color:#bcd8ff;background:#1f2a3a}
.btn.on{background:#2f4a66}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-left:auto}
.chip{font-size:11px;border-radius:12px;padding:2px 9px;border:1px solid #3a3f47}
.tag-context{background:#3b3220;color:#f0c674;border-color:#5a4a25}
.tag-role{background:#232f3b;color:#9dd0ff;border-color:#2f4a66}
.tag-c0{background:#1f3a33;color:#8fe3c8;border-color:#2f5a4d}.tag-c1{background:#3b2233;color:#f0a0c8;border-color:#5a2f4a}.tag-c2{background:#2f2640;color:#c8a8f0;border-color:#4a3a66}
.tag-c3{background:#3b2a20;color:#f0b890;border-color:#5a3f2f}.tag-c4{background:#2a3320;color:#c8e39a;border-color:#3f4a2f}.tag-c5{background:#2c2f33;color:#d6d8dc;border-color:#454a52}
.sig{display:flex;align-items:center;gap:8px;padding:10px 16px;flex-wrap:wrap;font:13px Menlo,monospace}
.p{display:inline-flex;align-items:center;gap:6px;background:#1b1d21;border:1px solid #3a3f47;border-radius:6px;padding:3px 8px;margin:0 6px 4px 0;font:12.5px Menlo,monospace}
.sig .p{margin:0}
.p .n{color:#d6d8dc}.p .t{color:#4ec9b0}
.arrow{color:#8a9099;font-size:16px;margin:0 4px}
.ret{display:inline-flex;align-items:center;background:#1f2a24;border:1px solid #2f5a45;border-radius:6px;padding:3px 10px;color:#9fe3c0}
.row{display:grid;grid-template-columns:96px minmax(0,1fr);gap:10px;padding:8px 16px;border-top:1px solid #2c3036;font-size:12.5px;align-items:start}
.row .k{color:#8a9099;padding-top:3px}
.eff{display:inline-flex;align-items:center;gap:6px;background:#1b1d21;border:1px solid #3a3f47;border-radius:6px;padding:3px 8px;margin:0 6px 4px 0;font:12px Menlo,monospace}
.eff img{width:14px;height:14px;image-rendering:pixelated}
.eff.raise{color:#f08c8c;border-color:#7a2f2f}
.none{color:#7d858f;font-size:12px}
code{font:12px Menlo,monospace;background:#1b1d21;border:1px solid #3a3f47;border-radius:4px;padding:1px 6px;margin:0 6px 4px 0;display:inline-block}
.doc{padding:10px 16px;border-top:1px solid #2c3036;color:#c9ced5;font-size:13px;line-height:1.7;white-space:pre-wrap}
.srcbox{border-top:1px solid #2c3036;background:#15171a}
.srcbox[hidden]{display:none}
.srcbox .h{color:#7d858f;font-size:11px;padding:6px 16px 0}
.srcbox .code{padding:6px 16px 10px;font:12px/1.6 Menlo,monospace;color:#c9ced5;overflow-x:auto}
.srcbox .code div{white-space:pre;min-height:1.6em}
.ln{display:inline-block;width:2.8em;color:#555b63;text-align:right;margin-right:1.1em;user-select:none;font-size:11px}
.ft{display:flex;gap:18px;align-items:center;padding:7px 16px;border-top:1px solid #33383f;font-size:12px;color:#b8bec7;flex-wrap:wrap}
.loc{margin-left:auto;color:#7d858f;font:11px Menlo,monospace}
.viol{font-size:11px;border-radius:4px;padding:1px 6px}
.viol-critical{background:#5a1d1d;color:#ffb0b0}.viol-major{background:#5a3a1d;color:#ffd0a0}.viol-minor{background:#3a3a1d;color:#e6e0a0}.viol-info{background:#1d3a5a;color:#a0c8ff}
.message{color:#8a9099;margin-top:24px}
`;

/** 頁の動き — 軸の札・tags のチップ・行の移動は拡張へ送り、拡張の答え(絞った結果)を描く。絞る判断は拡張の側(model.ts)だけ。source の開閉だけは頁の中で閉じる。 */
const PAGE_SCRIPT = `
const vscode = acquireVsCodeApi();
document.addEventListener('click', (event) => {
  const target = event.target instanceof Element ? event.target.closest('[data-axis],[data-line],[data-src],#clear') : null;
  if (target === null) { return; }
  event.preventDefault();
  if (target.id === 'clear') { vscode.postMessage({ type: 'clear' }); return; }
  if (target.hasAttribute('data-src')) {
    const box = document.getElementById('src-' + target.getAttribute('data-src'));
    if (box !== null) { box.hidden = !box.hidden; target.classList.toggle('on', !box.hidden); }
    return;
  }
  if (target.hasAttribute('data-line')) {
    vscode.postMessage({ type: 'open', line: Number(target.getAttribute('data-line')), character: Number(target.getAttribute('data-character')) });
    return;
  }
  vscode.postMessage({ type: 'toggle', axis: target.getAttribute('data-axis'), value: target.getAttribute('data-value') });
});
window.addEventListener('message', (event) => {
  const message = event.data;
  if (message.type !== 'filter') { return; }
  document.getElementById('axes').innerHTML = message.axes;
  document.getElementById('summary').textContent = message.summary;
  const shown = new Set(message.visible);
  for (const card of document.querySelectorAll('.card')) { card.hidden = !shown.has(card.id); }
  window.scrollTo(0, 0);
});
`;
