// 用語の索引と文章検査の進捗を VS Code に表示する。構文解釈は Rust の結果を使う。
import * as vscode from 'vscode';
import * as path from 'path';
import type { LintStore } from './store';
import { lintChildren, violationRoots, type LintNode } from './view';
import { lintTreeItem } from './panel';
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
  return `${p.incremental ? '差分 ' : ''}${state}${p.pendingChanges ? '（変更反映待ち）' : ''} ${p.completed}/${p.total}件 · ${p.files}ファイル · キャッシュ ${p.cacheHits} · 未測定 ${p.unmeasured}`;
}
type DocNode = LintNode | { readonly tag: 'progress'; readonly root: string }
  | { readonly tag: 'terms'; readonly definitions: readonly TermDefinition[] }
  | { readonly tag: 'term'; readonly definition: TermDefinition };

/** 既存の違反パネルと同じ規則・ファイル・指摘の木を使う。進捗だけでは木を作り直さない。 */
export class DocTree implements vscode.TreeDataProvider<DocNode>, vscode.Disposable {
  private readonly change = new vscode.EventEmitter<DocNode | undefined>();
  readonly onDidChangeTreeData = this.change.event;
  private readonly off: () => void;
  private readonly offProgress: () => void;
  private timer: NodeJS.Timeout | undefined;
  private roots: DocNode[] | undefined;
  private readonly children = new WeakMap<DocNode, DocNode[]>();
  private readonly parents = new WeakMap<DocNode, DocNode>();
  constructor(private readonly store: LintStore) {
    this.off = store.onDidChange(() => {
      if (this.timer !== undefined) { return; }
      this.timer = setTimeout(() => {
        this.timer = undefined;
        this.roots = undefined;
        this.change.fire(undefined);
      }, 200);
    });
    this.offProgress = store.onDidChangeDocumentWorkspace(() => {
      for (const node of this.roots ?? []) { if (node.tag === 'progress') { this.change.fire(node); } }
    });
  }
  getTreeItem(node: DocNode): vscode.TreeItem {
    switch (node.tag) {
      case 'progress': {
        const p = this.store.docWorkspaces().get(node.root)?.progress;
        const item = new vscode.TreeItem(path.basename(node.root));
        item.id = 'doc:progress:' + node.root;
        item.description = p === undefined ? '未測定' : progressText(p);
        item.tooltip = node.root + (p?.phase === 'failed' ? '\n' + p.reason : '');
        item.iconPath = new vscode.ThemeIcon(p?.phase === 'running' ? 'sync~spin' : p?.phase === 'failed' ? 'warning' : 'checklist');
        return item;
      }
      case 'terms': {
        const item = new vscode.TreeItem('用語の定義', vscode.TreeItemCollapsibleState.Collapsed);
        item.id = 'doc:terms'; item.description = node.definitions.length + '件'; return item;
      }
      case 'term': {
        const d = node.definition;
        const item = new vscode.TreeItem(d.title);
        item.id = 'doc:term:' + JSON.stringify([d.location.path, d.location.start, d.id]);
        item.description = d.id; item.tooltip = d.explanation;
        item.command = { command: TERM_OPEN, title: '用語を開く', arguments: [d.id, d.location.path] };
        item.iconPath = new vscode.ThemeIcon('book'); return item;
      }
      default: {
        const item = lintTreeItem(node, this.store.layers());
        item.id = 'doc:' + item.id;
        return item;
      }
    }
  }
  getChildren(node?: DocNode): DocNode[] {
    if (node === undefined) {
      if (this.roots === undefined) {
        const findings = this.store.violations().filter((v) => v.source === 'doc-linter');
        this.roots = [
          ...[...this.store.docWorkspaces().keys()].map((root): DocNode => ({ tag: 'progress', root })),
          { tag: 'terms', definitions: this.store.termIndex().definitions },
          ...(findings.length === 0 ? [{ tag: 'message' as const, label: '文書の指摘はありません（検査状態は上段に表示）' }]
            : violationRoots(findings, this.store.rules())),
        ];
      }
      return this.roots;
    }
    const cached = this.children.get(node);
    if (cached !== undefined) { return cached; }
    let rows: DocNode[];
    switch (node.tag) {
      case 'progress': case 'term': rows = []; break;
      case 'terms': rows = node.definitions.map((definition) => ({ tag: 'term', definition })); break;
      default: rows = lintChildren(node);
    }
    this.children.set(node, rows);
    for (const child of rows) { this.parents.set(child, node); }
    return rows;
  }
  getParent(node: DocNode): DocNode | undefined { return this.parents.get(node); }
  dispose(): void {
    this.off(); this.offProgress();
    if (this.timer !== undefined) { clearTimeout(this.timer); }
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
