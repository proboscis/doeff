// `doeff-indexer hy-index` を呼ぶ口(effect)と、それを子 process で実行する handler。
// 子 process は同時に 1 つだけ走らせ、失敗は理由の文字列にして返す(例外を外へ漏らさない)。

import * as cp from 'child_process';
import { parseHyIndexJson, type HyIndexDocument, type RejectedFile } from './contract';

/** 索引の依頼 — root 全体・指定の file・編集中の内容(stdin)。 */
export type HyIndexRequest =
  | { readonly tag: 'root'; readonly root: string }
  | { readonly tag: 'files'; readonly root: string; readonly files: readonly string[] }
  | { readonly tag: 'stdin'; readonly root: string; readonly path: string; readonly text: string };

/** 索引の結果 — 読めた・失敗した(理由)・binary が hy-index を知らない。 */
export type HyIndexOutcome =
  | { readonly tag: 'ok'; readonly document: HyIndexDocument; readonly rejected: readonly RejectedFile[] }
  | { readonly tag: 'failed'; readonly reason: string }
  | { readonly tag: 'unsupported'; readonly binary: string; readonly reason: string };

/** 索引を取る口。業務側(indexService)はこの口だけを使う。 */
export interface HyIndexer {
  index(request: HyIndexRequest): Promise<HyIndexOutcome>;
}

/** 依頼を hy-index の引数に写す(契約の「呼び出し」節のとおり)。 */
export function hyIndexArgs(request: HyIndexRequest): string[] {
  switch (request.tag) {
    case 'root':
      return ['hy-index', '--root', request.root];
    case 'files':
      return ['hy-index', '--root', request.root, '--file', ...request.files];
    case 'stdin':
      return ['hy-index', '--root', request.root, '--stdin', '--path', request.path];
    default: {
      const unreachable: never = request;
      throw new Error(`網羅されていない依頼: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 子 process 1 回の結果。 */
type ProcessResult =
  | { readonly tag: 'exited'; readonly code: number | null; readonly stdout: string; readonly stderr: string }
  | { readonly tag: 'error'; readonly reason: string };

/** 子 process を 1 回走らせ、stdout・stderr・終了コードを集める(時間切れは殺して理由にする)。 */
function runProcess(
  binary: string,
  args: readonly string[],
  cwd: string,
  stdin: string | undefined,
  timeoutMs: number
): Promise<ProcessResult> {
  return new Promise((resolve) => {
    let settled = false;
    // 結果を 1 度だけ返す(error と close の両方が来ても 2 度目は捨てる)
    const finish = (result: ProcessResult): void => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        resolve(result);
      }
    };
    const child = cp.spawn(binary, [...args], { cwd, stdio: ['pipe', 'pipe', 'pipe'] });
    const stdout: Buffer[] = [];
    const stderr: Buffer[] = [];
    const timer = setTimeout(() => {
      child.kill();
      finish({ tag: 'error', reason: `${timeoutMs}ms を過ぎたので止めた` });
    }, timeoutMs);
    child.stdout.on('data', (chunk: Buffer) => stdout.push(chunk));
    child.stderr.on('data', (chunk: Buffer) => stderr.push(chunk));
    child.on('error', (error) => finish({ tag: 'error', reason: `起動できない: ${error.message}` }));
    child.on('close', (code) =>
      finish({
        tag: 'exited',
        code,
        stdout: Buffer.concat(stdout).toString('utf8'),
        stderr: Buffer.concat(stderr).toString('utf8')
      })
    );
    child.stdin.on('error', () => undefined); // 子が stdin を読まずに終わった時の EPIPE は close 側で扱う
    child.stdin.end(stdin ?? '');
  });
}

/** binary の探し方(拡張の既存の doeff-indexer の探索)を受け取る口。 */
export type LocateIndexer = () => Promise<string>;

/** 子 process で hy-index を走らせる handler。同時に 1 つ・binary の対応の確認は 1 度だけ。 */
export class ChildProcessHyIndexer implements HyIndexer {
  private tail: Promise<unknown> = Promise.resolve();
  private binary: Promise<{ readonly tag: 'found'; readonly path: string } | { readonly tag: 'missing'; readonly reason: string }> | undefined;
  private support = new Map<string, Promise<boolean>>();

  constructor(
    private readonly locate: LocateIndexer,
    private readonly timeoutMs: number
  ) {}

  /** 依頼を列に積み、前の子 process が終わってから走らせる。 */
  index(request: HyIndexRequest): Promise<HyIndexOutcome> {
    const run = this.tail.then(() => this.runNow(request));
    this.tail = run.catch(() => undefined);
    return run;
  }

  /** binary を 1 度だけ探す(見つからない時の通知が繰り返されないように結果を持つ)。 */
  private findBinary() {
    if (this.binary === undefined) {
      this.binary = this.locate().then(
        (found) => ({ tag: 'found' as const, path: found }),
        (error: unknown) => ({ tag: 'missing' as const, reason: `doeff-indexer が見つからない: ${String(error)}` })
      );
    }
    return this.binary;
  }

  /** binary が hy-index を知っているかを `hy-index --help` の終了コードで 1 度だけ確かめる。 */
  private supportsHyIndex(binary: string, cwd: string): Promise<boolean> {
    let known = this.support.get(binary);
    if (known === undefined) {
      known = runProcess(binary, ['hy-index', '--help'], cwd, undefined, this.timeoutMs).then(
        (result) => result.tag === 'exited' && result.code === 0
      );
      this.support.set(binary, known);
    }
    return known;
  }

  /** 1 つの依頼を実際に走らせ、stdout を契約の型に読む。 */
  private async runNow(request: HyIndexRequest): Promise<HyIndexOutcome> {
    const binary = await this.findBinary();
    if (binary.tag === 'missing') {
      return { tag: 'failed', reason: binary.reason };
    }
    if (!(await this.supportsHyIndex(binary.path, request.root))) {
      return {
        tag: 'unsupported',
        binary: binary.path,
        reason: `${binary.path} は subcommand hy-index を知らない(古い doeff-indexer)`
      };
    }
    const stdin = request.tag === 'stdin' ? request.text : undefined;
    const result = await runProcess(binary.path, hyIndexArgs(request), request.root, stdin, this.timeoutMs);
    if (result.tag === 'error') {
      return { tag: 'failed', reason: result.reason };
    }
    if (result.code !== 0) {
      const detail = result.stderr.trim().split('\n').slice(0, 5).join(' / ');
      return { tag: 'failed', reason: `終了コード ${String(result.code)}: ${detail}` };
    }
    const parsed = parseHyIndexJson(result.stdout);
    if (parsed.tag === 'rejected') {
      return { tag: 'failed', reason: `出力を捨てた: ${parsed.reason}` };
    }
    return { tag: 'ok', document: parsed.document, rejected: parsed.rejected };
  }
}
