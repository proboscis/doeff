import * as assert from 'assert';
import * as fs from 'fs';
import * as path from 'path';
import { parseLintJson, type LintReport } from '../../lint/contract';
import { binaryCandidates, lintArgs, splitCommand } from '../../lint/runner';
import { LintStore } from '../../lint/store';
import {
  atLeast,
  diagnosticOf,
  diagnosticsByPath,
  parseMinSeverity,
  displayRange,
  groupDescription,
  groupLabel,
  groupTooltipLines,
  inlineAnnotations,
  lintChildren,
  mapRoots,
  OUTSIDE_LAYERS,
  panelViolationRoots,
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
    case 'summary':
      return `summary ${node.level} ${node.counts.total}`;
    case 'group':
      return `group ${node.level} ${groupLabel(node.rule, node.summary)} (${node.violations.length})`;
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

  test('規則の短い名と家族(更新 6)— 出ていれば読み、古い linter の出力(欄なし)は null・契約に無い家族は理由つきで捨てる', () => {
    const r = report('report.json');
    assert.strictEqual(r.rules[0].title, '層の向きに逆らう import');
    assert.strictEqual(r.rules[0].family, 'layer');
    assert.strictEqual(r.rules[2].title, null);
    assert.strictEqual(r.rules[2].family, null);
    // 知らない家族は出力を捨てず、その規則だけ一般の印(null)にして「拡張が古い」の理由に控える(#848)
    const castle = parseLintJson(readFixture('report.json').replace('"family": "layer"', '"family": "castle"'));
    assert.strictEqual(castle.tag, 'ok');
    assert.strictEqual(castle.tag === 'ok' ? castle.report.rules[0].family : 'x', null);
    assert.match(castle.tag === 'ok' ? castle.report.unknown.join('\n') : '', /rules\[0\]\.family: 知らない語 "castle"/);
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

  test('素の名の頭は開発版の置き場を PATH より先に試す(書き込み直後の hook と同じ順・agora-redesign #848)・path を名指した頭はそのまま', () => {
    assert.deepStrictEqual(binaryCandidates('doeff-linter', '/h'), [
      path.join('/h', '.local', 'share', 'doeff-linter-dev', 'doeff-linter'),
      'doeff-linter',
      path.join('/h', '.cargo', 'bin', 'doeff-linter'),
      path.join('/h', '.local', 'bin', 'doeff-linter'),
      path.join('/opt/homebrew/bin', 'doeff-linter'),
      path.join('/usr/local/bin', 'doeff-linter')
    ]);
    assert.deepStrictEqual(binaryCandidates('/w/target/release/doeff-linter', '/h'), ['/w/target/release/doeff-linter']);
  });

  test('全体は引数なし、編集中の file は --stdin --path', () => {
    const base = ['--output-format', 'editor-json'];
    assert.deepStrictEqual(lintArgs(base, { tag: 'root', root: '/repo' }), base);
    assert.deepStrictEqual(lintArgs(base, { tag: 'stdin', root: '/repo', path: '/repo/a.hy', text: '(x)', version: 1 }), [
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

  test('違反の木 — 重大さの要約の行、(重大さ, 規則) の束(重い順)→ file → 違反(行の順)。重大さの欄の無い古い linter は重さから', () => {
    const r = report('report.json');
    const roots = violationRoots(r.violations, r.rules);
    assert.deepStrictEqual(roots.map(show), [
      'summary critical 0',
      'summary major 1',
      'summary minor 1',
      'summary info 1',
      'group major DOEFF201 層の向きに逆らう import (1)',
      'group minor DOEFF201 層の向きに逆らう import (1)',
      'group info DOEFF101 (1)'
    ]);
    const [goal] = lintChildren(roots[4]);
    assert.strictEqual(show(goal), 'file goal.hy (1)');
    assert.deepStrictEqual(lintChildren(goal).map(show), ['violation 12 error']);
    assert.deepStrictEqual(violationRoots([], r.rules).map(show), ['(linter の違反はありません)']);
    // 規則の一覧がまだ無い(1 file の実行だけ)時も、番号だけの見出しで束ねる
    assert.deepStrictEqual(violationRoots(r.violations, []).slice(4).map(show), ['group major DOEFF201 (1)', 'group minor DOEFF201 (1)', 'group info DOEFF101 (1)']);
  });

  test('全体の実行の失敗は表に出し、違反 0 件の「違反はありません」と取り違えない(agora-redesign #1631)', () => {
    const r = report('report.json');
    const reason = '終了コード 2: architecture.hy の誤り: :in は lines か names';
    const store = new LintStore();
    // 古い binary が設定を読めず、最初の全体の実行から落ちた — 表は失敗の札だけ
    store.failRoot('/w', reason);
    assert.deepStrictEqual(
      panelViolationRoots(store.rootRuns(), store.violations(), store.rules()).map(show),
      [`(linter が失敗(/w): ${reason})`]
    );
    // 成功すれば札は消える
    store.replaceRoot('/w', { ...r, root: '/w' });
    assert.deepStrictEqual(store.failures(), []);
    assert.strictEqual(panelViolationRoots(store.rootRuns(), store.violations(), store.rules())[0].tag, 'summary');
    // 成功の後に落ちたら、前の結果の上に札を足す(古い結果と分かるように)
    store.failRoot('/w', reason);
    const roots = panelViolationRoots(store.rootRuns(), store.violations(), store.rules()).map(show);
    assert.strictEqual(roots[0], `(linter が失敗(/w): ${reason})`);
    assert.strictEqual(roots[1], 'summary critical 0');
    // folder を外せば札も消える
    store.removeRoot('/w');
    assert.deepStrictEqual(panelViolationRoots(store.rootRuns(), store.violations(), store.rules()).map(show), ['(linter の違反はありません)']);
  });

  test('起動直後の「実行中」は結果が無いだけで「違反はありません」と取り違えない(agora-redesign #1650)', () => {
    const store = new LintStore();
    // 最初の全体の実行を始めた直後 — まだ結果が無い。表は「実行中」の札だけで「違反はありません」を出さない
    store.beginRoot('/w');
    const running = panelViolationRoots(store.rootRuns(), store.violations(), store.rules()).map(show);
    assert.deepStrictEqual(running, ['(linter を実行中(/w)…)']);
    assert.ok(!running.some((line) => line.includes('違反はありません')));
    // 全体の実行が終わり、違反が本当に 0 件ならそのとおり出す
    const empty = { ...report('report.json'), root: '/w', violations: [] };
    store.replaceRoot('/w', empty);
    assert.deepStrictEqual(
      panelViolationRoots(store.rootRuns(), store.violations(), store.rules()).map(show),
      ['(linter の違反はありません)']
    );
  });

  test('再実行の間は前の違反を消さない — 先頭が実行中の札、続いて前の summary 行(agora-redesign #1650)', () => {
    const r = report('report.json');
    const reason = '終了コード 2: architecture.hy の誤り: :in は lines か names';
    const store = new LintStore();
    store.replaceRoot('/w', { ...r, root: '/w' });
    assert.strictEqual(store.violations().length, 3);
    // 再実行を始めても、前の違反は波線にも表にも残る(消えない)
    store.beginRoot('/w');
    const running = panelViolationRoots(store.rootRuns(), store.violations(), store.rules()).map(show);
    assert.strictEqual(running[0], '(linter を実行中(/w)…)');
    assert.strictEqual(running[1], 'summary critical 0');
    assert.strictEqual(store.violations().length, 3, '実行中も前の違反をそのまま返す');
    // 実行中に落ちたら、前の結果の上に失敗の札を足す(#1631 の表示のまま)
    store.failRoot('/w', reason);
    const failed = panelViolationRoots(store.rootRuns(), store.violations(), store.rules()).map(show);
    assert.strictEqual(failed[0], `(linter が失敗(/w): ${reason})`);
    assert.strictEqual(failed[1], 'summary critical 0');
  });

  test('束の件数と hover — 重さが混ざれば内訳、law の名と ADR と規則の文は hover に、名の無い古い linter には案内', () => {
    const r = report('report.json');
    const byRule = new Map<string, typeof r.violations[number][]>();
    for (const v of r.violations) {
      byRule.set(v.rule, [...(byRule.get(v.rule) ?? []), v]);
    }
    const group = (rule: string): LintNode => ({ tag: 'group', level: 'major', rule, summary: violationRoots(r.violations, r.rules).flatMap((n) => (n.tag === 'group' && n.rule === rule ? [n.summary] : []))[0], violations: byRule.get(rule) ?? [] });
    const plain = group('DOEFF101');
    const layer = group('DOEFF201');
    assert.ok(plain.tag === 'group' && layer.tag === 'group');
    assert.strictEqual(groupDescription(layer.violations), '2 件(error 1・warning 1)');
    assert.strictEqual(groupDescription(plain.violations), '1 件');
    assert.deepStrictEqual(groupTooltipLines(layer.rule, layer.summary, layer.violations), [
      'DOEFF201 層の向きに逆らう import — 2 件(error 1・warning 1)',
      'law: core-imports-only-intent(ADR-CONTROLLERS-CHOOSE-ENVIRONMENT-BY-HANDLER-SET)',
      'core は intent だけを import する'
    ]);
    const old = groupTooltipLines(plain.rule, plain.summary, plain.violations);
    assert.strictEqual(old[0], 'DOEFF101 — 1 件');
    assert.match(old[old.length - 1], /規則の短い名が無い/);
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

suite('linter の違反が数千件の時の出し方', () => {
  test('作業係 D の doeff-linter の実出力の抜粋は契約の入口を通る(layers・explanation・layer_reason・service)', () => {
    const r = report('real-excerpt.json');
    assert.strictEqual(r.layers.length, 5);
    assert.ok(r.violations.every((v) => v.explanation !== null));
    assert.ok(r.modules.every((m) => m.layerReason !== null));
    assert.ok(r.modules.every((m) => m.service === null), 'service の欄は在って、今の repo では null');
  });

  test('行末の注記と左端の印は既定で warning 以上だけ、波線は全部', () => {
    const violations = report('report.json').violations;
    assert.deepStrictEqual(atLeast(violations, 'warning').map((v) => v.severity), ['error', 'warning']);
    assert.deepStrictEqual(atLeast(violations, 'error').map((v) => v.severity), ['error']);
    assert.strictEqual(atLeast(violations, 'info').length, 3);
    const byPath = diagnosticsByPath(violations);
    assert.strictEqual([...byPath.values()].reduce((n, l) => n + l.length, 0), 3);
    assert.strictEqual(byPath.get('/repo/controllers/kanban/core/goal.hy')?.length, 2);
  });

  test('設定の最小の重さ — 知らない値は undefined(呼ぶ側が理由を出す)', () => {
    assert.strictEqual(parseMinSeverity('info'), 'info');
    assert.strictEqual(parseMinSeverity('fatal'), undefined);
    assert.strictEqual(parseMinSeverity(undefined), undefined);
  });
});
