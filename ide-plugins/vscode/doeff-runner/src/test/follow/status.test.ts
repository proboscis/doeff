import * as assert from 'assert';
import * as fs from 'fs';
import * as path from 'path';
import {
  EMPTY_LEDGER,
  failureMessage,
  followNotices,
  followStatusPath,
  manifestVersion,
  parseFollowStatus,
  readFailureLedger,
  reloadMessage,
  runningBuild,
  unseenNotices,
  type FollowNotice,
  type FollowStatus,
  type RunningBuild
} from '../../follow/status';

const FIXTURES = path.join(__dirname, '..', '..', '..', 'test-fixtures', 'follow');
const RUNNER = 'proboscis.doeff-runner';
const HIGHLIGHTER = 'proboscis.python-semantic-highlighter';
const INSTALLED_RUNNER = '4141414141414141414141414141414141414141';
const INSTALLED_HIGHLIGHTER = '1640164016401640164016401640164016401640';

/** 追随が書く形の fixture(dotfiles の vsix_follow.status_json と同じ形)を契約の入口で読む。 */
function fixture(): FollowStatus {
  const read = parseFollowStatus(fs.readFileSync(path.join(FIXTURES, 'status.json'), 'utf8'));
  if (read.tag !== 'ok') {
    assert.fail(`status.json を読めない: ${read.reason}`);
  }
  assert.deepStrictEqual(read.skipped, []);
  return read.status;
}

/** 身元ごとの動いている組み立ての表を、followNotices が引く口にする(表に無い身元は absent)。 */
function runningOf(table: Readonly<Record<string, RunningBuild>>): (identity: string) => RunningBuild {
  return (identity) => table[identity] ?? { tag: 'absent' };
}

/** 報せを比べやすい短い文字列にする。 */
function show(notice: FollowNotice): string {
  switch (notice.tag) {
    case 'reload':
      return `reload ${notice.name} ${notice.version} ${notice.commit.slice(0, 4)}`;
    case 'failure':
      return `failure ${notice.name} ${notice.commit.slice(0, 4)}`;
    default:
      return 'unknown';
  }
}

suite('vsix の追随の報せ — 状態 file の契約と、Reload の通知・失敗の警告の判定(agora-redesign #1043)', () => {
  test('状態 file の path は書き手と同じ解き方 — XDG_STATE_HOME、無ければ(空でも)<home>/.local/state の下の ai/vsix-follow/status.json', () => {
    assert.strictEqual(followStatusPath({ XDG_STATE_HOME: '/s' }, '/h'), path.join('/s', 'ai', 'vsix-follow', 'status.json'));
    assert.strictEqual(followStatusPath({}, '/h'), path.join('/h', '.local', 'state', 'ai', 'vsix-follow', 'status.json'));
    assert.strictEqual(followStatusPath({ XDG_STATE_HOME: '' }, '/h'), path.join('/h', '.local', 'state', 'ai', 'vsix-follow', 'status.json'));
  });

  test('書き手の形を読む — 知らない欄(written_at・written_by)は読み飛ばし、失敗の欄は null か commit・理由・時刻', () => {
    const status = fixture();
    assert.deepStrictEqual(
      status.extensions.map((e) => [e.name, e.identity, e.installed?.version, e.failure?.commit.slice(0, 4) ?? null]),
      [
        ['doeff-runner', RUNNER, '0.6.41', null],
        ['python-semantic-highlighter', HIGHLIGHTER, '1.6.4', '1650']
      ]
    );
  });

  test('知らない版・読めない JSON・形の違う file は黙る(silent)— 欄の欠けた行はその行だけ読み飛ばす', () => {
    const body = JSON.parse(fs.readFileSync(path.join(FIXTURES, 'status.json'), 'utf8'));
    for (const broken of ['{broken', '[]', JSON.stringify({ ...body, schema: 2 }), JSON.stringify({ extensions: [] }), JSON.stringify({ schema: 1 })]) {
      assert.strictEqual(parseFollowStatus(broken).tag, 'silent', broken);
    }
    const torn = JSON.stringify({
      ...body,
      extensions: [{ name: 'no-identity', installed: null, failure: null }, { ...body.extensions[1], failure: { commit: 'x' } }, body.extensions[0]]
    });
    const read = parseFollowStatus(torn);
    assert.strictEqual(read.tag, 'ok');
    if (read.tag === 'ok') {
      assert.deepStrictEqual(read.status.extensions.map((e) => e.name), ['doeff-runner']);
      assert.strictEqual(read.skipped.length, 2);
    }
  });

  test('動いている組み立て — 入った dir の印があればその commit、無い・読めない印は package.json の版だけ、版も無ければ absent', () => {
    const mark = JSON.stringify({ commit: 'c'.repeat(40), identity: RUNNER, version: '0.6.41', written_by: 'x' });
    assert.deepStrictEqual(runningBuild(mark, '0.6.41'), { tag: 'marked', commit: 'c'.repeat(40), version: '0.6.41' });
    assert.deepStrictEqual(runningBuild(undefined, '0.6.34'), { tag: 'unmarked', version: '0.6.34' });
    assert.deepStrictEqual(runningBuild('{broken', '0.6.34'), { tag: 'unmarked', version: '0.6.34' });
    assert.deepStrictEqual(runningBuild(JSON.stringify({ version: '0.6.41' }), '0.6.41'), { tag: 'unmarked', version: '0.6.41' });
    assert.deepStrictEqual(runningBuild(undefined, undefined), { tag: 'absent' });
    assert.strictEqual(manifestVersion({ name: 'doeff-runner', version: '0.6.41' }), '0.6.41');
    assert.strictEqual(manifestVersion({ version: 1 }), undefined);
    assert.strictEqual(manifestVersion(undefined), undefined);
  });

  test('Reload の通知 — 入った commit と動いている印の commit が違えば出す。同じなら出さない。highlighter も自分の印で判じる', () => {
    const status = fixture();
    const current = runningOf({
      [RUNNER]: { tag: 'marked', commit: INSTALLED_RUNNER, version: '0.6.41' },
      [HIGHLIGHTER]: { tag: 'marked', commit: INSTALLED_HIGHLIGHTER, version: '1.6.4' }
    });
    assert.deepStrictEqual(followNotices(status, current).map(show), ['failure python-semantic-highlighter 1650']);
    const stale = runningOf({
      [RUNNER]: { tag: 'marked', commit: 'a'.repeat(40), version: '0.6.41' },
      [HIGHLIGHTER]: { tag: 'marked', commit: 'b'.repeat(40), version: '1.6.4' }
    });
    assert.deepStrictEqual(followNotices(status, stale).map(show), [
      'reload doeff-runner 0.6.41 4141',
      'reload python-semantic-highlighter 1.6.4 1640',
      'failure python-semantic-highlighter 1650'
    ]);
  });

  test('印の無い古い dir は版で判じる — 0.6.34 が動いていて 0.6.41 が入っていれば出す。同じ版なら判じない。窓に居ない拡張も判じない', () => {
    const status = fixture();
    assert.deepStrictEqual(followNotices(status, runningOf({ [RUNNER]: { tag: 'unmarked', version: '0.6.34' } })).map(show), [
      'reload doeff-runner 0.6.41 4141',
      'failure python-semantic-highlighter 1650'
    ]);
    assert.deepStrictEqual(followNotices(status, runningOf({ [RUNNER]: { tag: 'unmarked', version: '0.6.41' } })).map(show), [
      'failure python-semantic-highlighter 1650'
    ]);
    const nothingInstalled: FollowStatus = { extensions: [{ ...status.extensions[0], installed: null }] };
    assert.deepStrictEqual(followNotices(nothingInstalled, runningOf({ [RUNNER]: { tag: 'unmarked', version: '0.6.34' } })), []);
  });

  test('同じ報せは繰り返さない — Reload は入った commit ごとに 1 回、失敗は身元・commit・理由ごとに 1 回(撃ち直しの時刻が変わっても)', () => {
    const status = fixture();
    const stale = runningOf({ [RUNNER]: { tag: 'marked', commit: 'a'.repeat(40), version: '0.6.40' } });
    const first = unseenNotices(followNotices(status, stale), EMPTY_LEDGER);
    assert.deepStrictEqual(first.show.map(show), ['reload doeff-runner 0.6.41 4141', 'failure python-semantic-highlighter 1650']);
    assert.deepStrictEqual(unseenNotices(followNotices(status, stale), first.ledger).show, []);

    const retried: FollowStatus = {
      extensions: status.extensions.map((e) => (e.failure === null ? e : { ...e, failure: { ...e.failure, at: '2026-09-29T13:35:00+09:00' } }))
    };
    assert.deepStrictEqual(unseenNotices(followNotices(retried, stale), first.ledger).show, [], '1 時間後の撃ち直しの同じ失敗をまた出した');

    const newer: FollowStatus = {
      extensions: [{ ...status.extensions[0], installed: { version: '0.6.42', commit: '4242424242424242424242424242424242424242', installedAt: 't' } }]
    };
    assert.deepStrictEqual(unseenNotices(followNotices(newer, stale), first.ledger).show.map(show), ['reload doeff-runner 0.6.42 4242']);
  });

  test('失敗の欄が消えた身元の控えは落とす — 後でまた同じ失敗が出れば、もう 1 度警告する', () => {
    const status = fixture();
    const current = runningOf({});
    const warned = unseenNotices(followNotices(status, current), EMPTY_LEDGER);
    assert.deepStrictEqual(Object.keys(warned.ledger.failures), [HIGHLIGHTER]);
    const recovered: FollowStatus = { extensions: status.extensions.map((e) => ({ ...e, failure: null })) };
    const cleared = unseenNotices(followNotices(recovered, current), warned.ledger);
    assert.deepStrictEqual(cleared.show, []);
    assert.deepStrictEqual(cleared.ledger.failures, {});
    assert.deepStrictEqual(unseenNotices(followNotices(status, current), cleared.ledger).show.map(show), [
      'failure python-semantic-highlighter 1650'
    ]);
  });

  test('保存した失敗の控えは形が違えば空にする。文は拡張の名・版・commit の頭 10 字・理由を載せる', () => {
    assert.deepStrictEqual(readFailureLedger(undefined), {});
    assert.deepStrictEqual(readFailureLedger(['x']), {});
    assert.deepStrictEqual(readFailureLedger({ [RUNNER]: 'k', broken: 1 }), { [RUNNER]: 'k' });
    const [reload, failure] = followNotices(fixture(), runningOf({ [RUNNER]: { tag: 'unmarked', version: '0.6.34' } }));
    assert.ok(reload.tag === 'reload' && failure.tag === 'failure');
    if (reload.tag === 'reload' && failure.tag === 'failure') {
      assert.strictEqual(reloadMessage(reload), 'doeff-runner の新しい版 0.6.41(4141414141)が入りました。Reload Window で有効になります。');
      assert.ok(failureMessage(failure).startsWith('python-semantic-highlighter の組み立てに失敗したため、前の版のままです(1650165016: npm ci'));
    }
  });
});
