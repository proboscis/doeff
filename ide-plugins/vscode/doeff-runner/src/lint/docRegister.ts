// 選ばれた検査範囲だけを読み、編集・作成・削除は変更パスをまとめて差分検査する。
import * as path from 'path';
import * as vscode from 'vscode';
import { WorkspaceJudge } from './docWorkspace';
import { RustWorkspaceRunner } from './docWorkspaceProcess';
import { registerTerms } from './termViews';
import type { LintStore } from './store';
import { docMode, registerDocMode } from './docMode';

export function registerDocLint(context: vscode.ExtensionContext, store: LintStore, output: vscode.OutputChannel): (document: vscode.TextDocument) => void {
  const runner = new RustWorkspaceRunner((root) => vscode.workspace.getConfiguration('doeff-runner.docLint', vscode.Uri.file(root)).get<string>('binary', 'doc-linter'));
  const judge = new WorkspaceJudge(runner, store);
  const timers = new Map<string, NodeJS.Timeout>();
  const pending = new Map<string, Set<string>>();
  const dirty = new Set<string>();
  const opened = new Map<string, readonly string[]>();
  const supported = (uri: vscode.Uri): boolean => uri.scheme === 'file' && /\.(hy|py|pyi|md|markdown|txt)$/i.test(uri.fsPath)
    && !uri.fsPath.split(path.sep).some((part) => ['.git', 'node_modules', 'target', '.venv', '.deps', '__pycache__'].includes(part));
  const openPaths = (folder: vscode.WorkspaceFolder): string[] => [...new Set(vscode.window.tabGroups.all.flatMap(group => group.tabs.flatMap(tab => {
    const input = tab.input;
    const uris = input instanceof vscode.TabInputText || input instanceof vscode.TabInputCustom ? [input.uri]
      : input instanceof vscode.TabInputTextDiff ? [input.original, input.modified] : [];
    return uris.filter(uri => supported(uri) && vscode.workspace.getWorkspaceFolder(uri)?.uri.toString() === folder.uri.toString()).map(uri => uri.fsPath);
  })))].sort();
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
    judge.remove(root); runner.forget(root); opened.delete(root);
    const mode = docMode(root);
    if (!vscode.workspace.isTrusted || mode === 'off' || mode === undefined) { return; }
    if (mode === 'workspace') {
      output.appendLine(`[doc-linter] 初回・明示要求の全体検査: ${root}（永続キャッシュを利用）`);
      judge.submit({ kind: 'initial', root, documents: overlays(folder) });
    } else {
      const paths = openPaths(folder);
      opened.set(root, paths);
      if (paths.length === 0) { return; }
      output.appendLine(`[doc-linter] 開いているファイルの検査: ${root}（${paths.length}ファイル）`);
      judge.submit({ kind: 'selected', root, paths, documents: overlays(folder).filter(d => paths.includes(d.path)) });
    }
  };
  const changed = (uri: vscode.Uri): void => {
    const folder = vscode.workspace.getWorkspaceFolder(uri);
    if (folder === undefined || !supported(uri) || !vscode.workspace.isTrusted) { return; }
    const root = folder.uri.fsPath;
    const mode = docMode(root);
    if (mode === 'off' || mode === undefined || (mode === 'openFiles' && !opened.get(root)?.includes(uri.fsPath))) { return; }
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
  const changedTabs = (): void => {
    for (const folder of vscode.workspace.workspaceFolders ?? []) {
      if (docMode(folder.uri.fsPath) !== 'openFiles') { continue; }
      const paths = openPaths(folder);
      const previous = opened.get(folder.uri.fsPath) ?? [];
      if (paths.length !== previous.length || paths.some((file, i) => file !== previous[i])) { run(folder); }
    }
  };
  const watcher = vscode.workspace.createFileSystemWatcher('**/*.{hy,py,pyi,md,markdown,txt}');
  context.subscriptions.push(watcher, watcher.onDidCreate(changed), watcher.onDidChange(changed), watcher.onDidDelete(changed),
    vscode.window.tabGroups.onDidChangeTabs(changedTabs),
    vscode.window.tabGroups.onDidChangeTabGroups(changedTabs),
    vscode.workspace.onDidChangeTextDocument((e) => {
      if (supported(e.document.uri) && e.contentChanges.length > 0) {
        if (e.document.isDirty) { dirty.add(e.document.uri.fsPath); } else { dirty.delete(e.document.uri.fsPath); }
        changed(e.document.uri);
      }
    }),
    vscode.workspace.onDidCloseTextDocument((doc) => { if (dirty.delete(doc.uri.fsPath)) { changed(doc.uri); } }),
    vscode.workspace.onDidChangeWorkspaceFolders((e) => {
      for (const f of e.removed) { clear(f.uri.fsPath); judge.remove(f.uri.fsPath); runner.forget(f.uri.fsPath); opened.delete(f.uri.fsPath); }
      for (const f of e.added) { run(f); }
    }),
    vscode.workspace.onDidChangeConfiguration((e) => {
      for (const f of vscode.workspace.workspaceFolders ?? []) {
        if (e.affectsConfiguration('doeff-runner.docLint', f.uri)) { run(f); }
      }
    }),
    vscode.workspace.onDidGrantWorkspaceTrust(() => { for (const f of vscode.workspace.workspaceFolders ?? []) { run(f); } }),
    vscode.commands.registerCommand('doeff-runner.docLint.rerun', () => { for (const f of vscode.workspace.workspaceFolders ?? []) { run(f); } }),
    { dispose: () => { for (const root of timers.keys()) { clear(root); } judge.dispose(); } });
  registerDocMode(context);
  registerTerms(context, store);
  for (const folder of vscode.workspace.workspaceFolders ?? []) { run(folder); }
  return () => undefined;
}
