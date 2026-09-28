// linter を呼ぶ口(effect)と、それを子 process で実行する handler。子 process は同時に 1 つ・時間切れつき。
// 終了コード 0(違反なし)と 1(違反あり)は正常、2 は引数の誤りか linter 自身の失敗(契約のとおり)。

import * as os from 'os';
import * as path from 'path';
import { runProcess } from '../hy/childProcess';
import { parseLintJson, type LintReport } from './contract';

/** linter への依頼 — repo 全体か、保存前の内容(stdin)の 1 file か、Jev の判定(SemanticRequest)。 */
export type LintRequest =
  | { readonly tag: 'root'; readonly root: string }
  | { readonly tag: 'stdin'; readonly root: string; readonly path: string; readonly text: string; readonly version: number }
  | SemanticRequest;

/**
 * Jev の判定の依頼。どちらも問うた時の document の版(version)を持ち、答えが返った時に版が進んでいれば古い答えとして捨てる。
 * - semantic: 保存した file の定義を Jev に問う(doeff-linter の --semantic・disk の内容を読む)
 * - semantic-change: 編集中に打つのが止まった時の中身(stdin)のうち、中身の変わった定義だけを Jev に問う(--semantic --semantic-changed。
 *   書きかけで読めない定義は linter が問わない)
 */
export type SemanticRequest =
  | { readonly tag: 'semantic'; readonly root: string; readonly path: string; readonly version: number }
  | { readonly tag: 'semantic-change'; readonly root: string; readonly path: string; readonly text: string; readonly version: number };

/** linter の結果 — 読めた・止めてある(設定で無効)・失敗した(理由)。 */
export type LintOutcome =
  | { readonly tag: 'ok'; readonly report: LintReport }
  | { readonly tag: 'disabled' }
  | { readonly tag: 'failed'; readonly reason: string };

/** linter を呼ぶ口。業務側(service)はこの口だけを使う。 */
export interface Linter {
  lint(request: LintRequest): Promise<LintOutcome>;
}

/** 設定の命令の文字列を引数の列に分ける(空白で区切り、'…' と "…" の中の空白は区切らない)。空なら []。 */
export function splitCommand(command: string): string[] {
  const parts: string[] = [];
  let current = '';
  let quote: '"' | "'" | undefined;
  let started = false;
  for (const ch of command) {
    if (quote !== undefined) {
      if (ch === quote) {
        quote = undefined;
      } else {
        current += ch;
      }
    } else if (ch === '"' || ch === "'") {
      quote = ch;
      started = true;
    } else if (/\s/.test(ch)) {
      if (started) {
        parts.push(current);
        current = '';
        started = false;
      }
    } else {
      current += ch;
      started = true;
    }
  }
  if (started) {
    parts.push(current);
  }
  return parts;
}

/** 依頼を linter の引数に写す(契約の「呼び出し」節のとおり — 全体は引数なし、1 file は --stdin --path、Jev は --semantic <file>、
 * 編集中の Jev は --stdin --path <file> --semantic --semantic-changed)。 */
export function lintArgs(base: readonly string[], request: LintRequest): string[] {
  switch (request.tag) {
    case 'root':
      return [...base];
    case 'stdin':
      return [...base, '--stdin', '--path', request.path];
    case 'semantic':
      return [...base, '--semantic', request.path];
    case 'semantic-change':
      return [...base, '--stdin', '--path', request.path, '--semantic', '--semantic-changed'];
    default: {
      const unreachable: never = request;
      throw new Error(`網羅されていない依頼: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 開発版の linter の置き場(home からの相対)。本線の doeff に自動で追いつく置き場で、書くのは dotfiles の追随の係だけ
 * (agora-redesign #848)。書き込み直後の hook と同じく PATH より先に見る — PATH の `~/.cargo/bin` の手組みの版は
 * 誰も更新せず、設定の新しい鍵で終了コード 2 になる。 */
const DEV_LINTER_DIR = ['.local', 'share', 'doeff-linter-dev'];

/** 命令の頭(binary)の候補 — 開発版の置き場 → PATH → 既知の場所(VS Code を GUI から起動すると PATH が短いことがある)。
 * path を名指した頭はそのまま使う。 */
export function binaryCandidates(head: string, home: string = os.homedir()): string[] {
  if (head.includes('/') || head.includes('\\')) {
    return [head];
  }
  return [
    path.join(home, ...DEV_LINTER_DIR, head),
    head,
    path.join(home, '.cargo', 'bin', head),
    path.join(home, '.local', 'bin', head),
    path.join('/opt/homebrew/bin', head),
    path.join('/usr/local/bin', head)
  ];
}

/** 子 process で linter を走らせる handler。同時に 1 つだけ。 */
export class ChildProcessLinter implements Linter {
  private tail: Promise<unknown> = Promise.resolve();

  constructor(
    /** workspace の root ごとの命令(設定 doeff-runner.hy.lintCommand。空なら無効) */
    private readonly commandFor: (root: string) => string,
    private readonly timeoutMs: number
  ) {}

  /** 依頼を列に積み、前の子 process が終わってから走らせる。 */
  lint(request: LintRequest): Promise<LintOutcome> {
    const run = this.tail.then(() => this.runNow(request));
    this.tail = run.catch(() => undefined);
    return run;
  }

  /** 1 つの依頼を実際に走らせ、stdout を契約の型に読む。 */
  private async runNow(request: LintRequest): Promise<LintOutcome> {
    const command = splitCommand(this.commandFor(request.root));
    const head = command[0];
    if (head === undefined) {
      return { tag: 'disabled' };
    }
    const args = lintArgs(command.slice(1), request);
    const stdin = request.tag === 'stdin' || request.tag === 'semantic-change' ? request.text : undefined;
    for (const binary of binaryCandidates(head)) {
      const result = await runProcess(binary, args, request.root, stdin, this.timeoutMs);
      if (result.tag === 'error') {
        if (result.commandMissing) {
          continue;
        }
        return { tag: 'failed', reason: `${binary}: ${result.reason}` };
      }
      if (result.code !== 0 && result.code !== 1) {
        const detail = result.stderr.trim().split('\n').slice(-5).join(' / ');
        return { tag: 'failed', reason: `終了コード ${String(result.code)}: ${detail}` };
      }
      const parsed = parseLintJson(result.stdout);
      return parsed.tag === 'ok'
        ? { tag: 'ok', report: parsed.report }
        : { tag: 'failed', reason: `出力を捨てた: ${parsed.reason}` };
    }
    return { tag: 'failed', reason: `${head} が見つからない(試した場所: ${binaryCandidates(head).join(', ')})` };
  }
}
