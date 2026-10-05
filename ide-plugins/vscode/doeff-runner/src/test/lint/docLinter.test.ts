import * as assert from 'assert';
import * as fs from 'fs';
import * as path from 'path';
import { parseDocReport, docFailure } from '../../lint/docContract';
import { DocumentJudge } from '../../lint/docJudge';
import type { DocRequest, DocOutcome, DocumentLinter } from '../../lint/docRunner';
import { parseLintJson, type LintReport } from '../../lint/contract';
import { LintStore } from '../../lint/store';

const FILE = '/repo/comment.hy';
const SOURCE = '(defk sample []\n  "説明")';
function report(status = 'violation'): string {
  return JSON.stringify({
    schema_version: 1,
    policy_version: 'readability-v1',
    results: [
      {
        unit: { source: FILE, line: 2, end_line: 2, kind: 'function', text: '説明' },
        status,
        measurement: 'measured',
        model: 'test',
        scores: ['DOC001', 'DOC002', 'DOC003', 'DOC004'].map((rule) => ({ rule, probability: 0.8 })),
        findings: [{ rule: 'DOC002', message: '用語の説明がありません', message_origin: 'rule', probability: 0.8 }],
      },
    ],
  });
}
function baseReport(): LintReport {
  const parsed = parseLintJson(fs.readFileSync(path.join(__dirname, '../../../test-fixtures/lint/report.json'), 'utf8'));
  if (parsed.tag !== 'ok') {
    throw new Error(parsed.reason);
  }
  return parsed.report;
}
const tick = (): Promise<void> => new Promise((resolve) => setImmediate(resolve));

suite('doc-linter と doeff-linter の共存', () => {
  test('行番号を変換し、要確認と未測定を診断として残す', () => {
    const [violation] = parseDocReport(report(), FILE, SOURCE);
    assert.strictEqual(violation.source, 'doc-linter');
    assert.strictEqual(violation.documentKind, 'function');
    assert.deepStrictEqual(violation.range, { start: { line: 1, character: 0 }, end: { line: 1, character: 7 } });
    assert.ok(parseDocReport(report('review'), FILE, SOURCE)[0].message.startsWith('要確認'));
    const raw = JSON.stringify({
      schema_version: 1,
      policy_version: 'v1',
      results: [
        {
          unit: { source: FILE, line: 2, end_line: 2, kind: 'function', text: '説明' },
          status: 'unmeasured',
          measurement: 'unmeasured',
          reason: '通信失敗',
        },
      ],
    });
    assert.strictEqual(parseDocReport(raw, FILE, SOURCE)[0].rule, 'DOC000');
  });
  test('別ファイル・未知の版・空の応答・偽の出典を合格にしない', () => {
    assert.throws(() => parseDocReport(report(), '/other.hy', SOURCE));
    assert.throws(() => parseDocReport(report().replace('"schema_version":1', '"schema_version":9'), FILE, SOURCE));
    assert.throws(() => parseDocReport('{}', FILE, SOURCE));
    assert.throws(() => parseDocReport(report().replace('"message_origin":"rule"', '"message_origin":"model"'), FILE, SOURCE));
    assert.throws(() => parseDocReport('{"schema_version":1,"policy_version":"v1","results":[]}', FILE, SOURCE));
    assert.deepStrictEqual(
      parseDocReport('{"schema_version":1,"policy_version":"v1","status":"not-applicable","results":[]}', FILE, SOURCE),
      [],
    );
  });
  test('片方の再実行・無効化でも他方の結果を保持し、同じ結果で再描画しない', () => {
    const store = new LintStore();
    const base = baseReport();
    let events = 0;
    store.onDidChange(() => events++);
    const docs = parseDocReport(report(), FILE, SOURCE);
    store.replaceDocumentFindings('/repo', FILE, docs);
    assert.strictEqual(store.violationsIn(FILE).length, 1, 'doeff の全体実行を待たない');
    store.replaceRoot('/repo', base);
    assert.strictEqual(store.violations().length, base.violations.length + 1);
    const before = events;
    store.replaceDocumentFindings('/repo', FILE, docs);
    assert.strictEqual(events, before);
    store.replaceFile('/repo', base.violations[0].path, base);
    assert.strictEqual(store.violationsIn(FILE).length, 1);
    store.replaceRoot('/repo', base);
    assert.strictEqual(store.violationsIn(FILE).length, 1);
    store.clearDocumentFindings(FILE);
    assert.strictEqual(store.violations().length, base.violations.length);
  });
  test('検査中の編集では古い返答を捨て、待機中の最新版だけを実行する', async () => {
    const pending: Array<{ request: DocRequest; resolve: (v: DocOutcome) => void }> = [];
    const external: DocumentLinter = { lint: (request) => new Promise((resolve) => pending.push({ request, resolve })) };
    const store = new LintStore();
    const judge = new DocumentJudge(external, store, () => undefined);
    const request: DocRequest = { root: '/repo', path: FILE, text: SOURCE, version: 1 };
    judge.submit(request);
    judge.invalidate(FILE);
    judge.submit({ ...request, version: 2 });
    judge.submit({ ...request, version: 3 });
    assert.strictEqual(pending.length, 1);
    pending[0].resolve({ tag: 'ok', violations: [docFailure(FILE, '古い')] });
    await tick();
    assert.strictEqual(store.violationsIn(FILE).length, 0);
    assert.strictEqual(pending.length, 2);
    assert.strictEqual(pending[1].request.version, 3);
    pending[1].resolve({ tag: 'ok', violations: [docFailure(FILE, '最新版')] });
    await tick();
    assert.ok(store.violationsIn(FILE)[0].message.includes('最新版'));
    judge.submit({ ...request, version: 4 });
    judge.invalidateRoot('/repo');
    pending[2].resolve({ tag: 'ok', violations: [docFailure(FILE, '外した')] });
    await tick();
    assert.strictEqual(store.violationsIn(FILE).length, 0);
    judge.dispose();
  });
});
