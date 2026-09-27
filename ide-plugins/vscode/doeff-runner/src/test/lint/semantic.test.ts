import * as assert from 'assert';
import * as fs from 'fs';
import * as path from 'path';
import { parseLintJson, type LintReport, type LintSemantic } from '../../lint/contract';
import { violationExplanationLines } from '../../lint/layers';
import { lintArgs } from '../../lint/runner';
import { isMissingJevKey, LatestPerKeyQueue, semanticStatus } from '../../lint/semantic';
import { diagnosticOf } from '../../lint/view';

const FIXTURES = path.join(__dirname, '..', '..', '..', 'test-fixtures', 'lint');

/** fixture を契約の入口で読む。 */
function report(name: string): LintReport {
  const parsed = parseLintJson(fs.readFileSync(path.join(FIXTURES, name), 'utf8'));
  if (parsed.tag !== 'ok') {
    assert.fail(`fixture ${name} を読めない: ${parsed.reason}`);
  }
  return parsed.report;
}

suite('保存した時の Jev の判定', () => {
  test('命令 — 設定の命令に --semantic <保存した file> を足す', () => {
    assert.deepStrictEqual(
      lintArgs(['--output-format', 'editor-json'], { tag: 'semantic', root: '/repo', path: '/repo/a.hy' }),
      ['--output-format', 'editor-json', '--semantic', '/repo/a.hy']
    );
  });

  test('間引き — 同時に 1 本、走っている間に同じ file の保存が続いたら最後の 1 回だけ', async () => {
    const ran: string[] = [];
    let release: () => void = () => undefined;
    const queue = new LatestPerKeyQueue<string>(async (key, request) => {
      ran.push(`${key}:${request}`);
      if (request === 'a1') {
        await new Promise<void>((resolve) => {
          release = resolve;
        });
      }
    });
    queue.enqueue('a', 'a1');
    queue.enqueue('a', 'a2');
    queue.enqueue('b', 'b1');
    queue.enqueue('a', 'a3');
    assert.strictEqual(queue.active, 'a');
    assert.strictEqual(queue.waiting, 2, 'a は a3 の 1 つに置き換わり、b1 と合わせて 2');
    release();
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.deepStrictEqual(ran, ['a:a1', 'b:b1', 'a:a3']);
    assert.strictEqual(queue.active, undefined);
  });

  test('状態の文 — 判定中・済(未判定 0)・未判定 N・較正が ok でなければ警告', () => {
    const summary = report('semantic.json').semantic;
    assert.ok(summary);
    const base: LintSemantic = { ...summary, calibration: 'ok', unjudged: 0 };
    assert.strictEqual(semanticStatus({ tag: 'idle' }), undefined);
    assert.match(semanticStatus({ tag: 'running', path: '/a.hy', waiting: 1 })?.text ?? '', /Jev: 判定中…/);
    assert.deepStrictEqual(
      [semanticStatus({ tag: 'done', summary: base })?.text, semanticStatus({ tag: 'done', summary: base })?.warning],
      ['$(sparkle) Jev: 済(未判定 0)', false]
    );
    const drifted = semanticStatus({ tag: 'done', summary });
    assert.strictEqual(drifted?.text, '$(sparkle) Jev: 未判定 3 · 較正 drifted');
    assert.strictEqual(drifted?.warning, true);
    assert.match(drifted?.tooltip ?? '', /較正の見張り: drifted/);
    assert.strictEqual(semanticStatus({ tag: 'no-key' })?.warning, true);
  });

  test('キーが無いことの見分け(linter の errors の文)', () => {
    assert.ok(isMissingJevKey(['Jev の API キーが無い(JEV_API_KEY・JEV_API_KEY_FILE・TYPESAFE_API_KEY・~/.config/jev/api_key)']));
    assert.ok(!isMissingJevKey(['/repo/broken.hy: 括弧が閉じていない']));
  });
});

suite('Jev の違反の読み込みと表示(契約の更新 5)', () => {
  test('source・probability・semantic を読み、無い古い出力は linter の違反として読む', () => {
    const r = report('semantic.json');
    assert.deepStrictEqual(
      r.violations.map((v) => `${v.source}:${v.probability}`),
      ['linter:null', 'linter:null', 'jev:0.93']
    );
    assert.strictEqual(r.semantic?.calibration, 'drifted');
    const old = report('report.json');
    assert.ok(old.violations.every((v) => v.source === 'linter' && v.probability === null));
    assert.strictEqual(old.semantic, null);
  });

  test('確率が 0〜1 でなければ理由つきで捨てる', () => {
    const parsed = parseLintJson(fs.readFileSync(path.join(FIXTURES, 'bad-probability.json'), 'utf8'));
    assert.strictEqual(parsed.tag, 'rejected');
    assert.match(parsed.tag === 'rejected' ? parsed.reason : '', /probability: 0〜1 の数でない/);
  });

  test('hover と問題の一覧に、確率と「Jev の判定(意味の規則・止めはしない)」の印', () => {
    const jev = report('semantic.json').violations[2];
    assert.strictEqual(violationExplanationLines(jev)[0], 'Jev の判定(意味の規則・止めはしない) p=0.93');
    assert.ok(diagnosticOf(jev).message.includes('Jev の判定(意味の規則・止めはしない) p=0.93'));
    assert.ok(!violationExplanationLines(report('semantic.json').violations[0]).some((l) => l.startsWith('Jev')));
  });
});
