import * as assert from 'assert';
import * as fs from 'fs';
import * as path from 'path';
import { parseLintJson, type LintBinding, type LintReport, type LintSignature } from '../../lint/contract';
import { LintStore } from '../../lint/store';
import { bindingHover, headerHover } from '../../defk/hover';
import {
  bindingPlan,
  bindingRevealed,
  effectAgreement,
  headerPlan,
  headerRevealed,
  multiBindingForms,
  parseRevealMode,
  shownEffects,
  signatureText,
  type LineSource
} from '../../defk/model';
import { bindingChipSvg, flowSvg, tagsSvg, textWidth, type Metrics } from '../../defk/svg';

const FIXTURES = path.join(__dirname, '..', '..', '..', 'test-fixtures', 'lint');

/** fixture の file の中身を文字列で読む。 */
function readFixture(name: string): string {
  return fs.readFileSync(path.join(FIXTURES, name), 'utf8');
}

/** 見出しの fixture(linter を実物の小さな repo に当てた出力)を契約の入口で読む。 */
function report(): LintReport {
  const parsed = parseLintJson(readFixture('signatures.json'));
  if (parsed.tag !== 'ok') {
    assert.fail(`fixture を読めない: ${parsed.reason}`);
  }
  return parsed.report;
}

/** fixture を作った source(core/flow.hy)の行。 */
const FLOW_SOURCE = `(import intent.rows [ReadRow Row])

(defk fetch [id n]
  {:tags {:context "demo" :role "program"}
   :pre [(: id str) (: n int)]
   :post [(: % Row)]
   :effects [ReadRow]}
  "行を読む"
  (<- row (ReadRow id))
  (var count 0)
  (:= count (+ count n))
  row)
`;

/** 文字列の行を行の口にする。 */
function lines(source: string): LineSource {
  const all = source.split('\n');
  return { lineCount: all.length, lineText: (line) => all[line] ?? '' };
}

const METRICS: Metrics = { fontSize: 13, lineHeight: 20, fontFamily: 'Menlo' };
const NO_SPRITES = (): undefined => undefined;

suite('defk の見出し — 契約 版 2 の読み込み', () => {
  test('signatures と bindings を読む(型・effect・Absent と Raise・tags・位置)', () => {
    const r = report();
    assert.strictEqual(r.version, 2);
    const sig = r.signatures[0];
    assert.strictEqual(sig.name, 'fetch');
    assert.deepStrictEqual(
      sig.params.map((p) => [p.name, p.type?.kind === 'name' ? p.type.name : '?']),
      [
        ['id', 'str'],
        ['n', 'int']
      ]
    );
    assert.strictEqual(sig.absent, true);
    assert.strictEqual(sig.declared?.[0].name, 'ReadRow');
    assert.strictEqual(sig.tags.get('role'), 'program');
    assert.deepStrictEqual(
      r.bindings.map((b) => [b.form, b.name, b.origin]),
      [
        ['<-', 'row', 'effect'],
        ['var', 'count', 'literal'],
        [':=', 'count', 'var']
      ]
    );
    assert.deepStrictEqual(r.unknown, []);
  });

  test('知らない語(規則の家族・見出しの種類・束縛の形・型の式の種類)は出力を捨てず、その項目だけ既定にして unknown に控える(#848)', () => {
    const castle = parseLintJson(readFixture('report.json').replace('"family": "layer"', '"family": "castle"'));
    assert.strictEqual(castle.tag, 'ok');
    if (castle.tag !== 'ok') {
      return;
    }
    assert.strictEqual(castle.report.rules[0].family, null, '知らない家族は一般の印(null)');
    assert.strictEqual(castle.report.violations.length, 3, '違反の欄は消えない');
    assert.match(castle.report.unknown[0], /rules\[0\]\.family: 知らない語 "castle"/);

    const text = readFixture('signatures.json').replace('"kind": "defk"', '"kind": "defmacro-k"').replace('"form": "var"', '"form": "let"');
    const newer = parseLintJson(text);
    assert.strictEqual(newer.tag, 'ok');
    if (newer.tag !== 'ok') {
      return;
    }
    assert.strictEqual(newer.report.signatures.length, 0, '知らない種類の見出しは描かない');
    assert.deepStrictEqual(
      newer.report.bindings.map((b) => b.name),
      ['row', 'count'],
      '知らない形の束縛だけを落とす'
    );
    assert.strictEqual(newer.report.unknown.length, 2);
    const union = parseLintJson(readFixture('signatures.json').replace('"kind": "union"', '"kind": "intersection"'));
    assert.ok(union.tag === 'ok');
    const answer = union.tag === 'ok' ? union.report.signatures[0].declared?.[0].answer : undefined;
    assert.deepStrictEqual(answer, { kind: 'unknown', text: '?' }, '知らない種類の型の式は読めない式として描く');
  });

  test('版 1 の出力(古い linter)も読み、見出しと束縛は空(違反の欄を消さない)', () => {
    const old = parseLintJson(readFixture('signatures.json').replace('"version": 2', '"version": 1'));
    assert.strictEqual(old.tag, 'ok');
    assert.deepStrictEqual(old.tag === 'ok' ? [old.report.signatures.length, old.report.bindings.length] : [], [0, 0]);
    const missing = parseLintJson(readFixture('signatures.json').replace('"version": 2', '"version": 1').replace('"signatures":', '"old_signatures":').replace('"bindings":', '"old_bindings":'));
    assert.strictEqual(missing.tag, 'ok', '版 1 には signatures の欄が無くてよい');
  });
});

suite('defk の見出し — 描く場所', () => {
  test('辞書が複数行: 頭の行を見出しに、辞書の 1 行目に型の流れ・2 行目に tags、辞書の文字は全部隠す', () => {
    const plan = headerPlan(report().signatures[0], lines(FLOW_SOURCE));
    assert.ok(plan !== undefined);
    assert.strictEqual(plan.headLine, 2);
    assert.deepStrictEqual(plan.name, { line: 2, start: 6, end: 11 });
    assert.deepStrictEqual(
      plan.hidden.map((h) => h.line),
      [3, 4, 5, 6]
    );
    assert.deepStrictEqual(plan.hidden[1], { line: 4, start: 3, end: 30 });
    assert.deepStrictEqual(plan.flowAt, { line: 3, character: 2, placement: 'before' });
    assert.deepStrictEqual(plan.tagsAt, { line: 4, character: 3, placement: 'before' });
    assert.deepStrictEqual(plan.lines, { start: 2, end: 11 });
  });

  test('辞書が 1 行: 型の流れは辞書の所、tags は頭の行の末尾', () => {
    const source = '(defk f [x]\n  {:pre [(: x int)] :post [(: % int)] :tags {:context "c" :role "judgment"}}\n  x)\n';
    const sig: LintSignature = {
      ...report().signatures[0],
      name: 'f',
      range: { start: { line: 0, character: 6 }, end: { line: 0, character: 7 } },
      fullRange: { start: { line: 0, character: 0 }, end: { line: 2, character: 4 } },
      contractRange: { start: { line: 1, character: 2 }, end: { line: 1, character: source.split('\n')[1].length } }
    };
    const plan = headerPlan(sig, lines(source));
    assert.ok(plan !== undefined);
    assert.deepStrictEqual(plan.flowAt, { line: 1, character: 2, placement: 'before' });
    assert.deepStrictEqual(plan.tagsAt, { line: 0, character: 11, placement: 'after' });
  });

  test('document と位置が合わない(版の食い違い)時は描かない', () => {
    assert.strictEqual(headerPlan(report().signatures[0], lines('(defk fetch [id n])\n')), undefined);
  });

  test('カーソルが入ったら元の文字: definition は定義の中・line は見出しの行だけ・never は見せない', () => {
    const plan = headerPlan(report().signatures[0], lines(FLOW_SOURCE));
    assert.ok(plan !== undefined);
    const inBody = [{ start: 9, end: 9 }];
    assert.strictEqual(headerRevealed(plan, inBody, 'definition'), true);
    assert.strictEqual(headerRevealed(plan, inBody, 'line'), false);
    assert.strictEqual(headerRevealed(plan, [{ start: 4, end: 4 }], 'line'), true);
    assert.strictEqual(headerRevealed(plan, [{ start: 4, end: 4 }], 'never'), false);
    assert.strictEqual(headerRevealed(plan, [{ start: 20, end: 20 }], 'definition'), false);
  });

  test('設定の値の読み方(知らない値は definition と理由)', () => {
    assert.deepStrictEqual(parseRevealMode(undefined), { mode: 'definition', problem: undefined });
    assert.strictEqual(parseRevealMode('line').mode, 'line');
    const odd = parseRevealMode('always');
    assert.strictEqual(odd.mode, 'definition');
    assert.match(odd.problem ?? '', /知らない値 "always"/);
  });
});

suite('defk の見出し — 型の流れと状態', () => {
  test('宣言と推論の突き合わせ(宣言だけ・推論だけ・宣言なし)', () => {
    const base = report().signatures[0];
    const readRow = base.declared![0];
    const put = { ...readRow, name: 'PutRow' };
    assert.deepStrictEqual(effectAgreement(base), { tag: 'match' });
    const off: LintSignature = { ...base, declared: [readRow], inferred: [readRow, put] };
    assert.deepStrictEqual(
      shownEffects(off).map((e) => [e.effect.name, e.state]),
      [
        ['ReadRow', 'both'],
        ['PutRow', 'undeclared']
      ]
    );
    assert.deepStrictEqual(effectAgreement({ ...base, declared: [readRow, put], inferred: [readRow] }), { tag: 'mismatch', count: 1 });
    assert.deepStrictEqual(effectAgreement({ ...base, declared: null }), { tag: 'undeclared' });
    assert.strictEqual(shownEffects({ ...base, declared: null })[0].state, 'inferred');
  });

  test('型の流れの文は (X, Y) -> Program[{E, Raise F}, Maybe[B]] の 1 つの形(deff は Program を付けない)', () => {
    const sig = report().signatures[0];
    assert.strictEqual(signatureText(sig), '(str, int) -> Program[{ReadRow, Raise Unreadable}, Maybe[Row]]');
    assert.strictEqual(signatureText({ ...sig, kind: 'deff', absent: false }), '(str, int) -> Row');
  });

  test('SVG: 型の札は editor の文字の大きさ・tags は小さい文字・effect が空なら「effect なし」', () => {
    const sig = report().signatures[0];
    const flow = flowSvg(sig, METRICS, NO_SPRITES);
    assert.strictEqual(flow.height, 20, '行の高さを変えない');
    assert.match(flow.svg, /font-size="13"[^>]*>Row</);
    assert.match(flow.svg, />Maybe\[</);
    assert.match(flow.svg, />Raise Unreadable</);
    assert.match(flow.svg, />ReadRow</);
    const empty = flowSvg({ ...sig, declared: [], inferred: [], raises: [], absent: false }, METRICS, NO_SPRITES);
    assert.match(empty.svg, />effect なし</);
    const tags = tagsSvg(sig.tags, { tag: 'match' }, 0, METRICS, NO_SPRITES);
    assert.match(tags.svg, /font-size="10"[^>]*>demo</);
    assert.match(tags.svg, />宣言 = 推論</);
    assert.match(tags.svg, />違反なし</);
    assert.match(tagsSvg(sig.tags, { tag: 'mismatch', count: 2 }, 3, METRICS, NO_SPRITES).svg, />違反 3</);
  });

  test('文字の幅の見積もり(半角 0.6・全角 1.0 文字分)', () => {
    assert.strictEqual(textWidth('Row', 10), 18);
    assert.strictEqual(textWidth('宣言', 10), 20);
  });

  test('hover: 型の流れの文・定義へ飛ぶ link・effect の答えの分け方', () => {
    const md = headerHover(report().signatures[0]);
    assert.match(md, /\(str, int\) -> Program\[\{ReadRow, Raise Unreadable\}, Maybe\[Row\]\]/);
    assert.match(md, /\[`Row`\]\(command:doeff-runner\.defk\.openLocation\?/);
    assert.match(md, /\| \[`ReadRow`\]\(command:[^|]+\| 宣言 = 推論 \| `Row \| Missing \| Unreadable` \| Missing \| Unreadable \|/);
  });
});

suite('束縛の型 — Text x <- …', () => {
  test('<- は開き括弧と頭と閉じ括弧を隠し、名の前に札・後ろに <-(Maybe と Raise は札に載る)', () => {
    const [bind] = report().bindings;
    const plan = bindingPlan(bind, lines(FLOW_SOURCE));
    assert.ok(plan !== undefined);
    assert.deepStrictEqual(plan.hidden, [
      { line: 8, start: 2, end: 5 },
      { line: 8, start: 22, end: 23 }
    ]);
    assert.strictEqual(plan.operator, '<-');
    assert.deepStrictEqual(plan.name, { line: 8, start: 6, end: 9 });
    assert.ok(plan.chip?.tag === 'type' && plan.chip.absent && plan.chip.raises.length === 1);
    assert.match(bindingChipSvg(plan.chip, undefined, METRICS, NO_SPRITES).svg, /stroke-dasharray/, 'Maybe は点線の札');
  });

  test('var は var の語を添えて =、:= は札なしで :=', () => {
    const [, declare, assign] = report().bindings;
    const varPlan = bindingPlan(declare, lines(FLOW_SOURCE));
    assert.strictEqual(varPlan?.prefix, 'var');
    assert.strictEqual(varPlan?.operator, '=');
    const assignPlan = bindingPlan(assign, lines(FLOW_SOURCE));
    assert.strictEqual(assignPlan?.chip, undefined);
    assert.strictEqual(assignPlan?.operator, ':=');
  });

  test('注釈つきの <- は注釈も隠す・型が分からなければ ? の札', () => {
    const source = '  (<- a str (helper id))\n';
    const bind: LintBinding = {
      form: '<-',
      name: 'a',
      path: '/repo/x.hy',
      range: { start: { line: 0, character: 6 }, end: { line: 0, character: 7 } },
      formRange: { start: { line: 0, character: 2 }, end: { line: 0, character: 24 } },
      headRange: { start: { line: 0, character: 3 }, end: { line: 0, character: 5 } },
      annotationRange: { start: { line: 0, character: 8 }, end: { line: 0, character: 11 } },
      valueRange: { start: { line: 0, character: 12 }, end: { line: 0, character: 23 } },
      type: null,
      origin: 'unknown',
      absent: false,
      raises: []
    };
    const plan = bindingPlan(bind, lines(source));
    assert.ok(plan !== undefined);
    assert.deepStrictEqual(plan.hidden[1], { line: 0, start: 7, end: 11 });
    assert.deepStrictEqual(plan.chip, { tag: 'unknown' });
    assert.match(bindingChipSvg(plan.chip, undefined, METRICS, NO_SPRITES).svg, />\?</);
    assert.match(bindingHover(bind), /分からない/);
  });

  test('1 つの setv に束縛が 2 つある形は描かない・カーソルの行は元の文字', () => {
    const [bind] = report().bindings;
    assert.strictEqual(multiBindingForms([bind, { ...bind, name: 'other' }]).size, 1);
    const plan = bindingPlan(bind, lines(FLOW_SOURCE));
    assert.ok(plan !== undefined);
    assert.strictEqual(bindingRevealed(plan, [{ start: 8, end: 8 }]), true);
    assert.strictEqual(bindingRevealed(plan, [{ start: 9, end: 9 }]), false);
  });
});

suite('置き場 — 見出しは版と組で置く', () => {
  test('stdin の結果の見出しを版つきで置き、知らない語を拡張が古い理由として集める', () => {
    const store = new LintStore();
    const r = report();
    store.replaceSignatures('/repo/core/flow.hy', 7, r);
    assert.strictEqual(store.signaturesFor('/repo/core/flow.hy')?.version, 7);
    assert.strictEqual(store.signaturesFor('/repo/core/flow.hy')?.signatures.length, 1);
    assert.deepStrictEqual(store.unknownVocabulary(), []);
    store.replaceSignatures('/repo/core/flow.hy', 8, { ...r, unknown: ['$.rules[0].family: 知らない語 "castle"'] });
    assert.deepStrictEqual(store.unknownVocabulary(), ['$.rules[0].family: 知らない語 "castle"']);
    store.forgetSignatures('/repo/core/flow.hy');
    assert.strictEqual(store.signaturesFor('/repo/core/flow.hy'), undefined);
  });
});
