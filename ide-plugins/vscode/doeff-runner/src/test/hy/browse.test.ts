import * as assert from 'assert';
import {
  applyFilters,
  availableAxes,
  axisId,
  axisValues,
  browseChildren,
  browseRoots,
  DEFAULT_BROWSE_VIEWS,
  describeView,
  parseAxis,
  parseBrowseViews,
  toSetting,
  upsertView,
  valueCounts,
  type Axis,
  type BrowseContext,
  type BrowseItem,
  type BrowseNode,
  type BrowseView
} from '../../hy/browse';
import type { HyDefinition } from '../../hy/contract';
import type { LintModule, LintViolation } from '../../lint/contract';
import { loadDocument } from './fixtures';

// 定義は hy-index の実出力(raw-workspace.json・生の副作用の証拠つき)に :tags を足した物、service と層は linter の要約の形。

const IO = '/raw/pkg/io_handlers.hy';
const PLAIN = '/raw/pkg/plain_open.hy';

/** 定義に :tags を足した閲覧の単位を作る。 */
function items(): BrowseItem[] {
  const document = loadDocument('raw-workspace.json');
  const tagsOf: Record<string, Record<string, string>> = {
    'http-handler': { context: 'fetch', role: 'foundation', owner: 'team-a' },
    'bad-program': { context: 'fetch', role: 'program' },
    'uses-open-var': { owner: 'team-b' }
  };
  return document.files.flatMap((file) =>
    file.definitions.map(
      (d): BrowseItem => ({
        path: file.path,
        module: file.module,
        definition: { ...d, tags: tagsOf[d.name] ?? null } as HyDefinition
      })
    )
  );
}

/** linter の要約(io_handlers は service fetch・層 foundation、plain_open は結果に無い)と、違反 1 件。 */
function context(): BrowseContext {
  const module: LintModule = {
    path: IO,
    layer: 'foundation',
    service: 'fetch',
    context: 'fetch-module',
    role: 'foundation',
    violations: 1,
    layerReason: null
  };
  const httpHandler = items().find((i) => i.definition.name === 'http-handler');
  assert.ok(httpHandler);
  const at = httpHandler.definition.range.start;
  const violation: LintViolation = {
    rule: 'DOEFF106',
    law: null,
    adr: null,
    severity: 'error',
    path: IO,
    range: { start: at, end: at },
    message: 'm',
    hint: null,
    key: null,
    registered: false,
    explanation: null,
    source: 'linter',
    probability: null
  };
  return {
    lintModule: (p) => (p === IO ? module : undefined),
    lintViolations: (p) => (p === IO ? [violation] : [])
  };
}

/** 名前の定義を引く。 */
function item(name: string): BrowseItem {
  const found = items().find((i) => i.definition.name === name);
  assert.ok(found, `${name} が無い`);
  return found;
}

const axis = (id: string): Axis => {
  const parsed = parseAxis(id);
  assert.ok(parsed, id);
  return parsed;
};

/** 節を短い文字列にする。 */
function show(node: BrowseNode): string {
  switch (node.tag) {
    case 'group':
      return `${node.value} (${node.items.length})`;
    case 'item':
      return node.item.definition.name;
    case 'message':
      return `(${node.label})`;
  }
}

suite('タグで閲覧 — 軸の値', () => {
  test('service・層は linter、context・role は定義の :tags → 無ければ linter の module、どちらも無ければ(不明)', () => {
    const ctx = context();
    assert.deepStrictEqual(axisValues(item('http-handler'), axis('service'), ctx), ['fetch']);
    assert.deepStrictEqual(axisValues(item('http-handler'), axis('layer'), ctx), ['foundation']);
    assert.deepStrictEqual(axisValues(item('http-handler'), axis('context'), ctx), ['fetch'], ':tags が先');
    assert.deepStrictEqual(axisValues(item('slow-helper'), axis('context'), ctx), ['fetch-module'], 'linter の module へ');
    assert.deepStrictEqual(axisValues(item('uses-open-var'), axis('service'), ctx), ['(不明)']);
    assert.deepStrictEqual(axisValues(item('uses-open-var'), axis('role'), ctx), ['(不明)']);
  });

  test(':tags に書いた任意の鍵は軸になる(context・role は決まった軸の側)', () => {
    const ctx = context();
    assert.deepStrictEqual(axisValues(item('http-handler'), axis('tag:owner'), ctx), ['team-a']);
    assert.deepStrictEqual(axisValues(item('slow-helper'), axis('tag:owner'), ctx), ['(不明)']);
    assert.deepStrictEqual(
      availableAxes(items()).map(axisId),
      ['service', 'layer', 'context', 'role', 'kind', 'raw', 'violation', 'tag:owner']
    );
  });

  test('kind・生の副作用の分類(複数・無ければ なし)・範囲に入る違反の規則', () => {
    const ctx = context();
    assert.deepStrictEqual(axisValues(item('clock-handler'), axis('kind'), ctx), ['defhandler']);
    assert.deepStrictEqual(axisValues(item('clock-handler'), axis('raw'), ctx), ['time']);
    assert.deepStrictEqual(axisValues(item('file-handler'), axis('raw'), ctx), ['file']);
    assert.deepStrictEqual(axisValues(item('d1'), axis('raw'), ctx), ['なし']);
    assert.deepStrictEqual(axisValues(item('http-handler'), axis('violation'), ctx), ['DOEFF106']);
    assert.deepStrictEqual(axisValues(item('d1'), axis('violation'), ctx), ['なし']);
  });
});

suite('タグで閲覧 — 並べ方と絞り込み', () => {
  test('2 段の並べ方 — 1 段目の束、展開で 2 段目、末端は定義(不明・なしは最後)', () => {
    const ctx = context();
    const view: BrowseView = { name: 'x', order: [axis('raw'), axis('kind')], filters: [] };
    const roots = browseRoots(items(), view, ctx);
    assert.deepStrictEqual(roots.map(show), ['async (1)', 'env (1)', 'file (2)', 'http (2)', 'process (1)', 'random (2)', 'time (2)', 'なし (13)']);
    const file = roots.find((r) => r.tag === 'group' && r.value === 'file');
    assert.ok(file);
    const kinds = browseChildren(file, view, ctx);
    assert.deepStrictEqual(kinds.map(show), ['defhandler (1)', 'effect-clause (1)']);
    assert.deepStrictEqual(browseChildren(kinds[0], view, ctx).map(show), ['file-handler']);
  });

  test('絞り込みは軸 = 値 の AND、合う物が無ければ札', () => {
    const ctx = context();
    const byOwner = applyFilters(items(), [{ axis: axis('tag:owner'), value: 'team-a' }], ctx);
    assert.deepStrictEqual(byOwner.map((i) => i.definition.name), ['http-handler']);
    const both = applyFilters(items(), [{ axis: axis('kind'), value: 'defk' }, { axis: axis('raw'), value: 'env' }], ctx);
    assert.deepStrictEqual(both.map((i) => i.definition.name), ['bad-program']);
    const none = browseRoots(items(), { name: 'x', order: [axis('kind')], filters: [{ axis: axis('kind'), value: 'defwhatever' }] }, ctx);
    assert.deepStrictEqual(none.map(show), ['(条件に合う定義はありません)']);
    assert.deepStrictEqual(valueCounts(items(), axis('service'), ctx).map((c) => `${c.value}:${c.count}`), [
      `fetch:${items().filter((i) => i.path === IO).length}`,
      `(不明):${items().filter((i) => i.path === PLAIN).length}`
    ]);
  });
});

suite('タグで閲覧 — 保存した見方', () => {
  test('同梱の見方 3 つと、見方の 1 行の説明', () => {
    assert.deepStrictEqual(DEFAULT_BROWSE_VIEWS.map((v) => v.name), ['service ▸ 層', '層 ▸ service', '生の副作用 ▸ service']);
    assert.strictEqual(
      describeView({ name: 'x', order: [axis('service'), axis('layer')], filters: [{ axis: axis('tag:owner'), value: 'team-a' }] }),
      'service ▸ 層 · 条件: owner = team-a'
    );
  });

  test('設定の読み書き — 形の違う見方は理由を返して飛ばし、同じ名前は置き換える', () => {
    const setting = [
      { name: 'owner 別', order: ['tag:owner', 'service'], filters: [{ axis: 'kind', value: 'defk' }] },
      { name: '壊れた', order: ['nope'] },
      { name: '長すぎ', order: ['kind', 'raw', 'layer', 'service'] },
      'x'
    ];
    const parsed = parseBrowseViews(setting);
    assert.deepStrictEqual(parsed.views.map((v) => v.name), ['owner 別']);
    assert.strictEqual(parsed.problems.length, 3);
    assert.deepStrictEqual(toSetting(parsed.views[0]), setting[0]);
    const saved = upsertView([toSetting(parsed.views[0])], { ...parsed.views[0], filters: [] });
    assert.deepStrictEqual(saved, [{ name: 'owner 別', order: ['tag:owner', 'service'], filters: [] }]);
    assert.deepStrictEqual(parseBrowseViews(undefined), { views: [], problems: [] });
  });
});
