import * as assert from 'assert';
import * as fs from 'fs';
import * as path from 'path';
import { parseLintJson, type LintBody, type LintReport } from '../../lint/contract';

const FIXTURES = path.join(__dirname, '..', '..', '..', 'test-fixtures', 'lint');

/**
 * 本体の文字の fixture(開発版の linter を packages/doeff-linter/tests/fixtures/body_view の見本
 * controllers/messaging/core/conversation_input.hy に `--stdin --path` で当てた出力。path の頭は /repo に置き換えた)。
 */
function fixtureText(): string {
  return fs.readFileSync(path.join(FIXTURES, 'bodies.json'), 'utf8');
}

/** 契約の入口で読む(読めなければ検を落とす)。 */
function read(text: string): LintReport {
  const parsed = parseLintJson(text);
  if (parsed.tag !== 'ok') {
    assert.fail(`fixture を読めない: ${parsed.reason}`);
  }
  return parsed.report;
}

/** 定義の本体を (1 始まりの行, 字下げを付けた字) にする(面と同じつなぎ方)。 */
function rendered(body: LintBody): [number, string][] {
  return body.lines.map((l) => [l.line + 1, '  '.repeat(l.depth) + ' '.repeat(l.pad) + l.segments.map((s) => s.text).join('')]);
}

function bodyOf(report: LintReport, name: string): LintBody {
  const found = report.bodies.find((b) => b.name === name);
  if (found === undefined) {
    assert.fail(`${name} の本体が無い`);
  }
  return found;
}

suite('本体の文字(editor-json の bodies)の読み込み', () => {
  test('契約の入口が bodies を読み、行・字の役・source の範囲・束縛の番号を持つ', () => {
    const r = read(fixtureText());
    assert.deepStrictEqual(
      r.bodies.map((b) => [b.kind, b.name]),
      [
        ['defk', 'judged'],
        ['defk', 'outcome-of'],
        ['defk', 'run-input-request']
      ]
    );
    const run = bodyOf(r, 'run-input-request');
    const settled = rendered(run).find(([line]) => line === 89);
    assert.deepStrictEqual(settled, [
      89,
      'val IntakeSettleLanded | IntakeSettleRefused | IntakeUnreachable settled ⇐ SettleIntake(settlement)'
    ]);
    const line = run.lines.find((l) => l.line + 1 === 89);
    assert.ok(line !== undefined);
    assert.deepStrictEqual(
      line.segments.filter((s) => s.role === 'effect').map((s) => [s.text, s.effect]),
      [['SettleIntake', 'SettleIntake']]
    );
    assert.strictEqual(line.segments[0].role, 'keyword');
    assert.deepStrictEqual(line.segments[0].range?.start, { line: 88, character: 3 });
    assert.ok(line.binding !== null);
    assert.strictEqual(r.bindings[line.binding].name, 'settled');
    // 型が分からない束縛は unknown-type の `?`
    const row = bodyOf(r, 'judged').lines.find((l) => l.line + 1 === 51);
    assert.ok(row !== undefined);
    assert.deepStrictEqual(
      row.segments.map((s) => [s.role, s.text]),
      [
        ['keyword', 'var'],
        ['text', ' '],
        ['unknown-type', '?'],
        ['text', ' '],
        ['name', 'row'],
        ['text', ' '],
        ['assign', '='],
        ['text', ' '],
        ['text', 'None']
      ]
    );
    // when / match の字下げと `→` の揃え(腕の中の match の中身は段と pad で match の語の列に揃う)
    const run2 = rendered(run).filter(([line]) => line >= 90);
    assert.deepStrictEqual(run2, [
      [90, 'match settled'],
      [91, '  IntakeSettleLanded → match outcome'],
      [92, '                         InputDone     → RunOutcome.DONE'],
      [93, '                         InputRejected → RunOutcome.REJECTED'],
      [94, '  _                  → RunOutcome.DEFERRED']
    ]);
    const inner = run.lines.find((l) => l.line + 1 === 92);
    assert.deepStrictEqual([inner?.depth, inner?.pad], [2, 21]);
    const when = bodyOf(r, 'judged').lines.find((l) => l.line + 1 === 52);
    assert.deepStrictEqual(when?.segments[0].role, 'keyword');
    assert.deepStrictEqual(r.unknown, []);
  });

  test('知らない語で束縛を落としても、本体の行の束縛の番号は読んだ bindings を指し直す', () => {
    const raw = JSON.parse(fixtureText());
    // judged の target の束縛を知らない形にする — 拡張はそれを描かず、後ろの束縛の番号が 1 つずつ詰まる
    const target = raw.bindings.findIndex((b: { name: string }) => b.name === 'target');
    assert.ok(target > 0, 'fixture に target の束縛が無い(前に module の束縛がある想定)');
    raw.bindings[target].form = 'future-form';
    const r = read(JSON.stringify(raw));
    assert.strictEqual(r.bindings.length, raw.bindings.length - 1);
    for (const body of r.bodies) {
      for (const line of body.lines) {
        if (line.binding === null) {
          continue;
        }
        const name = line.segments.find((s) => s.role === 'name');
        assert.strictEqual(r.bindings[line.binding].name, name?.text);
      }
    }
    const dropped = bodyOf(r, 'judged').lines.find((l) => l.line + 1 === 50);
    assert.strictEqual(dropped?.binding, null);
    assert.ok(r.unknown.some((u) => u.includes('future-form')));
  });

  test('知らない字の役は null(ただの字)にして控え、出力を捨てない。bodies の無い古い linter は []', () => {
    const raw = JSON.parse(fixtureText());
    raw.bodies[0].lines[0].segments[0].role = 'future-role';
    const r = read(JSON.stringify(raw));
    assert.strictEqual(r.bodies[0].lines[0].segments[0].role, null);
    assert.ok(r.unknown.some((u) => u.includes('future-role')));
    delete raw.bodies;
    assert.deepStrictEqual(read(JSON.stringify(raw)).bodies, []);
  });

  test('束縛の前の語(lazy / session)を読み、欄の無い古い linter は null', () => {
    const raw = JSON.parse(fixtureText());
    raw.bindings[0].modifier = 'lazy';
    delete raw.bindings[1].modifier;
    const r = read(JSON.stringify(raw));
    assert.strictEqual(r.bindings[0].modifier, 'lazy');
    assert.strictEqual(r.bindings[1].modifier, null);
  });
});

suite('本体の文字の読み込み — U2 の linter との互換', () => {
  test('pad の無い行(U2 の linter の出力)は 0 と読み、出力を捨てない', () => {
    const raw = JSON.parse(fixtureText());
    for (const b of raw.bodies) {
      for (const l of b.lines) {
        delete l.pad;
      }
    }
    const r = read(JSON.stringify(raw));
    assert.ok(r.bodies.every((b) => b.lines.every((l) => l.pad === 0)));
  });
});
