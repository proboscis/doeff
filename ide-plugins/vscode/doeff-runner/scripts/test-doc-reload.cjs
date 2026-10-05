// インストール済み拡張を本物の VS Code で起動し、大量の診断とウィンドウ再読み込みを検査する。
const vscode = require('vscode');
const fs = require('fs');
const path = require('path');
const assert = require('assert');

exports.run = async function run() {
  const root = process.env.DOC_RELOAD_ROOT;
  const evidence = process.env.DOC_RELOAD_EVIDENCE;
  assert(root && evidence, '対象と検証記録の保存先が必要です');
  const started = Date.now();
  let previousTick = started;
  let maxDelayMs = 0;
  const heartbeat = setInterval(() => {
    const now = Date.now();
    maxDelayMs = Math.max(maxDelayMs, now - previousTick);
    previousTick = now;
  }, 50);
  const stages = fs.existsSync(evidence) ? JSON.parse(fs.readFileSync(evidence, 'utf8')) : [];
  console.log(`再読み込み検査: 段階${stages.length + 1}を開始`);
  try {
    const extension = vscode.extensions.getExtension('proboscis.doeff-runner');
    assert(extension, '拡張が読み込まれていません');
    await extension.activate();
    console.log('再読み込み検査: 拡張起動済み');
    const activationMs = Date.now() - started;
    void vscode.commands.executeCommand('doeff-doc-linter.focus');
    const deadline = Date.now() + 60000;
    let diagnosticCount = 0;
    while (Date.now() < deadline) {
      diagnosticCount = vscode.languages.getDiagnostics()
        .filter(([uri]) => uri.fsPath.startsWith(root + path.sep))
        .reduce((count, [, rows]) => count + rows.filter(d => d.source === 'doc-linter' && d.code === 'DOC001').length, 0);
      if (diagnosticCount === 10000) { break; }
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.strictEqual(diagnosticCount, 10000, '全件の診断が届くこと');
    const diagnosticsMs = Date.now() - started;
    await vscode.window.showTextDocument(vscode.Uri.file(path.join(root, 'document-0.md')));
    await new Promise(resolve => setTimeout(resolve, 100));
    assert(maxDelayMs < 2000, `拡張ホストの応答が ${maxDelayMs}ms 停止しました`);
    stages.push({ stage: stages.length === 0 ? '起動' : '再読み込み', version: extension.packageJSON.version,
      extensionPath: extension.extensionPath, activationMs, diagnosticsMs, diagnosticCount, maxDelayMs });
    fs.writeFileSync(evidence, JSON.stringify(stages, null, 2) + '\n');
  } finally {
    clearInterval(heartbeat);
  }
  if (stages.length === 1) {
    try {
      await vscode.commands.executeCommand('workbench.action.reloadWindow');
    } catch (error) {
      // 再読み込みで古い拡張ホストの RPC が閉じる。2回目の記録が実際の再起動を証明する。
      if (error.name !== 'Canceled') { throw error; }
    }
    await new Promise(() => {}); // 新しい拡張ホストでこの検査が再開する。
  }
};
