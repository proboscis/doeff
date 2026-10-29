// doeff-agentic の CLI(`pip install doeff-agentic` が入れる Python の実行 file doeff-agentic)を呼ぶ口。
// workflow の一覧は `doeff-agentic ps --json` が書く workflow の配列(WorkflowInfo.to_dict() の並び・更新の新しい順)。
// vscode を import しない(unit test が読む)。

import * as cp from 'child_process';
import { promisify } from 'util';

const execFileAsync = promisify(cp.execFile);

/** 呼ぶ実行 file の名。 */
export const AGENTIC_CLI = 'doeff-agentic';

/** CLI が見つからない時に見せる入れ方。 */
export const AGENTIC_INSTALL_HINT = 'pip install doeff-agentic';

/** workflow の一覧を問う引数。 */
export const PS_JSON_ARGS: readonly string[] = ['ps', '--json'];

/** workflow の中の agent 1 つ(AgentInfo.to_dict())。 */
export interface AgentState {
  name: string;
  status: string;
  session_name: string;
  pane_id?: string | null;
  started_at: string;
  last_output_hash?: string | null;
}

/** 記録された workflow 1 つ(WorkflowInfo.to_dict())。 */
export interface WorkflowInfo {
  id: string;
  name: string;
  status: string;
  started_at: string;
  updated_at: string;
  current_agent?: string | null;
  agents: AgentState[];
  last_slog?: unknown;
  error?: string | null;
}

/** `ps --json` の stdout を workflow の配列として読む。配列でなければ投げる。 */
export function parseWorkflows(stdout: string): WorkflowInfo[] {
  const parsed: unknown = JSON.parse(stdout);
  if (!Array.isArray(parsed)) {
    throw new Error(`Expected workflows array from ${AGENTIC_CLI} ps`);
  }
  return parsed as WorkflowInfo[];
}

/** `<command> ps --json` を走らせて workflow の一覧を返す。走らない・読めない時は投げる。 */
export async function listWorkflows(command: string = AGENTIC_CLI): Promise<WorkflowInfo[]> {
  const { stdout } = await execFileAsync(command, [...PS_JSON_ARGS], {
    maxBuffer: 10 * 1024 * 1024,
    timeout: 5000
  });
  return parseWorkflows(stdout);
}
