// 「タグで閲覧」の中身 — 定義を軸(service・層・context・role・任意のタグの鍵・kind・生の副作用・違反の規則)で並べ替え、
// 絞り込み、保存した見方を読み書きする純粋な関数。判定はしない(値はどれも hy-index と linter の出力を読むだけ)。

import * as path from 'path';
import type { HyDefinition } from './contract';
import type { LintModule, LintViolation } from '../lint/contract';

/** 決まった軸(閉じた集合)。 */
export const BUILTIN_AXES = ['service', 'layer', 'context', 'role', 'kind', 'raw', 'violation'] as const;
export type BuiltinAxis = (typeof BUILTIN_AXES)[number];

/** 軸 — 決まった軸か、定義の :tags に書かれた任意の鍵。 */
export type Axis = { readonly tag: 'builtin'; readonly name: BuiltinAxis } | { readonly tag: 'tag'; readonly key: string };

/** 値が無い時の札(軸ごと)。 */
export const UNKNOWN_VALUE = '(不明)';
export const NONE_VALUE = 'なし';

/** 閲覧の単位 — 定義 1 つ。 */
export interface BrowseItem {
  readonly path: string;
  /** 定義の file の module 名(Hy の dotted) */
  readonly module: string;
  readonly definition: HyDefinition;
}

/** 軸の値を引くための linter の結果(path で引く)。 */
export interface BrowseContext {
  readonly lintModule: (filePath: string) => LintModule | undefined;
  readonly lintViolations: (filePath: string) => readonly LintViolation[];
}

/** 絞り込みの条件 1 つ(軸 = 値)。 */
export interface BrowseFilter {
  readonly axis: Axis;
  readonly value: string;
}

/** 保存した見方 — 名前・軸の順(1〜3 段)・絞り込み。 */
export interface BrowseView {
  readonly name: string;
  readonly order: readonly Axis[];
  readonly filters: readonly BrowseFilter[];
}

/** 軸を設定の文字列にする(`service`・`tag:owner`)。 */
export function axisId(axis: Axis): string {
  return axis.tag === 'builtin' ? axis.name : `tag:${axis.key}`;
}

/** 設定の文字列を軸に読む(知らない名前は undefined)。 */
export function parseAxis(text: string): Axis | undefined {
  if (text.startsWith('tag:') && text.length > 4) {
    return { tag: 'tag', key: text.slice(4) };
  }
  const name = BUILTIN_AXES.find((a) => a === text);
  return name === undefined ? undefined : { tag: 'builtin', name };
}

/** 軸の見出し(パネル・QuickPick)。 */
export function axisLabel(axis: Axis): string {
  if (axis.tag === 'tag') {
    return axis.key;
  }
  switch (axis.name) {
    case 'service':
      return 'service';
    case 'layer':
      return '層';
    case 'context':
      return 'context';
    case 'role':
      return 'role';
    case 'kind':
      return 'kind';
    case 'raw':
      return '生の副作用';
    case 'violation':
      return '違反の規則';
    default: {
      const unreachable: never = axis.name;
      throw new Error(`網羅されていない軸: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 範囲の中に位置が入るか(違反を定義へ割り当てる用)。 */
function within(def: HyDefinition, line: number, character: number): boolean {
  const r = def.fullRange;
  const afterStart = line > r.start.line || (line === r.start.line && character >= r.start.character);
  const beforeEnd = line < r.end.line || (line === r.end.line && character <= r.end.character);
  return afterStart && beforeEnd;
}

/** 定義の範囲に入る linter の違反。 */
export function violationsOf(item: BrowseItem, ctx: BrowseContext): LintViolation[] {
  return ctx
    .lintViolations(item.path)
    .filter((v) => within(item.definition, v.range.start.line, v.range.start.character));
}

/** 重ねずに並べる(書いた順)。 */
function unique(values: readonly string[]): string[] {
  return [...new Set(values)];
}

/**
 * 定義の軸の値(複数の値を持つ軸は全部 — その各値の下に出る)。
 * - service・layer: linter の modules(無ければ「(不明)」)
 * - context・role: 定義の :tags → 無ければ linter の module の context・role(linter が MODULE-TAGS から読んだ物)→ 無ければ「(不明)」
 * - 任意のタグの鍵: 定義の :tags(無ければ「(不明)」)
 * - kind: 定義の kind / raw: 生の副作用の直接の証拠の分類(無ければ「なし」)/ violation: 範囲に入る違反の規則(無ければ「なし」)
 */
export function axisValues(item: BrowseItem, axis: Axis, ctx: BrowseContext): string[] {
  const tags = item.definition.tags;
  if (axis.tag === 'tag') {
    return [tags?.[axis.key] ?? UNKNOWN_VALUE];
  }
  switch (axis.name) {
    case 'service': {
      const service = ctx.lintModule(item.path)?.service ?? null;
      return [service ?? UNKNOWN_VALUE];
    }
    case 'layer':
      return [ctx.lintModule(item.path)?.layer ?? UNKNOWN_VALUE];
    case 'context':
      return [tags?.context ?? ctx.lintModule(item.path)?.context ?? UNKNOWN_VALUE];
    case 'role':
      return [tags?.role ?? ctx.lintModule(item.path)?.role ?? UNKNOWN_VALUE];
    case 'kind':
      return [item.definition.kind];
    case 'raw': {
      const categories = unique(item.definition.raw.direct.map((e) => e.category));
      return categories.length > 0 ? categories : [NONE_VALUE];
    }
    case 'violation': {
      const rules = unique(violationsOf(item, ctx).map((v) => v.rule));
      return rules.length > 0 ? rules : [NONE_VALUE];
    }
    default: {
      const unreachable: never = axis.name;
      throw new Error(`網羅されていない軸: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 使える軸の一覧 — 決まった軸と、定義の :tags に現れた鍵(context・role を除く・名前の順)。 */
export function availableAxes(items: readonly BrowseItem[]): Axis[] {
  const keys = new Set<string>();
  for (const item of items) {
    for (const key of Object.keys(item.definition.tags ?? {})) {
      if (key !== 'context' && key !== 'role') {
        keys.add(key);
      }
    }
  }
  return [
    ...BUILTIN_AXES.map((name): Axis => ({ tag: 'builtin', name })),
    ...[...keys].sort().map((key): Axis => ({ tag: 'tag', key }))
  ];
}

/** 絞り込みの条件をすべて満たす定義だけにする(条件の AND)。 */
export function applyFilters(items: readonly BrowseItem[], filters: readonly BrowseFilter[], ctx: BrowseContext): BrowseItem[] {
  return items.filter((item) => filters.every((f) => axisValues(item, f.axis, ctx).includes(f.value)));
}

/** 軸の値ごとの件数(絞り込みの値を選ぶ QuickPick 用・多い順)。 */
export function valueCounts(items: readonly BrowseItem[], axis: Axis, ctx: BrowseContext): Array<{ readonly value: string; readonly count: number }> {
  const counts = new Map<string, number>();
  for (const item of items) {
    for (const value of axisValues(item, axis, ctx)) {
      counts.set(value, (counts.get(value) ?? 0) + 1);
    }
  }
  return [...counts.entries()].map(([value, count]) => ({ value, count })).sort((a, b) => b.count - a.count || a.value.localeCompare(b.value));
}

/** 閲覧の木の節。 */
export type BrowseNode =
  | {
      readonly tag: 'group';
      readonly axis: Axis;
      readonly value: string;
      /** order の何段目か(0 始まり) */
      readonly depth: number;
      readonly items: readonly BrowseItem[];
    }
  | { readonly tag: 'item'; readonly item: BrowseItem }
  | { readonly tag: 'message'; readonly label: string };

/** 値の並べ方 — 名前の順、「(不明)」と「なし」は最後。 */
function valueOrder(a: string, b: string): number {
  const last = (v: string): number => (v === UNKNOWN_VALUE || v === NONE_VALUE ? 1 : 0);
  return last(a) - last(b) || a.localeCompare(b);
}

/** 1 段分の束を作る(定義は持つ値の数だけの束に入る)。 */
function groupsAt(items: readonly BrowseItem[], order: readonly Axis[], depth: number, ctx: BrowseContext): BrowseNode[] {
  const axis = order[depth];
  const byValue = new Map<string, BrowseItem[]>();
  for (const item of items) {
    for (const value of axisValues(item, axis, ctx)) {
      const list = byValue.get(value);
      if (list === undefined) {
        byValue.set(value, [item]);
      } else {
        list.push(item);
      }
    }
  }
  return [...byValue.keys()]
    .sort(valueOrder)
    .map((value) => ({ tag: 'group', axis, value, depth, items: byValue.get(value) ?? [] }));
}

/** 末端の定義を並べる(file の path・行の順)。 */
function itemNodes(items: readonly BrowseItem[]): BrowseNode[] {
  return [...items]
    .sort((a, b) => a.path.localeCompare(b.path) || a.definition.range.start.line - b.definition.range.start.line)
    .map((item) => ({ tag: 'item', item }));
}

/** 木の最上段 — 絞り込んでから、軸の順の 1 段目で束ねる(軸が無ければ定義を並べる)。 */
export function browseRoots(items: readonly BrowseItem[], view: BrowseView, ctx: BrowseContext): BrowseNode[] {
  const filtered = applyFilters(items, view.filters, ctx);
  if (filtered.length === 0) {
    return [{ tag: 'message', label: '条件に合う定義はありません' }];
  }
  return view.order.length === 0 ? itemNodes(filtered) : groupsAt(filtered, view.order, 0, ctx);
}

/** 節の子(展開した時に作る)— 次の軸があればその束、無ければ定義。 */
export function browseChildren(node: BrowseNode, view: BrowseView, ctx: BrowseContext): BrowseNode[] {
  switch (node.tag) {
    case 'group':
      return node.depth + 1 < view.order.length ? groupsAt(node.items, view.order, node.depth + 1, ctx) : itemNodes(node.items);
    case 'item':
    case 'message':
      return [];
    default: {
      const unreachable: never = node;
      throw new Error(`網羅されていない節: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 束の中の違反の数(束の項目の説明用)。 */
export function groupViolationCount(items: readonly BrowseItem[], ctx: BrowseContext): number {
  return items.reduce((n, item) => n + violationsOf(item, ctx).length, 0);
}

/** 今の見方を 1 行で説明する(view の説明欄)。 */
export function describeView(view: BrowseView): string {
  const order = view.order.length === 0 ? '(並べない)' : view.order.map(axisLabel).join(' ▸ ');
  const filters = view.filters.map((f) => `${axisLabel(f.axis)} = ${f.value}`).join(' かつ ');
  return filters === '' ? order : `${order} · 条件: ${filters}`;
}

/** 同梱の見方(設定に無くても出す)。 */
export const DEFAULT_BROWSE_VIEWS: readonly BrowseView[] = [
  { name: 'service ▸ 層', order: [{ tag: 'builtin', name: 'service' }, { tag: 'builtin', name: 'layer' }], filters: [] },
  { name: '層 ▸ service', order: [{ tag: 'builtin', name: 'layer' }, { tag: 'builtin', name: 'service' }], filters: [] },
  { name: '生の副作用 ▸ service', order: [{ tag: 'builtin', name: 'raw' }, { tag: 'builtin', name: 'service' }], filters: [] }
];

/** 保存した見方の設定の形(JSON)。 */
export interface BrowseViewSetting {
  readonly name: string;
  readonly order: string[];
  readonly filters: Array<{ readonly axis: string; readonly value: string }>;
}

/** 見方を設定に書く形にする。 */
export function toSetting(view: BrowseView): BrowseViewSetting {
  return {
    name: view.name,
    order: view.order.map(axisId),
    filters: view.filters.map((f) => ({ axis: axisId(f.axis), value: f.value }))
  };
}

/** 設定の見方の一覧を読む(形の違う見方は理由を返して飛ばす)。 */
export function parseBrowseViews(setting: unknown): { readonly views: BrowseView[]; readonly problems: string[] } {
  if (setting === undefined || setting === null) {
    return { views: [], problems: [] };
  }
  if (!Array.isArray(setting)) {
    return { views: [], problems: ['設定 browseViews は見方の配列であること'] };
  }
  const views: BrowseView[] = [];
  const problems: string[] = [];
  setting.forEach((raw: unknown, i) => {
    const where = `browseViews[${i}]`;
    if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
      problems.push(`${where} が object でない`);
      return;
    }
    const fields = new Map<string, unknown>(Object.entries(raw));
    const name = fields.get('name');
    const order = fields.get('order');
    const filters = fields.get('filters') ?? [];
    if (typeof name !== 'string' || name === '') {
      problems.push(`${where}.name が文字列でない`);
      return;
    }
    if (!Array.isArray(order) || order.length > 3) {
      problems.push(`${where}.order は軸の名前の配列(3 段まで)であること`);
      return;
    }
    const axes = order.map((a: unknown) => (typeof a === 'string' ? parseAxis(a) : undefined));
    if (axes.some((a) => a === undefined)) {
      problems.push(`${where}.order に知らない軸(使える軸: ${BUILTIN_AXES.join(', ')}, tag:<鍵>)`);
      return;
    }
    if (!Array.isArray(filters)) {
      problems.push(`${where}.filters が配列でない`);
      return;
    }
    const parsedFilters: BrowseFilter[] = [];
    for (const f of filters) {
      const item: unknown = f;
      const entry = typeof item === 'object' && item !== null ? new Map<string, unknown>(Object.entries(item)) : undefined;
      const axisText = entry?.get('axis');
      const value = entry?.get('value');
      const axis = typeof axisText === 'string' ? parseAxis(axisText) : undefined;
      if (axis === undefined || typeof value !== 'string') {
        problems.push(`${where}.filters に読めない条件 ${JSON.stringify(item)}`);
        return;
      }
      parsedFilters.push({ axis, value });
    }
    views.push({ name, order: axes.filter((a): a is Axis => a !== undefined), filters: parsedFilters });
  });
  return { views, problems };
}

/** 保存した見方の一覧に 1 つ足す(同じ名前は置き換える)。 */
export function upsertView(saved: readonly BrowseViewSetting[], view: BrowseView): BrowseViewSetting[] {
  return [...saved.filter((s) => s.name !== view.name), toSetting(view)];
}

/** 定義の場所の表示(`module:行`)。 */
export function itemPlace(item: BrowseItem): string {
  return `${item.module || path.basename(item.path)}:${item.definition.range.start.line + 1}`;
}
