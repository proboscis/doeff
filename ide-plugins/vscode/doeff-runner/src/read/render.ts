// 定義を読む面の HTML を組む純粋な関数(VS Code に触らない)。カード = 定義などの実体 1 つの部品を HTML で見せる物で、
// 構文はなぞらない(v2・operator 2026-09-28 "so we are not to follow syntax like using. we want better html visualization of
// entity like defk")。見本 = docs/design/hy-reading-plane/artifacts/v5/entity.html(左に軸・上に 1 行の切り替え・右に実体のカード)。
// カードは 1 行に畳める(v4)。面に出る文字は labels.ts の表からだけ引く(v5)。
// 本体の文字(val / var の表)は linter の印字が入るまで出さず、元の Hy は実体ごとの source ボタンで開閉する(v3 3 節)。

import type { HyDefinition } from '../hy/contract';
import { pieceStyle, PLAIN, sliceHighlight, type Piece, type SourceColoring } from '../hy/highlight/spans';
import { LINT_LEVELS, type LintBody, type LintBodySegment, type LintSignature, type LintViolation } from '../lint/contract';
import { LEVEL_COLORS } from '../lint/severity';
import { displayRange, WHOLE_LINE } from '../lint/view';
import { answerText, headerEffects, typeText } from '../defk/model';
import { cardKey, LINE_FIELDS, type FoldState, type LineField } from './fold';
import { docFirstLine, escapeHtml, tagClass, type Glyphs } from './html';
import { contractRow, declaredEffectChips, decoratorBadges, effectChip, entityLineArgs, entityRows, indexSignatureRows, relationBand, usedByRow, type ChipContext } from './entity';
import { effectHover, nameHover, nameScope, violationTipHtml, type NameScope, type RuleTitles } from './hover';
import { effectRef, entityLink, linkPieces, resolveEntity, sourceLinks, typeHtml, type EntityRef } from './resolve';
import { LABELS } from './labels';
import { NAMES_ONLY_PARAMS, TALL_SIGNATURE_CHARS, TALL_SIGNATURE_PARAMS } from './layout';
import { axisKey, axisTitle, facets, SEARCH_KEY, visibleCards, worstLevel, type Card, type CardPlacement, type Facet, type Selection } from './model';
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

/** 引数が多いか型が長い見出しか(閾は layout.ts — 引数の数と、引数と return type の型の文字の合計)(1 行のチップでは return type が折り返しに埋もれるので、縦の表で描くため)。 */
export function isTallSignature(signature: LintSignature): boolean {
  const chars = signature.params.reduce((n, p) => n + typeText(p.type).length, 0) + answerText(signature).length;
  return signature.params.length >= TALL_SIGNATURE_PARAMS || chars > TALL_SIGNATURE_CHARS;
}

/** 型を候補ごとの小さなチップにする(union の `|` は薄く・None は破線で弱く — 候補の切れ目を見せるため)。 */
function typeChips(type: LintSignature['answer'], graph: CallGraph): string {
  const members = type !== null && type.kind === 'union' ? type.members : [type];
  return members
    .map((m) => {
      const text = typeText(m);
      return `<span class="${text === 'None' ? 'none' : ''}">${typeHtml(m, graph)}</span>`;
    })
    .join('<i>|</i>');
}

/** 答えの型の文字(answerText と同じ綴り — 名の型は押せる)。 */
function answerHtml(signature: LintSignature, graph: CallGraph): string {
  return signature.absent ? `Maybe[${typeHtml(signature.answer, graph)}]` : typeHtml(signature.answer, graph);
}

/** 引数と答え(defk / deff の見出しから・開いたカード)— 短ければ 1 行のチップ、長ければ縦の表(v6)。 */
function signatureStrip(signature: LintSignature, graph: CallGraph): string {
  if (isTallSignature(signature)) {
    const rows = signature.params.map((p) => `<span class="n">${escapeHtml(p.name)}</span><span class="tc">${typeChips(p.type, graph)}</span>`).join('');
    const answer = signature.absent ? `<span>${answerHtml(signature, graph)}</span>` : typeChips(signature.answer, graph);
    return `<div class="sig2"><div class="lab">${escapeHtml(LABELS.args)}</div>${rows}<div class="rt"><span class="k">${escapeHtml(LABELS.returnType)}</span><span class="tc">${answer}</span></div></div>`;
  }
  const params = signature.params
    .map((p) => `<span class="p"><span class="n">${escapeHtml(p.name)}</span><span class="t">${typeHtml(p.type, graph)}</span></span>`)
    .join('');
  return `<div class="sig">${params === '' ? `<span class="none">${escapeHtml(LABELS.noArgs)}</span>` : params}<span class="arrow">→</span><span class="ret" title="${escapeHtml(LABELS.returnType)}">${answerHtml(signature, graph)}</span></div>`;
}

/** effect のチップ(絵つき・Raise は赤い札・hover に引数と答えと説明の 1 行目 — 名は定義へ押せる)。 */
function effectChips(signature: LintSignature, declared: HyDefinition['effects'], ctx: ChipContext): string {
  const targets = new Map((declared ?? []).map((d) => [d.name, d.target]));
  return headerEffects(signature)
    .map((e) => {
      // linter の位置 → 索引の宣言の target(linter が解かない effect の class — agora の EffectBase の defclass)
      const location: EntityRef = { tag: 'first', refs: [{ tag: 'location', location: e.definition }, { tag: 'target', target: targets.get(e.name) ?? null }] };
      if (e.kind === 'raise') {
        return entityLink(`Raise ${escapeHtml(e.name)}`, resolveEntity(location, ctx.graph), 'eff raise');
      }
      return effectChip(e.name, location, ctx);
    })
    .join('');
}

/** effects の欄(開いたカード)。effect が無ければ欄ごと出さない。 */
function effectRow(signature: LintSignature, declared: HyDefinition['effects'], ctx: ChipContext): string {
  const chips = effectChips(signature, declared, ctx);
  const partial = signature.inferenceComplete ? '' : `<span class="none">(${escapeHtml(LABELS.inferencePartial)})</span>`;
  if (chips === '') {
    return signature.inferenceComplete ? '' : `<div class="row"><span class="k">${escapeHtml(LABELS.effects)}</span><div>${partial}</div></div>`;
  }
  return `<div class="row"><span class="k">${escapeHtml(LABELS.effects)}</span><div>${chips}${partial}</div></div>`;
}

/** 1 行の形の「args / return type」— `(name: T, …) → R`(見出しの無い実体は入れ子の定義の名・引数の名)。 */
function lineArgs(card: Card, graph: CallGraph): string {
  const s = card.signature;
  if (s !== undefined) {
    const answer = `<span class="r">${answerHtml(s, graph)}</span>`;
    if (s.params.length >= NAMES_ONLY_PARAMS) {
      // 引数が多い時は名だけ(型は hover で)。return type は常に出す(v6 2.2 節)
      const typed = s.params.map((p) => `${p.name}: ${typeText(p.type)}`).join('\n');
      return `<span class="f f-args" title="${escapeHtml(typed)}">(${s.params.map((p) => escapeHtml(p.name)).join(', ')}) → ${answer}</span>`;
    }
    const params = s.params.map((p) => `${escapeHtml(p.name)}: <span class="t">${typeHtml(p.type, graph)}</span>`).join(', ');
    return `<span class="f f-args">(${params}) → ${answer}</span>`;
  }
  return entityLineArgs(card, graph);
}

/** カードを描く材料(カード以外)。 */
export interface CardContext {
  readonly glyphs: Glyphs;
  readonly fold: FoldState;
  /** 索引の全 file の呼び出しの表(関係の数と木の材料) */
  readonly graph: CallGraph;
  /** カードの file 全体の色(editor と同じ文法・theme・記号ごとの色。まだ塗れていなければ undefined) */
  readonly coloringOf: (card: Card) => SourceColoring | undefined;
  /** 規則の ID → 短い名(linter の rules[].title — 違反の吹き出しの見出し) */
  readonly ruleTitles: RuleTitles;
}

/** 違反の印 1 つが指す違反と、その吹き出しの template の id(同じ違反の印は、どこに描いても同じ吹き出しを指す — v13)。 */
interface TipMark {
  readonly violation: LintViolation;
  readonly tip: string;
}

/** カードの中の違反の印(置き場つき)。 */
interface CardMark extends TipMark {
  readonly place: CardPlacement;
}

/** 印の並びが指す吹き出しの id(空白で区切る — webview は順に写す)。 */
function tipIds(marks: readonly TipMark[]): string {
  return marks.map((m) => m.tip).join(' ');
}

/** 規則の ID の札(重大さの色・hover で吹き出し)。text を渡せばその文字(file の帯の行番号つき)。 */
function markBadge(mark: TipMark, text: string = mark.violation.rule): string {
  return `<span class="viol viol-${mark.violation.level}" data-tip="${escapeHtml(mark.tip)}">${escapeHtml(text)}</span>`;
}

/** 印の並びの一番重い重さの色で下線を引く(印が無ければそのまま)。 */
function underlined(html: string, marks: readonly TipMark[]): string {
  const level = worstLevel(marks.map((m) => m.violation));
  return level === undefined ? html : `<span class="vmark vm-${level}" data-tip="${escapeHtml(tipIds(marks))}">${html}</span>`;
}

/** 吹き出しの中身(印 1 つにつき template 1 つ — 中身は hover.ts の純粋な関数が組む)。 */
function tipTemplates(marks: readonly TipMark[], titles: RuleTitles): string {
  return marks.map((m) => `<template id="${escapeHtml(m.tip)}">${violationTipHtml(m.violation, titles.get(m.violation.rule) ?? null)}</template>`).join('');
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
    // 註の行 comment(linter の U20a で足した役)— 色は面の担当が決めるまで既定の字
    case 'name':
    case 'assign':
    case 'text':
    case 'comment':
    case null:
      return '';
    default: {
      const unreachable: never = role;
      throw new Error(`網羅されていない字の役: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 本体の字の中の名(Hy の識別子 — `.` の属性は切る)。 */
const BODY_NAME = /([A-Za-z_][\w\-?!*]*)/;

/** 本体の 1 行を描く材料 — 絵と索引(effect の hover)・定義の名の表(名の hover)・その行(0 始まり)。 */
interface LineContext {
  readonly chips: ChipContext;
  readonly scope: NameScope;
  readonly line: number;
  /** 本体の file の path(linter が位置を解かなかった字を、その位置の索引の呼び出しで引くため) */
  readonly path: string;
}

/**
 * 名や字の並び(色の無い字)— 束縛か引数の名には、hover に型を出す印を付ける(v1 2.5 節「束縛の型の hover」。その行までの
 * 最後の束縛の型、束縛でなければ引数の型)。
 */
function namedText(text: string, at: LineContext): string {
  return text
    .split(BODY_NAME)
    .map((piece, i) => {
      const title = i % 2 === 1 ? nameHover(at.scope, piece, at.line) : undefined;
      return title === undefined ? escapeHtml(piece) : `<span class="var" title="${escapeHtml(title)}">${escapeHtml(piece)}</span>`;
    })
    .join('');
}

/**
 * 本体の字 1 つが指す定義の候補(v12 — 型・effect・呼び・名の役で、linter が定義の位置を解いた物。effect は同名の defeffect でも引く)。
 * lisp の目印の字と、役の無い字は押せない(lisp は書かれたまま — v1 制約 2)。
 */
function segmentTargets(segment: LintBodySegment, graph: CallGraph, path: string): readonly string[] {
  // linter の位置 → その位置の索引の呼び出しの target(linter が解かない effect の class・import した呼び)
  const at: readonly EntityRef[] =
    segment.range === null ? [] : [{ tag: 'call-at', path, line: segment.range.start.line, character: segment.range.start.character }];
  const location: EntityRef = { tag: 'first', refs: [{ tag: 'location', location: segment.definition }, ...at] };
  switch (segment.role) {
    case 'effect':
      return resolveEntity(effectRef(segment.effect ?? segment.text, location), graph);
    case 'type':
    case 'call':
    case 'name':
      return resolveEntity(location, graph);
    case 'keyword':
    case 'unknown-type':
    case 'bind':
    case 'assign':
    case 'text':
    case 'lisp':
    case 'comment':
    case null:
      return [];
    default: {
      const unreachable: never = segment.role;
      throw new Error(`網羅されていない字の役: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 字 1 つ(effect は絵を添え、hover に effect の中身。名は hover に型。定義に当たる字は押せる — v12)。 */
function renderSegment(segment: LintBodySegment, at: LineContext): string {
  const cls = segmentClass(segment.role);
  const effect = segment.role === 'effect' ? segment.effect : null;
  const img = effect === null ? undefined : at.chips.glyphs.effect(effect);
  const glyph = img === undefined ? '' : `<img class="gl" src="${escapeHtml(img)}" alt="">`;
  const targets = segmentTargets(segment, at.chips.graph, at.path);
  if (cls === '') {
    return `${glyph}${entityLink(namedText(segment.text, at), targets)}`;
  }
  const title = segment.role === 'lisp' ? LABELS.lispAsIs : effect === null ? undefined : effectHover(effect, at.chips.graph);
  return entityLink(`${glyph}${escapeHtml(segment.text)}`, targets, cls, title);
}

/**
 * 本体の文字(v2 2.2 節・v3 2 節 — operator 承認 "yeah val var when match is perfect.")— 行番号 = source の行、字下げ = 段、
 * effect を通す束縛(⇐)の行は薄い背景、本体の setv は警告の印。組み立ては linter の bodies(読み方の正本は linter)。
 */
function bodyBlock(body: LintBody, chips: ChipContext, marks: readonly CardMark[], scope: NameScope): string {
  if (body.lines.length === 0) {
    return '';
  }
  // 本体の行に置いた違反(placeOf の body)は、その source の行を描く本体の行へ(v1 制約 4 — 問題の欄は source 側のまま)。
  // 行番号 → その行の印の索引
  const byLine = new Map<number, CardMark[]>();
  for (const mark of marks) {
    if (mark.place.tag === 'body') {
      byLine.set(mark.place.line, [...(byLine.get(mark.place.line) ?? []), mark]);
    }
  }
  const lines = body.lines
    .map((line) => {
      const bound = line.segments.some((s) => s.role === 'bind');
      const warning =
        line.warning === null ? '' : `<span class="warn" title="${escapeHtml(line.warning.message)}">⚠ ${escapeHtml(line.warning.kind ?? LABELS.warning)}</span>`;
      const badges = (byLine.get(line.line) ?? []).map((m) => markBadge(m)).join('');
      const text = `${'  '.repeat(line.depth)}${' '.repeat(line.pad)}${line.segments.map((s) => renderSegment(s, { chips, scope, line: line.line, path: body.path })).join('')}`;
      return `<div class="${bound ? 'bl bound' : 'bl'}" data-src-line="${line.line + 1}"><span class="ln">${line.line + 1}</span>${text}${warning}${badges}</div>`;
    })
    .join('');
  return `<div class="body">${lines}</div>`;
}

/** 色つきの 1 片の HTML(色も字の形も無ければ文字だけ)。 */
function pieceHtml(piece: Piece): string {
  const style = pieceStyle(piece);
  return style === '' ? escapeHtml(piece.text) : `<span style="${style}">${escapeHtml(piece.text)}</span>`;
}

/** source の 1 行に掛かる違反の下線 1 本(列は UTF-16 の [start, end))。 */
interface LineMark {
  readonly start: number;
  readonly end: number;
  readonly mark: TipMark;
}

/**
 * source の行 line に掛かる違反の下線(v13 の 4 — source の箱の範囲に重大さの色の下線)。範囲が空(file の先頭の置き場の
 * 違反など)なら行全体、複数行の範囲なら間の行は行全体(違反の表の editor の選択と同じ displayRange)。
 */
function marksOnLine(marks: readonly TipMark[], line: number): LineMark[] {
  return marks.flatMap((mark) => {
    const range = displayRange(mark.violation.range, undefined);
    if (line < range.start.line || line > range.end.line) {
      return [];
    }
    const start = line === range.start.line ? range.start.character : 0;
    const end = line === range.end.line ? range.end.character : WHOLE_LINE;
    return [{ start, end, mark }];
  });
}

/** 色つきの片に、その片に掛かる違反の印を添えた物。 */
interface MarkedPiece extends Piece {
  readonly marks: readonly TipMark[];
}

/**
 * 1 行の片を下線の境で割り、片ごとに掛かる違反の印を添える(offset = 片の並びの頭の列)— source の箱で、違反の範囲だけに
 * 下線を引くため(色の片・定義の link の境とは別に割れる)。
 */
function splitAtMarks(pieces: readonly Piece[], offset: number, spans: readonly LineMark[]): MarkedPiece[] {
  const out: MarkedPiece[] = [];
  let column = offset;
  for (const piece of pieces) {
    const from = column;
    const to = column + piece.text.length;
    const cuts = [...new Set([from, to, ...spans.flatMap((s) => [s.start, s.end]).filter((c) => c > from && c < to)])].sort((a, b) => a - b);
    for (let i = 0; i + 1 < cuts.length; i += 1) {
      const [a, b] = [cuts[i], cuts[i + 1]];
      const marks = spans.filter((s) => s.start <= a && b <= s.end).map((s) => s.mark);
      out.push({ text: piece.text.slice(a - from, b - from), color: piece.color, fontStyle: piece.fontStyle, marks });
    }
    column = to;
  }
  return out;
}

/**
 * 元の Hy(source の行番号つき・読むだけ)。file 全体の色があれば定義の範囲を切り出して editor と同じ色で描き
 * (記号ごとの色は file 全体の記号の順で決まるので、切り出してから塗らない)、無ければ色なしの文字で描く。
 * カードの違反(頭・本体の両方)は、その範囲に重大さの色の下線を引く(箱を開いた時だけ見える — v13 の 4)。
 */
function sourceBox(card: Card, fileLabel: string, coloring: SourceColoring | undefined, graph: CallGraph, marks: readonly TipMark[]): string {
  const lines = card.source.split('\n');
  const last = card.firstLine + lines.length - 1;
  const pieces = coloring === undefined ? undefined : sliceHighlight(coloring.lines, coloring.spans, card.definition.fullRange);
  const colored = pieces !== undefined && pieces.length === lines.length ? pieces : undefined;
  // 記号のうち定義に当たる物は押せる(v12)— 引数と束縛の名は局所の名なので引かない
  const filePath = graph.definitions.get(card.definition.qualifiedName)?.path;
  const locals = new Set([...card.definition.params, ...card.bindings.map((b) => b.name), ...(card.signature?.params.map((p) => p.name) ?? [])]);
  const links = filePath === undefined || card.source === '' ? [] : sourceLinks(filePath, card.definition.fullRange, graph, locals);
  const body = (text: string, i: number): string => {
    const line = card.definition.fullRange.start.line + i;
    const offset = i === 0 ? card.definition.fullRange.start.character : 0;
    const here = links.filter((l) => l.line === line);
    const plain: readonly Piece[] = [{ text, color: null, fontStyle: PLAIN }];
    const pieces = splitAtMarks(colored === undefined ? plain : colored[i], offset, marksOnLine(marks, line));
    return linkPieces(pieces, offset, here, (piece, cut) => underlined(pieceHtml({ text: cut, color: piece.color, fontStyle: piece.fontStyle }), piece.marks));
  };
  // 行の目印 data-hy-line は source の行(1 始まり)— 違反の項目から来た時にその行へ送るため(本体の行の data-src-line とは別の名)
  const numbered = lines.map((text, i) => `<div data-hy-line="${card.firstLine + i}"><span class="ln">${card.firstLine + i}</span>${body(text, i)}</div>`).join('');
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
  // 違反の印 — 頭(名に下線と札)・本体の行(札)・source の範囲(下線)・足(数)。どの印も同じ吹き出しを指す(v13)
  const marks: CardMark[] = card.violations.map((p, i) => ({ violation: p.violation, place: p.place, tip: `tip-${card.id}-${i}` }));
  const headMarks = marks.filter((m) => m.place.tag === 'head');
  const name = `${underlined(`<span class="name">${escapeHtml(d.name)}</span>`, headMarks)}${headMarks.map((m) => markBadge(m)).join('')}`;
  const head = `<div class="hd"><span class="kind k-${escapeHtml(d.kind)}">${escapeHtml(d.kind)}</span>${name}${decoratorBadges(d)}${toggle}${buttons}<span class="chips full-only">${tagChips('chip')}</span></div>`;
  const relation = relationOf(ctx.graph, d.qualifiedName);
  const relationText = `${LABELS.callers} <b>${relation.callers}</b> · ${LABELS.tests} <b>${relation.tests}</b>`;
  // 帯の callers / callees は木の入口(v7 3 節の入口 a)
  const qn = escapeHtml(d.qualifiedName);
  const band = relationBand(card, ctx.graph);
  const location = `${escapeHtml(card.place)}:${card.firstLine}`;
  const doc = docFirstLine(d.docstring);
  const effects = card.signature === undefined ? declaredEffectChips(d, ctx) : effectChips(card.signature, d.effects, ctx);
  const line = [
    lineArgs(card, ctx.graph),
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
      ? signatureStrip(card.signature, ctx.graph) + effectRow(card.signature, d.effects, ctx)
      : typed
        ? indexSignatureRows(d, ctx)
        : entityRows(card, ctx)) + contractRow(d);
  const docBlock = d.docstring === null ? '' : `<div class="doc">${escapeHtml(d.docstring)}</div>`;
  const usedBy = usedByRow(card, ctx.graph);
  const body = card.body === undefined ? '' : bodyBlock(card.body, ctx, marks, nameScope(card.bindings, card.signature));
  const level = worstLevel(card.violations.map((p) => p.violation));
  const violations =
    level === undefined
      ? ''
      : `<span class="viol viol-${level}" data-tip="${escapeHtml(tipIds(marks))}">${escapeHtml(LABELS.violations)} ${card.violations.length}</span>`;
  const foot = `<div class="ft">${band}${violations}<span class="loc">${location}</span></div>`;
  const fileLabel = card.place.split('/').pop() ?? card.place;
  // source を持たないカード(repo 全体の面の索引だけのカード)は、開く・source を押すとその file を読み込む(v3 3 節 — 索引の位置から切り出す)
  const lazy = card.source === '' ? ' data-lazy' : '';
  const classes = open ? 'card open' : 'card';
  // 吹き出しの中身はカードの中に置く(カードだけを描き直して送る時も、印と吹き出しがずれないため)
  return `<section class="${classes}" id="${card.id}" data-key="${escapeHtml(key)}" data-qn="${qn}"${lazy}${hidden ? ' hidden' : ''}>${head}<div class="line">${line}</div><div class="full">${middle}${docBlock}${usedBy}${body}</div>${sourceBox(card, fileLabel, ctx.coloringOf(card), ctx.graph, marks)}<div class="full">${foot}</div>${tipTemplates(marks, ctx.ruleTitles)}</section>`;
}

/** 面の状態 — 索引にその file が無い時・設定で切った時は理由を出す。 */
export type PlaneState =
  /** file 1 つの面 — カードと、どのカードにも置けなかった違反(band — file の見出しの帯・model の bandViolations) */
  | { readonly tag: 'cards'; readonly cards: readonly Card[]; readonly band: readonly LintViolation[]; readonly selection: Selection }
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
  /** 規則の ID → 短い名(linter の rules[].title — 違反の吹き出しの見出し) */
  readonly ruleTitles: RuleTitles;
  /** webview の CSP の出どころ(`webview.cspSource`) */
  readonly cspSource: string;
  /** script に付ける 1 回限りの数 */
  readonly nonce: string;
}

/**
 * file の見出しの帯 — どの定義の範囲にも入らない違反(file の先頭の import の向き・置き場・宣言にない依存と、索引と linter の
 * 版のずれで範囲に入らない物)の札を並べる(v13 の 1(c) — linter が出した違反を読む面から消さない)。無ければ空。
 */
export function renderFileBand(violations: readonly LintViolation[], titles: RuleTitles): string {
  const level = worstLevel(violations);
  if (level === undefined) {
    return '';
  }
  const marks: TipMark[] = violations.map((violation, i) => ({ violation, tip: `tip-file-${i}` }));
  const badges = marks.map((m) => markBadge(m, `${m.violation.rule} :${m.violation.range.start.line + 1}`)).join('');
  return `<div class="fileband band-${level}"><span class="k">${escapeHtml(LABELS.fileLevel)} ${escapeHtml(LABELS.violations)}</span>${badges}${tipTemplates(marks, titles)}</div>`;
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
  return { glyphs, showTests, graph, signatureOf: (qn) => signatures.get(qn), handlersOf: (qn) => graph.handlers.get(qn) ?? 0 };
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
    coloringOf: input.state.tag === 'workspace' ? input.state.coloringOf : () => input.coloring,
    ruleTitles: input.ruleTitles
  };
  const content = (() => {
    switch (input.state.tag) {
      case 'message':
        return { axes: '', summary: '', band: '', bar: '', picker: '', tree: '', cards: `<p class="message">${escapeHtml(input.state.text)}</p>` };
      case 'cards': {
        const { cards, band, selection } = input.state;
        const shown = new Set(visibleCards(cards, selection).map((c) => c.id));
        const all = facets(cards, selection);
        return {
          axes: renderFacets(all, Number.POSITIVE_INFINITY),
          summary: summaryText(shown.size, cards.length, all),
          band: renderFileBand(band, input.ruleTitles),
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
          band: '',
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
${content.band}
${content.bar}
</div>
<div id="tree">${content.tree}</div>
<div id="cards">${content.cards}</div>
</main>
<script nonce="${input.nonce}">${PAGE_SCRIPT}</script>
</body>
</html>`;
}

/**
 * 重大さの色の CSS — 札の地と字・名と source の範囲の下線・file の帯の縁。色は lint/severity.ts の 1 つの表からだけ引く
 * (面に色の表を増やさない — agora-redesign #1685 の 5)。
 */
const LEVEL_STYLE = LINT_LEVELS.map((level) => {
  const c = LEVEL_COLORS[level];
  return `.viol-${level}{background:${c.background};color:${c.foreground}}.vm-${level}{text-decoration:underline wavy ${c.underline};text-decoration-skip-ink:none;text-underline-offset:3px}.band-${level}{border-left:3px solid ${c.underline}}`;
}).join('\n');

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
${LEVEL_STYLE}
.viol[data-tip],.vmark{cursor:help}
.hd .viol{font-family:-apple-system,sans-serif}
.fileband{display:flex;gap:8px;align-items:center;flex-wrap:wrap;background:#22252a;border:1px solid #33383f;border-radius:8px;padding:5px 12px;margin-bottom:10px;font-size:12px}
.fileband .k{color:#8a9099;font-size:11.5px}
.vt-pop{position:fixed;z-index:20;max-width:560px;max-height:60vh;overflow-y:auto;box-sizing:border-box;background:#16181c;border:1px solid #454a52;border-radius:8px;box-shadow:0 6px 20px rgba(0,0,0,.55);padding:8px 12px;font:12.5px/1.6 -apple-system,"Hiragino Sans",sans-serif;color:#d6d8dc}
.vt-pop[hidden]{display:none}
.vt + .vt{border-top:1px solid #33383f;margin-top:8px;padding-top:8px}
.vt-h{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
.vt-h b{font:600 12.5px Menlo,monospace;color:#f2e6a8}
.vt-t{color:#e6e9ee}
.vt-h .viol{margin-left:auto}
.vt-m{color:#8a9099;font-size:11.5px}
.vt-msg{margin-top:4px;white-space:pre-wrap}
.vt-r{display:grid;grid-template-columns:84px minmax(0,1fr);gap:8px;margin-top:4px;white-space:pre-wrap}
.vt-r .k{color:#8a9099;font-size:11.5px}
.vt-b{display:flex;gap:6px;margin-top:8px}
.message{color:#8a9099;margin-top:24px}
.rel{background:transparent;border:none;color:#b8bec7;font-size:12px;padding:0;text-decoration:underline dotted #5f6670}
.rel:hover{color:#dfeeff}
.relgroup{display:inline-flex;gap:4px;align-items:baseline}
.tname-sm{background:transparent;border:none;padding:0;font:12px Menlo,monospace;color:#c9ced5;cursor:pointer}
.tname-sm:hover{color:#f2e6a8;text-decoration:underline}
.tname-sm .qual{color:#7d858f;font-size:10.5px;margin-left:4px}
.use{display:inline-flex;gap:6px;align-items:baseline;margin:0 14px 4px 0}
.use b{color:#8a9099;font-weight:500}
.deco{font:10.5px Menlo,monospace;border-radius:4px;padding:1px 6px;border:1px solid #5a4a8a;color:#c3b6ff;background:#2a2440}
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
.ent{cursor:pointer}
.ent:hover{text-decoration:underline}
.var{cursor:help;border-radius:3px}
.ent .var{cursor:pointer}
.var:hover{background:#2a3340}
.srcbox .code div.flash{outline:2px solid #4a76a8;background:#2f4a66}
`;

/**
 * 頁の動き — 軸の札・tags のチップ・行の移動・畳む・1 行の切り替えは拡張へ送り、拡張の答えを描く(判断と覚えるのは拡張の側)。
 * source の開閉だけは頁の中で閉じる(覚えない)。
 */
const PAGE_SCRIPT = `
const vscode = acquireVsCodeApi();
// 頁を丸ごと描き直しても(source の色が塗れた時・file が変わった時)、読んでいた位置と開いた source の箱を戻すため — webview の
// state に覚える(state は頁を差し替えても残る)
const view = vscode.getState() || { scrollY: 0, sources: [] };
function rememberSource(id, open) {
  view.sources = view.sources.filter((s) => s !== id).concat(open ? [id] : []);
  vscode.setState(view);
}
for (const id of view.sources) {
  const box = document.getElementById('src-' + id);
  const button = document.querySelector('[data-src="' + id + '"]');
  if (box !== null) { box.hidden = false; }
  if (button !== null) { button.classList.add('on'); }
}
window.scrollTo(0, view.scrollY);
// 違反の印の吹き出し(v13 — 頭・本体・source・帯・足のどの印からも同じ吹き出し)。中身は拡張が組んだ template(印の
// data-tip が指す id)を写すだけで、ここでは文を作らない。印から吹き出しへ指を動かせるよう、離れてから少し待って閉じる
const pop = document.createElement('div');
pop.className = 'vt-pop';
pop.hidden = true;
document.body.appendChild(pop);
let popOwner = null;
let popTimer;
function hidePop() { clearTimeout(popTimer); pop.hidden = true; popOwner = null; }
function showPop(mark) {
  const html = mark.getAttribute('data-tip').split(' ').map((id) => { const t = document.getElementById(id); return t === null ? '' : t.innerHTML; }).join('');
  if (html === '') { return; }
  pop.innerHTML = html;
  pop.hidden = false;
  popOwner = mark;
  const r = mark.getBoundingClientRect();
  const left = Math.max(8, Math.min(r.left, window.innerWidth - pop.offsetWidth - 8));
  const above = r.top - pop.offsetHeight - 4;
  const top = r.bottom + 4 + pop.offsetHeight > window.innerHeight - 8 && above > 8 ? above : r.bottom + 4;
  pop.style.left = left + 'px';
  pop.style.top = top + 'px';
}
document.addEventListener('mouseover', (event) => {
  const el = event.target instanceof Element ? event.target : null;
  const mark = el === null ? null : el.closest('[data-tip]');
  if (mark !== null) {
    clearTimeout(popTimer);
    if (mark !== popOwner) { showPop(mark); }
    return;
  }
  if (el !== null && el.closest('.vt-pop') !== null) { clearTimeout(popTimer); return; }
  if (popOwner !== null) { clearTimeout(popTimer); popTimer = setTimeout(hidePop, 250); }
});
let scrollTimer;
window.addEventListener('scroll', () => {
  hidePop();
  clearTimeout(scrollTimer);
  scrollTimer = setTimeout(() => { view.scrollY = window.scrollY; vscode.setState(view); }, 100);
});
document.addEventListener('click', (event) => {
  const target = event.target instanceof Element ? event.target.closest('[data-vopen],[data-vlist],[data-axis],[data-line],[data-src],[data-fold],[data-tree-root],[data-tree-dir],[data-reveal],[data-node-toggle],#clear,#fold-all,#unfold-all,#tree-more,#tree-close') : null;
  if (target === null) { return; }
  event.preventDefault();
  // 吹き出しのボタン — 違反の目印(拡張が JSON で持たせた物)をそのまま送り返す(open in editor / show in violations)
  if (target.hasAttribute('data-vopen') || target.hasAttribute('data-vlist')) {
    const holder = target.closest('[data-vref]');
    if (holder !== null) {
      vscode.postMessage({ type: target.hasAttribute('data-vopen') ? 'violation-open' : 'violation-list', ref: JSON.parse(holder.getAttribute('data-vref')) });
    }
    hidePop();
    return;
  }
  if (target.hasAttribute('data-node-toggle')) {
    const node = target.closest('.tn');
    if (node !== null) { const closed = node.classList.toggle('closed'); target.textContent = closed ? '▸' : '▾'; }
    return;
  }
  if (target.hasAttribute('data-tree-root')) { vscode.postMessage({ type: 'tree', root: target.getAttribute('data-tree-root'), direction: target.getAttribute('data-tree-dir') }); return; }
  if (target.hasAttribute('data-tree-dir')) { vscode.postMessage({ type: 'tree-direction', direction: target.getAttribute('data-tree-dir') }); return; }
  if (target.id === 'tree-more') { vscode.postMessage({ type: 'tree-more' }); return; }
  if (target.id === 'tree-close') { vscode.postMessage({ type: 'tree-close' }); return; }
  // 実体の名 — 素の click はカードへ、Cmd / Ctrl + click は editor の定義へ(v12)。値は候補の完全修飾名を空白で区切った物
  if (target.hasAttribute('data-reveal')) {
    const qualifiedNames = target.getAttribute('data-reveal').split(' ').filter((qn) => qn !== '');
    vscode.postMessage({ type: 'reveal', qualifiedNames, editor: event.metaKey || event.ctrlKey });
    return;
  }
  if (target.id === 'clear') { vscode.postMessage({ type: 'clear' }); return; }
  if (target.id === 'fold-all') { vscode.postMessage({ type: 'fold-all' }); return; }
  if (target.id === 'unfold-all') { vscode.postMessage({ type: 'unfold-all' }); return; }
  if (target.hasAttribute('data-fold')) { vscode.postMessage({ type: 'fold', key: target.getAttribute('data-fold') }); return; }
  const card = target.closest('.card');
  const qualifiedName = card === null ? '' : card.getAttribute('data-qn');
  if (target.hasAttribute('data-src')) {
    if (card !== null && card.hasAttribute('data-lazy')) { vscode.postMessage({ type: 'hydrate', qualifiedName, source: true }); return; }
    const box = document.getElementById('src-' + target.getAttribute('data-src'));
    if (box !== null) { box.hidden = !box.hidden; target.classList.toggle('on', !box.hidden); rememberSource(target.getAttribute('data-src'), !box.hidden); }
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
      if (message.showSource && box !== null) { box.hidden = false; rememberSource(message.id, true); }
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
  if (message.type === 'top') {
    window.scrollTo(0, 0);
    return;
  }
  if (message.type === 'reveal') {
    const card = document.getElementById(message.id);
    if (card !== null) {
      card.hidden = false;
      // 行があれば本体のその行へ(本体に無い行 — 頭・契約 — ならカードへ)
      const row = message.line === null ? null : card.querySelector('[data-src-line="' + message.line + '"]');
      // 違反の項目から来た時は source の箱も開き、source のその行へ送る(違反は source の行の話 — 本体の行も光らせる)
      const box = message.showSource ? document.getElementById('src-' + message.id) : null;
      if (box !== null) {
        box.hidden = false;
        rememberSource(message.id, true);
        const button = card.querySelector('[data-src]');
        if (button !== null) { button.classList.add('on'); }
      }
      const srcRow = box === null || message.line === null ? null : box.querySelector('[data-hy-line="' + message.line + '"]');
      const lit = [row, srcRow].filter((el) => el !== null);
      const targets = lit.length === 0 ? [card] : lit;
      (srcRow ?? row ?? card).scrollIntoView({ block: 'center' });
      for (const target of targets) {
        target.classList.add('flash');
        setTimeout(() => target.classList.remove('flash'), 1600);
      }
    }
  }
});
// 知らせを受けられるようになったことを面へ伝える(それまでに面が送った知らせは面が溜めていて、ここで届く)
vscode.postMessage({ type: 'ready' });
`;
