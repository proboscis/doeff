// workspace ごとの最新版だけを検査し、全ファイルの結果と進捗を同じ store へ投影する。
import type { LintViolation } from './contract';
import type { LintStore } from './store';
import type { WorkspaceRequest, WorkspaceRunner } from './docWorkspaceProcess';
import { EMPTY_COUNTS, EMPTY_INDEX, type WorkspaceEvent, type Counts, type DocProgress } from './docWorkspaceContract';

export class WorkspaceJudge {
  private readonly pending = new Map<string, WorkspaceRequest>();
  private active: { readonly request: WorkspaceRequest; readonly abort: AbortController } | undefined;
  private disposed = false;
  constructor(
    private readonly runner: WorkspaceRunner,
    private readonly store: LintStore,
  ) {}
  submit(request: WorkspaceRequest): void {
    if (this.disposed) {
      return;
    }
    const previous = this.pending.get(request.root);
    if (previous !== undefined && request.kind === 'changed') {
      const documents = new Map(previous.documents.map((d) => [d.path, d]));
      for (const file of request.paths) { documents.delete(file); }
      for (const doc of request.documents) { documents.set(doc.path, doc); }
      request = previous.kind === 'initial'
        ? { ...previous, documents: [...documents.values()] }
        : { ...request, paths: [...new Set([...previous.paths, ...request.paths])], documents: [...documents.values()] };
    }
    this.pending.set(request.root, request);
    if (request.kind === 'initial' && this.active?.request.root === request.root) {
      this.active.abort.abort();
    }
    const current = this.store.docWorkspaces().get(request.root);
    this.store.setDocumentWorkspace(request.root, current?.index ?? EMPTY_INDEX, {
      ...(current?.progress ?? EMPTY_COUNTS), phase: current?.progress.phase === 'running' ? 'running' : 'queued', pendingChanges: true,
    });
    void this.drain();
  }
  remove(root: string): void {
    this.pending.delete(root);
    if (this.active?.request.root === root) {
      this.active.abort.abort();
    }
    this.store.clearDocumentRoot(root);
    this.store.removeDocumentWorkspace(root);
  }
  dispose(): void {
    this.disposed = true;
    this.active?.abort.abort();
    this.pending.clear();
  }
  private async drain(): Promise<void> {
    if (this.active !== undefined || this.disposed) {
      return;
    }
    for (const [root, request] of this.pending) {
      if (this.disposed) {
        break;
      }
      this.pending.delete(root);
      const abort = new AbortController();
      this.active = { request, abort };
      if (request.kind === 'initial') { this.store.clearDocumentRoot(root); }
      const results = new Map<string, Map<string, readonly LintViolation[]>>();
      let counts: Counts = EMPTY_COUNTS;
      let index = EMPTY_INDEX;
      const changedFiles = new Set<string>();
      let timer: NodeJS.Timeout | undefined;
      const flush = (progress: DocProgress): void => {
        if (timer !== undefined) { clearTimeout(timer); timer = undefined; }
        if (abort.signal.aborted || this.disposed) { return; }
        const findings = new Map<string, readonly LintViolation[]>();
        for (const file of changedFiles) {
          findings.set(file, [...(results.get(file)?.values() ?? [])].flat());
        }
        changedFiles.clear();
        this.store.updateDocumentWorkspace(root, index, { ...progress, incremental: request.kind === 'changed', pendingChanges: this.pending.has(root) }, findings);
      };
      const observe = (event: WorkspaceEvent): void => {
        if (abort.signal.aborted || this.disposed) {
          return;
        }
        if (event.event === 'index') {
          index = event.snapshot.index;
          counts = { ...EMPTY_COUNTS, files: event.snapshot.files.size, total: event.snapshot.total };
          const affected = new Set(event.snapshot.affected ?? event.snapshot.files.keys());
          for (const file of affected) { changedFiles.add(file); }
          for (const issue of event.snapshot.issues) {
            if (request.kind === 'changed' && !affected.has(issue.path)) { continue; }
            const rows = results.get(issue.path) ?? new Map();
            rows.set(`issue-${rows.size}`, [issue]);
            results.set(issue.path, rows);
            changedFiles.add(issue.path);
          }
        } else {
          if (event.event === 'done' && index === EMPTY_INDEX && request.kind === 'changed') {
            const current = this.store.docWorkspaces().get(root);
            if (current !== undefined) {
              this.store.setDocumentWorkspace(root, current.index, { ...current.progress, phase: 'complete', pendingChanges: this.pending.has(root) });
            }
            return;
          }
          counts = { ...counts, completed: event.completed, cacheHits: event.cacheHits, unmeasured: event.unmeasured };
          if (event.event === 'result') {
            const rows = results.get(event.path) ?? new Map();
            rows.set(`unit-${event.unit}`, event.violations);
            results.set(event.path, rows);
            changedFiles.add(event.path);
          }
        }
        if (event.event === 'index' || event.event === 'done') {
          flush({ ...counts, phase: event.event === 'done' ? 'complete' : 'running' });
        } else if (timer === undefined) {
          timer = setTimeout(() => flush({ ...counts, phase: 'running' }), 200);
        }
      };
      try {
        await this.runner.run(request, observe, abort.signal);
      } catch (e) {
        if (!abort.signal.aborted && !this.disposed) {
          flush({ ...counts, phase: 'failed', reason: e instanceof Error ? e.message : String(e) });
        }
      } finally {
        if (timer !== undefined) { clearTimeout(timer); }
        this.active = undefined;
      }
    }
  }
}
