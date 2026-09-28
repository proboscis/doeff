import * as assert from 'assert';
import { EXCLUDED_DIRS, HY_EXCLUDE_GLOB, isExcludedPath } from '../../hy/hyPaths';
import { ChildProcessHyIndexer } from '../../hy/indexer';
import {
  emptyIndexLines,
  nextStatus,
  REINDEX_COMMAND,
  SHOW_OUTPUT_COMMAND,
  type HyIndexStatus
} from '../../hy/indexStatus';

const ROOT = '/repo';

suite('Hy の索引が空の時の説明', () => {
  test('道具が無い時は、無いことと作り方と作り直しの口を出す(「まだありません」で黙らない)', () => {
    const lines = emptyIndexLines({ tag: 'tool-missing', reason: 'doeff-indexer が見つからない: Error: doeff-indexer not found' });
    const text = lines.map((l) => l.label).join('\n');
    assert.ok(text.includes('doeff-indexer が見つかりません'));
    assert.ok(text.includes('DOEFF_INDEXER_PATH'));
    assert.ok(lines.some((l) => l.command === REINDEX_COMMAND));
    assert.ok(lines.some((l) => l.command === SHOW_OUTPUT_COMMAND));
    assert.ok(lines.some((l) => l.tooltip?.includes('not found') === true));
  });

  test('古い道具・作れなかった・Hy の file が無い・作っている最中を言い分ける', () => {
    const cases: Array<[HyIndexStatus, string]> = [
      [{ tag: 'unsupported', binary: '/bin/x', reason: 'r' }, '古く'],
      [{ tag: 'failed', root: ROOT, reason: '終了コード 2: panic\n詳細' }, '理由: 終了コード 2: panic'],
      [{ tag: 'no-hy-files' }, 'Hy の file'],
      [{ tag: 'indexing', root: ROOT }, '作っています'],
      [{ tag: 'waiting' }, '起動の途中'],
      [{ tag: 'ready', files: 3 }, '0 件']
    ];
    for (const [status, expected] of cases) {
      const text = emptyIndexLines(status).map((l) => l.label).join('\n');
      assert.ok(text.includes(expected), `${status.tag}: ${text}`);
      assert.ok(!text.includes('まだありません'), status.tag);
    }
  });
});

suite('Hy の索引の状態の進み方', () => {
  const waiting: HyIndexStatus = { tag: 'waiting' };

  test('道具が無いのは 1 file の依頼で分かっても状態にする', () => {
    assert.deepStrictEqual(nextStatus(waiting, { tag: 'files', root: ROOT }, { tag: 'missing', reason: 'x' }), {
      tag: 'tool-missing',
      reason: 'x'
    });
  });

  test('root 全体の成功は ready、失敗は failed', () => {
    assert.deepStrictEqual(nextStatus(waiting, { tag: 'root', root: ROOT }, { tag: 'ok', files: 5 }), { tag: 'ready', files: 5 });
    assert.deepStrictEqual(nextStatus(waiting, { tag: 'root', root: ROOT }, { tag: 'failed', reason: 'r' }), {
      tag: 'failed',
      root: ROOT,
      reason: 'r'
    });
  });

  test('1 file の失敗・成功は状態を変えない(1 file の崩れで一覧の説明を消さない)', () => {
    const ready: HyIndexStatus = { tag: 'ready', files: 5 };
    assert.strictEqual(nextStatus(ready, { tag: 'stdin', root: ROOT }, { tag: 'failed', reason: 'r' }), ready);
    assert.strictEqual(nextStatus(ready, { tag: 'files', root: ROOT }, { tag: 'ok', files: 5 }), ready);
  });
});

suite('索引の対象から外す path', () => {
  test('.git の下の写し(dotfiles-run の slot 等)は外す', () => {
    assert.ok(isExcludedPath('/Users/k/repos/agora-controllers/.git/dotfiles-run/slots/s/controllers/x.hy'));
    assert.ok(isExcludedPath('/r/.venv/lib/a.hy'));
    assert.ok(isExcludedPath('C:\\r\\node_modules\\a.hy'));
    assert.ok(isExcludedPath('/r/.git'));
  });

  test('名前の一部が除外の名に似ているだけの dir は外さない', () => {
    assert.ok(!isExcludedPath('/r/controllers/targets/a.hy'));
    assert.ok(!isExcludedPath('/r/.github/a.hy'));
  });

  test('glob と判定は同じ一覧から作る', () => {
    for (const dir of EXCLUDED_DIRS) {
      assert.ok(HY_EXCLUDE_GLOB.includes(dir), dir);
    }
  });
});

suite('道具の探し直し', () => {
  test('見つからなかった結果は missing で返し、forget の後は探し直す', async () => {
    let calls = 0;
    const indexer = new ChildProcessHyIndexer(
      async () => {
        calls += 1;
        throw new Error('doeff-indexer not found');
      },
      1000,
      () => undefined
    );
    const first = await indexer.index({ tag: 'root', root: ROOT });
    assert.strictEqual(first.tag, 'missing');
    await indexer.index({ tag: 'files', root: ROOT, files: ['/repo/a.hy'] });
    assert.strictEqual(calls, 1, '同じ走行の中では探し直さない(通知を繰り返さない)');
    indexer.forget();
    await indexer.index({ tag: 'root', root: ROOT });
    assert.strictEqual(calls, 2);
  });
});
