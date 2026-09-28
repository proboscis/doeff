// 実体の種類ごとの欄と下の帯(v2 2.1 節の表・agora-redesign #910 U4)の HTML を組む純粋な関数(VS Code に触らない)。
// 材料は hy-index 版 5(欄の型・答えの型・宣言した effect・effect 節の handles・型でない契約)と、呼び出しの表の逆引き。
// defk / deff の見出しは linter の signature が正本なので、ここは見出しの無い実体(defeffect・defrecord・defenum・defhandler・
// 最上位の変数 ほか)と、全部の実体に共通の「契約」の欄と下の帯を受け持つ。

import type { HyDefinition, HyTypeNote } from '../hy/contract';
import { effectHover } from './hover';
import { escapeHtml, type Glyphs } from './html';
import { LABELS } from './labels';
import { NAMES_ONLY_PARAMS, TALL_SIGNATURE_CHARS, TALL_SIGNATURE_PARAMS } from './layout';
import type { Card } from './model';
import { calleesInTree, indexTypeText, indexUnionMembers, type CallGraph } from './tree';

/** 欄 1 つの行。 */
function row(label: string, body: string): string {
  return `<div class="row"><span class="k">${escapeHtml(label)}</span><div>${body}</div></div>`;
}

/** 名と型のチップ(型が無ければ名だけ)。 */
function nameTypeChip(name: string, type: HyTypeNote | undefined): string {
  const t = type === undefined ? '' : `<span class="t">${escapeHtml(indexTypeText(type))}</span>`;
  return `<span class="p"><span class="n">${escapeHtml(name)}</span>${t}</span>`;
}

/** effect のチップを描く材料 — 絵の口と、hover に effect の中身を引く索引の表(render.ts の CardContext がそのまま渡る)。 */
export interface ChipContext {
  readonly glyphs: Glyphs;
  readonly graph: CallGraph;
}

/** effect のチップ(絵つき・hover に引数と答えと説明の 1 行目)。 */
function effectChip(name: string, ctx: ChipContext): string {
  const src = ctx.glyphs.effect(name);
  return `<span class="eff" title="${escapeHtml(effectHover(name, ctx.graph))}">${src === undefined ? '' : `<img src="${escapeHtml(src)}" alt="">`}${escapeHtml(name)}</span>`;
}

/**
 * decorator の札の文字(v9 — 型の性質を頭で見せるため): `dataclass :frozen True` は `frozen dataclass`、`dataclass` だけなら
 * `dataclass`、他は書かれたとおり(索引 版 6 の綴り — 外側の括弧を外し空白を 1 つに詰めた物)。
 */
export function decoratorLabel(text: string): string {
  const [head, ...rest] = text.split(' ');
  if (head !== 'dataclass' && head !== 'dataclasses.dataclass') {
    return text;
  }
  const options = rest.join(' ');
  return /(^| ):frozen True( |$)/.test(options) ? 'frozen dataclass' : 'dataclass';
}

/** 頭の decorator の札(書かれたままの綴りは hover)。 */
export function decoratorBadges(definition: HyDefinition): string {
  return definition.decorators.map((d) => `<span class="deco" title="${escapeHtml(d)}">${escapeHtml(decoratorLabel(d))}</span>`).join('');
}

/** 宣言した effect のチップ(索引 版 5 — 1 行の effects に。linter の見出しが無い時)。 */
export function declaredEffectChips(definition: HyDefinition, ctx: ChipContext): string {
  return (definition.effects ?? []).map((e) => effectChip(e.name, ctx)).join('');
}

/** 入れ子の定義のうち指定の種類(書いた順)。 */
function membersOf(card: Card, kind: HyDefinition['kind']): HyDefinition[] {
  return card.members.filter((m) => m.kind === kind);
}

/** 欄 1 つ — 名と型(型を書いていない欄は undefined)。 */
interface FieldView {
  readonly name: string;
  readonly type: HyTypeNote | undefined;
}

/**
 * 型の欄を書いた順に並べるため — 入れ子の定義の欄(field)の名に索引の欄の型(param_types)を当て、欄の定義の無い型の欄
 * (defrecord の欄)は型の順に足す(v9: defclass の `#^ T x` も defrecord と同じ読み手で param_types に載る)。
 */
function fieldsOf(card: Card): FieldView[] {
  const typed = new Map(card.definition.paramTypes.map((p) => [p.name, p.type]));
  const members = membersOf(card, 'field').map((m) => m.name);
  const rest = card.definition.paramTypes.map((p) => p.name).filter((name) => !members.includes(name));
  return [...members, ...rest].map((name) => ({ name, type: typed.get(name) }));
}

/** 欄のチップの並び(defeffect の引数の欄に使う — 型の欄の縦の表は fieldsRow)。 */
function fieldChips(card: Card): string {
  return fieldsOf(card)
    .map((f) => nameTypeChip(f.name, f.type))
    .join('');
}

/** 欄が多いか型が長いか(関数の見出しと同じ v6 の閾で、型の欄も縦の表にするため — v9)。 */
function isTallFields(fields: readonly FieldView[]): boolean {
  const chars = fields.reduce((n, f) => n + (f.type === undefined ? 0 : indexTypeText(f.type).length), 0);
  return fields.length >= TALL_SIGNATURE_PARAMS || chars > TALL_SIGNATURE_CHARS;
}

/** 型の欄(defrecord・deftype・defclass の開いた形)— 短ければ 1 行のチップ、長ければ名と型の縦の表(union は候補ごとのチップ)。 */
function fieldsRow(card: Card): string {
  const fields = fieldsOf(card);
  if (fields.length === 0) {
    return '';
  }
  if (!isTallFields(fields)) {
    return row(LABELS.fields, fields.map((f) => nameTypeChip(f.name, f.type)).join(''));
  }
  const rows = fields
    .map((f) => {
      const members = f.type === undefined ? [] : indexUnionMembers(f.type);
      const chips = members.map((m) => `<span class="${m === 'None' ? 'none' : ''}">${escapeHtml(m)}</span>`).join('<i>|</i>');
      return `<span class="n">${escapeHtml(f.name)}</span><span class="tc">${chips}</span>`;
    })
    .join('');
  return `<div class="sig2 fields"><div class="lab">${escapeHtml(LABELS.fields)}</div>${rows}</div>`;
}

/** 型でない契約の欄(全部の実体に共通 — 型の注記は引数と答えの型へ溶けるので、残った述語だけ。無ければ出さない)。 */
export function contractRow(definition: HyDefinition): string {
  const clauses = [
    ...definition.contracts.map((c) => `${c.side}: ${c.text}`),
    ...(definition.checks ?? []).map((c) => `check: ${c}`)
  ];
  return clauses.length === 0 ? '' : row(LABELS.contract, clauses.map((c) => `<code>${escapeHtml(c)}</code>`).join(''));
}

/** 関数の引数の名と型(索引 版 5 — 型を書いていない引数は名だけ)。 */
function typedParams(definition: HyDefinition): Array<{ readonly name: string; readonly type: HyTypeNote | undefined }> {
  const typed = new Map(definition.paramTypes.map((p) => [p.name, p.type]));
  const names = definition.params.length > 0 ? definition.params : definition.paramTypes.map((p) => p.name);
  return names.map((name) => ({ name, type: typed.get(name) }));
}

/**
 * linter の見出しがまだ無い関数(defk・deff・defn)の欄 — 索引 版 5 の引数と答えの型・宣言した effect で描く
 * (repo 全体の面では他の file の定義が全部これ。linter の見出しが届けば render.ts が見出しで描き直す)。
 */
export function indexSignatureRows(definition: HyDefinition, ctx: ChipContext): string {
  const params = typedParams(definition).map((p) => nameTypeChip(p.name, p.type)).join('');
  const answer = definition.answerType === null ? '' : `<span class="arrow">→</span><span class="ret">${escapeHtml(indexTypeText(definition.answerType))}</span>`;
  const sig = `<div class="sig">${params === '' ? `<span class="none">${escapeHtml(LABELS.noArgs)}</span>` : params}${answer}</div>`;
  const effects = (definition.effects ?? []).map((e) => effectChip(e.name, ctx)).join('');
  return sig + (effects === '' ? '' : row(LABELS.effects, effects));
}

/** 見出しの無い実体の欄(v2 2.1 節の表)。 */
export function entityRows(card: Card, ctx: ChipContext): string {
  const d = card.definition;
  switch (d.kind) {
    case 'defeffect': {
      const answer = d.answerType === null ? '' : `<span class="arrow">→</span><span class="ret">${escapeHtml(indexTypeText(d.answerType))}</span>`;
      const fields = fieldChips(card);
      return `<div class="sig">${fields === '' ? `<span class="none">${escapeHtml(LABELS.noArgs)}</span>` : fields}${answer}</div>`;
    }
    case 'defrecord':
    case 'deftype':
      return fieldsRow(card);
    case 'defclass': {
      // v9: defrecord と同じ欄(型つき・v6 の閾で縦の表)に、ある時だけ基底と method
      const bases = d.bases.length === 0 ? '' : row(LABELS.bases, d.bases.map((b) => `<span class="p"><span class="t">${escapeHtml(b)}</span></span>`).join(''));
      const methods = membersOf(card, 'method');
      const methodRow = methods.length === 0 ? '' : row(LABELS.methods, methods.map((m) => `<span class="p"><span class="n">${escapeHtml(m.name)}</span></span>`).join(''));
      return fieldsRow(card) + bases + methodRow;
    }
    case 'defenum': {
      const values = membersOf(card, 'enum-member');
      return values.length === 0 ? '' : row(LABELS.values, values.map((m) => `<span class="p"><span class="n">${escapeHtml(m.name)}</span></span>`).join(''));
    }
    case 'defhandler': {
      const handles = membersOf(card, 'effect-clause').map((m) => effectChip(m.handles?.name ?? m.name, ctx));
      const uses = (d.effects ?? []).map((e) => effectChip(e.name, ctx));
      return [handles.length === 0 ? '' : row(LABELS.handles, handles.join('')), uses.length === 0 ? '' : row(LABELS.effects, uses.join(''))].join('');
    }
    case 'variable': {
      const type = d.answerType === null ? '' : row(LABELS.type, `<span class="p"><span class="t">${escapeHtml(indexTypeText(d.answerType))}</span></span>`);
      const first = card.source.split('\n')[0];
      return `${type}${row(LABELS.value, `<span class="lisp" title="${escapeHtml(LABELS.lispAsIs)}">${escapeHtml(first)}</span>`)}`;
    }
    default: {
      // 他の種類(deftest・defn など)は引数の名・基底・入れ子の定義
      const rows: string[] = [];
      if (d.params.length > 0) {
        rows.push(row(LABELS.args, d.params.map((p) => nameTypeChip(p, d.paramTypes.find((t) => t.name === p)?.type)).join('')));
      }
      if (d.bases.length > 0) {
        rows.push(row(LABELS.bases, d.bases.map((b) => `<span class="p"><span class="t">${escapeHtml(b)}</span></span>`).join('')));
      }
      const methods = membersOf(card, 'method');
      if (methods.length > 0) {
        rows.push(row(LABELS.methods, methods.map((m) => `<span class="p"><span class="n">${escapeHtml(m.name)}</span></span>`).join('')));
      }
      return rows.join('');
    }
  }
}

/** 1 行の形の args / return type(見出しの無い実体 — 見本 v5 の `(id: str) → InputRow | …` の形)。 */
export function entityLineArgs(card: Card): string {
  const d = card.definition;
  const typed = (items: ReadonlyArray<{ readonly name: string; readonly type: HyTypeNote }>): string =>
    items.map((p) => `${escapeHtml(p.name)}: <span class="t">${escapeHtml(indexTypeText(p.type))}</span>`).join(', ');
  switch (d.kind) {
    case 'defeffect': {
      const answer = d.answerType === null ? '' : ` → <span class="r">${escapeHtml(indexTypeText(d.answerType))}</span>`;
      return `<span class="f f-args">(${typed(d.paramTypes)})${answer}</span>`;
    }
    case 'defk':
    case 'deff':
    case 'defn': {
      // linter の見出しが無い関数 — 索引の型で。引数が多い時は名だけ(型は hover)
      const params = typedParams(d);
      const answer = d.answerType === null ? '' : ` → <span class="r">${escapeHtml(indexTypeText(d.answerType))}</span>`;
      if (params.length >= NAMES_ONLY_PARAMS) {
        const title = params.map((p) => `${p.name}: ${p.type === undefined ? '?' : indexTypeText(p.type)}`).join('\n');
        return `<span class="f f-args" title="${escapeHtml(title)}">(${params.map((p) => escapeHtml(p.name)).join(', ')})${answer}</span>`;
      }
      const shown = params.map((p) => (p.type === undefined ? escapeHtml(p.name) : `${escapeHtml(p.name)}: <span class="t">${escapeHtml(indexTypeText(p.type))}</span>`));
      return `<span class="f f-args">(${shown.join(', ')})${answer}</span>`;
    }
    case 'defrecord':
    case 'deftype':
    case 'defclass': {
      // 型の欄は数が多くても型を出す(v9 の見本 `(ref: str, text: str, request-id: str | None, …)`)。型の無い欄は名だけ
      const fields = fieldsOf(card);
      if (fields.length > 0) {
        const shown = fields.map((f) => (f.type === undefined ? escapeHtml(f.name) : `${escapeHtml(f.name)}: <span class="t">${escapeHtml(indexTypeText(f.type))}</span>`));
        return `<span class="f f-args">(${shown.join(', ')})</span>`;
      }
      break;
    }
    case 'defenum': {
      const values = membersOf(card, 'enum-member').map((m) => escapeHtml(m.name));
      return values.length === 0 ? '' : `<span class="f f-args">${values.join(' | ')}</span>`;
    }
    case 'defhandler': {
      const handles = membersOf(card, 'effect-clause').map((m) => escapeHtml(m.handles?.name ?? m.name));
      return handles.length === 0 ? '' : `<span class="f f-args">${escapeHtml(LABELS.handles)}: ${handles.join(', ')}</span>`;
    }
    default:
      break;
  }
  const fields = membersOf(card, 'field').map((m) => escapeHtml(m.name));
  if (fields.length > 0) {
    return `<span class="f f-args">(${fields.join(', ')})</span>`;
  }
  return d.params.length === 0 ? '' : `<span class="f f-args">(${d.params.map(escapeHtml).join(', ')})</span>`;
}

/** 帯に名を出す数(残りは +N — 全部は木で見る)。 */
const BAND_NAMES = 3;

/** used by の欄に 1 つの関係ごとに出す名の数(残りは +N)。 */
const USED_BY_NAMES = 6;

/**
 * 関係の名のボタンの並び(押すとそのカードへ)。別の file の同名の定義が並ぶ時は file 名を添える — 名だけでは
 * `judged · judged` のようにどれか見分けられないため(V11 の実物の画面で見つけた)。
 */
function nameButtons(qualifiedNames: readonly string[], graph: CallGraph, limit: number): string {
  const nameOf = (qn: string): string => graph.definitions.get(qn)?.definition.name ?? qn;
  const counts = new Map<string, number>();
  for (const qn of qualifiedNames) {
    counts.set(nameOf(qn), (counts.get(nameOf(qn)) ?? 0) + 1);
  }
  const shown = qualifiedNames.slice(0, limit).map((qn) => {
    const name = nameOf(qn);
    const file = graph.definitions.get(qn)?.path.split('/').pop()?.replace(/\.hy$/, '');
    const qual = (counts.get(name) ?? 0) > 1 && file !== undefined ? `<span class="qual">${escapeHtml(file)}</span>` : '';
    return `<button class="tname-sm" data-reveal="${escapeHtml(qn)}">${escapeHtml(name)}${qual}</button>`;
  });
  const more = qualifiedNames.length > limit ? ` · +${qualifiedNames.length - limit}` : '';
  return `${shown.join(' · ')}${more}`;
}

/** 帯の関係の名の並び(見本 v2 の `callers 2: run-requests · …`)。 */
function nameList(qualifiedNames: readonly string[], graph: CallGraph): string {
  return qualifiedNames.length === 0 ? '' : `: ${nameButtons(qualifiedNames, graph, BAND_NAMES)}`;
}

/**
 * defclass の used by の欄(v9 — 型の軸と同じ材料をカードの側から見た形): この型を引数に取る定義(arg of)・返す定義(returns)・
 * 欄に持つ型と effect(field of)・この名を呼んで作る定義(made in — test は帯の tests にあるので除く)。どれも無ければ出さない。
 */
export function usedByRow(card: Card, graph: CallGraph): string {
  const d = card.definition;
  if (d.kind !== 'defclass') {
    return '';
  }
  const qn = d.qualifiedName;
  const madeIn = (graph.callers.get(qn) ?? []).filter((other) => graph.definitions.get(other)?.definition.kind !== 'deftest');
  const groups: ReadonlyArray<readonly [string, readonly string[]]> = [
    [LABELS.argOf, graph.acceptedBy.get(qn) ?? []],
    [LABELS.returns, graph.returnedBy.get(qn) ?? []],
    [LABELS.fieldOf, graph.fieldOf.get(qn) ?? []],
    [LABELS.madeIn, madeIn]
  ];
  const shown = groups
    .filter(([, names]) => names.length > 0)
    .map(([label, names]) => `<span class="use"><b>${escapeHtml(label)}</b>${nameButtons(names, graph, USED_BY_NAMES)}</span>`);
  return shown.length === 0 ? '' : row(LABELS.usedBy, shown.join(''));
}

/** 帯の関係 1 つ(数を押すと木 — 呼び出しの向きがある物だけ)と、その名。 */
function relationButton(qn: string, direction: 'callers' | 'callees', label: string, names: readonly string[], graph: CallGraph): string {
  return `<span class="relgroup"><button class="rel" data-tree-root="${escapeHtml(qn)}" data-tree-dir="${direction}">${escapeHtml(label)} <b>${names.length}</b></button>${nameList(names, graph)}</span>`;
}

/** 帯の関係 1 つ(数は押せない物)と、その名。 */
function relationNames(label: string, names: readonly string[], graph: CallGraph): string {
  return `<span class="relgroup">${escapeHtml(label)} <b>${names.length}</b>${nameList(names, graph)}</span>`;
}

/**
 * 下の帯の関係(v2 2.1 節の表・ラベルは v5 の表)— 実体の種類ごとに出す関係が変わる。数を押すと木、名を押すとそのカードへ。
 * 数と名は木と同じ呼び出しの表から(帯と木で数え方を 1 つにするため)。
 */
export function relationBand(card: Card, graph: CallGraph): string {
  const definition = card.definition;
  const qn = definition.qualifiedName;
  const isTest = (other: string): boolean => graph.definitions.get(other)?.definition.kind === 'deftest';
  const allCallers = graph.callers.get(qn) ?? [];
  const callers = allCallers.filter((other) => !isTest(other));
  const tests = allCallers.filter(isTest);
  const types = card.facts.types.length === 0 ? '' : `<span class="relgroup" title="${escapeHtml(card.facts.types.join(', '))}">${escapeHtml(LABELS.types)} <b>${card.facts.types.length}</b></span>`;
  switch (definition.kind) {
    case 'defeffect':
      return relationButton(qn, 'callers', LABELS.usedBy, callers, graph) + relationNames(LABELS.handlers, graph.handlerDefinitions.get(qn) ?? [], graph);
    case 'defrecord':
    case 'deftype':
      return relationNames(LABELS.returnedBy, graph.returnedBy.get(qn) ?? [], graph) + relationNames(LABELS.acceptedBy, graph.acceptedBy.get(qn) ?? [], graph);
    case 'defenum':
      return relationButton(qn, 'callers', LABELS.usedBy, callers, graph);
    case 'defhandler':
      return relationButton(qn, 'callers', LABELS.installedAt, callers, graph);
    case 'deftest':
      return relationButton(qn, 'callees', LABELS.callees, calleesInTree(graph, qn), graph);
    case 'variable':
      return '';
    default:
      return (
        relationButton(qn, 'callers', LABELS.callers, callers, graph) +
        relationButton(qn, 'callees', LABELS.callees, calleesInTree(graph, qn), graph) +
        relationNames(LABELS.tests, tests, graph) +
        types
      );
  }
}

