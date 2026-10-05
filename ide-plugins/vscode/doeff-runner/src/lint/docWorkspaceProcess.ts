// workspace 全体の検査を逐次読み取る外部 I/O。キャンセル時は自分の子プロセスだけを停止する。
import * as cp from 'child_process';
import * as readline from 'readline';
import { docBinaryCandidates } from './docRunner';
import { readWorkspaceEvent, type DocSnapshot, type WorkspaceEvent } from './docWorkspaceContract';

export interface WorkspaceRequest {
  readonly root: string;
  readonly documents: readonly { readonly path: string; readonly text: string }[];
}
export interface WorkspaceRunner {
  run(request: WorkspaceRequest, observe: (event: WorkspaceEvent) => void, signal: AbortSignal): Promise<void>;
}

export class RustWorkspaceRunner implements WorkspaceRunner {
  constructor(private readonly binary: (root: string) => string) {}
  async run(request: WorkspaceRequest, observe: (event: WorkspaceEvent) => void, signal: AbortSignal): Promise<void> {
    for (const binary of docBinaryCandidates(this.binary(request.root))) {
      const found = await this.start(binary, request, observe, signal);
      if (found) {
        return;
      }
    }
    throw new Error('doc-linter が見つかりません。docLint.binary を確認してください');
  }
  private start(
    binary: string,
    request: WorkspaceRequest,
    observe: (event: WorkspaceEvent) => void,
    signal: AbortSignal,
  ): Promise<boolean> {
    return new Promise((resolve, reject) => {
      if (signal.aborted) {
        resolve(true);
        return;
      }
      const child = cp.spawn(binary, ['workspace', '--root', request.root, '--stdin', '--workers', '4'], {
        cwd: request.root,
        stdio: ['pipe', 'pipe', 'pipe'],
      });
      let snapshot: DocSnapshot | undefined;
      let done = false;
      let error: Error | undefined;
      let missing = false;
      let stderr = '';
      const abort = (): void => {
        child.kill();
      };
      signal.addEventListener('abort', abort, { once: true });
      const watchdog = setInterval(() => {
        if (Date.now() - lastOutput > 120_000) {
          error = new Error('検査から120秒間応答がありません');
          child.kill();
        }
      }, 10_000);
      let lastOutput = Date.now();
      const lines = readline.createInterface({ input: child.stdout });
      lines.on('line', (line) => {
        if (signal.aborted || error !== undefined) {
          return;
        }
        lastOutput = Date.now();
        try {
          const event = readWorkspaceEvent(line, request.root, snapshot);
          if (event.event === 'index') {
            if (snapshot !== undefined) {
              throw new Error('索引が重複しました');
            }
            snapshot = event.snapshot;
          }
          if (event.event === 'done') {
            done = true;
          }
          observe(event);
        } catch (e) {
          error = e instanceof Error ? e : new Error(String(e));
          child.kill();
        }
      });
      child.stderr.setEncoding('utf8');
      child.stderr.on('data', (part: string) => {
        stderr = (stderr + part).slice(-8192);
      });
      child.on('error', (e: NodeJS.ErrnoException) => {
        missing = e.code === 'ENOENT';
        error = e;
      });
      child.on('close', (code) => {
        clearInterval(watchdog);
        signal.removeEventListener('abort', abort);
        lines.close();
        if (signal.aborted) {
          resolve(true);
        } else if (missing) {
          resolve(false);
        } else if (error !== undefined) {
          reject(error);
        } else if (!done || (code !== 0 && code !== 1 && code !== 2)) {
          reject(new Error(`検査が未完了です: ${stderr || `終了コード ${code}`}`));
        } else {
          resolve(true);
        }
      });
      child.stdin.on('error', () => undefined); // EPIPE は close の未完了判定へ合流する。
      child.stdin.end(JSON.stringify({ documents: request.documents }));
    });
  }
}
