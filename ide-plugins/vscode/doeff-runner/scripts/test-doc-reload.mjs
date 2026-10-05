// インストール済み拡張を本物の VS Code で起動し、大量の診断とウィンドウ再読み込みを検査する。
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert';
import { createRequire } from 'node:module';
// VS Code が提供する仮想の CommonJS モジュールは Node の ESM 解決では読めない。
const loadExtensionModule = createRequire(import.meta.url);
const vscode = loadExtensionModule('vscode');

export async function run() {
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
    const { LintStore } = loadExtensionModule(path.join(extension.extensionPath, 'out/lint/store'));
    const { DocTree } = loadExtensionModule(path.join(extension.extensionPath, 'out/lint/termViews'));
    const { docFailure } = loadExtensionModule(path.join(extension.extensionPath, 'out/lint/docContract'));
    const data = new LintStore();
    const index = { definitions: [], references: [] };
    const progress = { phase: 'running', files: 360, total: 36000, completed: 0, cacheHits: 0, unmeasured: 0 };
    const findings = new Map(Array.from({ length: 360 }, (_, file) => {
      const target = path.join(root, 'document-' + file + '.md');
      return [target, Array.from({ length: 100 }, (_, line) => ({ ...docFailure(target, '確認', line), rule: 'DOC001' }))];
    }));
    data.updateDocumentWorkspace(root, index, progress, findings);
    const tree = new DocTree(data);
    const roots = tree.getChildren();
    assert(roots.length < 20, '36000件を最上段に展開しない');
    const group = roots.find(n => n.tag === 'group');
    assert(group && group.rule === 'DOC001');
    const files = tree.getChildren(group);
    assert.strictEqual(files.length, 360);
    const leaves = tree.getChildren(files[0]);
    assert.strictEqual(leaves.length, 100);
    assert.deepStrictEqual(tree.getChildren(leaves[0]), [], '指摘は終端であり、一覧を子として返さない');
    assert.strictEqual(tree.getParent(leaves[0]), files[0]);
    assert.strictEqual(tree.getTreeItem(leaves[0]).id, tree.getTreeItem(leaves[0]).id);
    let fullRefreshes = 0;
    tree.onDidChangeTreeData(node => { if (node === undefined) fullRefreshes++; });
    for (let i = 1; i <= 100; i++) data.setDocumentWorkspace(root, index, { ...progress, completed: i });
    await new Promise(resolve => setTimeout(resolve, 300));
    assert.strictEqual(tree.getChildren(), roots, '進捗のみで木を作り直さない');
    assert.strictEqual(fullRefreshes, 0);
    tree.dispose();

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
    const editor = await vscode.window.showTextDocument(vscode.Uri.file(path.join(root, 'document-0.md')));
    const replacement = '変更した段落だけを差分検査する。'.repeat(5 + stages.length);
    const edit = new vscode.WorkspaceEdit();
    edit.replace(editor.document.uri, editor.document.lineAt(0).range, replacement);
    assert(await vscode.workspace.applyEdit(edit));
    const editStarted = Date.now();
    let updated = false;
    while (Date.now() - editStarted < 10000) {
      updated = vscode.languages.getDiagnostics(editor.document.uri).some(d =>
        d.source === 'doc-linter' && d.code === 'DOC001' && d.range.start.line === 0 && d.range.end.character === replacement.length);
      if (updated) break;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    assert(updated, '編集中の本文に差分の診断が届くこと');
    const editedMs = Date.now() - editStarted;
    assert(await editor.document.save());
    assert(maxDelayMs < 2000, `拡張ホストの応答が ${maxDelayMs}ms 停止しました`);
    stages.push({ stage: stages.length === 0 ? '起動' : '再読み込み', version: extension.packageJSON.version,
      extensionPath: extension.extensionPath, groupedFindings: 36000, progressFullRefreshes: 0, activationMs, diagnosticsMs, diagnosticCount, editedMs, maxDelayMs });
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
}
