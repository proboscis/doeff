// 定義を読む面の HTML を組む純粋な関数(VS Code に触らない)。カード = 定義などの実体 1 つの部品を HTML で見せる物で、
// 構文はなぞらない(v2・operator 2026-09-28 "so we are not to follow syntax like using. we want better html visualization of
// entity like defk")。見本 = docs/design/hy-reading-plane/artifacts/v5/entity.html(左に軸・上に 1 行の切り替え・右に実体のカード)。
// カードは 1 行に畳める(v4)。面に出る文字は labels.ts の表からだけ引く(v5)。
// 本体の文字(val / var の表)は linter の印字が入るまで出さず、元の Hy は実体ごとの source ボタンで開閉する(v3 3 節)。

import { pieceStyle, sliceHighlight, type Piece, type SourceColoring } from '../hy/highlight/spans';
import type { LintBody, LintBodySegment, LintSignature, LintViolation } from '../lint/contract';
import { answerText, headerEffects, typeText } from '../defk/model';
import { cardKey, LINE_FIELDS, type FoldState, type LineField } from './fold';
import { escapeHtml, tagClass, type Glyphs } from './html';
import { contractRow, declaredEffectChips, entityLineArgs, entityRows, indexSignatureRows, relationBand } from './entity';
import { LABELS } from './labels';
import { axisKey, axisTitle, facets, SEARCH_KEY, visibleCards, worstLevel, type Card, type Facet, type Selection } from './model';
import { relationOf, type CallGraph, type CallTree } from './tree';
import { renderTree, type TreeRenderContext } from './treeRender';

/** 1 行の切り替えの欄の見出し(labels の表から)。 */
const LINE_FIELD_LABEL: Readonly<Record<LineField, string>> = {
  args: LABELS.argsReturnType,
  effects: LABELS.effects,
  tags: LABELS.tags,
  doc: LABELS.docFirstLine,
  relations: LABELS.callersTests,
  location: LABELS.location
};

/** 左の軸の欄(軸ごとに値の札)。値の多い軸は上位 limit 個と選んだ値だけ見せ、残りは数だけ(repo 全体の面の型・置き場の軸のため)。 */
export function renderFacets(all: readonly Facet[], limit: number): string {
  return all
    .map((facet) => {
      const key = axisKey(facet.axis);
      const kept = facet.values.filter((v, i) => i < limit || v.selected);
      const hiddenCount = facet.values.length - kept.length;
      const chips = kept
        .map((v) => {
          const classes = ['facet', v.selected ? 'on' : '', v.count === 0 && !v.selected ? 'empty' : ''].filter((c) => c !== '').join(' ');
          return `<button class="${classes}" data-axis="${escapeHtml(key)}" data-value="${escapeHtml(v.value)}">${escapeHtml(v.value)}<small>${v.count}</small></button>`;
        })
        .join('');
      const rest = hiddenCount === 0 ? '' : `<span class="more">+${hiddenCount}</span>`;
      return `<h2>${escapeHtml(LABELS.axis)}: ${escapeHtml(axisTitle(facet.axis))}</h2><div class="fac">${chips}${rest}</div>`;
    })
    .join('');
}

/** 絞り込みの結果の 1 行(選んだ軸の積と件数)。 */
export function summaryText(shown: number, total: number, all: readonly Facet[] = []): string {
  const chosen = all
    .map((f) => ({ title: axisTitle(f.axis), values: f.values.filter((v) => v.selected).map((v) => v.value) }))
    .filter((c) => c.values.length > 0)
    .map((c) => `${c.title} = ${c.values.join(' or ')}`);
  const count = shown === total ? `${LABELS.definitions} ${total}` : `${LABELS.definitions} ${shown} / ${total}`;
  return chosen.length === 0 ? count : `${chosen.join(' × ')} → ${count}`;
}

/** 1 行の切り替えの欄と、全部畳む / 全部開く。 */
export function renderLineBar(state: FoldState): string {
  const boxes = LINE_FIELDS.map(
    (f) => `<label><input type="checkbox" data-line-field="${f}"${state.line.has(f) ? ' checked' : ''}>${escapeHtml(LINE_FIELD_LABEL[f])}</label>`
  ).join('');
  return `<div class="linebar"><b>${escapeHtml(LABELS.showInLine)}</b>${boxes}<span class="sep"></span><button class="btn" id="fold-all">${escapeHtml(LABELS.foldAll)}</button><button class="btn" id="unfold-all">${escapeHtml(LABELS.unfoldAll)}</button></div>`;
}

/** 1 行に出す欄の class(body に付け、CSS で欄を出し入れする — 切り替えで頁を描き直さないため)。 */
export function lineClasses(state: FoldState): string {
  return LINE_FIELDS.filter((f) => state.line.has(f))
    .map((f) => `show-${f}`)
    .join(' ');
}

/** 縦の表に切り替える閾(v6 2.1 節・席の既定で戻せる): 引数の数と、引数と return type の型の文字の合計。 */
export const TALL_SIGNATURE_PARAMS = 4;
export const TALL_SIGNATURE_CHARS = 60;
/** 畳んだ 1 行で型を省いて名だけにする引数の数(v6 2.2 節)。 */
export const NAMES_ONLY_PARAMS = 4;

/** 引数が多いか型が長い見出しか(1 行のチップでは return type が折り返しに埋もれるので、縦の表で描くため)。 */
export function isTallSignature(signature: LintSignature): boolean {
  const chars = signature.params.reduce((n, p) => n + typeText(p.type).length, 0) + answerText(signature).length;
  return signature.params.length >= TALL_SIGNATURE_PARAMS || chars > TALL_SIGNATURE_CHARS;
}

/** 型を候補ごとの小さなチップにする(union の `|` は薄く・None は破線で弱く — 候補の切れ目を見せるため)。 */
function typeChips(type: LintSignature['answer']): string {
  const members = type !== null && type.kind === 'union' ? type.members : [type];
  return members
    .map((m) => {
      const text = typeText(m);
      return `<span class="${text === 'None' ? 'none' : ''}">${escapeHtml(text)}</span>`;
    })
    .join('<i>|</i>');
}

/** 引数と答え(defk / deff の見出しから・開いたカード)— 短ければ 1 行のチップ、長ければ縦の表(v6)。 */
function signatureStrip(signature: LintSignature): string {
  if (isTallSignature(signature)) {
    const rows = signature.params.map((p) => `<span class="n">${escapeHtml(p.name)}</span><span class="tc">${typeChips(p.type)}</span>`).join('');
    const answer = signature.absent ? `<span>${escapeHtml(answerText(signature))}</span>` : typeChips(signature.answer);
    return `<div class="sig2"><div class="lab">${escapeHtml(LABELS.args)}</div>${rows}<div class="rt"><span class="k">${escapeHtml(LABELS.returnType)}</span><span class="tc">${answer}</span></div></div>`;
  }
  const params = signature.params
    .map((p) => `<span class="p"><span class="n">${escapeHtml(p.name)}</span><span class="t">${escapeHtml(typeText(p.type))}</span></span>`)
    .join('');
  return `<div class="sig">${params === '' ? `<span class="none">${escapeHtml(LABELS.noArgs)}</span>` : params}<span class="arrow">→</span><span class="ret" title="${escapeHtml(LABELS.returnType)}">${escapeHtml(answerText(signature))}</span></div>`;
}

/** effect のチップ(絵つき・Raise は赤い札)。 */
function effectChips(signature: LintSignature, glyphs: Glyphs): string {
  return headerEffects(signature)
    .map((e) => {
      if (e.kind === 'raise') {
        return `<span class="eff raise">Raise ${escapeHtml(e.name)}</span>`;
      }
      const src = glyphs.effect(e.name);
      const img = src === undefined ? '' : `<img src="${escapeHtml(src)}" alt="">`;
      return `<span class="eff">${img}${escapeHtml(e.name)}</span>`;
    })
    .join('');
}

/** effects の欄(開いたカード)。effect が無ければ欄ごと出さない。 */
function effectRow(signature: LintSignature, glyphs: Glyphs): string {
  const chips = effectChips(signature, glyphs);
  const partial = signature.inferenceComplete ? '' : `<span class="none">(${escapeHtml(LABELS.inferencePartial)})</span>`;
  if (chips === '') {
    return signature.inferenceComplete ? '' : `<div class="row"><span class="k">${escapeHtml(LABELS.effects)}</span><div>${partial}</div></div>`;
  }
  return `<div class="row"><span class="k">${escapeHtml(LABELS.effects)}</span><div>${chips}${partial}</div></div>`;
}

/** 1 行の形の「args / return type」— `(name: T, …) → R`(見出しの無い実体は入れ子の定義の名・引数の名)。 */
function lineArgs(card: Card): string {
  const s = card.signature;
  if (s !== undefined) {
    const answer = `<span class="r">${escapeHtml(answerText(s))}</span>`;
    if (s.params.length >= NAMES_ONLY_PARAMS) {
      // 引数が多い時は名だけ(型は hover で)。return type は常に出す(v6 2.2 節)
      const typed = s.params.map((p) => `${p.name}: ${typeText(p.type)}`).join('\n');
      return `<span class="f f-args" title="${escapeHtml(typed)}">(${s.params.map((p) => escapeHtml(p.name)).join(', ')}) → ${answer}</span>`;
    }
    const params = s.params.map((p) => `${escapeHtml(p.name)}: <span class="t">${escapeHtml(typeText(p.type))}</span>`).join(', ');
    return `<span class="f f-args">(${params}) → ${answer}</span>`;
  }
  return entityLineArgs(card);
}

/** 説明の 1 行目(先頭の 1 文を省略記号で切る)。 */
export function docFirstLine(docstring: string | null, limit = 80): string {
  if (docstring === null) {
    return '';
  }
  const first = docstring.split('\n')[0].trim();
  const sentence = /^[^。.!?！？]*[。.!?！？]?/.exec(first)?.[0] ?? first;
  return sentence.length > limit ? `${sentence.slice(0, limit - 1)}…` : sentence;
}

/** カードを描く材料(カード以外)。 */
export interface CardContext {
  readonly glyphs: Glyphs;
  readonly fold: FoldState;
  /** 索引の全 file の呼び出しの表(関係の数と木の材料) */
  readonly graph: CallGraph;
  /** カードの file 全体の色(editor と同じ文法・theme・記号ごとの色。まだ塗れていなければ undefined) */
  readonly coloringOf: (card: Card) => SourceColoring | undefined;
}

/** 本体の字の役 → 色の class(色は U16 で theme の token の色に寄せる — 今は見本 v3 の色)。 */
function segmentClass(role: LintBodySegment['role']): string {
  switch (role) {
    case 'keyword':
      return 'kw';
    case 'type':
      return 'b';
    case 'unknown-type':
      return 'q';
    case 'bind':
      return 'arrow-bind';
    case 'effect':
      return 'fx';
    case 'call':
      return 'fn';
    case 'lisp':
      return 'lisp';
    case 'name':
    case 'assign':
    case 'text':
    case null:
      return '';
    default: {
      const unreachable: never = role;
      throw new Error(`網羅されていない字の役: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 字 1 つ(effect は絵を添える)。 */
function renderSegment(segment: LintBodySegment, glyphs: Glyphs): string {
  const cls = segmentClass(segment.role);
  const img = segment.role === 'effect' && segment.effect !== null ? glyphs.effect(segment.effect) : undefined;
  const glyph = img === undefined ? '' : `<img class="gl" src="${escapeHtml(img)}" alt="">`;
  const text = escapeHtml(segment.text);
  return cls === '' ? `${glyph}${text}` : `<span class="${cls}"${segment.role === 'lisp' ? ` title="${escapeHtml(LABELS.lispAsIs)}"` : ''}>${glyph}${text}</span>`;
}

/**
 * 本体の文字(v2 2.2 節・v3 2 節 — operator 承認 "yeah val var when match is perfect.")— 行番号 = source の行、字下げ = 段、
 * effect を通す束縛(⇐)の行は薄い背景、本体の setv は警告の印。組み立ては linter の bodies(読み方の正本は linter)。
 */
function bodyBlock(body: LintBody, glyphs: Glyphs, violations: readonly LintViolation[]): string {
  if (body.lines.length === 0) {
    return '';
  }
  // linter の違反は、その source の行を描く本体の行へ(v1 制約 4 — 問題の欄は source 側のまま)
  const byLine = new Map<number, LintViolation[]>();
  for (const v of violations) {
    byLine.set(v.range.start.line, [...(byLine.get(v.range.start.line) ?? []), v]);
  }
  const lines = body.lines
    .map((line) => {
      const bound = line.segments.some((s) => s.role === 'bind');
      const warning =
        line.warning === null ? '' : `<span class="warn" title="${escapeHtml(line.warning.message)}">⚠ ${escapeHtml(line.warning.kind ?? LABELS.warning)}</span>`;
      const here = byLine.get(line.line) ?? [];
      const level = worstLevel(here);
      const marks =
        level === undefined ? '' : `<span class="viol viol-${level}" title="${escapeHtml(here.map((v) => `${v.rule}: ${v.message}`).join('\n'))}">${escapeHtml(here.map((v) => v.rule).join(' '))}</span>`;
      const text = `${'  '.repeat(line.depth)}${' '.repeat(line.pad)}${line.segments.map((s) => renderSegment(s, glyphs)).join('')}`;
      return `<div class="${bound ? 'bl bound' : 'bl'}" data-src-line="${line.line + 1}"><span class="ln">${line.line + 1}</span>${text}${warning}${marks}</div>`;
    })
    .join('');
  return `<div class="body">${lines}</div>`;
}

/** 色つきの 1 片の HTML(色も字の形も無ければ文字だけ)。 */
function pieceHtml(piece: Piece): string {
  const style = pieceStyle(piece);
  return style === '' ? escapeHtml(piece.text) : `<span style="${style}">${escapeHtml(piece.text)}</span>`;
}

/**
 * 元の Hy(source の行番号つき・読むだけ)。file 全体の色があれば定義の範囲を切り出して editor と同じ色で描き
 * (記号ごとの色は file 全体の記号の順で決まるので、切り出してから塗らない)、無ければ色なしの文字で描く。
 */
function sourceBox(card: Card, fileLabel: string, coloring: SourceColoring | undefined): string {
  const lines = card.source.split('\n');
  const last = card.firstLine + lines.length - 1;
  const pieces = coloring === undefined ? undefined : sliceHighlight(coloring.lines, coloring.spans, card.definition.fullRange);
  const colored = pieces !== undefined && pieces.length === lines.length ? pieces : undefined;
  const body = (text: string, i: number): string => (colored === undefined ? escapeHtml(text) : colored[i].map(pieceHtml).join(''));
  const numbered = lines.map((text, i) => `<div><span class="ln">${card.firstLine + i}</span>${body(text, i)}</div>`).join('');
  return `<div class="srcbox" id="src-${card.id}" hidden><div class="h">${escapeHtml(LABELS.hySource)} · ${escapeHtml(fileLabel)}:${card.firstLine}–${last}</div><div class="code">${numbered}</div></div>`;
}

/**
 * カード 1 枚 — 頭(種類・名・開閉・source と open in editor)は常に。畳んだ時は 1 行(切り替えた欄)、開いた時は
 * 引数と答え・effects・欄・説明・関係の数・置き場。元の Hy は source ボタンで開閉(畳んでいても出せる)。
 */
export function renderCard(card: Card, ctx: CardContext, hidden: boolean): string {
  const d = card.definition;
  const key = cardKey(d);
  const open = ctx.fold.open.has(key);
  const tagChips = (cls: string): string =>
    Object.entries(d.tags ?? {})
      .map(
        ([k, value]) =>
          `<button class="${cls} ${tagClass(k)}" data-axis="${escapeHtml(`tag:${k}`)}" data-value="${escapeHtml(value)}">${escapeHtml(k)}: ${escapeHtml(value)}</button>`
      )
      .join('');
  const start = d.fullRange.start;
  const toggle = `<button class="fold" data-fold="${escapeHtml(key)}" title="${escapeHtml(open ? LABELS.fold : LABELS.unfold)}">${open ? '▾' : '▸'}</button>`;
  const buttons = `<span class="srcbar"><button class="btn" data-src="${card.id}">${escapeHtml(LABELS.source)}</button><button class="btn" data-line="${start.line}" data-character="${start.character}">${escapeHtml(LABELS.openInEditor)}</button></span>`;
  const head = `<div class="hd"><span class="kind k-${escapeHtml(d.kind)}">${escapeHtml(d.kind)}</span><span class="name">${escapeHtml(d.name)}</span>${toggle}${buttons}<span class="chips full-only">${tagChips('chip')}</span></div>`;
  const relation = relationOf(ctx.graph, d.qualifiedName);
  const relationText = `${LABELS.callers} <b>${relation.callers}</b> · ${LABELS.tests} <b>${relation.tests}</b>`;
  // 帯の callers / callees は木の入口(v7 3 節の入口 a)
  const qn = escapeHtml(d.qualifiedName);
  const band = relationBand(card, ctx.graph);
  const location = `${escapeHtml(card.place)}:${card.firstLine}`;
  const doc = docFirstLine(d.docstring);
  const effects = card.signature === undefined ? declaredEffectChips(d, ctx.glyphs) : effectChips(card.signature, ctx.glyphs);
  const line = [
    lineArgs(card),
    effects === '' ? '' : `<span class="f f-effects">${effects}</span>`,
    d.tags === null ? '' : `<span class="f f-tags">${tagChips('mini')}</span>`,
    doc === '' ? '' : `<span class="f f-doc">${escapeHtml(doc)}</span>`,
    `<span class="f f-relations">${relationText}</span>`,
    `<span class="f f-location loc">${location}</span>`
  ].join('');
  const typed = d.kind === 'defk' || d.kind === 'deff';
  // 契約(型でない述語)は索引から来るので、linter の見出しの有無に関わらず出す
  const middle =
    (card.signature !== undefined
      ? signatureStrip(card.signature) + effectRow(card.signature, ctx.glyphs)
      : typed
        ? indexSignatureRows(d, ctx.glyphs)
        : entityRows(card, ctx.glyphs)) + contractRow(d);
  const docBlock = d.docstring === null ? '' : `<div class="doc">${escapeHtml(d.docstring)}</div>`;
  const body = card.body === undefined ? '' : bodyBlock(card.body, ctx.glyphs, card.violations);
  const level = worstLevel(card.violations);
  const violations =
    level === undefined
      ? ''
      : `<span class="viol viol-${level}" title="${escapeHtml(card.violations.map((v) => `${v.rule}: ${v.message}`).join('\n'))}">${escapeHtml(LABELS.violations)} ${card.violations.length}</span>`;
  const foot = `<div class="ft">${band}${violations}<span class="loc">${location}</span></div>`;
  const fileLabel = card.place.split('/').pop() ?? card.place;
  // source を持たないカード(repo 全体の面の索引だけのカード)は、開く・source を押すとその file を読み込む(v3 3 節 — 索引の位置から切り出す)
  const lazy = card.source === '' ? ' data-lazy' : '';
  const classes = open ? 'card open' : 'card';
  return `<section class="${classes}" id="${card.id}" data-key="${escapeHtml(key)}" data-qn="${qn}"${lazy}${hidden ? ' hidden' : ''}>${head}<div class="line">${line}</div><div class="full">${middle}${docBlock}${body}</div>${sourceBox(card, fileLabel, ctx.coloringOf(card))}<div class="full">${foot}</div></section>`;
}

/** 面の状態 — 索引にその file が無い時・設定で切った時は理由を出す。 */
export type PlaneState =
  | { readonly tag: 'cards'; readonly cards: readonly Card[]; readonly selection: Selection }
  | WorkspaceState
  | { readonly tag: 'message'; readonly text: string };

/**
 * repo 全体の面の状態(#910 U9)— 全カードで軸と数を作り、見せるのは積んだカード(木・帯・名から寄せた物)と、絞った先の上限まで。
 * 全部を描かないのは、repo の定義が 1 万近くあり、頁が重くなって読めなくなるため。
 */
export interface WorkspaceState {
  readonly tag: 'workspace';
  readonly cards: readonly Card[];
  readonly selection: Selection;
  /** 積んだカードの完全修飾名(新しい物が上) */
  readonly pinned: readonly string[];
  /** 絞った先のカードを描く上限 */
  readonly limit: number;
  /** 左の欄の軸ごとに見せる値の数の上限(選んだ値はいつも見せる) */
  readonly facetLimit: number;
  /** カードの file 全体の色(読み込んだ file の分だけ) */
  readonly coloringOf: (card: Card) => SourceColoring | undefined;
}

/** repo 全体の面の右の列(積んだカード + 絞った先の上限まで)と、上の行の件数の文。 */
export function renderWorkspaceCards(state: WorkspaceState, ctx: CardContext): { readonly html: string; readonly summary: string; readonly axes: string } {
  const all = facets(state.cards, state.selection);
  const visible = visibleCards(state.cards, state.selection);
  const byName = new Map(state.cards.map((c) => [c.definition.qualifiedName, c]));
  const pinned = state.pinned.flatMap((qn) => {
    const card = byName.get(qn);
    return card === undefined ? [] : [card];
  });
  const pinnedNames = new Set(pinned.map((c) => c.definition.qualifiedName));
  const rest = visible.filter((c) => !pinnedNames.has(c.definition.qualifiedName));
  const shown = rest.slice(0, state.limit);
  const stack = pinned.length === 0 ? '' : `<div class="stackhead">${escapeHtml(LABELS.stacked)}</div>${pinned.map((c) => renderCard(c, ctx, false)).join('')}<div class="stackhead">${escapeHtml(LABELS.matches)}</div>`;
  const more = rest.length > shown.length ? `<p class="message">${escapeHtml(LABELS.showing)} ${shown.length} / ${rest.length} — ${escapeHtml(LABELS.narrowWithAxes)}</p>` : '';
  return {
    html: stack + shown.map((c) => renderCard(c, ctx, false)).join('') + more,
    summary: summaryText(visible.length, state.cards.length, all),
    axes: renderFacets(all, state.facetLimit)
  };
}

/** 面の頁の材料。 */
export interface PageInput {
  /** workspace の root から見た file の path(置き場の表示) */
  readonly place: string;
  readonly state: PlaneState;
  readonly glyphs: Glyphs;
  readonly fold: FoldState;
  readonly graph: CallGraph;
  /** 開いている呼び出しの木(無ければ undefined)と、その木で deftest を出すか */
  readonly tree: { readonly tree: CallTree; readonly showTests: boolean } | undefined;
  /** 開いた document の file 全体の色(まだ塗れていなければ undefined — source は色なしで描く) */
  readonly coloring: SourceColoring | undefined;
  /** webview の CSP の出どころ(`webview.cspSource`) */
  readonly cspSource: string;
  /** script に付ける 1 回限りの数 */
  readonly nonce: string;
}

/** 左の欄の下の call tree の根の選び(v7 3 節の入口 b — この file の定義から選ぶ)。 */
export function renderTreePicker(cards: readonly Card[], current: string | undefined): string {
  const options = cards
    .map((c) => `<option value="${escapeHtml(c.definition.qualifiedName)}"${c.definition.qualifiedName === current ? ' selected' : ''}>${escapeHtml(c.definition.name)}</option>`)
    .join('');
  return `<h2>${escapeHtml(LABELS.callTree)}</h2><select id="tree-root"><option value="">${escapeHtml(LABELS.pickRoot)}</option>${options}</select>`;
}

/** 木を描く材料 — 開いている file の定義は linter の見出しで、他は索引の型の綴りで。 */
function treeContext(cards: readonly Card[], graph: CallGraph, glyphs: Glyphs, showTests: boolean): TreeRenderContext {
  const signatures = new Map(cards.flatMap((c) => (c.signature === undefined ? [] : [[c.definition.qualifiedName, c.signature] as const])));
  return { glyphs, showTests, signatureOf: (qn) => signatures.get(qn), handlersOf: (qn) => graph.handlers.get(qn) ?? 0 };
}

/** 木の欄の HTML(頁の中と、木を変えた時に webview へ送る分で同じ物を使うため)。木が無ければ空。 */
export function renderTreePart(
  cards: readonly Card[],
  graph: CallGraph,
  glyphs: Glyphs,
  part: { readonly tree: CallTree; readonly showTests: boolean } | undefined
): string {
  return part === undefined ? '' : renderTree(part.tree, treeContext(cards, graph, glyphs, part.showTests));
}

/** 今の名の検索の文字(検索の欄に戻すため)。 */
function searchOf(state: PlaneState): ReadonlySet<string> {
  switch (state.tag) {
    case 'cards':
    case 'workspace':
      return state.selection.get(SEARCH_KEY) ?? new Set<string>();
    case 'message':
      return new Set<string>();
    default: {
      const unreachable: never = state;
      throw new Error(`網羅されていない状態: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 頁の全体(左に軸・上に 1 行の切り替え・右に実体のカード)。 */
export function renderPage(input: PageInput): string {
  const ctx: CardContext = {
    glyphs: input.glyphs,
    fold: input.fold,
    graph: input.graph,
    coloringOf: input.state.tag === 'workspace' ? input.state.coloringOf : () => input.coloring
  };
  const content = (() => {
    switch (input.state.tag) {
      case 'message':
        return { axes: '', summary: '', bar: '', picker: '', tree: '', cards: `<p class="message">${escapeHtml(input.state.text)}</p>` };
      case 'cards': {
        const { cards, selection } = input.state;
        const shown = new Set(visibleCards(cards, selection).map((c) => c.id));
        const all = facets(cards, selection);
        return {
          axes: renderFacets(all, Number.POSITIVE_INFINITY),
          summary: summaryText(shown.size, cards.length, all),
          bar: renderLineBar(input.fold),
          picker: renderTreePicker(cards, input.tree?.tree.root.qualifiedName),
          tree: renderTreePart(cards, input.graph, input.glyphs, input.tree),
          cards: cards.map((c) => renderCard(c, ctx, !shown.has(c.id))).join('')
        };
      }
      case 'workspace': {
        const listed = renderWorkspaceCards(input.state, ctx);
        return {
          axes: listed.axes,
          summary: listed.summary,
          bar: renderLineBar(input.fold),
          picker: '',
          tree: renderTreePart(input.state.cards, input.graph, input.glyphs, input.tree),
          cards: listed.html
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
<body class="${lineClasses(input.fold)}">
<aside class="axes"><input id="search" type="search" placeholder="${escapeHtml(LABELS.searchNames)}" value="${escapeHtml([...(searchOf(input.state))].join(' '))}"><div id="axes">${content.axes}</div>${content.picker}<div class="hint">${escapeHtml(LABELS.axesHint)}</div></aside>
<main class="main">
<div class="top">
<div class="crumb"><b>${escapeHtml(input.place)}</b><span id="summary">${escapeHtml(content.summary)}</span><button class="btn" id="clear">${escapeHtml(LABELS.clearFilter)}</button></div>
${content.bar}
</div>
<div id="tree">${content.tree}</div>
<div id="cards">${content.cards}</div>
</main>
<script nonce="${input.nonce}">${PAGE_SCRIPT}</script>
</body>
</html>`;
}

/** 頁の見た目(見本 artifacts/v5/entity.html の色と部品に合わせる)。 */
const PAGE_STYLE = `
html,body{background:#1b1d21;color:#d6d8dc}
body{font-family:-apple-system,"Hiragino Sans",sans-serif;margin:0;display:grid;grid-template-columns:240px minmax(0,1fr);min-height:100vh}
button{font:inherit;cursor:pointer}
.axes{background:#15171a;border-right:1px solid #2c3036;padding:16px 14px;font-size:12.5px;position:sticky;top:0;height:100vh;overflow-y:auto;box-sizing:border-box}
.axes h2{font-size:11px;letter-spacing:.08em;color:#8a9099;margin:14px 0 6px;text-transform:uppercase}
#axes h2:first-child{margin-top:0}
.fac{display:flex;flex-wrap:wrap;gap:5px}
.facet{border:1px solid #3a3f47;border-radius:12px;padding:1px 8px;color:#b8bec7;background:#1f2227;font-size:12px}
.facet small{color:#7d858f;margin-left:4px}
.facet.on{background:#2f4a66;border-color:#4a76a8;color:#dfeeff}
.facet.on small{color:#bcd8ff}
.facet.empty{opacity:.4}
.hint{color:#7d858f;font-size:11px;line-height:1.6;margin-top:18px}
.main{padding:0 22px 40px;min-width:0}
.top{position:sticky;top:0;z-index:3;background:#1b1d21;padding-top:14px}
.crumb{display:flex;gap:12px;align-items:center;font-size:12px;color:#8a9099;margin-bottom:10px;flex-wrap:wrap}
.crumb b{color:#c9ced5;font-weight:600;font-family:Menlo,monospace}
.linebar{display:flex;gap:14px;align-items:center;flex-wrap:wrap;background:#22252a;border:1px solid #33383f;border-radius:8px;padding:8px 14px;margin-bottom:14px;font-size:12.5px;color:#c9ced5}
.linebar label{display:inline-flex;gap:5px;align-items:center;cursor:pointer}
.linebar .sep{width:1px;height:16px;background:#3a3f47}
.card{background:#22252a;border:1px solid #33383f;border-radius:10px;margin:0 0 4px;box-shadow:0 1px 0 #000}
.card[hidden]{display:none}
.card:not(.open) .full,.card:not(.open) .full-only{display:none}
.card.open .line{display:none}
.hd{display:flex;align-items:center;gap:10px;padding:5px 14px;flex-wrap:wrap}
.card.open .hd{border-bottom:1px solid #33383f}
.kind{font-size:10.5px;font-weight:700;letter-spacing:.06em;color:#1b1d21;background:#e2c46a;border-radius:4px;padding:2px 6px}
.k-deff{background:#d8cf8a}.k-defn{background:#a8a8a8}.k-defeffect,.k-effect-clause{background:#8fd3ff}.k-defrecord,.k-deftype{background:#b9e39a}
.k-defhandler{background:#f0a8a8}.k-deftest{background:#c8a8f0}.k-defenum{background:#f0b890}.k-defclass{background:#a0a8ff}.k-variable{background:#bfc5cc}
.name{font:600 16px Menlo,monospace;color:#f2e6a8}
.fold{border:1px solid #3a3f47;background:#1b1d21;color:#b8bec7;border-radius:4px;padding:0 6px;font-size:11px}
.srcbar{display:flex;gap:6px}
.btn{font-size:11px;border:1px solid #4a76a8;border-radius:5px;padding:2px 8px;color:#bcd8ff;background:#1f2a3a}
.btn.on{background:#2f4a66}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-left:auto}
.chip{font-size:11px;border-radius:12px;padding:2px 9px;border:1px solid #3a3f47}
.mini{font-size:10px;border-radius:9px;padding:0 6px;border:1px solid #3a3f47;margin-right:4px}
.tag-context{background:#3b3220;color:#f0c674;border-color:#5a4a25}
.tag-role{background:#232f3b;color:#9dd0ff;border-color:#2f4a66}
.tag-c0{background:#1f3a33;color:#8fe3c8;border-color:#2f5a4d}.tag-c1{background:#3b2233;color:#f0a0c8;border-color:#5a2f4a}.tag-c2{background:#2f2640;color:#c8a8f0;border-color:#4a3a66}
.tag-c3{background:#3b2a20;color:#f0b890;border-color:#5a3f2f}.tag-c4{background:#2a3320;color:#c8e39a;border-color:#3f4a2f}.tag-c5{background:#2c2f33;color:#d6d8dc;border-color:#454a52}
.line{display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:0 14px 6px;font:12.5px Menlo,monospace;color:#d6d8dc}
.line .f{display:none;align-items:center;gap:4px}
.show-args .line .f-args,.show-effects .line .f-effects,.show-tags .line .f-tags,.show-doc .line .f-doc,.show-relations .line .f-relations,.show-location .line .f-location{display:inline-flex}
.show-args .line .f-args{display:inline}
.line .f-doc{font:12px -apple-system,"Hiragino Sans",sans-serif;color:#b8bec7}
.line .f-relations{font:12px -apple-system,sans-serif;color:#b8bec7}
.line .f-location{margin-left:auto}
.line .eff{margin:0 4px 0 0;padding:0 6px}
.t{color:#4ec9b0}.r{color:#9fe3c0}
.sig2{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:4px 18px;padding:10px 16px;align-items:baseline;font:13px Menlo,monospace}
.sig2 .lab{color:#8a9099;font:11px -apple-system,sans-serif;letter-spacing:.06em;text-transform:uppercase;grid-column:1/3}
.sig2 .n{color:#d6d8dc}
.sig2 .rt{grid-column:1/3;display:grid;grid-template-columns:max-content minmax(0,1fr);gap:4px 18px;background:#1f2a24;border:1px solid #2f5a45;border-radius:6px;padding:6px 10px;margin-top:6px}
.sig2 .rt .k{color:#7fbf9f;font:11px -apple-system,sans-serif;letter-spacing:.06em;text-transform:uppercase;align-self:center}
.tc{display:inline-flex;gap:4px;flex-wrap:wrap;align-items:center}
.tc span{background:#1b1d21;border:1px solid #2f4a45;border-radius:4px;padding:1px 6px;color:#4ec9b0}
.tc span.none{border-style:dashed;color:#7d858f;border-color:#3a3f47}
.tc i{color:#5f6670;font-style:normal}
.rt .tc span{color:#9fe3c0;border-color:#2f5a45}
.rt .tc span.none{color:#7d858f;border-color:#3a3f47}
.sig{display:flex;align-items:center;gap:8px;padding:10px 16px;flex-wrap:wrap;font:13px Menlo,monospace}
.p{display:inline-flex;align-items:center;gap:6px;background:#1b1d21;border:1px solid #3a3f47;border-radius:6px;padding:3px 8px;margin:0 6px 4px 0;font:12.5px Menlo,monospace}
.sig .p{margin:0}
.p .n{color:#d6d8dc}.p .t{color:#4ec9b0}
.arrow{color:#8a9099;font-size:16px;margin:0 4px}
.ret{display:inline-flex;align-items:center;background:#1f2a24;border:1px solid #2f5a45;border-radius:6px;padding:3px 10px;color:#9fe3c0}
.row{display:grid;grid-template-columns:96px minmax(0,1fr);gap:10px;padding:8px 16px;border-top:1px solid #2c3036;font-size:12.5px;align-items:start}
.row .k{color:#8a9099;padding-top:3px}
.eff{display:inline-flex;align-items:center;gap:6px;background:#1b1d21;border:1px solid #3a3f47;border-radius:6px;padding:3px 8px;margin:0 6px 4px 0;font:12px Menlo,monospace;color:#8fd3ff}
.eff img{width:14px;height:14px;image-rendering:pixelated}
.eff.raise{color:#f08c8c;border-color:#7a2f2f}
.none{color:#7d858f;font-size:12px}
code{font:12px Menlo,monospace;background:#1b1d21;border:1px solid #3a3f47;border-radius:4px;padding:1px 6px;margin:0 6px 4px 0;display:inline-block}
.doc{padding:10px 16px;border-top:1px solid #2c3036;color:#c9ced5;font-size:13px;line-height:1.7;white-space:pre-wrap}
.body{border-top:1px solid #2c3036;padding:10px 16px 12px;font:12.5px/1.7 Menlo,monospace;overflow-x:auto;color:#d6d8dc}
.body .bl{white-space:pre;min-height:1.7em}
.body .bl.bound{background:#262a31;border-radius:4px}
.body .kw{color:#c586c0}.body .b{color:#4ec9b0}.body .fx{color:#8fd3ff}.body .fn{color:#dcdcaa}.body .arrow-bind{color:#8fd3ff}
.body .q{color:#7d858f;border:1px dashed #555b63;border-radius:3px;padding:0 3px;font-size:11px}
.body .lisp{color:#b8bec7;border-bottom:1px dashed #6e7681}
.body .gl{width:13px;height:13px;image-rendering:pixelated;vertical-align:-2px;margin-right:2px}
.body .warn{margin-left:10px;color:#e6c07b;font:11px -apple-system,sans-serif}
.body .viol{margin-left:10px;font-family:-apple-system,sans-serif}
.srcbox{border-top:1px solid #2c3036;background:var(--vscode-editor-background,#15171a)}
.srcbox[hidden]{display:none}
.srcbox .h{color:#7d858f;font-size:11px;padding:6px 16px 0}
.srcbox .code{padding:6px 16px 10px;font-family:var(--vscode-editor-font-family,Menlo,monospace);font-size:12px;line-height:1.6;color:var(--vscode-editor-foreground,#c9ced5);overflow-x:auto}
.srcbox .code div{white-space:pre;min-height:1.6em}
.ln{display:inline-block;width:2.8em;color:#555b63;text-align:right;margin-right:1.1em;user-select:none;font-size:11px}
.ft{display:flex;gap:18px;align-items:center;padding:7px 16px;border-top:1px solid #33383f;font-size:12px;color:#b8bec7;flex-wrap:wrap}
.ft b{color:#e6e9ee}
.loc{margin-left:auto;color:#7d858f;font:11px Menlo,monospace}
.viol{font-size:11px;border-radius:4px;padding:1px 6px}
.viol-critical{background:#5a1d1d;color:#ffb0b0}.viol-major{background:#5a3a1d;color:#ffd0a0}.viol-minor{background:#3a3a1d;color:#e6e0a0}.viol-info{background:#1d3a5a;color:#a0c8ff}
.message{color:#8a9099;margin-top:24px}
.rel{background:transparent;border:none;color:#b8bec7;font-size:12px;padding:0;text-decoration:underline dotted #5f6670}
.rel:hover{color:#dfeeff}
.relgroup{display:inline-flex;gap:4px;align-items:baseline}
.tname-sm{background:transparent;border:none;padding:0;font:12px Menlo,monospace;color:#c9ced5;cursor:pointer}
.tname-sm:hover{color:#f2e6a8;text-decoration:underline}
#search{width:100%;box-sizing:border-box;margin:0 0 12px;background:#1f2227;color:#d6d8dc;border:1px solid #3a3f47;border-radius:6px;padding:4px 8px;font:12px Menlo,monospace}
#tree-root{width:100%;background:#1f2227;color:#d6d8dc;border:1px solid #3a3f47;border-radius:6px;padding:3px 6px;font:12px Menlo,monospace}
.tree{background:#22252a;border:1px solid #33383f;border-radius:10px;margin:0 0 16px;padding:0 0 8px}
.treebar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:8px 14px;border-bottom:1px solid #33383f;font-size:12.5px;color:#c9ced5}
.treebar .k,.treesum .k{color:#8a9099;font-size:11.5px}
.treebar .mono{font-family:Menlo,monospace;color:#f2e6a8}
.treebar .sep,.treesum .sep{width:1px;height:14px;background:#3a3f47}
.treebar .depth{border:1px solid #4a76a8;border-radius:4px;padding:0 6px;color:#bcd8ff}
.treebar label{display:inline-flex;gap:4px;align-items:center}
.treesum{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:6px 14px;border-bottom:1px solid #2c3036;font-size:12px;color:#b8bec7}
.treesum .eff{margin:0}
.tnodes,.tnodes ul{list-style:none;margin:0;padding:0}
.tnodes ul{padding-left:22px}
.tn.closed > ul{display:none}
.trow{padding:3px 14px 3px 10px;gap:8px;flex-wrap:nowrap;white-space:nowrap}
.trow > *{flex:none}
.trow .f-args{flex:0 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis}
.trow .kind{font-size:10px;padding:1px 5px}
.tt{width:14px;display:inline-block;text-align:center;color:#8a9099;background:transparent;border:none;padding:0;font-size:11px}
.tname{background:transparent;border:none;padding:0;font:600 13px Menlo,monospace;color:#f2e6a8;cursor:pointer}
.tname:hover{text-decoration:underline}
.again-node .kind,.again-node .tname{opacity:.45}
.again{color:#7d858f;font:11px -apple-system,sans-serif}
.more{color:#7d858f;font:11px -apple-system,sans-serif}
.tcount{margin-left:auto;color:#8a9099;font:11px -apple-system,sans-serif}
.card.flash,.bl.flash{outline:2px solid #4a76a8}
.bl.flash{background:#2f4a66}
`;

/**
 * 頁の動き — 軸の札・tags のチップ・行の移動・畳む・1 行の切り替えは拡張へ送り、拡張の答えを描く(判断と覚えるのは拡張の側)。
 * source の開閉だけは頁の中で閉じる(覚えない)。
 */
const PAGE_SCRIPT = `
const vscode = acquireVsCodeApi();
document.addEventListener('click', (event) => {
  const target = event.target instanceof Element ? event.target.closest('[data-axis],[data-line],[data-src],[data-fold],[data-tree-root],[data-tree-dir],[data-reveal],[data-node-toggle],#clear,#fold-all,#unfold-all,#tree-more,#tree-close') : null;
  if (target === null) { return; }
  event.preventDefault();
  if (target.hasAttribute('data-node-toggle')) {
    const node = target.closest('.tn');
    if (node !== null) { const closed = node.classList.toggle('closed'); target.textContent = closed ? '▸' : '▾'; }
    return;
  }
  if (target.hasAttribute('data-tree-root')) { vscode.postMessage({ type: 'tree', root: target.getAttribute('data-tree-root'), direction: target.getAttribute('data-tree-dir') }); return; }
  if (target.hasAttribute('data-tree-dir')) { vscode.postMessage({ type: 'tree-direction', direction: target.getAttribute('data-tree-dir') }); return; }
  if (target.id === 'tree-more') { vscode.postMessage({ type: 'tree-more' }); return; }
  if (target.id === 'tree-close') { vscode.postMessage({ type: 'tree-close' }); return; }
  if (target.hasAttribute('data-reveal')) { vscode.postMessage({ type: 'reveal', qualifiedName: target.getAttribute('data-reveal') }); return; }
  if (target.id === 'clear') { vscode.postMessage({ type: 'clear' }); return; }
  if (target.id === 'fold-all') { vscode.postMessage({ type: 'fold-all' }); return; }
  if (target.id === 'unfold-all') { vscode.postMessage({ type: 'unfold-all' }); return; }
  if (target.hasAttribute('data-fold')) { vscode.postMessage({ type: 'fold', key: target.getAttribute('data-fold') }); return; }
  const card = target.closest('.card');
  const qualifiedName = card === null ? '' : card.getAttribute('data-qn');
  if (target.hasAttribute('data-src')) {
    if (card !== null && card.hasAttribute('data-lazy')) { vscode.postMessage({ type: 'hydrate', qualifiedName, source: true }); return; }
    const box = document.getElementById('src-' + target.getAttribute('data-src'));
    if (box !== null) { box.hidden = !box.hidden; target.classList.toggle('on', !box.hidden); }
    return;
  }
  if (target.hasAttribute('data-line')) {
    vscode.postMessage({ type: 'open', line: Number(target.getAttribute('data-line')), character: Number(target.getAttribute('data-character')), qualifiedName });
    return;
  }
  vscode.postMessage({ type: 'toggle', axis: target.getAttribute('data-axis'), value: target.getAttribute('data-value') });
});
let searchTimer;
document.addEventListener('input', (event) => {
  const target = event.target;
  if (target instanceof HTMLInputElement && target.id === 'search') {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => vscode.postMessage({ type: 'search', text: target.value }), 150);
  }
});
document.addEventListener('change', (event) => {
  const target = event.target;
  if (target instanceof HTMLInputElement && target.hasAttribute('data-line-field')) {
    vscode.postMessage({ type: 'line', field: target.getAttribute('data-line-field') });
  }
  if (target instanceof HTMLInputElement && target.id === 'tree-tests') { vscode.postMessage({ type: 'tree-tests' }); }
  if (target instanceof HTMLSelectElement && target.id === 'tree-root' && target.value !== '') { vscode.postMessage({ type: 'tree', root: target.value, direction: 'callees' }); }
});
window.addEventListener('message', (event) => {
  const message = event.data;
  if (message.type === 'filter') {
    document.getElementById('axes').innerHTML = message.axes;
    document.getElementById('summary').textContent = message.summary;
    const shown = new Set(message.visible);
    for (const card of document.querySelectorAll('.card')) { card.hidden = !shown.has(card.id); }
    window.scrollTo(0, 0);
    return;
  }
  if (message.type === 'fold') {
    const open = new Set(message.open);
    for (const card of document.querySelectorAll('.card')) {
      const isOpen = open.has(card.getAttribute('data-key'));
      card.classList.toggle('open', isOpen);
      const button = card.querySelector('[data-fold]');
      if (button !== null) { button.textContent = isOpen ? '▾' : '▸'; }
    }
    document.body.className = message.lineClasses;
    for (const box of document.querySelectorAll('[data-line-field]')) { box.checked = message.line.includes(box.getAttribute('data-line-field')); }
    return;
  }
  if (message.type === 'tree') {
    document.getElementById('tree').innerHTML = message.html;
    if (message.html !== '') { document.getElementById('tree').scrollIntoView({ block: 'start' }); }
    return;
  }
  if (message.type === 'card') {
    const old = document.getElementById(message.id);
    if (old !== null) {
      old.outerHTML = message.html;
      const box = document.getElementById('src-' + message.id);
      if (message.showSource && box !== null) { box.hidden = false; }
    }
    return;
  }
  if (message.type === 'cards') {
    document.getElementById('cards').innerHTML = message.html;
    document.getElementById('axes').innerHTML = message.axes;
    document.getElementById('summary').textContent = message.summary;
    if (message.scrollTop) { window.scrollTo(0, 0); }
    return;
  }
  if (message.type === 'reveal') {
    const card = document.getElementById(message.id);
    if (card !== null) {
      card.hidden = false;
      // 行があれば本体のその行へ(本体に無い行 — 頭・契約 — ならカードへ)
      const row = message.line === null ? null : card.querySelector('[data-src-line="' + message.line + '"]');
      const target = row === null ? card : row;
      target.scrollIntoView({ block: 'center' });
      target.classList.add('flash');
      setTimeout(() => target.classList.remove('flash'), 1600);
    }
  }
});
`;
