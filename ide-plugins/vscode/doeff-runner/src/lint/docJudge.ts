// 編集中の文書を検査する順序を管理する。同時実行は1プロセス、待機中はファイルごとに最新版だけを保持する。
import { docFailure } from './docContract';
import type { DocRequest, DocumentLinter } from './docRunner';
import type { LintStore } from './store';

export class DocumentJudge {
  private readonly pending = new Map<string, DocRequest>();
  private readonly latest = new Map<string, DocRequest>();
  private running = false;
  private disposed = false;
  constructor(
    private readonly linter: DocumentLinter,
    private readonly store: LintStore,
    private readonly log: (message: string) => void,
  ) {}

  /** 同じ版・本文への重複要求は省き、別の要求なら前の返答を無効にする。 */
  submit(request: DocRequest, force = false): void {
    if (this.disposed) {
      return;
    }
    const before = this.latest.get(request.path);
    if (!force && before?.version === request.version && before.text === request.text) {
      return;
    }
    this.latest.set(request.path, request);
    this.pending.set(request.path, request);
    void this.drain();
  }
  /** 編集・close・無効化で古い位置の診断を消す。既に実行中の古い返答も採用しない。 */
  invalidate(filePath: string): void {
    this.latest.delete(filePath);
    this.pending.delete(filePath);
    this.store.clearDocumentFindings(filePath);
  }
  /** フォルダーが workspace から外れた後の返答も採用しない。 */
  invalidateRoot(root: string): void {
    for (const [file, request] of this.latest) {
      if (request.root === root) {
        this.invalidate(file);
      }
    }
    this.store.clearDocumentRoot(root);
  }
  dispose(): void {
    this.disposed = true;
    for (const file of this.latest.keys()) {
      this.store.clearDocumentFindings(file);
    }
    this.latest.clear();
    this.pending.clear();
  }
  private async drain(): Promise<void> {
    if (this.running || this.disposed) {
      return;
    }
    this.running = true;
    try {
      for (const [file, request] of this.pending) {
        this.pending.delete(file);
        const result = await this.linter
          .lint(request)
          .catch((error: unknown) => ({ tag: 'failed' as const, reason: error instanceof Error ? error.message : String(error) }));
        if (this.disposed || this.latest.get(file) !== request) {
          continue;
        }
        if (result.tag === 'failed') {
          this.log(`[doc-linter] ${file}: ${result.reason}`);
        }
        this.store.replaceDocumentFindings(request.root, file, result.tag === 'ok' ? result.violations : [docFailure(file, result.reason)]);
      }
    } finally {
      this.running = false;
    }
  }
}
