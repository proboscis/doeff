import * as assert from 'assert';
import * as fs from 'fs';
import * as path from 'path';
import { parseLintJson, type LintModule, type LintReport } from '../../lint/contract';
import {
  layerBadge,
  layerStatusText,
  layerSummary,
  layerTableMarkdown,
  tagAt,
  tagHoverMarkdown,
  violationExplanationLines
} from '../../lint/layers';
import { diagnosticOf } from '../../lint/view';

const FIXTURES = path.join(__dirname, '..', '..', '..', 'test-fixtures', 'lint');

/** fixture を契約の入口で読む(読めなければ理由つきで落とす)。 */
function report(name: string): LintReport {
  const parsed = parseLintJson(fs.readFileSync(path.join(FIXTURES, name), 'utf8'));
  if (parsed.tag !== 'ok') {
    assert.fail(`fixture ${name} を読めない: ${parsed.reason}`);
  }
  return parsed.report;
}

/** path の末尾で module を引く。 */
function moduleOf(r: LintReport, suffix: string): LintModule {
  const found = r.modules.find((m) => m.path.endsWith(suffix));
  assert.ok(found, `${suffix} が無い`);
  return found;
}

suite('linter の層の説明の読み込み(契約の更新 3)', () => {
  test('layers・explanation・layer_reason を読む', () => {
    const r = report('report.json');
    assert.deepStrictEqual(r.layers.map((l) => l.name), ['core', 'intent', 'protocol', 'foundation']);
    assert.strictEqual(r.layers[2].knows, null);
    assert.strictEqual(r.violations[0].explanation?.lawStatement, 'core の module は intent の module だけを import する');
    assert.strictEqual(r.violations[2].explanation, null);
    assert.strictEqual(moduleOf(r, 'goal.hy').layerReason, 'path が controllers/<文脈>/core/ の下');
  });

  test('更新 3 の欄が無い古い出力は、説明を空として読む', () => {
    const old = report('single-file.json');
    assert.deepStrictEqual(old.layers, []);
    assert.strictEqual(old.violations[0].explanation, null);
    assert.strictEqual(old.modules[0].layerReason, null);
  });

  test('更新 3 の欄が在って形が違えば、理由つきで全体を捨てる', () => {
    const parsed = parseLintJson(fs.readFileSync(path.join(FIXTURES, 'bad-explanation.json'), 'utf8'));
    assert.strictEqual(parsed.tag, 'rejected');
    assert.match(parsed.tag === 'rejected' ? parsed.reason : '', /explanation: 欄 "reason" が無い/);
  });
});

suite('層を見分ける表示(文はすべて linter から)', () => {
  test('エクスプローラーの印 — 文字は層の頭文字、色は違反を優先、層の外は印なし', () => {
    const r = report('report.json');
    const goal = layerBadge(moduleOf(r, 'goal.hy'), r.layers);
    assert.deepStrictEqual(goal, {
      badge: 'C',
      color: 'problemsErrorIcon.foreground',
      tooltip: '層 core — 業務の判断と Program\npath が controllers/<文脈>/core/ の下\nlinter の違反 2 件'
    });
    const wire = layerBadge(moduleOf(r, 'wire.hy'), r.layers);
    assert.strictEqual(wire?.badge, 'P');
    assert.strictEqual(wire?.color, 'charts.green', 'linter の layers の 3 番目の色');
    assert.strictEqual(layerBadge(moduleOf(r, 'tool.hy'), r.layers), undefined);
    assert.strictEqual(layerBadge(moduleOf(r, 'thing.hy'), r.layers)?.color, 'foreground', 'linter が説明しない層');
  });

  test('ステータスバーの文 — 説明が無ければ層の名前だけ', () => {
    const r = report('report.json');
    assert.strictEqual(layerStatusText(moduleOf(r, 'wire.hy'), r.layers), '層: protocol — 相手の話し方へ訳す handler(context: chat)');
    assert.strictEqual(layerStatusText(moduleOf(r, 'wire.hy'), []), '層: protocol(context: chat)');
    assert.strictEqual(layerStatusText(moduleOf(r, 'tool.hy'), r.layers), '層: (層の外)');
    assert.strictEqual(layerSummary('foundation', r.layers), '');
  });

  test('説明の表 — linter の layers の並びで、無い欄は空。layers が無ければ設定の場所を案内する', () => {
    const r = report('report.json');
    const table = layerTableMarkdown(r.layers);
    assert.ok(table.includes('| **protocol** | 相手の話し方へ訳す handler |  |  |  |'));
    assert.ok(table.indexOf('**core**') < table.indexOf('**intent**'));
    assert.ok(layerTableMarkdown([]).includes('[tool.doeff-linter.layers.describe.<層>]'));
  });

  test('タグの hover — MODULE-TAGS・:tags・:role の上でだけ、書かれた role と linter の層と問い', () => {
    const r = report('report.json');
    const line = '(val MODULE-TAGS {:context "kanban" :role "program"})';
    assert.strictEqual(tagAt(line, 2), undefined, 'val の上は対象外');
    const tag = tagAt(line, 8);
    assert.deepStrictEqual(tag, { role: 'program', context: 'kanban' });
    assert.deepStrictEqual(tagAt('  {:pre [] :post [] :tags {:context "chat" :role "protocol"}}', 22), { role: 'protocol', context: 'chat' });
    const hover = tagHoverMarkdown(tag ?? { role: null, context: null }, moduleOf(r, 'goal.hy'), r.layers);
    assert.ok(hover.includes('**層 core**(linter) — 業務の判断と Program'));
    assert.ok(hover.includes('層の決め方: path が controllers/<文脈>/core/ の下'));
    assert.ok(hover.includes('- 迷った時の問い: 通信の方法が変わってもこのコードは変わらないか?'));
    assert.ok(tagHoverMarkdown({ role: 'x', context: null }, undefined, r.layers).includes('linter の結果にまだありません'));
  });

  test('違反の説明 — これは何か・なぜ違反か・law・直し方を問題の一覧の文にも出す', () => {
    const [first, , info] = report('report.json').violations;
    assert.deepStrictEqual(violationExplanationLines(first), [
      'これは何か: この file は層 core — path が controllers/kanban/core/ の下',
      'なぜ違反か: core は intent だけを import する。controllers.foundation.records は層 foundation',
      'law: core の module は intent の module だけを import する',
      '直し方: core は intent だけを読む。記録は intent の effect を出し、protocol の翻訳の handler で訳す'
    ]);
    assert.deepStrictEqual(violationExplanationLines(info), []);
    assert.ok(diagnosticOf(first).message.includes('なぜ違反か: core は intent だけを import する'));
  });
});
