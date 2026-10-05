// 全対応文書を検査し、編集・作成・削除後はキャッシュを使って再検査する。
import * as vscode from 'vscode';
import { WorkspaceJudge } from './docWorkspace';
import { RustWorkspaceRunner } from './docWorkspaceProcess';
import { registerTerms } from './termViews';
import type { LintStore } from './store';

export function registerDocLint(
  context: vscode.ExtensionContext,
  store: LintStore,
  output: vscode.OutputChannel,
): (document: vscode.TextDocument) => void {
  const judge = new WorkspaceJudge(
    new RustWorkspaceRunner((root) =>
      vscode.workspace.getConfiguration('doeff-runner.docLint', vscode.Uri.file(root)).get<string>('binary', 'doc-linter'),
    ),
    store,
  );
  const timers = new Map<string, NodeJS.Timeout>();
  const dirty = new Set<string>();
  const supported = (doc: vscode.TextDocument): boolean =>
    doc.uri.scheme === 'file' && /\.(hy|py|pyi|md|markdown|txt)$/i.test(doc.uri.fsPath);
  const run = (folder: vscode.WorkspaceFolder): void => {
    const root = folder.uri.fsPath;
    if (
      !vscode.workspace.isTrusted ||
      !vscode.workspace.getConfiguration('doeff-runner.docLint', folder.uri).get<boolean>('enabled', true)
    ) {
      judge.remove(root);
      return;
    }
    output.appendLine(`[doc-linter] workspace を検査: ${root}（永続キャッシュを利用）`);
    judge.submit({
      root,
      documents: vscode.workspace.textDocuments
        .filter(
          (doc) => supported(doc) && doc.isDirty && vscode.workspace.getWorkspaceFolder(doc.uri)?.uri.toString() === folder.uri.toString(),
        )
        .map((doc) => ({ path: doc.uri.fsPath, text: doc.getText() })),
    });
  };
  const schedule = (folder: vscode.WorkspaceFolder): void => {
    const root = folder.uri.fsPath;
    const timer = timers.get(root);
    if (timer !== undefined) {
      clearTimeout(timer);
    }
    judge.remove(root);
    timers.set(
      root,
      setTimeout(() => {
        timers.delete(root);
        run(folder);
      }, 2000),
    );
  };
  const changed = (uri: vscode.Uri): void => {
    const folder = vscode.workspace.getWorkspaceFolder(uri);
    if (folder !== undefined) {
      schedule(folder);
    }
  };
  const watcher = vscode.workspace.createFileSystemWatcher('**/*.{hy,py,pyi,md,markdown,txt}');
  context.subscriptions.push(
    watcher,
    watcher.onDidCreate(changed),
    watcher.onDidChange(changed),
    watcher.onDidDelete(changed),
    vscode.workspace.onDidChangeTextDocument((e) => {
      if (supported(e.document) && e.contentChanges.length > 0) {
        if (e.document.isDirty) { dirty.add(e.document.uri.fsPath); }
        else { dirty.delete(e.document.uri.fsPath); }
        changed(e.document.uri);
      }
    }),
    vscode.workspace.onDidCloseTextDocument((doc) => {
      if (dirty.delete(doc.uri.fsPath)) {
        changed(doc.uri);
      }
    }),
    vscode.workspace.onDidChangeWorkspaceFolders((e) => {
      for (const f of e.removed) {
        const t = timers.get(f.uri.fsPath);
        if (t !== undefined) {
          clearTimeout(t);
          timers.delete(f.uri.fsPath);
        }
        judge.remove(f.uri.fsPath);
      }
      for (const f of e.added) {
        run(f);
      }
    }),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration('doeff-runner.docLint')) {
        for (const f of vscode.workspace.workspaceFolders ?? []) {
          run(f);
        }
      }
    }),
    vscode.workspace.onDidGrantWorkspaceTrust(() => {
      for (const f of vscode.workspace.workspaceFolders ?? []) {
        run(f);
      }
    }),
    vscode.commands.registerCommand('doeff-runner.docLint.rerun', () => {
      for (const f of vscode.workspace.workspaceFolders ?? []) {
        run(f);
      }
    }),
    {
      dispose: () => {
        for (const t of timers.values()) {
          clearTimeout(t);
        }
        judge.dispose();
      },
    },
  );
  registerTerms(context, store);
  for (const folder of vscode.workspace.workspaceFolders ?? []) {
    run(folder);
  }
  return () => undefined;
}
