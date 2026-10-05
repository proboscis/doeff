// 検査範囲の設定と選択操作。3つのモードを同じ設定から読み書きする。
import * as vscode from 'vscode';

export type DocLintMode = 'openFiles' | 'workspace' | 'off';
export const SELECT_DOC_MODE = 'doeff-runner.docLint.selectMode';
export const DOC_MODES: ReadonlyArray<{ readonly mode: DocLintMode; readonly label: string; readonly detail: string }> = [
  { mode: 'openFiles', label: '開いているファイルのみ', detail: '開いているタブの文書を検査します。用語の定義・参照もこの範囲が対象です。' },
  { mode: 'workspace', label: 'プロジェクト全体', detail: '全対応ファイルを検査し、その後は変更されたファイルだけ更新します。' },
  { mode: 'off', label: 'オフ', detail: '実行中と予約中の文章検査を停止します。キャッシュは保持します。' },
];
export function isDocMode(value: unknown): value is DocLintMode {
  return value === 'openFiles' || value === 'workspace' || value === 'off';
}
export function docMode(root: string): DocLintMode | undefined {
  const value: unknown = vscode.workspace.getConfiguration('doeff-runner.docLint', vscode.Uri.file(root)).get('mode', 'openFiles');
  return isDocMode(value) ? value : undefined;
}
export function docModeLabel(mode: DocLintMode | undefined): string {
  return DOC_MODES.find(option => option.mode === mode)?.label ?? '検査範囲の設定が不正です';
}
export function registerDocMode(context: vscode.ExtensionContext): void {
  context.subscriptions.push(vscode.commands.registerCommand(SELECT_DOC_MODE, async (root: unknown, requested: unknown) => {
    const folders = vscode.workspace.workspaceFolders ?? [];
    let folder = typeof root === 'string' ? folders.find(f => f.uri.fsPath === root) : undefined;
    if (folder === undefined) {
      folder = folders.length === 1 ? folders[0] : (await vscode.window.showQuickPick(
        folders.map(f => ({ label: f.name, description: f.uri.fsPath, folder: f })), { title: '検査範囲を変更するプロジェクト' },
      ))?.folder;
    }
    if (folder === undefined) { return; }
    const current = docMode(folder.uri.fsPath);
    const chosen = isDocMode(requested) ? requested : (await vscode.window.showQuickPick(
      DOC_MODES.map(option => ({ ...option, description: option.mode === current ? '現在の設定' : '' })),
      { title: `${folder.name}：文章の検査範囲`, placeHolder: '検査するファイルの範囲を選択' },
    ))?.mode;
    if (chosen === undefined) { return; }
    const config = vscode.workspace.getConfiguration('doeff-runner.docLint', folder.uri);
    const target = folders.length > 1 || config.inspect('mode')?.workspaceFolderValue !== undefined
      ? vscode.ConfigurationTarget.WorkspaceFolder : vscode.ConfigurationTarget.Workspace;
    await config.update('mode', chosen, target);
  }));
}
