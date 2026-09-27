// 子 process を 1 回走らせる境界の module。hy-index と uv への問い合わせの両方がここだけを通る。

import * as cp from 'child_process';

/** 子 process 1 回の結果。 */
export type ProcessResult =
  | { readonly tag: 'exited'; readonly code: number | null; readonly stdout: string; readonly stderr: string }
  | { readonly tag: 'error'; readonly reason: string; readonly commandMissing: boolean };

/** 子 process を 1 回走らせ、stdout・stderr・終了コードを集める(時間切れは殺して理由にする)。 */
export function runProcess(
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
      finish({ tag: 'error', reason: `${timeoutMs}ms を過ぎたので止めた`, commandMissing: false });
    }, timeoutMs);
    child.stdout.on('data', (chunk: Buffer) => stdout.push(chunk));
    child.stderr.on('data', (chunk: Buffer) => stderr.push(chunk));
    child.on('error', (error: NodeJS.ErrnoException) =>
      finish({ tag: 'error', reason: `起動できない: ${error.message}`, commandMissing: error.code === 'ENOENT' })
    );
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
