// `doeff-indexer hy-index` を呼ぶ口(effect)と、それを子 process で実行する handler。
// 子 process は同時に 1 つだけ走らせ、失敗は理由の文字列にして返す(例外を外へ漏らさない)。

import { runProcess } from './childProcess';
import { parseHyIndexJson, type HyIndexDocument, type RejectedFile } from './contract';

/** 索引の依頼 — root 全体・指定の file・編集中の内容(stdin)。 */
export type HyIndexRequest =
  | { readonly tag: 'root'; readonly root: string }
  | { readonly tag: 'files'; readonly root: string; readonly files: readonly string[] }
  | { readonly tag: 'stdin'; readonly root: string; readonly path: string; readonly text: string };

/** 索引の結果 — 読めた・失敗した(理由)・binary が見つからない・binary が hy-index を知らない。 */
export type HyIndexOutcome =
  | { readonly tag: 'ok'; readonly document: HyIndexDocument; readonly rejected: readonly RejectedFile[] }
  | { readonly tag: 'failed'; readonly reason: string }
  | { readonly tag: 'missing'; readonly reason: string }
  | { readonly tag: 'unsupported'; readonly binary: string; readonly reason: string };

/** 索引を取る口。業務側(indexService)はこの口だけを使う。 */
export interface HyIndexer {
  index(request: HyIndexRequest): Promise<HyIndexOutcome>;
}

/** 依頼を hy-index の引数に写す(契約の「呼び出し」節のとおり。目録の追加の file があれば添える)。 */
export function hyIndexArgs(request: HyIndexRequest, rawCatalogExtra: string | undefined): string[] {
  const extra = rawCatalogExtra === undefined ? [] : ['--raw-catalog-extra', rawCatalogExtra];
  switch (request.tag) {
    case 'root':
      return ['hy-index', '--root', request.root, ...extra];
    case 'files':
      return ['hy-index', '--root', request.root, ...extra, '--file', ...request.files];
    case 'stdin':
      return ['hy-index', '--root', request.root, ...extra, '--stdin', '--path', request.path];
    default: {
      const unreachable: never = request;
      throw new Error(`網羅されていない依頼: ${JSON.stringify(unreachable)}`);
    }
  }
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
    private readonly timeoutMs: number,
    /** 生の副作用の目録の追加(利用者の設定)を書いた JSON の file(無ければ undefined) */
    private readonly rawCatalogExtra: () => string | undefined
  ) {}

  /** 依頼を列に積み、前の子 process が終わってから走らせる。 */
  index(request: HyIndexRequest): Promise<HyIndexOutcome> {
    const run = this.tail.then(() => this.runNow(request));
    this.tail = run.catch(() => undefined);
    return run;
  }

  /** 探した binary と対応の確認を忘れる(次の依頼で探し直す — 道具を入れた後の作り直しの口)。 */
  forget(): void {
    this.binary = undefined;
    this.support.clear();
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
      return { tag: 'missing', reason: binary.reason };
    }
    if (!(await this.supportsHyIndex(binary.path, request.root))) {
      return {
        tag: 'unsupported',
        binary: binary.path,
        reason: `${binary.path} は subcommand hy-index を知らない(古い doeff-indexer)`
      };
    }
    const stdin = request.tag === 'stdin' ? request.text : undefined;
    const result = await runProcess(binary.path, hyIndexArgs(request, this.rawCatalogExtra()), request.root, stdin, this.timeoutMs);
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
