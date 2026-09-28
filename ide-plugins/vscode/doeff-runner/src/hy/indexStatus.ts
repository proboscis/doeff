// Hy の索引の状態 — 索引が無い時に「なぜ無いのか」と「どうすれば作れるか」を言うための閉じた型と、
// その説明の文を作る純粋な関数。状態を決めるのは indexService、描くのはパネル(ここは vscode に依らない)。

/** 索引の状態。 */
export type HyIndexStatus =
  /** 起動直後 — まだどの folder も調べていない */
  | { readonly tag: 'waiting' }
  /** 開いている folder に Hy の file が 1 つも無い */
  | { readonly tag: 'no-hy-files' }
  /** root 全体の索引を作っている最中 */
  | { readonly tag: 'indexing'; readonly root: string }
  /** 索引を作れた */
  | { readonly tag: 'ready'; readonly files: number }
  /** 索引を作る道具 doeff-indexer が見つからない */
  | { readonly tag: 'tool-missing'; readonly reason: string }
  /** 見つかった doeff-indexer が hy-index を知らない(古い版) */
  | { readonly tag: 'unsupported'; readonly binary: string; readonly reason: string }
  /** doeff-indexer は在るが、索引を作れなかった・出力を読めなかった */
  | { readonly tag: 'failed'; readonly root: string; readonly reason: string };

/** 状態の説明の 1 行 — 押すと命令を撃つ行もある。 */
export interface StatusLine {
  readonly label: string;
  readonly tooltip?: string;
  /** 押した時に撃つ命令の id */
  readonly command?: string;
}

/** 索引を作り直す命令(道具の探し直しから始める)。 */
export const REINDEX_COMMAND = 'doeff-runner.hy.reindex';
/** Output を開く命令。 */
export const SHOW_OUTPUT_COMMAND = 'doeff-runner.hy.showOutput';

const RETRY_LINE: StatusLine = { label: '→ ここを押すと索引を作り直します', command: REINDEX_COMMAND };
const OUTPUT_LINE: StatusLine = { label: '→ 詳しい理由は Output の doeff-runner に出ています', command: SHOW_OUTPUT_COMMAND };

/** 定義の一覧が空の時に出す行 — なぜ無いのかと、どうすれば作れるか。 */
export function emptyIndexLines(status: HyIndexStatus): StatusLine[] {
  switch (status.tag) {
    case 'waiting':
      return [{ label: 'Hy の索引をまだ作り始めていません(起動の途中です)' }];
    case 'no-hy-files':
      return [
        { label: '開いている folder に Hy の file(.hy・.hyk・.hyp)がありません' },
        { label: 'Hy の file を持つ folder を開くと、索引を自動で作ります' }
      ];
    case 'indexing':
      return [{ label: `Hy の索引を作っています… (${status.root})` }];
    case 'ready':
      return [{ label: `索引を作りましたが、定義が 0 件でした(${status.files} file)` }, OUTPUT_LINE];
    case 'tool-missing':
      return [
        { label: '索引を作る道具 doeff-indexer が見つかりません', tooltip: status.reason },
        {
          label: '拡張に同梱の doeff-indexer が無い版です。同梱の版の拡張を入れ直すか、環境変数 DOEFF_INDEXER_PATH で道具の置き場を指してください',
          tooltip: status.reason
        },
        RETRY_LINE,
        OUTPUT_LINE
      ];
    case 'unsupported':
      return [
        { label: `doeff-indexer が古く、Hy の索引(hy-index)を作れません: ${status.binary}`, tooltip: status.reason },
        { label: 'doeff-indexer を新しい版にしてください(拡張に同梱の版を入れ直すのが早道です)' },
        RETRY_LINE
      ];
    case 'failed':
      return [
        { label: `Hy の索引を作れませんでした (${status.root})`, tooltip: status.reason },
        { label: `理由: ${firstLine(status.reason)}`, tooltip: status.reason },
        RETRY_LINE,
        OUTPUT_LINE
      ];
    default: {
      const unreachable: never = status;
      throw new Error(`網羅されていない状態: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 長い理由を 1 行に詰める(全文は tooltip に置く)。 */
function firstLine(text: string): string {
  const line = text.split('\n')[0] ?? '';
  return line.length > 160 ? `${line.slice(0, 157)}…` : line;
}

/** 索引の依頼の結果から、次の状態を決める(root 全体の依頼の結果だけが ready と failed を決める)。 */
export type IndexResult =
  | { readonly tag: 'ok'; readonly files: number }
  | { readonly tag: 'missing'; readonly reason: string }
  | { readonly tag: 'unsupported'; readonly binary: string; readonly reason: string }
  | { readonly tag: 'failed'; readonly reason: string };

/**
 * 依頼 1 つの結果で状態を進める。
 * - 道具が無い・古いは、どの依頼で分かっても状態にする(以後の依頼も同じ理由で落ちる)。
 * - root 全体の依頼の成功・失敗は ready・failed にする。
 * - 1 file の依頼の失敗は状態を変えない(Output に出すだけ — 1 file の括弧の崩れで一覧を消さない)。
 */
export function nextStatus(
  current: HyIndexStatus,
  request: { readonly tag: 'root' | 'files' | 'stdin'; readonly root: string },
  result: IndexResult
): HyIndexStatus {
  switch (result.tag) {
    case 'missing':
      return { tag: 'tool-missing', reason: result.reason };
    case 'unsupported':
      return { tag: 'unsupported', binary: result.binary, reason: result.reason };
    case 'ok':
      return request.tag === 'root' ? { tag: 'ready', files: result.files } : current;
    case 'failed':
      return request.tag === 'root' ? { tag: 'failed', root: request.root, reason: result.reason } : current;
    default: {
      const unreachable: never = result;
      throw new Error(`網羅されていない結果: ${JSON.stringify(unreachable)}`);
    }
  }
}
