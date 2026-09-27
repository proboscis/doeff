import * as assert from 'assert';
import * as fs from 'fs';
import * as path from 'path';
import { parseLintJson, type LintReport } from '../../lint/contract';
import { lintArgs, splitCommand } from '../../lint/runner';
import { LintStore } from '../../lint/store';
import {
  diagnosticOf,
  displayRange,
  inlineAnnotations,
  lintChildren,
  mapRoots,
  OUTSIDE_LAYERS,
  ruleNodes,
  violationCount,
  violationRoots,
  type LintNode
} from '../../lint/view';

const FIXTURES = path.join(__dirname, '..', '..', '..', 'test-fixtures', 'lint');

/** fixture の file の中身を文字列で読む。 */
function readFixture(name: string): string {
  return fs.readFileSync(path.join(FIXTURES, name), 'utf8');
}

/** 正しい fixture を契約の入口で読む。 */
function report(name: string): LintReport {
  const parsed = parseLintJson(readFixture(name));
  if (parsed.tag !== 'ok') {
    assert.fail(`fixture ${name} を読めない: ${parsed.reason}`);
  }
  return parsed.report;
}

/** 節を短い文字列にする(木の形を比べる用)。 */
function show(node: LintNode): string {
  switch (node.tag) {
    case 'law':
      return `law ${node.label} (${node.violations.length})`;
    case 'file':
      return `file ${node.label} (${node.violations.length})`;
    case 'violation':
      return `violation ${node.violation.range.start.line} ${node.violation.severity}`;
    case 'rule':
      return `rule ${node.rule.rule}${node.rule.wired ? '' : ' (針なし)'}`;
    case 'layer':
      return `layer ${node.label} (${violationCount(node)})`;
    case 'dir':
      return `dir ${node.label} (${violationCount(node)})`;
    case 'module':
      return `module ${node.entry.relative} (${node.entry.module.violations})`;
    case 'message':
      return `(${node.label})`;
  }
}

suite('linter の出力の契約の読み込み', () => {
  test('契約どおりの出力は全部読める', () => {
    const r = report('report.json');
    assert.strictEqual(r.violations.length, 3);
    assert.strictEqual(r.violations[0].law, 'core-imports-only-intent');
    assert.strictEqual(r.violations[2].law, null);
    assert.strictEqual(r.modules.length, 6);
    assert.strictEqual(r.rules[1].wired, false);
    assert.deepStrictEqual(r.errors, ['/repo/broken.hy: 括弧が閉じていない']);
  });

  test('版違い・欄の欠け・契約に無い重さ・壊れた JSON は理由つきで捨てる', () => {
    const reason = (name: string): string => {
      const parsed = parseLintJson(readFixture(name));
      return parsed.tag === 'rejected' ? parsed.reason : '(読めてしまった)';
    };
    assert.ok(reason('bad-version.json').startsWith('契約の版が違う'), reason('bad-version.json'));
    assert.match(reason('missing-law.json'), /violations\[0\]: 欄 "law" が無い/);
    assert.match(reason('bad-severity.json'), /severity: 契約に無い値 "fatal"/);
    const broken = parseLintJson('{"version": 1, "root"');
    assert.strictEqual(broken.tag, 'rejected');
  });
});

suite('linter の呼び方', () => {
  test('命令の文字列を引数に分ける(引用の中の空白は区切らない・空なら無効)', () => {
    assert.deepStrictEqual(splitCommand('doeff-linter --output-format editor-json'), ['doeff-linter', '--output-format', 'editor-json']);
    assert.deepStrictEqual(splitCommand(`uv run "my linter" --json ''`), ['uv', 'run', 'my linter', '--json', '']);
    assert.deepStrictEqual(splitCommand('   '), []);
  });

  test('全体は引数なし、編集中の file は --stdin --path', () => {
    const base = ['--output-format', 'editor-json'];
    assert.deepStrictEqual(lintArgs(base, { tag: 'root', root: '/repo' }), base);
    assert.deepStrictEqual(lintArgs(base, { tag: 'stdin', root: '/repo', path: '/repo/a.hy', text: '(x)' }), [
      ...base,
      '--stdin',
      '--path',
      '/repo/a.hy'
    ]);
  });
});

suite('linter の結果の置き場', () => {
  test('1 file の実行はその file の違反と要約だけを差し替え、他の file と規則の一覧は全体の結果のまま', () => {
    const store = new LintStore();
    store.replaceFile('/repo', '/repo/controllers/kanban/core/goal.hy', report('single-file.json'));
    assert.deepStrictEqual(store.violations(), [], '全体の結果が無い root には差し替えない');
    store.replaceRoot('/repo', report('report.json'));
    assert.strictEqual(store.violations().length, 3);
    store.replaceFile('/repo', '/repo/controllers/kanban/core/goal.hy', report('single-file.json'));
    assert.deepStrictEqual(
      store.violations().map((v) => `${path.basename(v.path)}:${v.range.start.line}`).sort(),
      ['effects.hy:0', 'goal.hy:3']
    );
    const goal = store.modules().find((m) => m.module.path.endsWith('goal.hy'));
    assert.strictEqual(goal?.module.violations, 1);
    assert.strictEqual(store.rules().length, 3);
    store.replaceRoot('/repo', report('report.json'));
    assert.strictEqual(store.violations().length, 3, '全体の実行で差し替えは捨てる');
  });
});

suite('linter の結果の見せ方', () => {
  test('波線の文 — 違反の文・直し方・規則の ID と law、重さは契約のまま', () => {
    const [newBreach, known, info] = report('report.json').violations;
    const d = diagnosticOf(newBreach);
    assert.strictEqual(d.severity, 'error');
    assert.strictEqual(d.code, 'DOEFF201');
    assert.deepStrictEqual(d.message.split('\n'), [
      '層 core が層 foundation の controllers.foundation.records を import している',
      'これは何か: この file は層 core — path が controllers/kanban/core/ の下',
      'なぜ違反か: core は intent だけを import する。controllers.foundation.records は層 foundation',
      'law: core の module は intent の module だけを import する',
      '直し方: core は intent だけを読む。記録は intent の effect を出し、protocol の翻訳の handler で訳す',
      '規則 DOEFF201 · law core-imports-only-intent · ADR-CONTROLLERS-CHOOSE-ENVIRONMENT-BY-HANDLER-SET'
    ]);
    assert.strictEqual(diagnosticOf(known).severity, 'warning');
    assert.ok(diagnosticOf(known).message.endsWith('登録簿に載った既知の破れ)'), diagnosticOf(known).message);
    assert.deepStrictEqual(diagnosticOf(info).message.split('\n'), ['setv の再代入', '規則 DOEFF101']);
  });

  test('違反の木 — law(無ければ規則の ID)→ file → 違反(行の順)', () => {
    const roots = violationRoots(report('report.json').violations);
    assert.deepStrictEqual(roots.map(show), ['law DOEFF101 (1)', 'law core-imports-only-intent (2)']);
    const [goal] = lintChildren(roots[1]);
    assert.strictEqual(show(goal), 'file goal.hy (2)');
    assert.deepStrictEqual(lintChildren(goal).map(show), ['violation 3 warning', 'violation 12 error']);
    assert.deepStrictEqual(violationRoots([]).map(show), ['(linter の違反はありません)']);
  });

  test('規則の一覧 — 針のある規則が先、針なし(見ていない規則)は後', () => {
    assert.deepStrictEqual(ruleNodes(report('report.json').rules).map(show), [
      'rule DOEFF101',
      'rule DOEFF201',
      'rule DOEFF299 (針なし)'
    ]);
  });

  test('層の地図 — 層の順(知らない層は後、層の外は最後)→ dir → file、数は linter の要約のまま', () => {
    const modules = report('report.json').modules.map((module) => ({ root: '/repo', module }));
    const roots = mapRoots(modules, report('report.json').violations);
    assert.deepStrictEqual(roots.map(show), [
      'layer core (2)',
      'layer intent (1)',
      'layer protocol (0)',
      'layer sandbox (0)',
      `layer ${OUTSIDE_LAYERS} (0)`
    ]);
    const [controllers] = lintChildren(roots[0]);
    assert.strictEqual(show(controllers), 'dir controllers (2)');
    const [kanban] = lintChildren(controllers);
    const [core] = lintChildren(kanban);
    assert.deepStrictEqual(lintChildren(core).map(show), [
      'module controllers/kanban/core/goal.hy (2)',
      'module controllers/kanban/core/plan.hy (0)'
    ]);
    assert.deepStrictEqual(lintChildren(roots[4]).map(show), ['dir scripts (0)']);
  });

  test('地図の file を展開すると、その file の違反が行の順に出る(違反の無い file は子なし)', () => {
    const r = report('report.json');
    const roots = mapRoots(r.modules.map((module) => ({ root: '/repo', module })), r.violations);
    const core = lintChildren(lintChildren(lintChildren(roots[0])[0])[0])[0];
    const [goal, plan] = lintChildren(core);
    assert.deepStrictEqual(lintChildren(goal).map(show), ['violation 3 warning', 'violation 12 error']);
    assert.deepStrictEqual(lintChildren(plan), []);
  });
});

suite('linter の違反を editor の上で見つけやすくする', () => {
  test('行末の注記 — 行ごとに 1 つ、最も強い重さの先頭の違反と、同じ行の他の件数', () => {
    const [newBreach, known, info] = report('report.json').violations;
    const sameLine = { ...info, severity: 'error' as const, range: known.range, rule: 'DOEFF102', message: '同じ行の別の違反' };
    const annotations = inlineAnnotations([newBreach, known, sameLine, info]);
    assert.deepStrictEqual(
      annotations.map((a) => `${a.line} ${a.severity} ${a.count} ${a.text}`),
      [
        '0 info 1 ● DOEFF101 setv の再代入',
        '3 error 2 ● DOEFF102 同じ行の別の違反(他 1 件)',
        '12 error 1 ● DOEFF201 層 core が層 foundation の controllers.foundation.records を import している'
      ]
    );
  });

  test('長い文は … で切る', () => {
    const [v] = report('report.json').violations;
    const [a] = inlineAnnotations([{ ...v, message: 'あ'.repeat(200) }]);
    assert.ok(a.text.endsWith('…'));
    assert.ok(a.text.length < 110);
  });

  test('空(0 幅)の範囲は行全体に広げ、幅のある範囲はそのまま', () => {
    const [newBreach, , info] = report('report.json').violations;
    assert.deepStrictEqual(displayRange(info.range, 17), { start: { line: 0, character: 0 }, end: { line: 0, character: 17 } });
    assert.strictEqual(displayRange(info.range, undefined).end.character, 10000);
    assert.deepStrictEqual(displayRange(newBreach.range, 5), newBreach.range);
  });
});
