import * as assert from 'assert';
import * as fs from 'fs';
import * as path from 'path';
import { parseLintJson, type LintReport } from '../../lint/contract';
import {
  ALL_VIOLATIONS,
  filterText,
  filterViolations,
  levelStatus,
  levelTally,
  nextLevelFilter,
  readSavedTally,
  saveTally,
  summaryDescription,
  summaryLabel
} from '../../lint/severity';
import { groupHeading, groupStanding, violationRoots, type LintNode } from '../../lint/view';

const FIXTURES = path.join(__dirname, '..', '..', '..', 'test-fixtures', 'lint');

/** 重大さの欄つきの fixture を契約の入口で読む。 */
function levels(): LintReport {
  const parsed = parseLintJson(fs.readFileSync(path.join(FIXTURES, 'levels.json'), 'utf8'));
  if (parsed.tag !== 'ok') {
    assert.fail(`levels.json を読めない: ${parsed.reason}`);
  }
  return parsed.report;
}

/** 節を短い文字列にする(木の形を比べる用)。 */
function show(node: LintNode): string {
  switch (node.tag) {
    case 'summary':
      return `${summaryLabel(node.level, node.counts)} | ${summaryDescription(node.counts, node.delta)}`;
    case 'group':
      return `${groupHeading(node.level, node.rule, node.summary, node.violations.length)} | ${groupStanding(node.violations)}`;
    case 'message':
      return `(${node.label})`;
    default:
      return node.tag;
  }
}

suite('違反の重大さ — 重大さ(repo の宣言)と立場(新しい・既知・照合中)の 2 軸', () => {
  test('契約の読み込み — level・standing・base_severity を読み、知らない語は捨てずに重さと registered から決めて控える', () => {
    const r = levels();
    const known = r.violations[0];
    assert.strictEqual(known.severity, 'warning');
    assert.strictEqual(known.baseSeverity, 'error');
    assert.strictEqual(known.standing, 'registered');
    assert.strictEqual(known.level, 'critical');
    const future = r.violations[10];
    assert.strictEqual(future.level, 'minor');
    assert.strictEqual(future.standing, 'new');
    assert.strictEqual(r.unknown.length, 2);
  });

  test('登録簿で波線の重さを下げても重大さは下げない — 既知の critical は critical で数える', () => {
    const tally = levelTally(levels().violations);
    assert.deepStrictEqual(tally.critical, { total: 3, new: 1, registered: 2, reconciling: 0 });
    assert.deepStrictEqual(tally.major, { total: 5, new: 3, registered: 1, reconciling: 1 });
    assert.deepStrictEqual(tally.minor, { total: 2, new: 1, registered: 1, reconciling: 0 });
    assert.deepStrictEqual(tally.info, { total: 1, new: 1, registered: 0, reconciling: 0 });
  });

  test('欄の最上段は重大さの要約 4 行、その下は critical から・新しい分の多い順の (重大さ, 規則) の束', () => {
    const r = levels();
    assert.deepStrictEqual(violationRoots(r.violations, r.rules).map(show), [
      'CRITICAL 3 | 新しい 1 · 既知 2',
      'MAJOR 5 | 新しい 3 · 既知 1 · 照合中 1',
      'MINOR 2 | 新しい 1 · 既知 1',
      'INFO 1 | 新しい 1 · 既知 0',
      'CRITICAL 3 · DOEFF114 宣言に無い置き場所の module | 新しい 1 · 既知 2',
      'MAJOR 2 · DOEFF112 | 新しい 2 · 既知 0',
      'MAJOR 2 · DOEFF110 | 新しい 1 · 既知 1',
      'MAJOR 1 · DOEFF108 | 新しい 0 · 既知 0 · 照合中 1',
      'MINOR 1 · DOEFF999 | 新しい 1 · 既知 0',
      'MINOR 1 · DOEFF123 | 新しい 0 · 既知 1',
      'INFO 1 · DOEFF107 | 新しい 1 · 既知 0'
    ]);
  });

  test('絞り込み — critical だけ・major 以上・新しい分だけ。要約は絞り込みによらず全部の数、0 件なら札', () => {
    const r = levels();
    const critical = { level: 'critical', standing: 'all' } as const;
    assert.deepStrictEqual(filterViolations(r.violations, critical).map((v) => v.range.start.line), [1, 2, 3]);
    const freshMajor = { level: 'major', standing: 'new' } as const;
    assert.deepStrictEqual(filterViolations(r.violations, freshMajor).map((v) => v.range.start.line), [3, 5, 6, 7]);
    assert.strictEqual(filterViolations(r.violations, ALL_VIOLATIONS).length, r.violations.length);
    const roots = violationRoots(r.violations, r.rules, critical).map(show);
    assert.strictEqual(roots.length, 5);
    assert.strictEqual(roots[0], 'CRITICAL 3 | 新しい 1 · 既知 2');
    assert.strictEqual(roots[4], 'CRITICAL 3 · DOEFF114 宣言に無い置き場所の module | 新しい 1 · 既知 2');
    const none = violationRoots(r.violations.slice(0, 2), r.rules, { level: 'critical', standing: 'new' }).map(show);
    assert.strictEqual(none[4], '(絞り込みに当たる違反はありません)');
    assert.strictEqual(filterText(freshMajor), 'major 以上 · 新しい分だけ');
    assert.deepStrictEqual([nextLevelFilter('all'), nextLevelFilter('critical'), nextLevelFilter('major')], ['critical', 'major', 'all']);
  });

  test('前回からの増減 — 覚えた値を読み戻し、新しい分の差を要約と状態バーに出す(形の違う値は増減を出さない)', () => {
    const r = levels();
    const tally = levelTally(r.violations);
    const saved = readSavedTally(JSON.parse(JSON.stringify(saveTally(tally))));
    assert.deepStrictEqual(saved, saveTally(tally));
    assert.strictEqual(readSavedTally({ error: { new: 1, total: 1 } }), undefined);
    assert.strictEqual(readSavedTally(null), undefined);
    const before = { ...saveTally(tally), critical: { new: 0, total: 2 }, major: { new: 5, total: 7 } };
    const roots = violationRoots(r.violations, r.rules, ALL_VIOLATIONS, before).map(show);
    assert.strictEqual(roots[0], 'CRITICAL 3 | 新しい 1 · 既知 2 · 前回から新しい +1');
    assert.strictEqual(roots[1], 'MAJOR 5 | 新しい 3 · 既知 1 · 照合中 1 · 前回から新しい -2');
    assert.strictEqual(roots[2], 'MINOR 2 | 新しい 1 · 既知 1 · 前回から新しい ±0');
    const status = levelStatus(tally, before);
    assert.strictEqual(status.text, '$(doeff-lint-error) CRITICAL 3(新しい 1 +1) · MAJOR 新しい 3 -2');
    assert.strictEqual(status.alarming, true);
    const calm = levelStatus(levelTally(r.violations.slice(0, 2)), undefined);
    assert.strictEqual(calm.text, '$(doeff-lint-error) CRITICAL 2(新しい 0) · MAJOR 新しい 0');
    assert.strictEqual(calm.alarming, false);
    assert.ok(!calm.tooltip.includes('前回 ='));
  });
});
