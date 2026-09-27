import * as assert from 'assert';
import * as fs from 'fs';
import * as path from 'path';
import { parseLintJson, type LintReport, type LintSemantic } from '../../lint/contract';
import { violationExplanationLines } from '../../lint/layers';
import { lintArgs, type Linter, type LintOutcome, type LintRequest, type SemanticRequest } from '../../lint/runner';
import {
  isCurrentAnswer,
  isMissingJevKey,
  LatestPerKeyQueue,
  pauseDelayMs,
  SemanticJudge,
  semanticStatus,
  type Clock,
  type OpenDocument,
  type Scheduled,
  type SemanticState,
  type SemanticTriggers
} from '../../lint/semantic';
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
      lintArgs(['--output-format', 'editor-json'], { tag: 'semantic', root: '/repo', path: '/repo/a.hy', version: 1 }),
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

/** 偽の時計 — advance で時を進め、期限の来た予約を順に呼ぶ。 */
class FakeClock implements Clock {
  private now = 0;
  private readonly entries: Array<{ at: number; callback: () => void; live: boolean }> = [];

  after(ms: number, callback: () => void): Scheduled {
    const entry = { at: this.now + ms, callback, live: true };
    this.entries.push(entry);
    return {
      cancel: () => {
        entry.live = false;
      }
    };
  }

  advance(ms: number): void {
    const end = this.now + ms;
    for (;;) {
      const due = this.entries.filter((e) => e.live && e.at <= end).sort((a, b) => a.at - b.at)[0];
      if (due === undefined) {
        break;
      }
      due.live = false;
      this.now = due.at;
      due.callback();
    }
    this.now = end;
  }
}

/** 偽の linter の口 — 依頼を覚え、答えは検が resolve で返す(子 process だけを偽物にする)。 */
class FakeLinter implements Linter {
  readonly calls: Array<{ readonly request: LintRequest; readonly resolve: (outcome: LintOutcome) => void }> = [];

  lint(request: LintRequest): Promise<LintOutcome> {
    return new Promise((resolve) => this.calls.push({ request, resolve }));
  }
}

/** 列と Promise の続きを流し切る。 */
const settle = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, 0));

/** 係と偽の世界(時計・linter・開いている document・置き場へ入った答え)。 */
function world(triggers: Partial<SemanticTriggers> = {}) {
  const clock = new FakeClock();
  const linter = new FakeLinter();
  const documents = new Map<string, OpenDocument>();
  const delivered: Array<{ readonly request: SemanticRequest; readonly report: LintReport }> = [];
  const logs: string[] = [];
  const states: SemanticState[] = [];
  const judge = new SemanticJudge({
    linter,
    clock,
    triggers: () => ({ onSave: true, onChange: true, pauseMs: 2000, ...triggers }),
    document: (p) => documents.get(p),
    deliver: (request, r) => delivered.push({ request, report: r }),
    log: (line) => logs.push(line),
    onState: (state) => states.push(state),
    notify: () => undefined
  });
  /** 打った — document の版を 1 進めて中身を text にし、係に知らせる。 */
  const type = (p: string, text: string): void => {
    documents.set(p, { root: '/repo', text, version: (documents.get(p)?.version ?? 0) + 1 });
    judge.edited(p);
  };
  return { clock, linter, documents, delivered, logs, states, judge, type };
}

const A = '/repo/a.hy';

suite('編集中の Jev の判定(打つのが止まった時)', () => {
  test('間を置く — 打つのが 2 秒止まるまで問わず、止まったら最新の中身で 1 回だけ問う', () => {
    const w = world();
    w.type(A, '(defk f [x] x)');
    w.clock.advance(1500);
    w.type(A, '(defk f [x] (+ x');
    w.clock.advance(1500);
    w.type(A, '(defk f [x] (+ x 1))');
    w.clock.advance(1999);
    assert.strictEqual(w.linter.calls.length, 0, '最後に打ってから 2 秒経つまでは問わない');
    w.clock.advance(1);
    assert.strictEqual(w.linter.calls.length, 1);
    assert.deepStrictEqual(w.linter.calls[0].request, { tag: 'semantic-change', root: '/repo', path: A, text: '(defk f [x] (+ x 1))', version: 3 });
    // 中身は stdin で渡し、中身の変わった定義だけを問う(書きかけで読めない定義は linter が問わない)
    assert.deepStrictEqual(lintArgs(['--output-format', 'editor-json'], w.linter.calls[0].request), [
      '--output-format',
      'editor-json',
      '--stdin',
      '--path',
      A,
      '--semantic',
      '--semantic-changed'
    ]);
    w.clock.advance(10_000);
    assert.strictEqual(w.linter.calls.length, 1, '打たなければ問い直さない');
  });

  test('古い答えを捨てる — 問うている間に打ったら、その答えは置き場へ入れず、最新の中身の答えだけを出す', async () => {
    const w = world();
    w.type(A, '(defk f [x] x)');
    w.clock.advance(2000);
    assert.strictEqual(w.linter.calls.length, 1);
    w.type(A, '(defk f [x] (inc x))');
    w.linter.calls[0].resolve({ tag: 'ok', report: report('semantic.json') });
    await settle();
    assert.strictEqual(w.delivered.length, 0, '版 1 の答えは版 2 の中身には出さない');
    assert.ok(w.logs.some((l) => l.includes('Jev の答えを捨てた')));
    w.clock.advance(2000);
    assert.strictEqual(w.linter.calls.length, 2);
    w.linter.calls[1].resolve({ tag: 'ok', report: report('semantic.json') });
    await settle();
    assert.deepStrictEqual(
      w.delivered.map((d) => d.request.version),
      [2]
    );
    assert.strictEqual(w.states[w.states.length - 1].tag, 'done');
  });

  test('走り始めていない古い中身の依頼は、打ったら取り下げる(古い中身を Jev に問わない)', async () => {
    const w = world();
    w.type(A, 'v1');
    w.clock.advance(2000);
    w.type(A, 'v2');
    w.clock.advance(2000); // v2 の依頼は v1 が走っている間は待つ
    w.type(A, 'v3'); // 待っていた v2 を取り下げる
    w.clock.advance(2000);
    w.linter.calls[0].resolve({ tag: 'ok', report: report('semantic.json') });
    await settle();
    const texts = w.linter.calls.map((c) => (c.request.tag === 'semantic-change' ? c.request.text : c.request.tag));
    assert.deepStrictEqual(texts, ['v1', 'v3']);
  });

  test('保存した時は編集中の待ちをやめ、保存した中身を問う。semanticOnChange を切れば編集中は問わない', async () => {
    const w = world();
    w.type(A, '(defk f [x] x)');
    w.clock.advance(500);
    w.judge.saved('/repo', A, 1);
    w.clock.advance(5000);
    assert.deepStrictEqual(
      w.linter.calls.map((c) => c.request.tag),
      ['semantic']
    );
    const off = world({ onChange: false });
    off.type(A, '(defk f [x] x)');
    off.clock.advance(5000);
    assert.strictEqual(off.linter.calls.length, 0);
  });

  test('答えが今の中身の物かの見分け — 編集中は版が同じ時だけ、保存は閉じても disk の中身のまま', () => {
    const change: SemanticRequest = { tag: 'semantic-change', root: '/repo', path: A, text: 'x', version: 4 };
    const save: SemanticRequest = { tag: 'semantic', root: '/repo', path: A, version: 4 };
    assert.deepStrictEqual(
      [isCurrentAnswer(change, 4), isCurrentAnswer(change, 5), isCurrentAnswer(change, undefined)],
      [true, false, false]
    );
    assert.deepStrictEqual([isCurrentAnswer(save, 4), isCurrentAnswer(save, 5), isCurrentAnswer(save, undefined)], [true, false, true]);
  });

  test('待つ秒の設定 — 無ければ 2 秒・0.5〜60 秒に収める', () => {
    assert.deepStrictEqual([pauseDelayMs(undefined), pauseDelayMs(2), pauseDelayMs(0.1), pauseDelayMs(600), pauseDelayMs(Number.NaN)], [2000, 2000, 500, 60_000, 2000]);
  });

  test('何も問わなかった実行(較正 not-run・問うた 0)は警告にしない', () => {
    const summary = report('semantic.json').semantic;
    assert.ok(summary);
    assert.strictEqual(semanticStatus({ tag: 'done', summary: { ...summary, calibration: 'not-run', asked: 0, unjudged: 0 } })?.warning, false);
    assert.strictEqual(semanticStatus({ tag: 'done', summary: { ...summary, calibration: 'not-run', asked: 2 } })?.warning, true);
  });
});
