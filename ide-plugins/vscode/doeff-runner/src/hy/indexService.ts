// Hy の索引を保つ係 — 起動時の root 全体・編集(debounce)と保存・file の作成と削除と改名・folder の増減を
// 索引の依頼に変え、結果を 1 つの store へ入れる。VS Code の event と store をつなぐだけで、解決の論理は持たない。

import * as vscode from 'vscode';
import type { HyFileIndex } from './contract';
import { HY_EXCLUDE_GLOB, HY_FILE_GLOB, isExcludedPath, isHyPath } from './hyPaths';
import type { HyIndexer, HyIndexOutcome, HyIndexRequest } from './indexer';
import { nextStatus, type HyIndexStatus, type IndexResult } from './indexStatus';
import { normalizeKey, type HyIndexStore } from './store';

export { HY_EXCLUDE_GLOB, HY_FILE_GLOB, isHyPath };

const EDIT_DEBOUNCE_MS = 500;
const DIRECTORY_REINDEX_DEBOUNCE_MS = 1500;

/** Output channel のうち、ここが使う面だけ。 */
export interface HyLog {
  appendLine(line: string): void;
}

/** 開いている document が Hy のものかを見る(languageId か拡張子)。 */
function isHyDocument(document: vscode.TextDocument): boolean {
  return (
    document.uri.scheme === 'file' &&
    (document.languageId === 'hy' || isHyPath(document.uri.fsPath)) &&
    !isExcludedPath(document.uri.fsPath)
  );
}

/** 索引の状態を読む面(パネルが「なぜ無いのか」を出すのに使う)。 */
export interface HyIndexStatusView {
  readonly status: HyIndexStatus;
  onDidChangeStatus(listener: () => void): () => void;
}

/** 索引を取り、store を最新に保つ係。 */
export class HyIndexService implements vscode.Disposable, HyIndexStatusView {
  private disabled = false;
  private readonly disposables: vscode.Disposable[] = [];
  private readonly debounces = new Map<string, NodeJS.Timeout>();
  private readonly rootRuns = new Map<string, Promise<void>>();
  private current: HyIndexStatus = { tag: 'waiting' };
  private readonly statusListeners = new Set<() => void>();

  constructor(
    private readonly store: HyIndexStore,
    private readonly indexer: HyIndexer,
    private readonly log: HyLog,
    private readonly onUnsupported: (reason: string) => void
  ) {}

  /** event の購読を始め、Hy の file を持つ各 folder の root 全体の索引を背景で取る。 */
  start(): void {
    this.disposables.push(
      vscode.workspace.onDidChangeTextDocument((event) => {
        if (event.contentChanges.length > 0 && isHyDocument(event.document)) {
          this.scheduleDocument(event.document, EDIT_DEBOUNCE_MS);
        }
      }),
      vscode.workspace.onDidSaveTextDocument((document) => {
        if (isHyDocument(document)) {
          this.scheduleDocument(document, 0);
        }
      }),
      vscode.workspace.onDidOpenTextDocument((document) => {
        if (isHyDocument(document) && this.store.get(document.uri.fsPath) === undefined) {
          this.scheduleDocument(document, 0);
        }
      }),
      vscode.workspace.onDidChangeWorkspaceFolders((event) => {
        for (const removed of event.removed) {
          this.store.removeUnder(removed.uri.fsPath);
        }
        for (const added of event.added) {
          void this.indexFolderIfHy(added);
        }
      })
    );
    const hyWatcher = vscode.workspace.createFileSystemWatcher(HY_FILE_GLOB);
    // dir ごとの削除・改名(改名 = 旧の削除 + 新の作成)も拾うため、削除と作成は全 path で見る
    const anyWatcher = vscode.workspace.createFileSystemWatcher('**/*', false, true, false);
    this.disposables.push(
      hyWatcher,
      anyWatcher,
      hyWatcher.onDidCreate((uri) => this.onDiskChange(uri)),
      hyWatcher.onDidChange((uri) => this.onDiskChange(uri)),
      anyWatcher.onDidCreate((uri) => void this.onPathCreated(uri)),
      anyWatcher.onDidDelete((uri) => {
        this.store.remove(uri.fsPath);
        this.store.removeUnder(uri.fsPath);
      })
    );
    void this.indexAllFolders();
  }

  /** 今の索引の状態。 */
  get status(): HyIndexStatus {
    return this.current;
  }

  /** 状態の変化を購読する。戻り値で購読をやめる。 */
  onDidChangeStatus(listener: () => void): () => void {
    this.statusListeners.add(listener);
    return () => this.statusListeners.delete(listener);
  }

  /** 状態を変えて購読者へ知らせる。 */
  private setStatus(status: HyIndexStatus): void {
    this.current = status;
    for (const listener of this.statusListeners) {
      listener();
    }
  }

  /** 全 folder を調べ、Hy の file を持つ folder の索引を取る(1 つも無ければ状態に出す)。 */
  private async indexAllFolders(): Promise<void> {
    const found = await Promise.all((vscode.workspace.workspaceFolders ?? []).map((folder) => this.indexFolderIfHy(folder)));
    if (!found.some((f) => f)) {
      this.setStatus({ tag: 'no-hy-files' });
    }
  }

  /** 止めた索引を再び動かし、全 folder の索引を取り直す(道具を入れ直した後の口 — 探し直しは呼び手が indexer に頼む)。 */
  resume(): void {
    this.disabled = false;
    this.setStatus({ tag: 'waiting' });
    void this.indexAllFolders();
  }

  /** 購読と保留中の debounce を止める。 */
  dispose(): void {
    for (const timer of this.debounces.values()) {
      clearTimeout(timer);
    }
    this.debounces.clear();
    for (const d of this.disposables) {
      d.dispose();
    }
  }

  /** 全 folder の root 全体の索引を取り直す(目録の設定が変わった時)。 */
  reindexAll(): void {
    void this.indexAllFolders();
  }

  /** folder に Hy の file が 1 つでもあれば、root 全体の索引を取る(Python だけの workspace では走らせない)。取ったかを返す。 */
  private async indexFolderIfHy(folder: vscode.WorkspaceFolder): Promise<boolean> {
    const any = await vscode.workspace.findFiles(
      new vscode.RelativePattern(folder, HY_FILE_GLOB),
      new vscode.RelativePattern(folder, HY_EXCLUDE_GLOB),
      1
    );
    if (any.length === 0) {
      return false;
    }
    await this.indexRoot(folder.uri.fsPath);
    return true;
  }

  /** root 全体の索引を取り直す(同じ root の走行が進行中なら、それに合流する)。 */
  indexRoot(root: string): Promise<void> {
    const key = normalizeKey(root);
    const running = this.rootRuns.get(key);
    if (running !== undefined) {
      return running;
    }
    if (!this.disabled) {
      this.setStatus({ tag: 'indexing', root });
    }
    const run = this.request({ tag: 'root', root })
      .then(() => this.reindexDirtyDocuments(root))
      .finally(() => this.rootRuns.delete(key));
    this.rootRuns.set(key, run);
    return run;
  }

  /** root 全体は disk の内容で取るので、保存前の編集がある document はその内容で取り直す。 */
  private reindexDirtyDocuments(root: string): void {
    for (const document of vscode.workspace.textDocuments) {
      if (document.isDirty && isHyDocument(document) && this.rootOf(document.uri) === root) {
        this.scheduleDocument(document, 0);
      }
    }
  }

  /** disk の上で作られた・書き換えられた file を取り直す(編集中の document は editor 側の event に任せる)。 */
  private onDiskChange(uri: vscode.Uri): void {
    if (isExcludedPath(uri.fsPath)) {
      return;
    }
    const open = vscode.workspace.textDocuments.find((d) => d.uri.fsPath === uri.fsPath);
    if (open !== undefined && open.isDirty) {
      return;
    }
    const root = this.rootOf(uri);
    if (root === undefined) {
      return;
    }
    void this.request({ tag: 'files', root, files: [uri.fsPath] });
  }

  /**
   * dir が作られた(dir の改名・移動を含む)時、中の Hy の file は 1 件ずつ通知されないことがあるので、
   * その folder 全体を少し待ってから取り直す(Hy の file を持たない folder では走らせない)。file の作成は hyWatcher 側が扱う。
   */
  private async onPathCreated(uri: vscode.Uri): Promise<void> {
    if (this.disabled || isHyPath(uri.fsPath)) {
      return;
    }
    const folder = vscode.workspace.getWorkspaceFolder(uri);
    if (folder === undefined || isExcludedPath(uri.fsPath)) {
      return;
    }
    let stat: vscode.FileStat;
    try {
      stat = await vscode.workspace.fs.stat(uri);
    } catch (error) {
      // 作られた直後に消えた path(一時 file 等)は取り直す物が無い
      this.log.appendLine(`[hy-index] 作られた path を確かめられない ${uri.fsPath}: ${String(error)}`);
      return;
    }
    if (stat.type !== vscode.FileType.Directory) {
      return;
    }
    const key = `root:${normalizeKey(folder.uri.fsPath)}`;
    const pending = this.debounces.get(key);
    if (pending !== undefined) {
      clearTimeout(pending);
    }
    this.debounces.set(
      key,
      setTimeout(() => {
        this.debounces.delete(key);
        void this.indexFolderIfHy(folder);
      }, DIRECTORY_REINDEX_DEBOUNCE_MS)
    );
  }

  /** document の今の内容を stdin で取る依頼を、path ごとに debounce して積む。 */
  private scheduleDocument(document: vscode.TextDocument, delayMs: number): void {
    const key = normalizeKey(document.uri.fsPath);
    const pending = this.debounces.get(key);
    if (pending !== undefined) {
      clearTimeout(pending);
    }
    this.debounces.set(
      key,
      setTimeout(() => {
        this.debounces.delete(key);
        const root = this.rootOf(document.uri);
        if (root === undefined || document.isClosed) {
          return;
        }
        void this.request({ tag: 'stdin', root, path: document.uri.fsPath, text: document.getText() });
      }, delayMs)
    );
  }

  /** uri が属する workspace folder の path(module 名の基準)。workspace の外なら undefined。 */
  private rootOf(uri: vscode.Uri): string | undefined {
    return vscode.workspace.getWorkspaceFolder(uri)?.uri.fsPath;
  }

  /** 依頼を索引の口へ渡し、結果を store に入れる(止まっている時は何もしない)。 */
  private async request(request: HyIndexRequest): Promise<void> {
    if (this.disabled) {
      return;
    }
    const outcome = await this.indexer.index(request);
    this.apply(request, outcome);
    this.setStatus(nextStatus(this.current, request, resultOf(outcome, this.store.entries().length)));
  }

  /** 索引の結果を store へ入れ、失敗・捨てた file・file ごとの errors を Output に出す。 */
  private apply(request: HyIndexRequest, outcome: HyIndexOutcome): void {
    switch (outcome.tag) {
      case 'unsupported':
        this.disable(outcome.reason);
        return;
      case 'missing':
        // 道具が無い時は file ごとに同じ理由が並ぶので、root 全体の依頼の時だけ出す(状態はパネルに出る)
        if (request.tag === 'root') {
          this.log.appendLine(`[hy-index] 索引を作る道具が無い (${describe(request)}): ${outcome.reason}`);
        }
        return;
      case 'failed':
        this.log.appendLine(`[hy-index] 失敗 (${describe(request)}): ${outcome.reason}`);
        return;
      case 'ok':
        break;
      default: {
        const unreachable: never = outcome;
        throw new Error(`網羅されていない結果: ${JSON.stringify(unreachable)}`);
      }
    }
    for (const problem of outcome.document.rawCatalogProblems) {
      this.log.appendLine(`[hy-index] 生の副作用の目録の追加を読めない: ${problem}`);
    }
    for (const rejected of outcome.rejected) {
      this.log.appendLine(`[hy-index] 契約に合わない file を捨てた ${rejected.path ?? '(path 不明)'}: ${rejected.reason}`);
    }
    const files = outcome.document.files;
    logFileErrors(this.log, files);
    switch (request.tag) {
      case 'root':
        this.store.replaceRoot(request.root, files);
        this.log.appendLine(`[hy-index] ${request.root}: ${files.length} file を索引した`);
        return;
      case 'files':
        this.replaceRequested(request.root, request.files, files);
        return;
      case 'stdin':
        this.replaceRequested(request.root, [request.path], files);
        return;
      default: {
        const unreachable: never = request;
        throw new Error(`網羅されていない依頼: ${JSON.stringify(unreachable)}`);
      }
    }
  }

  /** 指定した file の索引を差し替える — 結果に出なかった file(除外の dir 等)は store から落とす。 */
  private replaceRequested(root: string, requested: readonly string[], files: readonly HyFileIndex[]): void {
    const returned = new Set(files.map((f) => normalizeKey(f.path)));
    for (const filePath of requested) {
      if (!returned.has(normalizeKey(filePath))) {
        this.store.remove(filePath);
      }
    }
    this.store.upsert(root, files);
  }

  /** binary が hy-index を知らない時に Hy の機能を止め、1 度だけ知らせる。 */
  private disable(reason: string): void {
    if (this.disabled) {
      return;
    }
    this.disabled = true;
    this.log.appendLine(`[hy-index] Hy の索引を止めた: ${reason}`);
    this.onUnsupported(reason);
  }
}

/** 索引の口の結果を、状態を決める材料に写す(ok の時は置き場の file の数を添える)。 */
function resultOf(outcome: HyIndexOutcome, files: number): IndexResult {
  switch (outcome.tag) {
    case 'ok':
      return { tag: 'ok', files };
    case 'missing':
      return { tag: 'missing', reason: outcome.reason };
    case 'unsupported':
      return { tag: 'unsupported', binary: outcome.binary, reason: outcome.reason };
    case 'failed':
      return { tag: 'failed', reason: outcome.reason };
    default: {
      const unreachable: never = outcome;
      throw new Error(`網羅されていない結果: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 依頼を Output の 1 行向けに短く書く。 */
function describe(request: HyIndexRequest): string {
  switch (request.tag) {
    case 'root':
      return `root ${request.root}`;
    case 'files':
      return `file ${request.files.join(', ')}`;
    case 'stdin':
      return `編集中 ${request.path}`;
    default: {
      const unreachable: never = request;
      throw new Error(`網羅されていない依頼: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 索引側が file ごとに積んだ errors(読めない・括弧の崩れ)を Output に出す。 */
function logFileErrors(log: HyLog, files: readonly HyFileIndex[]): void {
  for (const file of files) {
    for (const error of file.errors) {
      log.appendLine(`[hy-index] ${file.path}: ${error}`);
    }
  }
}
