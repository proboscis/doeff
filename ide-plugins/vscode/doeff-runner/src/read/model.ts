// 定義を読む面(agora-redesign #910・案 D)の中身 — 定義 1 つを 1 枚のカードにし、軸で絞る純粋な関数(VS Code に触らない)。
// 定義の一覧の正本は hy-index(置き場の 1 file の索引)。面が自分で file を歩いて定義を探すことはしない。
// 型と effect は linter の editor-json の signature を材料に添えるだけ(読み方の正本は linter)。
//
// 軸: kind と、定義の :tags の key ごとに 1 軸(key を列挙しない — 索引に新しい key が現れれば軸が増える)と、effect・type・
// tests・location(見本 v5 の左の欄)。effect と type は 1 つの定義が複数の値を持つ(その各値の下に数える)。
// 絞り込み: 同じ軸の中で選んだ値は「どれか」、軸どうしは「全部」(= 定義の集合の積。木ではない)。

import type { HyDefinition, HyRange } from '../hy/contract';
import type { LintBinding, LintBody, LintLevel, LintSignature, LintViolation } from '../lint/contract';
import { LABELS } from './labels';

/** 軸 — 定義の kind・:tags の key 1 つ・使う effect・使う型・テストの有無・置き場。 */
export type PlaneAxis =
  | { readonly tag: 'kind' }
  | { readonly tag: 'tag'; readonly key: string }
  | { readonly tag: 'effect' }
  | { readonly tag: 'type' }
  | { readonly tag: 'tests' }
  | { readonly tag: 'location' };

/** 決まった軸の見出し(面に出る文字は labels の表から — v5)。 */
const FIXED_AXIS_TITLES = { kind: LABELS.kind, effect: LABELS.effect, type: LABELS.type, tests: LABELS.tests, location: LABELS.location } as const;

/** tests の軸の値(labels の表から — v5 の has tests / no tests)。 */
export const HAS_TESTS = LABELS.hasTests;
export const NO_TESTS = LABELS.noTests;

/** その軸の値を持たない定義の値。 */
export const NO_VALUE = LABELS.noValue;

/** tags の key のうち先に並べる物(残りは名前の順)。 */
const LEADING_TAG_KEYS: readonly string[] = ['context', 'role'];

/** 軸を 1 つの文字にする(webview との受け渡しと選択の表の鍵)。 */
export function axisKey(axis: PlaneAxis): string {
  switch (axis.tag) {
    case 'kind':
      return 'kind';
    case 'tag':
      return `tag:${axis.key}`;
    case 'effect':
    case 'type':
    case 'tests':
    case 'location':
      return axis.tag;
    default: {
      const unreachable: never = axis;
      throw new Error(`網羅されていない軸: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 軸の文字を軸に読む(知らない形は undefined)。 */
export function parseAxisKey(text: string): PlaneAxis | undefined {
  switch (text) {
    case 'kind':
    case 'effect':
    case 'type':
    case 'tests':
    case 'location':
      return { tag: text };
    default:
      break;
  }
  if (text.startsWith('tag:') && text.length > 4) {
    return { tag: 'tag', key: text.slice(4) };
  }
  return undefined;
}

/** 軸の見出し。 */
export function axisTitle(axis: PlaneAxis): string {
  switch (axis.tag) {
    case 'tag':
      return axis.key;
    case 'kind':
    case 'effect':
    case 'type':
    case 'tests':
    case 'location':
      return FIXED_AXIS_TITLES[axis.tag];
    default: {
      const unreachable: never = axis;
      throw new Error(`網羅されていない軸: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** カード 1 枚 — 定義 1 つと、それに添える材料。 */
export interface Card {
  /** 面の中での名札(file の中の定義の順番) */
  readonly id: string;
  readonly definition: HyDefinition;
  /** 入れ子の定義(defrecord の欄・defenum の値・defhandler の effect の節・defclass の method — 書いた順) */
  readonly members: readonly HyDefinition[];
  /** defk / deff の見出し(linter がまだ答えていない・版が古い・他の kind は undefined) */
  readonly signature: LintSignature | undefined;
  /** defk / deff の本体の文字の行(linter の bodies — 無ければ undefined) */
  readonly body: LintBody | undefined;
  /** 定義の範囲に入る linter の違反と、カードの中の置き場(頭か本体の行 — placeOf) */
  readonly violations: readonly PlacedViolation[];
  /** 定義の範囲に入る linter の束縛(本体の名の hover に型を出す — v1 2.5 節) */
  readonly bindings: readonly LintBinding[];
  /** 定義の source(書かれたままの lisp) */
  readonly source: string;
  /** source の最初の行(1 始まり) */
  readonly firstLine: number;
  /** 軸の値のうち索引と呼び出しの表から引く物(effect・type・tests・location) */
  readonly facts: CardFacts;
  /** 定義の file の path(索引の root から — 置き場の表示) */
  readonly place: string;
}

/** カードの軸の値のうち、定義だけでは決まらない物。 */
export interface CardFacts {
  /** 使う effect(宣言した effect・defhandler は解く effect・defeffect は自分の名) */
  readonly effects: readonly string[];
  /** 引数と答えに書いた、repo の中の型の名 */
  readonly types: readonly string[];
  /** その定義を呼ぶ deftest がある */
  readonly tested: boolean;
  /** 置き場(file の dir — 索引の root から) */
  readonly location: string;
}

/** カードを作る材料。 */
export interface PlaneInput {
  /** 索引のその file の定義(書いた順とは限らない) */
  readonly definitions: readonly HyDefinition[];
  /** linter の見出し(無ければ空) */
  readonly signatures: readonly LintSignature[];
  /** linter の本体の文字の行(無ければ空) */
  readonly bodies: readonly LintBody[];
  /** linter のその file の違反 */
  readonly violations: readonly LintViolation[];
  /** linter のその file の束縛(無ければ空) */
  readonly bindings: readonly LintBinding[];
  /** 開いた document の行 */
  readonly lines: readonly string[];
  /** その定義を呼ぶ deftest の数(呼び出しの表から) */
  readonly testsOf: (qualifiedName: string) => number;
  /** この file の path(索引の root から) */
  readonly place: string;
}

/** 重ねずに並べる(書いた順)。 */
function unique(values: readonly string[]): string[] {
  return [...new Set(values)];
}

/** 定義の effect の軸の値(索引の版 5 の宣言・effect 節・defeffect の名)。 */
function effectsOf(definition: HyDefinition, members: readonly HyDefinition[]): string[] {
  if (definition.kind === 'defeffect') {
    return [definition.name];
  }
  const declared = (definition.effects ?? []).map((e) => e.name);
  const handled = members.flatMap((m) => (m.handles === null ? [] : [m.handles.name]));
  return unique([...declared, ...handled]);
}

/** 定義の type の軸の値(引数と答えの型の中の、repo の中で解けた名)。 */
function typesOf(definition: HyDefinition): string[] {
  const notes = [...definition.paramTypes.map((p) => p.type), ...(definition.answerType === null ? [] : [definition.answerType])];
  return unique(notes.flatMap((n) => n.names.filter((name) => name.target !== null).map((name) => name.name)));
}

/** 位置が範囲に入るか。 */
function within(range: HyRange, line: number, character: number): boolean {
  const afterStart = line > range.start.line || (line === range.start.line && character >= range.start.character);
  const beforeEnd = line < range.end.line || (line === range.end.line && character <= range.end.character);
  return afterStart && beforeEnd;
}

/** 範囲の文字を行から切り出す。 */
export function sourceOf(range: HyRange, lines: readonly string[]): string {
  const picked: string[] = [];
  for (let line = range.start.line; line <= range.end.line && line < lines.length; line += 1) {
    const text = lines[line];
    const from = line === range.start.line ? range.start.character : 0;
    const to = line === range.end.line ? range.end.character : text.length;
    picked.push(text.slice(from, to));
  }
  return picked.join('\n');
}

/** linter の定義ごとの出力(見出し・本体)を、索引の定義に対応させる(同じ kind・名・頭の行)。 */
function sameDefinition<T extends { readonly kind: string; readonly name: string; readonly fullRange: { readonly start: { readonly line: number } } }>(
  definition: HyDefinition,
  items: readonly T[]
): T | undefined {
  return items.find((s) => s.kind === definition.kind && s.name === definition.name && s.fullRange.start.line === definition.fullRange.start.line);
}

/** 定義の順(source の位置)。 */
function byPosition(a: HyDefinition, b: HyDefinition): number {
  return a.fullRange.start.line - b.fullRange.start.line || a.fullRange.start.character - b.fullRange.start.character;
}

/**
 * 違反の置き場(定義 1 つに対して — agora-redesign #1685 の 1・設計 v13)。linter が出した違反は、この 3 つのどれか 1 か所に
 * 必ず出す(どのカードにも置けない違反は file の帯へ — bandViolations)。
 */
export type ViolationPlacement =
  /** 定義の頭 — カードの頭の名に重大さの色の下線と規則の ID の札 */
  | { readonly tag: 'head' }
  /** 本体の行 — その source の行(0 始まり)を描く本体の行に札(source の箱の同じ範囲にも下線) */
  | { readonly tag: 'body'; readonly line: number }
  /** 定義の範囲の外 — file の見出しの帯 */
  | { readonly tag: 'file' };

/** カードの中の置き場(頭か本体の行)。 */
export type CardPlacement = Exclude<ViolationPlacement, { readonly tag: 'file' }>;

/** カードに置いた違反 1 件 — 違反と、カードの中の置き場。 */
export interface PlacedViolation {
  readonly violation: LintViolation;
  readonly place: CardPlacement;
}

/**
 * 違反の置き場を決める純粋な関数 — 違反の範囲の始まりが、定義の範囲の外 → file(file の先頭の import の向き・置き場・宣言に
 * ない依存、索引と linter の版のずれ)、定義の名の範囲 → head(Jev の 201〜205 は証拠の行を返さないので頭に付けるだけ)、
 * 本体(linter の bodies)の行 → body、範囲の中で本体のどの行にも当たらない(契約の辞書・本体の無い kind・linter の本体が
 * まだ無い)→ head。
 */
export function placeOf(violation: LintViolation, definition: HyDefinition, body: LintBody | undefined): ViolationPlacement {
  const { line, character } = violation.range.start;
  if (!within(definition.fullRange, line, character)) {
    return { tag: 'file' };
  }
  if (within(definition.range, line, character)) {
    return { tag: 'head' };
  }
  return body !== undefined && body.lines.some((l) => l.line === line) ? { tag: 'body', line } : { tag: 'head' };
}

/** 定義のカードに置く違反(範囲の外の違反は除く)。 */
function placedIn(violations: readonly LintViolation[], definition: HyDefinition, body: LintBody | undefined): PlacedViolation[] {
  return violations.flatMap((violation) => {
    const place = placeOf(violation, definition, body);
    return place.tag === 'file' ? [] : [{ violation, place }];
  });
}

/** どのカードにも置けなかった違反(file の見出しの帯へ — 消さない)。linter の順のまま。 */
export function bandViolations(violations: readonly LintViolation[], cards: readonly Card[]): LintViolation[] {
  const placed = new Set(cards.flatMap((c) => c.violations.map((p) => p.violation)));
  return violations.filter((v) => !placed.has(v));
}

/** カードの一覧 — 入れ子でない定義(container の無い物)を source の順に 1 枚ずつ。入れ子の定義はそのカードの部品にする。 */
export function buildCards(input: PlaneInput): Card[] {
  const top = input.definitions.filter((d) => d.container === null).slice().sort(byPosition);
  return top.map((definition, i) => {
    const members = input.definitions
      .filter((d) => d.container === definition.name && within(definition.fullRange, d.fullRange.start.line, d.fullRange.start.character))
      .slice()
      .sort(byPosition);
    const body = sameDefinition(definition, input.bodies);
    return {
      id: `d${i}`,
      definition,
      members,
      facts: {
        effects: effectsOf(definition, members),
        types: typesOf(definition),
        tested: input.testsOf(definition.qualifiedName) > 0,
        location: locationOf(input.place)
      },
      place: input.place,
      signature: sameDefinition(definition, input.signatures),
      body,
      violations: placedIn(input.violations, definition, body),
      bindings: input.bindings.filter((b) => within(definition.fullRange, b.range.start.line, b.range.start.character)),
      source: sourceOf(definition.fullRange, input.lines),
      firstLine: definition.fullRange.start.line + 1
    };
  });
}

/** 値の無い軸は NO_VALUE 1 つにする。 */
function orNone(values: readonly string[]): string[] {
  return values.length === 0 ? [NO_VALUE] : [...values];
}

/** カードのその軸の値(複数の値を持つ軸は全部 — その各値の下に数える)。 */
export function valuesOf(card: Card, axis: PlaneAxis): string[] {
  switch (axis.tag) {
    case 'kind':
      return [card.definition.kind];
    case 'tag':
      return [card.definition.tags?.[axis.key] ?? NO_VALUE];
    case 'effect':
      return orNone(card.facts.effects);
    case 'type':
      return orNone(card.facts.types);
    case 'tests':
      return [card.facts.tested ? HAS_TESTS : NO_TESTS];
    case 'location':
      return [card.facts.location];
    default: {
      const unreachable: never = axis;
      throw new Error(`網羅されていない軸: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 使える軸 — kind と、カードの :tags に現れた key の全部(context・role を先に、残りは名前の順)と、effect・type・tests・location。 */
export function axesOf(cards: readonly Card[]): PlaneAxis[] {
  const keys = new Set<string>();
  for (const card of cards) {
    for (const key of Object.keys(card.definition.tags ?? {})) {
      keys.add(key);
    }
  }
  const leading = LEADING_TAG_KEYS.filter((k) => keys.has(k));
  const rest = [...keys].filter((k) => !LEADING_TAG_KEYS.includes(k)).sort();
  return [
    { tag: 'kind' },
    ...[...leading, ...rest].map((key): PlaneAxis => ({ tag: 'tag', key })),
    { tag: 'effect' },
    { tag: 'type' },
    { tag: 'tests' },
    { tag: 'location' }
  ];
}

/** 選んだ値 — 軸の文字 → 値の集合(空の集合の軸は絞らない)。 */
export type Selection = ReadonlyMap<string, ReadonlySet<string>>;

/** 値 1 つの選択を入れ替える(選んでいれば外し、選んでいなければ足す)。 */
export function toggle(selection: Selection, axis: PlaneAxis, value: string): Selection {
  const key = axisKey(axis);
  const current = new Set(selection.get(key) ?? []);
  if (current.has(value)) {
    current.delete(value);
  } else {
    current.add(value);
  }
  const next = new Map(selection);
  if (current.size === 0) {
    next.delete(key);
  } else {
    next.set(key, current);
  }
  return next;
}

/** 選択の中で名の検索の文字を持つ鍵(軸ではない — 軸の積と同じ絞りに乗せるため同じ表に置く)。 */
export const SEARCH_KEY = 'search';

/** 名の検索の文字を置く(空なら外す)。 */
export function setSearch(selection: Selection, text: string): Selection {
  const next = new Map(selection);
  const query = text.trim();
  if (query === '') {
    next.delete(SEARCH_KEY);
  } else {
    next.set(SEARCH_KEY, new Set([query]));
  }
  return next;
}

/** 名が検索の文字を含むか(大文字と小文字を分けない・名と完全修飾名のどちらでも)。 */
function nameMatches(card: Card, query: string): boolean {
  const q = query.toLowerCase();
  return card.definition.name.toLowerCase().includes(q) || card.definition.qualifiedName.toLowerCase().includes(q);
}

/** カードが選択に合うか(除く軸を渡すと、その軸の選択は見ない — 値ごとの数えに使う)。 */
function matches(card: Card, selection: Selection, except?: string): boolean {
  for (const [key, values] of selection) {
    if (key === except || values.size === 0) {
      continue;
    }
    if (key === SEARCH_KEY) {
      if (![...values].every((q) => nameMatches(card, q))) {
        return false;
      }
      continue;
    }
    const axis = parseAxisKey(key);
    if (axis === undefined || !valuesOf(card, axis).some((v) => values.has(v))) {
      return false;
    }
  }
  return true;
}

/** 選択に合うカード(軸の中は「どれか」、軸どうしは「全部」)。 */
export function visibleCards(cards: readonly Card[], selection: Selection): Card[] {
  return cards.filter((card) => matches(card, selection));
}

/** 軸の値 1 つの札 — 値・その値を選んだら残る数・選んでいるか。 */
export interface FacetValue {
  readonly value: string;
  readonly count: number;
  readonly selected: boolean;
}

/** 軸 1 本の札の並び。 */
export interface Facet {
  readonly axis: PlaneAxis;
  readonly values: readonly FacetValue[];
}

/** 値の並べ方 — 多い順、NO_VALUE は最後。 */
function facetOrder(a: FacetValue, b: FacetValue): number {
  const last = (v: FacetValue): number => (v.value === NO_VALUE ? 1 : 0);
  return last(a) - last(b) || b.count - a.count || a.value.localeCompare(b.value);
}

/**
 * 軸ごとの札 — 値ごとの数は「他の軸の選択はそのままで、この軸をその値にしたら残る数」(同じ軸の選択は数えに入れない)。
 * 選んだ値は数が 0 でも残す(外せるように)。
 */
export function facets(cards: readonly Card[], selection: Selection): Facet[] {
  return axesOf(cards).map((axis) => {
    const key = axisKey(axis);
    const chosen = selection.get(key) ?? new Set<string>();
    const counts = new Map<string, number>();
    for (const card of cards) {
      const hit = matches(card, selection, key);
      for (const value of valuesOf(card, axis)) {
        if (!counts.has(value)) {
          counts.set(value, 0);
        }
        if (hit) {
          counts.set(value, (counts.get(value) ?? 0) + 1);
        }
      }
    }
    for (const value of chosen) {
      if (!counts.has(value)) {
        counts.set(value, 0);
      }
    }
    const values = [...counts.entries()].map(([value, count]) => ({ value, count, selected: chosen.has(value) })).sort(facetOrder);
    return { axis, values };
  });
}

/** 違反の重さの順(小さいほど重い)。 */
const LEVEL_RANK: Readonly<Record<LintLevel, number>> = { critical: 0, major: 1, minor: 2, info: 3 };

/** カードの違反の一番重い重さ(無ければ undefined)。 */
export function worstLevel(violations: readonly LintViolation[]): LintLevel | undefined {
  let worst: LintLevel | undefined;
  for (const v of violations) {
    if (worst === undefined || LEVEL_RANK[v.level] < LEVEL_RANK[worst]) {
      worst = v.level;
    }
  }
  return worst;
}

/** 置き場の軸の値 — file の dir の末尾 2 段(見本 v5 の `messaging/core` の形。root の直下は `.`)。 */
export function locationOf(relativePath: string): string {
  const parts = relativePath.split(/[\\/]/).slice(0, -1);
  return parts.length === 0 ? '.' : parts.slice(-2).join('/');
}
