// 実際の Rust CLI と拡張の呼び出し経路を使用。外部の Jev HTTP だけを差し替える。
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { RustWorkspaceRunner } = require('../out/lint/docWorkspaceProcess');

async function main() {
  const binary = process.argv[2];
  assert.ok(binary, 'doc-linter の実行ファイルを指定してください');
  const root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'doc-project-rules-')));
  const guide = path.join(root, 'guide.md');
  const other = path.join(root, 'other.md');
  const configPath = path.join(root, '.doc-linter.json');
  const config = { version: 1, profiles: [{ paths: ['guide.md'], purpose: '登録を解除できる', reader: '開発者', terms: [
    { preferred: '依存オブジェクト', meaning: '関数へ渡す引数', forbidden_phrases: ['土台オブジェクト'] },
  ] }] };
  await fs.writeFile(guide, '# 操作\n\n土台オブジェクトを渡す。\n');
  await fs.writeFile(other, '# 別の文書\n\n独立した説明。\n');
  await fs.writeFile(path.join(root, 'closed.md'), '自動的に検査対象へ追加しない。');
  await fs.writeFile(configPath, JSON.stringify(config));
  let calls = 0;
  const server = http.createServer((req, res) => {
    let data = '';
    req.on('data', chunk => { data += chunk; });
    req.on('end', () => {
      calls++;
      const body = JSON.parse(data);
      assert.ok(body.state.text);
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ model: 'test', usage: { input_tokens: 1 }, answers: Object.fromEntries(
        Object.keys(body.questions).map(rule => [rule, { type: 'noul', noul: 0.1 }]),
      ) }));
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  process.env.JEV_BASE_URL = `http://127.0.0.1:${server.address().port}/test`;
  process.env.JEV_WIRE = 'direct';
  process.env.JEV_MODEL = 'test';
  process.env.JEV_API_KEY = 'test-only';
  process.env.DOC_LINTER_CACHE_DIR = path.join(root, '.cache');
  const runner = new RustWorkspaceRunner(() => binary);
  const run = async request => {
    const events = [];
    await runner.run({ root, documents: [], ...request }, event => events.push(event), new AbortController().signal);
    return events;
  };
  try {
    const first = await run({ kind: 'selected', paths: [guide, other] });
    assert.equal(calls, 2);
    assert.ok(first[0].snapshot.issues.some(i => i.rule === 'DOC201'));
    assert.equal(first[0].snapshot.files.size, 2);
    await run({ kind: 'changed', paths: [guide] });
    assert.equal(calls, 2, '同じ本文の通知で再推論しない');
    const reload = await run({ kind: 'selected', paths: [guide, other] });
    assert.equal(calls, 2, '再読み込みで永続キャッシュを使う');
    assert.equal(reload.at(-1).cacheHits, 2);
    config.profiles[0].purpose = '条件を理解して登録を解除できる';
    await fs.writeFile(configPath, JSON.stringify(config));
    const changed = await run({ kind: 'changed', paths: [], rulesChanged: true });
    assert.deepEqual(changed[0].snapshot.affected, [guide]);
    assert.equal(calls, 3, '規則の影響を受ける文書だけ再推論する');
    await run({ kind: 'changed', paths: [], rulesChanged: true });
    assert.equal(calls, 3, '同一の設定変更通知では再推論しない');
    await fs.writeFile(configPath, '{ invalid');
    await assert.rejects(() => run({ kind: 'changed', paths: [], rulesChanged: true }));
    await fs.unlink(configPath);
    const removed = await run({ kind: 'changed', paths: [], rulesChanged: true });
    assert.deepEqual(removed[0].snapshot.affected, [guide]);
    assert.ok(!removed[0].snapshot.issues.some(i => i.rule === 'DOC201'));
    console.log(JSON.stringify({ result: 'passed', calls, files: 2, checked: ['対象範囲', '用語診断', '同一本文', '永続キャッシュ', '規則変更', '規則不正', '規則削除'] }));
  } finally {
    await new Promise(resolve => server.close(resolve));
    await fs.rm(root, { recursive: true, force: true });
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
