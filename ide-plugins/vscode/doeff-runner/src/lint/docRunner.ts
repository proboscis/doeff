// 文書の検査要求を Rust CLI に渡す外部 I/O の境界。シェルを介さず本文を stdin に渡す。
import * as os from 'os';
import * as path from 'path';
import { runProcess } from '../hy/childProcess';
import type { LintViolation } from './contract';
import { parseDocReport } from './docContract';

export interface DocRequest {
  readonly root: string;
  readonly path: string;
  readonly text: string;
  readonly version: number;
}
export type DocOutcome =
  | { readonly tag: 'ok'; readonly violations: readonly LintViolation[] }
  | { readonly tag: 'failed'; readonly reason: string };
export interface DocumentLinter {
  lint(request: DocRequest): Promise<DocOutcome>;
}

/** GUI 起動時にも既定のインストール場所から実行できる。設定した絶対パスはそのまま使う。 */
export function docBinaryCandidates(binary: string): string[] {
  return binary.includes('/') || binary.includes('\\')
    ? [binary]
    : [
        path.join(os.homedir(), '.local', 'bin', binary),
        binary,
        path.join(os.homedir(), '.cargo', 'bin', binary),
        path.join('/opt/homebrew/bin', binary),
      ];
}

export class RustDocumentLinter implements DocumentLinter {
  constructor(private readonly binaryFor: (root: string) => string) {}
  async lint(request: DocRequest): Promise<DocOutcome> {
    for (const binary of docBinaryCandidates(this.binaryFor(request.root))) {
      const result = await runProcess(
        binary,
        ['lint', '--stdin', '--path', request.path, '--json', '--workers', '4'],
        request.root,
        request.text,
        120_000,
      );
      if (result.tag === 'error') {
        if (result.commandMissing) {
          continue;
        }
        return { tag: 'failed', reason: result.reason };
      }
      // 2 は「要確認・未測定」を含む正常な JSON の場合もあるので、本文を必ず読む。
      if (result.code !== 0 && result.code !== 1 && result.code !== 2) {
        return { tag: 'failed', reason: `doc-linter が終了しました: ${result.code}` };
      }
      try {
        return { tag: 'ok', violations: parseDocReport(result.stdout, request.path, request.text) };
      } catch (error) {
        return {
          tag: 'failed',
          reason: `${error instanceof Error ? error.message : String(error)}${result.stderr.trim() === '' ? '' : `: ${result.stderr.trim()}`}`,
        };
      }
    }
    return { tag: 'failed', reason: 'doc-linter が見つかりません。設定 doeff-runner.docLint.binary に実行ファイルを指定してください。' };
  }
}
