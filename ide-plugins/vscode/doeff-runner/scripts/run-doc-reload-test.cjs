// 通信先だけをローカル HTTP に置き換え、実 CLI・永続キャッシュ・配布済み拡張を通す。
const fs = require('fs');
const os = require('os');
const path = require('path');
const http = require('http');
const { spawn } = require('child_process');
const assert = require('assert');

async function main() {
  const extension = process.env.DOC_RELOAD_EXTENSION;
  const binary = process.env.DOC_RELOAD_BINARY;
  const executable = process.env.DOC_RELOAD_VSCODE;
  const evidence = process.env.DOC_RELOAD_EVIDENCE;
  assert(extension && binary && executable && evidence, '拡張・CLI・VS Code・記録先を指定してください');
  const temp = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'doc-reload-')));
  console.log(`検証用ディレクトリ: ${temp}`);
  const root = path.join(temp, 'workspace');
  const driver = path.join(temp, 'driver');
  const failure = path.join(temp, 'failure.txt');
  fs.mkdirSync(driver);
  fs.writeFileSync(path.join(driver, 'package.json'), JSON.stringify({ name: 'doc-reload-test', publisher: 'local',
    version: '0.0.1', engines: { vscode: '^1.90.0' }, main: 'driver.cjs', activationEvents: ['onStartupFinished'],
    extensionDependencies: ['Proboscis.doeff-runner'] }));
  // VS Code の extensionTestsPath は reload でテスト用プロセスを終了するため、
  // 専用の検証拡張を使い、通常のウィンドウと同じ経路で再読み込みする。
  fs.writeFileSync(path.join(driver, 'driver.cjs'), `
const vscode = require('vscode');
exports.activate = async () => {
  try { await require(${JSON.stringify(path.join(__dirname, 'test-doc-reload.cjs'))}).run(); }
  catch (error) { require('fs').writeFileSync(${JSON.stringify(failure)}, String(error.stack || error)); }
  await vscode.commands.executeCommand('workbench.action.quit');
};\n`);
  fs.mkdirSync(path.join(root, '.vscode'), { recursive: true });
  fs.writeFileSync(path.join(root, '.vscode', 'settings.json'), JSON.stringify({ 'doeff-runner.docLint.binary': binary }));
  for (let file = 0; file < 100; file++) {
    fs.writeFileSync(path.join(root, `document-${file}.md`), Array.from({ length: 100 }, (_, i) =>
      `文書${file}の項目${i}は、再読み込み後もキャッシュから診断を復元するための検査です。`).join('\n\n'));
  }
  let requests = 0;
  const body = JSON.stringify({ model: 'reload-test', answers: Object.fromEntries(
    ['DOC001', 'DOC002', 'DOC003', 'DOC004'].map(rule => [rule, { type: 'noul', noul: rule === 'DOC001' ? 0.9 : 0.1 }])
  ), usage: { input_tokens: 1 } });
  const server = http.createServer((request, response) => {
    request.resume();
    request.on('end', () => { requests++; response.writeHead(200, { 'Content-Type': 'application/json' }); response.end(body); });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  assert(address && typeof address !== 'string');
  const env = { ...process.env, JEV_BASE_URL: `http://127.0.0.1:${address.port}`, JEV_WIRE: 'direct',
    JEV_MODEL: 'reload-test', JEV_API_KEY: 'local-test', DOC_LINTER_CACHE_DIR: path.join(temp, 'cache'),
    DOC_RELOAD_ROOT: root, DOC_RELOAD_EVIDENCE: evidence };
  try {
    await new Promise((resolve, reject) => {
      const child = spawn(binary, ['workspace', '--root', root], { env, stdio: ['ignore', 'ignore', 'pipe'] });
      let error = '';
      child.stderr.on('data', chunk => { error += chunk.toString(); });
      child.on('error', reject);
      child.on('exit', code => code === 1 ? resolve() : reject(new Error(`キャッシュ準備が失敗: ${code}: ${error}`)));
    });
    assert.strictEqual(requests, 10000);
    console.log('10000件のキャッシュを準備。VS Code の起動と再読み込みを検査します。');
    await new Promise((resolve, reject) => {
      const child = spawn(executable, [root, '--extensionDevelopmentPath=' + extension, '--extensionDevelopmentPath=' + driver,
        '--user-data-dir=' + path.join(temp, 'profile'), '--extensions-dir=' + path.join(temp, 'extensions'),
        '--disable-extensions', '--disable-workspace-trust', '--skip-welcome', '--skip-release-notes'], { env, stdio: 'inherit' });
      const deadline = setTimeout(() => { child.kill(); reject(new Error('実機検証が90秒で終わりませんでした')); }, 90000);
      child.on('error', error => { clearTimeout(deadline); reject(error); });
      child.on('exit', code => { clearTimeout(deadline); code === 0 ? resolve() : reject(new Error(`VS Code 終了コード: ${code}`)); });
    });
    if (fs.existsSync(failure)) { throw new Error(fs.readFileSync(failure, 'utf8')); }
    assert.strictEqual(requests, 10000, '再読み込み時に保存済みの本文を再測定しない');
    const stages = JSON.parse(fs.readFileSync(evidence, 'utf8'));
    assert.strictEqual(stages.length, 2, '実際のウィンドウ再読み込みまで検査する');
    console.log(JSON.stringify({ stages, requests, temp }, null, 2));
  } finally {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
