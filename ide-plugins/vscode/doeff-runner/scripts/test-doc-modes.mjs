import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const v = require('vscode');
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(predicate, label) {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) { if (predicate()) return; await wait(40); }
  assert.fail(label);
}
export async function run() {
  const root = process.env.DOC_MODES_ROOT;
  const evidence = process.env.DOC_MODES_EVIDENCE;
  assert(root && evidence);
  const lines = file => fs.readFileSync(file, 'utf8').trim().split('\n').filter(Boolean).map(s => JSON.parse(s));
  const calls = () => lines(process.env.DOC_MODES_CALLS);
  const requests = () => lines(process.env.DOC_MODES_REQUESTS);
  const uri = name => v.Uri.file(path.join(root, name + '.md'));
  const findings = name => v.languages.getDiagnostics(uri(name)).filter(d => d.source === 'doc-linter' && d.code === 'DOC001');
  const allFindings = () => v.languages.getDiagnostics().flatMap(([, ds]) => ds.filter(d => d.source === 'doc-linter'));
  const mode = value => v.commands.executeCommand('doeff-runner.docLint.selectMode', root, value);
  const config = () => v.workspace.getConfiguration('doeff-runner.docLint', v.Uri.file(root));
  const extension = v.extensions.getExtension('proboscis.doeff-runner'); assert(extension); await extension.activate();
  const contributions = extension.packageJSON.contributes;
  assert(contributions.menus['view/title'].some(item => item.command === 'doeff-runner.docLint.selectMode' && item.when === 'view == doeff-doc-linter' && item.group.startsWith('navigation')));
  assert.equal(contributions.commands.find(item => item.command === 'doeff-runner.docLint.selectMode').icon, '$(filter)');
  assert.equal(config().get('mode'), 'openFiles', '既定は開いているファイルのみ');
  if (fs.existsSync(evidence)) {
    const before = JSON.parse(fs.readFileSync(evidence, 'utf8'));
    await until(() => findings('a').length === 1 && findings('b').length === 1, '再読み込み後に開いたファイルの診断が復元する');
    assert.equal(findings('closed').length, 0);
    assert.equal(requests().length, before.requests, '再読み込みも永続キャッシュを使う');
    assert.equal(calls().filter(c => !c.args.includes('--incremental')).length, 1, '全体検査は明示的に選択した一回だけ');
    await mode('off'); await until(() => allFindings().length === 0, 'オフ');
    fs.writeFileSync(evidence, JSON.stringify({ ...before, stage: 'complete', persistedMode: 'openFiles', reloadCacheRequests: 0, version: extension.packageJSON.version }, null, 2));
    return;
  }
  await wait(500);
  assert.equal(calls().length, 0, 'タブがなければ CLI を起動しない');
  await v.workspace.openTextDocument(uri('closed'));
  await wait(400);
  assert.equal(calls().length, 0, '表示していない内部の TextDocument は対象にしない');
  const a = await v.window.showTextDocument(uri('a'), { preview: false });
  await until(() => findings('a').length === 1, 'a の診断');
  assert.equal(requests().length, 1);
  const replace = async (editor, text) => {
    const edit = new v.WorkspaceEdit();
    const last = editor.document.lineAt(editor.document.lineCount - 1);
    edit.replace(editor.document.uri, new v.Range(0, 0, last.lineNumber, last.text.length), text);
    assert(await v.workspace.applyEdit(edit));
  };
  await replace(a, '保存前の文章の変更だけを検査する説明です。');
  await until(() => requests().length === 2, '保存前の本文を検査');
  await until(() => findings('a')[0]?.range.end.character === a.document.getText().length, '診断が編集へ追随');
  fs.writeFileSync(uri('closed').fsPath, '閉じたファイルを外から変更した説明です。');
  await wait(600);
  assert.equal(requests().length, 2, '閉じたファイルの変更を検査しない');
  const b = await v.window.showTextDocument(uri('b'), { preview: false });
  await until(() => findings('b').length === 1, '複数の開いたタブを検査');
  assert.equal(findings('a').length, 1);
  const bTab = v.window.tabGroups.all.flatMap(g => g.tabs).find(t => t.input instanceof v.TabInputText && t.input.uri.fsPath === b.document.uri.fsPath);
  assert(bTab); await v.window.tabGroups.close(bTab);
  await until(() => findings('b').length === 0 && findings('a').length === 1, '閉じたタブの診断を除く');
  await v.window.showTextDocument(uri('b'), { preview: false });
  await until(() => findings('b').length === 1, '再び開く');
  assert.equal(requests().length, 3, '再び開いてもキャッシュを使う');
  assert(calls().every(c => c.args.includes('--incremental')), '開いたファイルのモードでは全体走査しない');
  assert(calls().every(c => c.body.documents.every(d => [uri('a').fsPath, uri('b').fsPath].includes(d.path))), '閉じたファイルを CLI に渡さない');
  // 右上のボタンと同じコマンドで選択 UI を開き、2番目の「全体」を選ぶ。
  const choosing = v.commands.executeCommand('doeff-runner.docLint.selectMode', root);
  await wait(200);
  await v.commands.executeCommand('workbench.action.quickOpenSelectNext');
  await v.commands.executeCommand('workbench.action.acceptSelectedQuickOpenItem');
  await choosing;
  assert.equal(config().get('mode'), 'workspace', '実際の選択 UI から全体を選ぶ');
  await until(() => allFindings().length === 4, '全体モードでは閉じたファイルも検査する');
  assert.equal(requests().length, 5);
  fs.writeFileSync(uri('closed').fsPath, '全体モードで変更した一つの文章を検査する。');
  await until(() => requests().length === 6, '全体モードの差分');
  assert.equal(calls().filter(c => !c.args.includes('--incremental')).length, 1);
  await mode('openFiles');
  await until(() => allFindings().length === 2 && findings('closed').length === 0, '全体から開いたファイルへ絞る');
  assert.equal(requests().length, 6);
  await replace(a, 'SLOW 実行中にオフへ切り替える検査です。');
  await until(() => requests().length === 7, '通信中になる');
  const previousCancellations = fs.readFileSync(process.env.DOC_MODES_CANCELLED, 'utf8').length;
  await mode('off'); await until(() => allFindings().length === 0, '通信中の検査を停止');
  await until(() => fs.readFileSync(process.env.DOC_MODES_CANCELLED, 'utf8').length > previousCancellations, '実際の CLI プロセスに停止が届く');
  const stoppedCalls = calls().length;
  await replace(a, '停止中に変更した本文は再開するまで検査しません。');
  fs.writeFileSync(uri('other').fsPath, '停止中に閉じた文書を変更した。');
  await wait(600);
  assert.equal(calls().length, stoppedCalls, 'オフでは起動が増えない');
  assert.equal(requests().length, 7);
  await mode('openFiles');
  await until(() => findings('a').length === 1 && findings('b').length === 1, '停止からの再開');
  assert.equal(requests().length, 8);
  assert(await a.document.save());
  await wait(500);
  assert.equal(requests().length, 8, '同じ内容の保存では再推論しない');
  const { DocTree } = require(path.join(extension.extensionPath, 'out/lint/termViews'));
  const { LintStore } = require(path.join(extension.extensionPath, 'out/lint/store'));
  const tree = new DocTree(new LintStore());
  const selector = tree.getChildren().find(n => n.tag === 'mode'); assert(selector, '空の結果でもモード選択が見える');
  const item = tree.getTreeItem(selector);
  assert.equal(item.command.command, 'doeff-runner.docLint.selectMode');
  assert(String(item.description).includes('開いている')); tree.dispose();
  assert.equal(config().inspect('mode').workspaceValue, 'openFiles', '選択は workspace へ保存');
  fs.writeFileSync(evidence, JSON.stringify({ stage: 'reload', requests: requests().length, wholeScans: 1, closedFileRequestsInOpenMode: 0,
    modes: ['openFiles', 'workspace', 'off'], cancellation: true, unsavedChanges: true }, null, 2));
  try { await v.commands.executeCommand('workbench.action.reloadWindow'); }
  catch (error) { if (error.name !== 'Canceled') throw error; }
  await new Promise(() => {});
}
