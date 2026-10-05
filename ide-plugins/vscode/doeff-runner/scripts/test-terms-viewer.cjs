// ネイティブの拡張ホストで、閉じた文書の診断と用語の移動・参照・説明表示を確認する。
const vscode = require('vscode');
const fs = require('fs');
const path = require('path');
const assert = require('assert');
exports.run = async function run() {
  const root = process.env.DOC_TERMS_ROOT;
  const evidence = process.env.DOC_TERMS_EVIDENCE;
  if (!root || !evidence) throw new Error('対象と記録先を指定してください');
  const ext = vscode.extensions.getExtension('proboscis.doeff-runner');
  assert(ext); await ext.activate();
  const uri = vscode.Uri.file(path.join(root, 'example.hy'));
  const closed = vscode.Uri.file(path.join(root, 'closed.md'));
  const doc = await vscode.workspace.openTextDocument(uri); await vscode.window.showTextDocument(doc);
  const deadline = Date.now() + 120000;
  while (!vscode.languages.getDiagnostics(closed).some(d => d.code === 'DOC101')) {
    if (Date.now() > deadline) throw new Error('開いていないファイルの診断が届きません');
    await new Promise(r => setTimeout(r, 200));
  }
  assert(!vscode.workspace.textDocuments.some(d => d.uri.toString() === closed.toString()), '閉じた文書を開かずに検査する');
  const offset = doc.getText().lastIndexOf('[宣言の環境変数]');
  assert(offset >= 0); const pos = doc.positionAt(offset + 2);
  const defs = await vscode.commands.executeCommand('vscode.executeDefinitionProvider', uri, pos);
  assert(defs.some(d => (d.uri || d.targetUri).fsPath === path.join(root, 'terms.md')));
  const refs = await vscode.commands.executeCommand('vscode.executeReferenceProvider', uri, pos);
  assert(refs.length >= 3);
  const hover = await vscode.commands.executeCommand('vscode.executeHoverProvider', uri, pos);
  assert(hover.flatMap(h => h.contents).map(c => typeof c === 'string' ? c : c.value).join('\n').includes('設定ファイル'));
  await vscode.commands.executeCommand('doeff-runner.terms.open', 'declared-env', uri.fsPath);
  assert.strictEqual(vscode.window.activeTextEditor.document.uri.fsPath, path.join(root, 'terms.md'));
  await vscode.commands.executeCommand('doeff-runner.read.open', uri);
  await vscode.commands.executeCommand('doeff-doc-linter.focus');
  await vscode.commands.executeCommand('workbench.action.closeAuxiliaryBar');
  fs.writeFileSync(evidence, JSON.stringify({ extension: ext.packageJSON.version, closedFileDiagnostics: vscode.languages.getDiagnostics(closed).map(d => ({ source: d.source, code: d.code })), definitions: defs.length, references: refs.length, hover: true, clickedDefinition: true, viewer: true }, null, 2));
  if (process.env.DOC_TERMS_SCREENSHOT) {
    await new Promise(r => setTimeout(r, 2500));
    const cp = require('child_process');
    const window = cp.execFileSync('swift', ['-e', 'import CoreGraphics; import AppKit; let ws=CGWindowListCopyWindowInfo([.excludeDesktopElements],kCGNullWindowID) as? [[String:Any]] ?? []; for w in ws { if (w[kCGWindowName as String] as? String ?? "").contains("Extension Development Host"), let pid = w[kCGWindowOwnerPID as String] as? Int32 { NSRunningApplication(processIdentifier: pid)?.activate(options: [.activateIgnoringOtherApps]); print(w[kCGWindowNumber as String] ?? ""); break } }'], { encoding: 'utf8' }).trim();
    assert(/^\d+$/.test(window), '検証用の VS Code ウィンドウが必要です');
    await new Promise(r => setTimeout(r, 2000));
    cp.execFileSync('screencapture', ['-x', '-l', window, process.env.DOC_TERMS_SCREENSHOT]);
  }
};
