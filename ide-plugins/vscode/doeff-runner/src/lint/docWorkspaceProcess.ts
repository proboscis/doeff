// Rust へ選択された範囲と差分を渡す I/O。本文と用語索引は workspace ごとに保持する。
import * as cp from 'child_process';
import * as fs from 'fs/promises';
import * as readline from 'readline';
import * as path from 'path';
import { docBinaryCandidates } from './docRunner';
import { EMPTY_INDEX, readWorkspaceEvent, type DocSnapshot, type WorkspaceEvent } from './docWorkspaceContract';

type Document = { readonly path: string; readonly text: string };
export type WorkspaceRequest = {
  readonly root: string;
  readonly documents: readonly Document[];
  readonly rulesChanged?: boolean;
} & ({ readonly kind: 'initial' } | { readonly kind: 'selected' | 'changed'; readonly paths: readonly string[] });
export interface WorkspaceRunner {
  run(request: WorkspaceRequest, observe: (event: WorkspaceEvent) => void, signal: AbortSignal): Promise<void>;
}
function wireIndex(snapshot: DocSnapshot): object {
  return { definitions: snapshot.index.definitions, references: snapshot.index.references, document_policies: snapshot.index.documentPolicies ?? {}, issues: snapshot.issues.map((v) => ({
    rule: v.rule, message: v.message, location: { path: v.path, ...v.range },
  })) };
}

/** 差分にも Git の除外を適用する。内容を読む前に、通知されたパスだけを照会する。 */
function ignoredPaths(root: string, files: readonly string[]): Promise<ReadonlySet<string>> {
  return new Promise((resolve, reject) => {
    const child = cp.spawn('git', ['check-ignore', '--no-index', '-z', '--stdin'], { cwd: root, stdio: ['pipe', 'pipe', 'pipe'] });
    let output = ''; let error = '';
    child.stdout.setEncoding('utf8'); child.stderr.setEncoding('utf8');
    child.stdout.on('data', (s: string) => { output += s; });
    child.stderr.on('data', (s: string) => { error += s; });
    child.on('error', reject);
    child.on('close', (code) => {
      if (code === 0 || code === 1) { resolve(new Set(output.split('\0').filter(Boolean))); }
      else if (code === 128 && error.includes('not a git repository')) { resolve(new Set()); }
      else { reject(new Error(`Git の除外設定を読めません: ${error}`)); }
    });
    child.stdin.on('error', () => undefined); // 起動失敗は error/close で報告する。
    child.stdin.end(files.join('\0') + '\0');
  });
}

export class RustWorkspaceRunner implements WorkspaceRunner {
  private readonly snapshots = new Map<string, DocSnapshot>();
  constructor(private readonly binary: (root: string) => string) {}
  forget(root: string): void { this.snapshots.delete(root); }
  async run(request: WorkspaceRequest, observe: (event: WorkspaceEvent) => void, signal: AbortSignal): Promise<void> {
    const previous = request.kind === 'selected'
      ? { files: new Map<string, string>(), index: EMPTY_INDEX, issues: [], total: 0 }
      : this.snapshots.get(request.root);
    if (request.kind === 'initial') {
      await this.execute(request.root, { documents: request.documents }, [], (event) => {
        if (event.event === 'index') { this.snapshots.set(request.root, event.snapshot); }
        observe(event);
      }, signal);
      return;
    }
    if (previous === undefined) { throw new Error('初回の索引がありません。文書検査の再実行が必要です'); }
    const documents: Document[] = [];
    const removed: string[] = [];
    const overlays = new Map(request.documents.map((d) => [d.path, d.text]));
    for (const file of request.paths) {
      const relative = path.relative(request.root, file);
      if (path.isAbsolute(relative) || relative === '..' || relative.startsWith(`..${path.sep}`)) { throw new Error('workspace 外の差分です'); }
    }
    const ignored = await ignoredPaths(request.root, request.paths);
    for (const file of request.paths) {
      if (ignored.has(file)) { if (previous.files.has(file)) { removed.push(file); } continue; }
      let text: string;
      try { text = overlays.get(file) ?? await fs.readFile(file, 'utf8'); }
      catch (error) {
        if (error instanceof Error && 'code' in error && error.code === 'ENOENT') {
          if (previous.files.has(file)) { removed.push(file); }
          continue;
        }
        throw error;
      }
      if (previous.files.get(file) !== text) { documents.push({ path: file, text }); }
    }
    if (signal.aborted) { return; }
    if (documents.length === 0 && removed.length === 0 && !request.rulesChanged) {
      if (request.kind === 'selected') {
        this.snapshots.set(request.root, previous);
        observe({ event: 'index', snapshot: previous });
      }
      // 同じ内容の保存や重複通知では Rust の起動も結果の再描画もしない。
      observe({ event: 'done', code: 0, completed: 0, cacheHits: 0, unmeasured: 0 });
      return;
    }
    let prepared: DocSnapshot | undefined;
    await this.execute(request.root, { documents, removed, index: wireIndex(previous) }, ['--incremental', '--index-only'], (event) => {
      if (event.event === 'index') { prepared = event.snapshot; }
    }, signal);
    if (signal.aborted) { return; }
    if (prepared === undefined || prepared.affected === undefined) { throw new Error('差分検査に対応した doc-linter が必要です'); }
    const files = new Map(previous.files);
    for (const doc of documents) { files.set(doc.path, doc.text); }
    for (const file of removed) { files.delete(file); }
    const affected = prepared.affected;
    const inputs = affected.flatMap((file) => {
      const text = files.get(file);
      return text === undefined ? [] : [{ path: file, text }];
    });
    await this.execute(request.root, { documents: inputs, removed, index: wireIndex(prepared) }, ['--incremental'], (event) => {
      if (event.event === 'index') {
        this.snapshots.set(request.root, { ...event.snapshot, files });
        observe({ ...event, snapshot: { ...event.snapshot, affected } });
      } else { observe(event); }
    }, signal);
  }
  private async execute(root: string, body: object, flags: readonly string[], observe: (event: WorkspaceEvent) => void, signal: AbortSignal): Promise<void> {
    for (const binary of docBinaryCandidates(this.binary(root))) {
      if (await this.start(binary, root, body, flags, observe, signal)) { return; }
    }
    throw new Error('doc-linter が見つかりません。docLint.binary を確認してください');
  }
  private start(binary: string, root: string, body: object, flags: readonly string[], observe: (event: WorkspaceEvent) => void, signal: AbortSignal): Promise<boolean> {
    return new Promise((resolve, reject) => {
      if (signal.aborted) { resolve(true); return; }
      const child = cp.spawn(binary, ['workspace', '--root', root, '--stdin', '--workers', '4', ...flags], {
        cwd: root, stdio: ['pipe', 'pipe', 'pipe'],
      });
      let snapshot: DocSnapshot | undefined;
      let done = false;
      let error: Error | undefined;
      let missing = false;
      let stderr = '';
      const abort = (): void => { child.kill(); };
      signal.addEventListener('abort', abort, { once: true });
      const watchdog = setInterval(() => {
        if (Date.now() - lastOutput > 120_000) { error = new Error('検査から120秒間応答がありません'); child.kill(); }
      }, 10_000);
      let lastOutput = Date.now();
      const lines = readline.createInterface({ input: child.stdout });
      lines.on('line', (line) => {
        if (signal.aborted || error !== undefined) { return; }
        lastOutput = Date.now();
        try {
          const event = readWorkspaceEvent(line, root, snapshot);
          if (event.event === 'index') {
            if (snapshot !== undefined) { throw new Error('索引が重複しました'); }
            snapshot = event.snapshot;
          }
          if (event.event === 'done') { done = true; }
          observe(event);
        } catch (e) { error = e instanceof Error ? e : new Error(String(e)); child.kill(); }
      });
      child.stderr.setEncoding('utf8');
      child.stderr.on('data', (part: string) => { stderr = (stderr + part).slice(-8192); });
      child.on('error', (e: NodeJS.ErrnoException) => { missing = e.code === 'ENOENT'; error = e; });
      child.on('close', (code) => {
        clearInterval(watchdog); signal.removeEventListener('abort', abort); lines.close();
        if (signal.aborted) { resolve(true); }
        else if (missing) { resolve(false); }
        else if (error !== undefined) { reject(error); }
        else if ((!done && !(flags.includes('--index-only') && snapshot !== undefined)) || (code !== 0 && code !== 1 && code !== 2)) {
          reject(new Error(`検査が未完了です: ${stderr || `終了コード ${code}`}`));
        } else { resolve(true); }
      });
      child.stdin.on('error', () => undefined); // EPIPE は close の未完了判定へ合流する。
      child.stdin.end(JSON.stringify(body));
    });
  }
}
