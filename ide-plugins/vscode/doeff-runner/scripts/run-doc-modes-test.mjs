// 実 CLI と VS Code を使い、外部の Jev 通信だけをローカル HTTP に置き換える。
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { spawn } from 'node:child_process';
import assert from 'node:assert/strict';

async function main() {
  const extension = process.env.DOC_MODES_EXTENSION;
  const binary = process.env.DOC_MODES_BINARY;
  const executable = process.env.DOC_MODES_VSCODE;
  const evidence = process.env.DOC_MODES_EVIDENCE;
  assert(extension && binary && executable && evidence);
  assert(!fs.existsSync(evidence), '新しい記録先を指定する');
  const temp = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'doc-modes-')));
  const root = path.join(temp, 'workspace');
  const driver = path.join(temp, 'driver');
  const failure = path.join(temp, 'failure.txt');
  const calls = path.join(temp, 'calls.jsonl');
  const requests = path.join(temp, 'requests.jsonl');
  for (const dir of [driver, path.join(root, '.vscode')]) fs.mkdirSync(dir, { recursive: true });
  for (const name of ['a', 'b', 'closed', 'other']) fs.writeFileSync(path.join(root, name + '.md'), `${name} の説明を検査する文書です。`);
  fs.writeFileSync(calls, ''); fs.writeFileSync(requests, '');
  const wrapper = path.join(temp, 'doc-linter.cjs');
  fs.writeFileSync(wrapper, `#!/usr/bin/env node
const fs = require('node:fs');
const child = require('node:child_process').spawn(${JSON.stringify(binary)}, process.argv.slice(2), { stdio: ['pipe', 'inherit', 'inherit'] });
let input = ''; process.stdin.setEncoding('utf8');
process.stdin.on('data', s => { input += s; });
process.stdin.on('end', () => { fs.appendFileSync(${JSON.stringify(calls)}, JSON.stringify({ args: process.argv.slice(2), body: JSON.parse(input) })+'\\n'); child.stdin.end(input); });
child.stdin.on('error', () => {});
process.on('SIGTERM', () => child.kill());
child.on('exit', (code, signal) => process.exit(signal ? 143 : code));
child.on('error', () => process.exit(127));
`, { mode: 0o755 });
  fs.writeFileSync(path.join(root, '.vscode/settings.json'), JSON.stringify({ 'doeff-runner.docLint.binary': wrapper }));
  fs.writeFileSync(path.join(driver, 'package.json'), JSON.stringify({ name: 'doc-modes-test', publisher: 'local', version: '0.0.1',
    engines: { vscode: '^1.90.0' }, main: 'driver.cjs', activationEvents: ['onStartupFinished'], extensionDependencies: ['Proboscis.doeff-runner'] }));
  fs.writeFileSync(path.join(driver, 'driver.cjs'), `const v=require('vscode'); exports.activate=async()=>{
try { await (await import(${JSON.stringify(new URL('test-doc-modes.mjs', import.meta.url).href)})).run(); }
catch(e) { require('fs').writeFileSync(${JSON.stringify(failure)}, String(e.stack || e)); }
await v.commands.executeCommand('workbench.action.quit'); };`);
  const body = JSON.stringify({ model: 'modes-test', answers: Object.fromEntries(
    ['DOC001', 'DOC002', 'DOC003', 'DOC004'].map(rule => [rule, { type: 'noul', noul: rule === 'DOC001' ? 0.9 : 0.1 }])) });
  const server = http.createServer((request, response) => {
    let sent = ''; request.setEncoding('utf8');
    request.on('data', chunk => { sent += chunk; });
    request.on('end', () => {
      const state = JSON.parse(sent).state;
      fs.appendFileSync(requests, JSON.stringify(state) + '\n');
      const send = () => { response.writeHead(200, { 'Content-Type': 'application/json' }); response.end(body); };
      if (state.text.includes('SLOW')) {
        const timer = setTimeout(send, 10000);
        response.on('close', () => clearTimeout(timer));
      } else send();
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address(); assert(address && typeof address !== 'string');
  const env = { ...process.env, JEV_BASE_URL: `http://127.0.0.1:${address.port}`, JEV_WIRE: 'direct', JEV_MODEL: 'modes-test',
    JEV_API_KEY: 'local-test', DOC_LINTER_CACHE_DIR: path.join(temp, 'cache'),
    DOC_MODES_ROOT: root, DOC_MODES_EVIDENCE: evidence, DOC_MODES_CALLS: calls, DOC_MODES_REQUESTS: requests };
  console.log(`3モードの実機検証: ${temp}`);
  try {
    await new Promise((resolve, reject) => {
      const child = spawn(executable, [root, '--extensionDevelopmentPath=' + extension, '--extensionDevelopmentPath=' + driver,
        '--user-data-dir=' + path.join(temp, 'profile'), '--extensions-dir=' + path.join(temp, 'extensions'), '--disable-extensions',
        '--disable-workspace-trust', '--skip-welcome', '--skip-release-notes'], { env, stdio: 'inherit' });
      const deadline = setTimeout(() => { child.kill(); reject(new Error('実機検証が90秒で終了しない')); }, 90000);
      child.on('error', e => { clearTimeout(deadline); reject(e); });
      child.on('exit', code => { clearTimeout(deadline); code === 0 ? resolve() : reject(new Error(`VS Code: ${code}`)); });
    });
    if (fs.existsSync(failure)) throw new Error(fs.readFileSync(failure, 'utf8'));
    const result = JSON.parse(fs.readFileSync(evidence, 'utf8'));
    assert.equal(result.stage, 'complete');
    console.log(JSON.stringify({ ...result, temp }, null, 2));
  } finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
