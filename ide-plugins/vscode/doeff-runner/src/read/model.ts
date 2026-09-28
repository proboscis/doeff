// 定義を読む面(agora-redesign #910・案 D)の中身 — 定義 1 つを 1 枚のカードにし、軸で絞る純粋な関数(VS Code に触らない)。
// 定義の一覧の正本は hy-index(置き場の 1 file の索引)。面が自分で file を歩いて定義を探すことはしない。
// 型と effect は linter の editor-json の signature を材料に添えるだけ(読み方の正本は linter)。
//
// 軸: kind と、定義の :tags の key ごとに 1 軸(key を列挙しない — 索引に新しい key が現れれば軸が増える)。
// 絞り込み: 同じ軸の中で選んだ値は「どれか」、軸どうしは「全部」(= 定義の集合の積。木ではない)。

import type { HyDefinition, HyRange } from '../hy/contract';
import type { LintLevel, LintSignature, LintViolation } from '../lint/contract';

/** 軸 — 定義の kind か、:tags の key 1 つ。 */
export type PlaneAxis = { readonly tag: 'kind' } | { readonly tag: 'tag'; readonly key: string };

/** その軸の値を持たない定義の値。 */
export const NO_VALUE = '(なし)';

/** tags の key のうち先に並べる物(残りは名前の順)。 */
const LEADING_TAG_KEYS: readonly string[] = ['context', 'role'];

/** 軸を 1 つの文字にする(webview との受け渡しと選択の表の鍵)。 */
export function axisKey(axis: PlaneAxis): string {
  switch (axis.tag) {
    case 'kind':
      return 'kind';
    case 'tag':
      return `tag:${axis.key}`;
    default: {
      const unreachable: never = axis;
      throw new Error(`網羅されていない軸: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 軸の文字を軸に読む(知らない形は undefined)。 */
export function parseAxisKey(text: string): PlaneAxis | undefined {
  if (text === 'kind') {
    return { tag: 'kind' };
  }
  if (text.startsWith('tag:') && text.length > 4) {
    return { tag: 'tag', key: text.slice(4) };
  }
  return undefined;
}

/** 軸の見出し。 */
export function axisTitle(axis: PlaneAxis): string {
  switch (axis.tag) {
    case 'kind':
      return 'kind';
    case 'tag':
      return axis.key;
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
  /** 定義の範囲に入る linter の違反 */
  readonly violations: readonly LintViolation[];
  /** 定義の source(書かれたままの lisp) */
  readonly source: string;
  /** source の最初の行(1 始まり) */
  readonly firstLine: number;
}

/** カードを作る材料。 */
export interface PlaneInput {
  /** 索引のその file の定義(書いた順とは限らない) */
  readonly definitions: readonly HyDefinition[];
  /** linter の見出し(無ければ空) */
  readonly signatures: readonly LintSignature[];
  /** linter のその file の違反 */
  readonly violations: readonly LintViolation[];
  /** 開いた document の行 */
  readonly lines: readonly string[];
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

/** 定義の見出しを引く(同じ kind・名・頭の行)。 */
function signatureOf(definition: HyDefinition, signatures: readonly LintSignature[]): LintSignature | undefined {
  return signatures.find(
    (s) => s.kind === definition.kind && s.name === definition.name && s.fullRange.start.line === definition.fullRange.start.line
  );
}

/** 定義の順(source の位置)。 */
function byPosition(a: HyDefinition, b: HyDefinition): number {
  return a.fullRange.start.line - b.fullRange.start.line || a.fullRange.start.character - b.fullRange.start.character;
}

/** カードの一覧 — 入れ子でない定義(container の無い物)を source の順に 1 枚ずつ。入れ子の定義はそのカードの部品にする。 */
export function buildCards(input: PlaneInput): Card[] {
  const top = input.definitions.filter((d) => d.container === null).slice().sort(byPosition);
  return top.map((definition, i) => ({
      id: `d${i}`,
      definition,
      members: input.definitions
        .filter((d) => d.container === definition.name && within(definition.fullRange, d.fullRange.start.line, d.fullRange.start.character))
        .slice()
        .sort(byPosition),
      signature: signatureOf(definition, input.signatures),
      violations: input.violations.filter((v) => within(definition.fullRange, v.range.start.line, v.range.start.character)),
      source: sourceOf(definition.fullRange, input.lines),
      firstLine: definition.fullRange.start.line + 1
    }));
}

/** カードのその軸の値。 */
export function valueOf(card: Card, axis: PlaneAxis): string {
  switch (axis.tag) {
    case 'kind':
      return card.definition.kind;
    case 'tag':
      return card.definition.tags?.[axis.key] ?? NO_VALUE;
    default: {
      const unreachable: never = axis;
      throw new Error(`網羅されていない軸: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 使える軸 — kind と、カードの :tags に現れた key の全部(context・role を先に、残りは名前の順)。 */
export function axesOf(cards: readonly Card[]): PlaneAxis[] {
  const keys = new Set<string>();
  for (const card of cards) {
    for (const key of Object.keys(card.definition.tags ?? {})) {
      keys.add(key);
    }
  }
  const leading = LEADING_TAG_KEYS.filter((k) => keys.has(k));
  const rest = [...keys].filter((k) => !LEADING_TAG_KEYS.includes(k)).sort();
  return [{ tag: 'kind' }, ...[...leading, ...rest].map((key): PlaneAxis => ({ tag: 'tag', key }))];
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

/** カードが選択に合うか(除く軸を渡すと、その軸の選択は見ない — 値ごとの数えに使う)。 */
function matches(card: Card, selection: Selection, except?: string): boolean {
  for (const [key, values] of selection) {
    if (key === except || values.size === 0) {
      continue;
    }
    const axis = parseAxisKey(key);
    if (axis === undefined || !values.has(valueOf(card, axis))) {
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

/** 値の並べ方 — 多い順、「(なし)」は最後。 */
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
      const value = valueOf(card, axis);
      if (!counts.has(value)) {
        counts.set(value, 0);
      }
      if (matches(card, selection, key)) {
        counts.set(value, (counts.get(value) ?? 0) + 1);
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
