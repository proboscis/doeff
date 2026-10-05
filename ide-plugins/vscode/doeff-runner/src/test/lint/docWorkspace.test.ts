import * as assert from 'assert';
import { WorkspaceJudge } from '../../lint/docWorkspace';
import { readWorkspaceEvent, type WorkspaceEvent, type DocIndex } from '../../lint/docWorkspaceContract';
import { LintStore } from '../../lint/store';
import type { WorkspaceRunner, WorkspaceRequest } from '../../lint/docWorkspaceProcess';
import { termLinks } from '../../read/termLinks';
import { docFailure } from '../../lint/docContract';

const ROOT = '/repo';
const FILE = '/repo/closed.md';
const rawIndex = JSON.stringify({
  event: 'index',
  schema_version: 1,
  total: 1,
  snapshot: { files: [{ path: FILE, text: '閉じた文書。' }], index: { definitions: [], references: [], issues: [] } },
});
const rawResult = JSON.stringify({
  event: 'result',
  path: FILE,
  completed: 1,
  cache_hits: 0,
  unmeasured: 1,
  report: {
    schema_version: 1,
    policy_version: 'v1',
    results: [
      {
        unit: { source: FILE, line: 1, end_line: 1, kind: 'document', text: '閉じた文書。' },
        measurement: 'unmeasured',
        status: 'unmeasured',
        reason: '通信失敗',
      },
    ],
  },
});
const rawDone = JSON.stringify({ event: 'done', code: 2, completed: 1, cache_hits: 0, unmeasured: 1 });
function events(): WorkspaceEvent[] {
  const index = readWorkspaceEvent(rawIndex, ROOT);
  assert.strictEqual(index.event, 'index');
  if (index.event !== 'index') {
    throw new Error('index');
  }
  return [index, readWorkspaceEvent(rawResult, ROOT, index.snapshot), readWorkspaceEvent(rawDone, ROOT, index.snapshot)];
}
const tick = (): Promise<void> => new Promise((resolve) => setImmediate(resolve));
suite('workspace の全文章・用語・キャッシュ進捗', () => {
  test('文書規則の指紋と用語違反を保持し、未測定として表示しない', () => {
    const event = readWorkspaceEvent(JSON.stringify({ event: 'index', schema_version: 1, total: 0, snapshot: {
      files: [{ path: FILE, text: '土台オブジェクト' }],
      index: { definitions: [], references: [], document_policies: { [FILE]: 'a'.repeat(64) }, issues: [
        { rule: 'DOC201', message: '依存オブジェクトと記述してください', location: { path: FILE, start: { line: 0, character: 0 }, end: { line: 0, character: 8 } } },
      ] },
    } }), ROOT);
    assert.strictEqual(event.event, 'index');
    if (event.event !== 'index') { throw new Error('index'); }
    assert.strictEqual(event.snapshot.index.documentPolicies?.[FILE], 'a'.repeat(64));
    assert.strictEqual(event.snapshot.issues[0].message, '依存オブジェクトと記述してください');
    assert.throws(() => readWorkspaceEvent(rawIndex.replace('"issues":[]', '"issues":[],"document_policies":{"/elsewhere.md":""}'), ROOT));
    assert.throws(() => readWorkspaceEvent(rawIndex.replace('"issues":[]', '"issues":[],"document_policies":{"/repo/a.md":"invalid"}'), ROOT));
  });
  test('設定変更の後に本文変更をまとめても規則の再確認を省略しない', async () => {
    const calls: WorkspaceRequest[] = [];
    const finishes: Array<() => void> = [];
    const runner: WorkspaceRunner = { run(request) { calls.push(request); return new Promise(resolve => finishes.push(resolve)); } };
    const judge = new WorkspaceJudge(runner, new LintStore());
    judge.submit({ kind: 'selected', root: ROOT, paths: [FILE], documents: [] });
    judge.submit({ kind: 'changed', root: ROOT, paths: [], documents: [], rulesChanged: true });
    judge.submit({ kind: 'changed', root: ROOT, paths: [FILE], documents: [{ path: FILE, text: '変更' }] });
    finishes[0]();
    await tick();
    assert.strictEqual(calls[1].rulesChanged, true);
    assert.strictEqual(calls[1].documents[0].text, '変更');
    finishes[1]();
    await tick();
    judge.dispose();
  });
  test('大量の結果をまとめて通知し、診断の全体走査を結果の件数だけ繰り返さない', async () => {
    const store = new LintStore();
    let notifications = 0;
    store.onDidChange(() => { notifications++; });
    const runner: WorkspaceRunner = {
      async run(_request, observe) {
        observe({ event: 'index', snapshot: {
          files: new Map(Array.from({ length: 10000 }, (_, i) => [`/repo/${i}.md`, '文章'])),
          index: { definitions: [], references: [] }, issues: [], total: 10000,
        } });
        for (let i = 0; i < 10000; i++) {
          const file = `/repo/${i}.md`;
          observe({ event: 'result', path: file, unit: '1', violations: [docFailure(file, '未測定')], completed: i + 1, cacheHits: 0, unmeasured: i + 1 });
        }
        observe({ event: 'done', code: 2, completed: 10000, cacheHits: 0, unmeasured: 10000 });
      },
    };
    const judge = new WorkspaceJudge(runner, store);
    judge.submit({ kind: 'initial', root: ROOT, documents: [] });
    await tick();
    assert.strictEqual(store.violations().length, 10000);
    assert.strictEqual(store.docWorkspaces().get(ROOT)?.progress.completed, 10000);
    assert.ok(notifications <= 4, `診断の全体更新: ${notifications}回`);
    judge.dispose();
  });
  test('進捗の件数だけが変わっても診断を再発行しない', () => {
    const store = new LintStore();
    const index: DocIndex = { definitions: [], references: [] };
    store.setDocumentWorkspace(ROOT, index, { files: 1, total: 10, completed: 0, cacheHits: 0, unmeasured: 0, phase: 'running' });
    let changes = 0;
    let progressChanges = 0;
    store.onDidChange(() => { changes++; });
    store.onDidChangeDocumentWorkspace(() => { progressChanges++; });
    store.setDocumentWorkspace(ROOT, index, { files: 1, total: 10, completed: 1, cacheHits: 1, unmeasured: 0, phase: 'running' });
    assert.strictEqual(changes, 0);
    assert.strictEqual(progressChanges, 1);
    assert.strictEqual(store.docWorkspaces().get(ROOT)?.progress.completed, 1);
  });
  test('検査途中でも結果を表示し、後続の失敗で未反映の結果を失わない', async () => {
    const store = new LintStore();
    const partial = new Promise<void>((resolve) => {
      store.onDidChange(() => { if (store.violationsIn(FILE).length > 0) { resolve(); } });
    });
    const runner: WorkspaceRunner = {
      async run(_request, observe) {
        events().slice(0, 2).forEach(observe);
        await partial;
        assert.strictEqual(store.docWorkspaces().get(ROOT)?.progress.phase, 'running');
        throw new Error('途中で停止');
      },
    };
    const judge = new WorkspaceJudge(runner, store);
    judge.submit({ kind: 'initial', root: ROOT, documents: [] });
    await partial;
    await tick();
    assert.strictEqual(store.docWorkspaces().get(ROOT)?.progress.phase, 'failed');
    assert.strictEqual(store.violationsIn(FILE).length, 1);
    judge.dispose();
  });
  test('閉じたファイルも指摘の store に入り、未測定を進捗から消さない', async () => {
    const runner: WorkspaceRunner = {
      async run(_r, observe) {
        events().forEach(observe);
      },
    };
    const store = new LintStore();
    const judge = new WorkspaceJudge(runner, store);
    judge.submit({ kind: 'initial', root: ROOT, documents: [] });
    await tick();
    assert.strictEqual(store.violationsIn(FILE)[0].rule, 'DOC000');
    assert.strictEqual(store.docWorkspaces().get(ROOT)?.progress.unmeasured, 1);
    assert.strictEqual(store.docWorkspaces().get(ROOT)?.progress.phase, 'complete');
    judge.remove(ROOT);
    assert.strictEqual(store.violations().length, 0);
    judge.dispose();
  });
  test('編集が続いても進行中の検査を中断せず、変更をまとめて後続の差分を実行する', async () => {
    const calls: Array<{ observe: (e: WorkspaceEvent) => void; signal: AbortSignal; finish: () => void; text: string }> = [];
    const runner: WorkspaceRunner = {
      run(request, observe, signal) {
        return new Promise((finish) => calls.push({ observe, signal, finish, text: request.documents[0]?.text ?? '' }));
      },
    };
    const store = new LintStore();
    const judge = new WorkspaceJudge(runner, store);
    judge.submit({ kind: 'initial', root: ROOT, documents: [] });
    judge.submit({ kind: 'changed', root: ROOT, paths: [FILE], documents: [{ path: FILE, text: '途中' }] });
    judge.submit({ kind: 'changed', root: ROOT, paths: [FILE], documents: [{ path: FILE, text: '最新' }] });
    assert.strictEqual(calls[0].signal.aborted, false);
    events().forEach(calls[0].observe);
    assert.strictEqual(store.violations().length, 1);
    calls[0].finish();
    await tick();
    assert.strictEqual(calls[1].text, '最新');
    judge.remove(ROOT);
    events().forEach(calls[1].observe);
    calls[1].finish();
    await tick();
    assert.strictEqual(store.docWorkspaces().size, 0);
    judge.dispose();
  });
  test('子プロセス失敗は未完了として残す', async () => {
    const store = new LintStore();
    const runner: WorkspaceRunner = {
      async run() {
        throw new Error('起動不能');
      },
    };
    const judge = new WorkspaceJudge(runner, store);
    judge.submit({ kind: 'initial', root: ROOT, documents: [] });
    await tick();
    assert.strictEqual(store.docWorkspaces().get(ROOT)?.progress.phase, 'failed');
    judge.dispose();
  });
  test('別workspaceのパス・索引なしの結果・途中の終了を拒否する', () => {
    assert.throws(() => readWorkspaceEvent(rawIndex.split(FILE).join('/other.md'), ROOT));
    assert.throws(() => readWorkspaceEvent(rawResult, ROOT));
    const index = readWorkspaceEvent(rawIndex, ROOT);
    if (index.event !== 'index') {
      throw new Error('index');
    }
    assert.throws(() => readWorkspaceEvent(rawDone.replace('"completed":1', '"completed":0'), ROOT, index.snapshot));
  });
  test('用語の定義と使用箇所のボタンは明示参照に限り、本文をHTMLとして実行しない', () => {
    const loc = { path: FILE, start: { line: 1, character: 0 }, end: { line: 1, character: 5 } };
    const index: DocIndex = {
      definitions: [{ id: 'env', title: '環境変数', explanation: '<script>危険</script>', location: loc }],
      references: [{ id: 'env', label: '環境変数', location: loc }],
    };
    const html = termLinks(index, FILE, 0, 3);
    assert.ok(html.includes('data-term-id="env"'));
    assert.ok(html.includes('使用箇所'));
    assert.ok(!html.includes('<script>'));
    assert.strictEqual(termLinks(index, '/other.md', 0, 3), '');
  });
});
