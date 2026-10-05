// 用語の索引と文章検査の進捗を VS Code に表示する。構文解釈は Rust の結果を使う。
import * as vscode from 'vscode';
import * as path from 'path';
import type { LintStore } from './store';
import type { TermLocation, TermDefinition, DocProgress } from './docWorkspaceContract';

export const TERM_OPEN = 'doeff-runner.terms.open';
export const TERM_REFERENCES = 'doeff-runner.terms.references';
export function termMessage(raw: unknown): void {
  if (raw === null || typeof raw !== 'object' || !('type' in raw) || raw.type !== 'term') {
    return;
  }
  if (
    !('id' in raw) ||
    typeof raw.id !== 'string' ||
    !('file' in raw) ||
    typeof raw.file !== 'string' ||
    !('references' in raw) ||
    typeof raw.references !== 'boolean'
  ) {
    return;
  }
  void vscode.commands.executeCommand(raw.references ? TERM_REFERENCES : TERM_OPEN, raw.id, raw.file);
}
const range = (loc: TermLocation): vscode.Range => new vscode.Range(loc.start.line, loc.start.character, loc.end.line, loc.end.character);
function includes(loc: TermLocation, document: vscode.TextDocument, position: vscode.Position): boolean {
  return loc.path === document.uri.fsPath && range(loc).contains(position);
}
function progressText(p: DocProgress): string {
  const state =
    p.phase === 'queued'
      ? '待機'
      : p.phase === 'running'
        ? '検査中'
        : p.phase === 'failed'
          ? '検査失敗'
          : p.unmeasured > 0
            ? '未測定あり'
            : '検査終了';
  return `${state} ${p.completed}/${p.total}件 · ${p.files}ファイル · キャッシュ ${p.cacheHits} · 未測定 ${p.unmeasured}`;
}
class DocTree implements vscode.TreeDataProvider<vscode.TreeItem>, vscode.Disposable {
  private readonly change = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this.change.event;
  private readonly off: () => void;
  private readonly offProgress: () => void;
  private timer: NodeJS.Timeout | undefined;
  constructor(private readonly store: LintStore) {
    const refresh = (): void => {
      if (this.timer === undefined) {
        this.timer = setTimeout(() => {
          this.timer = undefined;
          this.change.fire();
        }, 150);
      }
    };
    this.off = store.onDidChange(refresh);
    this.offProgress = store.onDidChangeDocumentWorkspace(refresh);
  }
  getTreeItem(item: vscode.TreeItem): vscode.TreeItem {
    return item;
  }
  getChildren(): vscode.TreeItem[] {
    const rows: vscode.TreeItem[] = [];
    for (const [root, s] of this.store.docWorkspaces()) {
      const item = new vscode.TreeItem(path.basename(root));
      item.description = progressText(s.progress);
      item.tooltip = `${root}\nGit の無視設定と依存物を除く全対応ファイル\n${progressText(s.progress)}${s.progress.phase === 'failed' ? `\n${s.progress.reason}` : ''}`;
      item.iconPath = new vscode.ThemeIcon(
        s.progress.phase === 'running' ? 'sync~spin' : s.progress.phase === 'failed' ? 'warning' : 'checklist',
      );
      rows.push(item);
    }
    const findings = this.store.violations().filter((v) => v.source === 'doc-linter');
    const summary = new vscode.TreeItem(`文章の指摘 ${findings.length}件（違反一覧にも表示）`);
    summary.iconPath = new vscode.ThemeIcon('comment-discussion');
    rows.push(summary);
    for (const d of this.store.termIndex().definitions) {
      const item = new vscode.TreeItem(d.title);
      item.description = d.id;
      item.tooltip = d.explanation;
      item.command = { command: TERM_OPEN, title: '用語を開く', arguments: [d.id, d.location.path] };
      item.iconPath = new vscode.ThemeIcon('book');
      rows.push(item);
    }
    for (const v of findings) {
      const item = new vscode.TreeItem(`${v.rule} ${path.basename(v.path)}:${v.range.start.line + 1}`);
      item.description = v.message.split('\n')[0];
      item.tooltip = v.message;
      item.command = {
        command: 'vscode.open',
        title: '指摘を開く',
        arguments: [
          vscode.Uri.file(v.path),
          { selection: new vscode.Range(v.range.start.line, v.range.start.character, v.range.end.line, v.range.end.character) },
        ],
      };
      rows.push(item);
    }
    return rows;
  }
  dispose(): void {
    this.off();
    this.offProgress();
    if (this.timer !== undefined) {
      clearTimeout(this.timer);
    }
    this.change.dispose();
  }
}

export function registerTerms(context: vscode.ExtensionContext, store: LintStore): void {
  const indexFor = (file: string) => {
    const folder = vscode.workspace.getWorkspaceFolder(vscode.Uri.file(file));
    return folder === undefined ? undefined : store.docWorkspaces().get(folder.uri.fsPath)?.index;
  };
  const definition = (id: string, file: string): TermDefinition | undefined => {
    const defs = indexFor(file)?.definitions.filter((d) => d.id === id) ?? [];
    return defs.length === 1 ? defs[0] : undefined;
  };
  const idAt = (doc: vscode.TextDocument, pos: vscode.Position): string | undefined => {
    const i = indexFor(doc.uri.fsPath);
    return i?.references.find((r) => includes(r.location, doc, pos))?.id ?? i?.definitions.find((d) => includes(d.location, doc, pos))?.id;
  };
  const tree = new DocTree(store);
  context.subscriptions.push(
    tree,
    vscode.window.createTreeView('doeff-doc-linter', { treeDataProvider: tree }),
    vscode.commands.registerCommand(TERM_OPEN, async (id: unknown, file: unknown) => {
      if (typeof id !== 'string' || typeof file !== 'string') {
        return;
      }
      const d = definition(id, file);
      if (d === undefined) {
        void vscode.window.showWarningMessage('用語の定義が一意に見つかりません');
        return;
      }
      await vscode.commands.executeCommand('vscode.openWith', vscode.Uri.file(d.location.path), 'default', {
        selection: range(d.location),
      });
    }),
    vscode.commands.registerCommand(TERM_REFERENCES, async (id: unknown, file: unknown) => {
      if (typeof id !== 'string' || typeof file !== 'string') {
        return;
      }
      const refs = indexFor(file)?.references.filter((r) => r.id === id) ?? [];
      const chosen = await vscode.window.showQuickPick(
        refs.map((r) => ({
          label: `${path.basename(r.location.path)}:${r.location.start.line + 1}`,
          description: r.label,
          location: r.location,
        })),
        { title: `${id} の使用箇所` },
      );
      if (chosen !== undefined) {
        await vscode.commands.executeCommand('vscode.openWith', vscode.Uri.file(chosen.location.path), 'default', {
          selection: range(chosen.location),
        });
      }
    }),
    vscode.languages.registerHoverProvider(
      { scheme: 'file' },
      {
        provideHover(doc, pos) {
          const id = idAt(doc, pos);
          if (id === undefined) {
            return;
          }
          const d = definition(id, doc.uri.fsPath);
          if (d === undefined) {
            return;
          }
          const md = new vscode.MarkdownString();
          md.appendText(`${d.title}\n\n${d.explanation}\n\n`);
          md.appendMarkdown(
            `[定義を開く](command:${TERM_OPEN}?${encodeURIComponent(JSON.stringify([id, doc.uri.fsPath]))}) · [使用箇所](command:${TERM_REFERENCES}?${encodeURIComponent(JSON.stringify([id, doc.uri.fsPath]))})`,
          );
          md.isTrusted = { enabledCommands: [TERM_OPEN, TERM_REFERENCES] };
          return new vscode.Hover(md);
        },
      },
    ),
    vscode.languages.registerDefinitionProvider(
      { scheme: 'file' },
      {
        provideDefinition(doc, pos) {
          const id = idAt(doc, pos);
          const d = id === undefined ? undefined : definition(id, doc.uri.fsPath);
          return d === undefined ? undefined : new vscode.Location(vscode.Uri.file(d.location.path), range(d.location));
        },
      },
    ),
    vscode.languages.registerReferenceProvider(
      { scheme: 'file' },
      {
        provideReferences(doc, pos, options) {
          const id = idAt(doc, pos);
          if (id === undefined) {
            return [];
          }
          const i = indexFor(doc.uri.fsPath);
          const locs = i?.references.filter((r) => r.id === id).map((r) => r.location) ?? [];
          if (options.includeDeclaration) {
            locs.push(...(i?.definitions.filter((d) => d.id === id).map((d) => d.location) ?? []));
          }
          return locs.map((loc) => new vscode.Location(vscode.Uri.file(loc.path), range(loc)));
        },
      },
    ),
  );
}
