// 初回だけ全体を読み、編集・作成・削除は変更パスをまとめて差分検査する。
import * as path from 'path';
import * as vscode from 'vscode';
import { WorkspaceJudge } from './docWorkspace';
import { RustWorkspaceRunner } from './docWorkspaceProcess';
import { registerTerms } from './termViews';
import type { LintStore } from './store';

export function registerDocLint(context: vscode.ExtensionContext, store: LintStore, output: vscode.OutputChannel): (document: vscode.TextDocument) => void {
  const runner = new RustWorkspaceRunner((root) => vscode.workspace.getConfiguration('doeff-runner.docLint', vscode.Uri.file(root)).get<string>('binary', 'doc-linter'));
  const judge = new WorkspaceJudge(runner, store);
  const timers = new Map<string, NodeJS.Timeout>();
  const pending = new Map<string, Set<string>>();
  const dirty = new Set<string>();
  const supported = (uri: vscode.Uri): boolean => uri.scheme === 'file' && /\.(hy|py|pyi|md|markdown|txt)$/i.test(uri.fsPath)
    && !uri.fsPath.split(path.sep).some((part) => ['.git', 'node_modules', 'target', '.venv', '.deps', '__pycache__'].includes(part));
  const enabled = (folder: vscode.WorkspaceFolder): boolean => vscode.workspace.isTrusted
    && vscode.workspace.getConfiguration('doeff-runner.docLint', folder.uri).get<boolean>('enabled', true);
  const overlays = (folder: vscode.WorkspaceFolder) => vscode.workspace.textDocuments
    .filter((doc) => supported(doc.uri) && doc.isDirty && vscode.workspace.getWorkspaceFolder(doc.uri)?.uri.toString() === folder.uri.toString())
    .map((doc) => ({ path: doc.uri.fsPath, text: doc.getText() }));
  const clear = (root: string): void => {
    const timer = timers.get(root);
    if (timer !== undefined) { clearTimeout(timer); timers.delete(root); }
    pending.delete(root);
  };
  const run = (folder: vscode.WorkspaceFolder): void => {
    const root = folder.uri.fsPath;
    clear(root);
    if (!enabled(folder)) { judge.remove(root); runner.forget(root); return; }
    output.appendLine(`[doc-linter] 初回・明示要求の全体検査: ${root}（永続キャッシュを利用）`);
    judge.submit({ kind: 'initial', root, documents: overlays(folder) });
  };
  const changed = (uri: vscode.Uri): void => {
    const folder = vscode.workspace.getWorkspaceFolder(uri);
    if (folder === undefined || !supported(uri) || !enabled(folder)) { return; }
    const root = folder.uri.fsPath;
    const paths = pending.get(root) ?? new Set<string>();
    paths.add(uri.fsPath);
    pending.set(root, paths);
    // 固定した短い窓で集約する。連続編集で送信時刻を延ばし続けない。
    if (timers.has(root)) { return; }
    timers.set(root, setTimeout(() => {
      timers.delete(root);
      const files = [...(pending.get(root) ?? [])];
      pending.delete(root);
      output.appendLine(`[doc-linter] 差分検査を予約: ${files.length}ファイル`);
      judge.submit({ kind: 'changed', root, paths: files, documents: overlays(folder).filter((d) => files.includes(d.path)) });
    }, 300));
  };
  const watcher = vscode.workspace.createFileSystemWatcher('**/*.{hy,py,pyi,md,markdown,txt}');
  context.subscriptions.push(watcher, watcher.onDidCreate(changed), watcher.onDidChange(changed), watcher.onDidDelete(changed),
    vscode.workspace.onDidChangeTextDocument((e) => {
      if (supported(e.document.uri) && e.contentChanges.length > 0) {
        if (e.document.isDirty) { dirty.add(e.document.uri.fsPath); } else { dirty.delete(e.document.uri.fsPath); }
        changed(e.document.uri);
      }
    }),
    vscode.workspace.onDidCloseTextDocument((doc) => { if (dirty.delete(doc.uri.fsPath)) { changed(doc.uri); } }),
    vscode.workspace.onDidChangeWorkspaceFolders((e) => {
      for (const f of e.removed) { clear(f.uri.fsPath); judge.remove(f.uri.fsPath); runner.forget(f.uri.fsPath); }
      for (const f of e.added) { run(f); }
    }),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration('doeff-runner.docLint')) { for (const f of vscode.workspace.workspaceFolders ?? []) { run(f); } }
    }),
    vscode.workspace.onDidGrantWorkspaceTrust(() => { for (const f of vscode.workspace.workspaceFolders ?? []) { run(f); } }),
    vscode.commands.registerCommand('doeff-runner.docLint.rerun', () => { for (const f of vscode.workspace.workspaceFolders ?? []) { run(f); } }),
    { dispose: () => { for (const root of timers.keys()) { clear(root); } judge.dispose(); } });
  registerTerms(context, store);
  for (const folder of vscode.workspace.workspaceFolders ?? []) { run(folder); }
  return () => undefined;
}
