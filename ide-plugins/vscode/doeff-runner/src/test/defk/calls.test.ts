import * as assert from 'assert';
import * as fs from 'fs';
import * as path from 'path';
import { parseLintJson, type LintReport } from '../../lint/contract';
import { LintStore, textStamp } from '../../lint/store';
import { callMarks, effectHeadSpans, rewriteAt, rewriteHover, shownRewrites } from '../../defk/calls';
import type { LineSource } from '../../defk/model';

const FIXTURES = path.join(__dirname, '..', '..', '..', 'test-fixtures', 'lint');

/** 呼びの置き換えの fixture(開発版の linter を小さな repo の demo/core.hy に当てた出力)を契約の入口で読むため。 */
function fixtureText(): string {
  return fs.readFileSync(path.join(FIXTURES, 'rewrites.json'), 'utf8');
}

/** fixture を契約の入口で読んで報告にするため(読めなければ検を落とす)。 */
function report(): LintReport {
  const parsed = parseLintJson(fixtureText());
  if (parsed.tag !== 'ok') {
    assert.fail(`fixture を読めない: ${parsed.reason}`);
  }
  return parsed.report;
}

/** fixture を作った source(demo/core.hy)。 */
const CORE_SOURCE = `(require doeff-hy.macros [defk <- val])
(import demo.intent [Row ReadRow])
(import helpers [shape-of])

(defk bump [n]
  {:pre [(: n int)] :post [(: % int)]}
  "doc"
  (+ n 1))

(defk subject [a row]
  {:pre [(: a int)] :post [(: % int)]}
  "doc"
  (val x (+ (! (bump 0)) 1))
  (<- got Row (ReadRow "id"))
  (when (is got None) (shape-of a :key (.get row "k")))
  (my-macro a)
  x)
`;

/** source を行の口にするため。 */
function lines(source: string): LineSource {
  const all = source.split('\n');
  return { lineCount: all.length, lineText: (line) => all[line] ?? '' };
}

/** 描く物を元の行に当てて、見える文字にするため(検で表示を文字で比べる)。 */
function render(source: string, report: LintReport, shown: readonly number[]): string[] {
  const all = source.split('\n');
  const marks = callMarks(report.rewrites, shown);
  return all.map((text, line) => {
    let out = '';
    let at = 0;
    for (const m of marks.filter((mark) => mark.span.line === line)) {
      out += text.slice(at, m.span.start) + (m.effect === null ? '' : `[${m.effect}]`) + m.text;
      at = m.span.end;
    }
    return out + text.slice(at);
  });
}

suite('呼びを f(a, b) の形で見せる表示(editor-json の rewrites)', () => {
  test('契約の入口が rewrites を読み、置き換えの種類・部品・親の番号を持つ', () => {
    const r = report();
    assert.deepStrictEqual(
      r.rewrites.map((w) => [w.kind, w.text, w.parent]),
      [
        ['infix', 'n + 1', null],
        ['infix', '!bump(0) + 1', null],
        ['perform', '!bump(0)', 1],
        ['call', 'bump(0)', 2],
        ['call', 'ReadRow("id")', null],
        ['infix', '(got is None)', null],
        ['call', 'shape-of(a, key=row.get("k"))', null],
        ['method', 'row.get("k")', 6]
      ]
    );
    const read = r.rewrites[4].parts[0];
    assert.strictEqual(read.role, 'effect');
    assert.ok(read.definition !== null && read.definition.path.endsWith('demo/intent.hy'));
    const bump = r.rewrites[3].parts[0];
    assert.strictEqual(bump.role, 'defk');
    assert.deepStrictEqual(bump.answer, { kind: 'name', name: 'int', definition: null });
  });

  test('カーソルの無い所は置き換え、知らない macro は lisp のまま・effect の前に絵', () => {
    const r = report();
    const shown = shownRewrites(r.rewrites, [], lines(CORE_SOURCE));
    const out = render(CORE_SOURCE, r, shown);
    assert.strictEqual(out[7], '  n + 1)');
    assert.strictEqual(out[12], '  (val x !bump(0) + 1)');
    assert.strictEqual(out[13], '  (<- got Row [ReadRow]ReadRow("id"))');
    assert.strictEqual(out[14], '  (when (got is None) shape-of(a, key=row.get("k")))');
    assert.strictEqual(out[15], '  (my-macro a)');
  });

  test('カーソルの行と選んだ範囲の行は元の lisp、外を lisp で見せる時は中も lisp', () => {
    const r = report();
    const shown = shownRewrites(r.rewrites, [{ start: 12, end: 12 }], lines(CORE_SOURCE));
    assert.deepStrictEqual(shown, [0, 4, 5, 6, 7]);
    const out = render(CORE_SOURCE, r, shown);
    assert.strictEqual(out[12], '  (val x (+ (! (bump 0)) 1))');
    const selected = shownRewrites(r.rewrites, [{ start: 13, end: 14 }], lines(CORE_SOURCE));
    assert.deepStrictEqual(selected, [0, 1, 2, 3]);
  });

  test('document と位置が合わない置き換え(版の食い違い)は描かない', () => {
    const r = report();
    const shorter = CORE_SOURCE.split('\n').slice(0, 13).join('\n');
    const shown = shownRewrites(r.rewrites, [], lines(shorter));
    assert.deepStrictEqual(shown, [0, 1, 2, 3]);
  });

  test('effect の頭の範囲は文字の置き換え(#841)の除外に渡す', () => {
    const r = report();
    const shown = shownRewrites(r.rewrites, [], lines(CORE_SOURCE));
    assert.deepStrictEqual(effectHeadSpans(r.rewrites, shown), [{ line: 13, start: 15, end: 22 }]);
  });

  test('hover は一番内側の置き換えの元の lisp と部品の定義への link・答えの型', () => {
    const r = report();
    const shown = shownRewrites(r.rewrites, [], lines(CORE_SOURCE));
    const inner = rewriteAt(r.rewrites, shown, 14, 42);
    assert.strictEqual(inner?.kind, 'method');
    const call = rewriteAt(r.rewrites, shown, 12, 16);
    assert.strictEqual(call?.text, 'bump(0)');
    const hover = rewriteHover(call!, 'doeff-runner.defk.openLocation');
    assert.ok(hover.includes('```hy\n(bump 0)\n```'));
    assert.ok(hover.includes('defk [`bump`](command:doeff-runner.defk.openLocation?'));
    assert.ok(hover.includes('→ `int`'));
    assert.strictEqual(rewriteAt(r.rewrites, shown, 15, 4), undefined);
  });

  test('rewrites の無い古い出力も読み、知らない種類は描き続けて「拡張が古い」を控える', () => {
    const old = parseLintJson(fixtureText().replace('"rewrites":', '"old_rewrites":'));
    assert.ok(old.tag === 'ok' && old.report.rewrites.length === 0);
    const newer = parseLintJson(fixtureText().replace('"kind": "method"', '"kind": "pipeline"').replace('"role": "method"', '"role": "macro"'));
    assert.ok(newer.tag === 'ok');
    if (newer.tag === 'ok') {
      assert.strictEqual(newer.report.rewrites[7].kind, null);
      assert.strictEqual(newer.report.rewrites[7].parts[0].role, null);
      assert.strictEqual(newer.report.unknown.length, 2);
    }
  });

  test('置き場は 1 file の実行の rewrites を版と一緒に持つ', () => {
    const store = new LintStore();
    store.replaceSignatures('/repo/demo/core.hy', textStamp(3, '(defk core [])'), report());
    const found = store.signaturesFor('/repo/demo/core.hy');
    assert.strictEqual(found?.version, 3);
    assert.strictEqual(found?.rewrites.length, 8);
  });
});
