// VS Code Extension Host で本物の CLI を使い、両方の診断と読む面の起動を確かめる。
// DOC_VIEWER_TARGET と DOC_VIEWER_EVIDENCE を明示して、--extensionTestsPath から起動する。
const vscode = require('vscode');
const fs = require('fs');

exports.run = async function run() {
  const target = process.env.DOC_VIEWER_TARGET;
  const evidence = process.env.DOC_VIEWER_EVIDENCE;
  if (!target || !evidence) throw new Error('対象と検証記録の保存先を指定してください');
  const extension = vscode.extensions.getExtension('proboscis.doeff-runner');
  if (!extension) throw new Error('doeff runner が読み込まれていません');
  await extension.activate();
  const uri = vscode.Uri.file(target);
  const document = await vscode.workspace.openTextDocument(uri);
  await vscode.window.showTextDocument(document);
  await vscode.commands.executeCommand('doeff-runner.docLint.rerun');
  await vscode.commands.executeCommand('doeff-runner.read.open', uri);
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    const found = vscode.languages.getDiagnostics(uri);
    const sources = new Set(found.map(d => d.source));
    if (sources.has('doeff-linter') && sources.has('doc-linter') && found.some(d => d.source === 'doc-linter' && String(d.code).startsWith('DOC00') && d.code !== 'DOC000')) {
      fs.writeFileSync(evidence, JSON.stringify({
        vscode: vscode.version, extension: extension.packageJSON.version, extensionPath: extension.extensionPath,
        target, diagnostics: found.map(d => ({source:d.source,code:d.code,line:d.range.start.line+1,message:d.message})),
        viewerOpened:true
      }, null, 2)+'\n');
      return;
    }
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw new Error('両方の診断が揃いません: '+JSON.stringify(vscode.languages.getDiagnostics(uri).map(d=>({source:d.source,code:d.code,message:d.message}))));
};
