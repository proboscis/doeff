// 外の module の置き場所を workspace の Python 環境に聞く handler(uv を通す)と、cache を捨てる合図の handler。
// `.venv/bin/python` を直に呼ばず、`uv run --project <root>` で環境を選ばせる。

import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { runProcess } from './childProcess';
import type { ChangeStamps, LocateBatch, ModuleLocation, ModuleLocator } from './externalCache';

/** uv 1 回の上限(uv の起動と hy の読み込みで普通は 1 秒ほど)。 */
const UV_TIMEOUT_MS = 10_000;

/**
 * 環境の中で走らせる問い合わせ — hy が入っていれば先に import して .hy の module も find_spec で引けるようにし、
 * module ごとに origin / missing / error を JSON で 1 行出す。
 */
const FIND_SPEC_SCRIPT = [
  'import importlib.util, json, sys',
  'try:',
  '    import hy  # noqa: F401 — .hy の module を find_spec で引けるようにする',
  'except Exception:',
  '    pass',
  'out = {}',
  'for m in sys.argv[1:]:',
  '    try:',
  '        spec = importlib.util.find_spec(m)',
  '        out[m] = {"origin": spec.origin} if spec is not None and spec.origin not in (None, "built-in", "frozen") else {"missing": True}',
  '    except ModuleNotFoundError:',
  '        out[m] = {"missing": True}',
  '    except Exception as e:',
  '        out[m] = {"error": type(e).__name__ + ": " + str(e)}',
  'print(json.dumps(out))'
].join('\n');

/** uv の在り処の候補(VS Code を GUI から起動すると PATH に homebrew 等が無いことがある)。 */
function uvCandidates(): string[] {
  const home = os.homedir();
  return [
    'uv',
    path.join(home, '.local', 'bin', 'uv'),
    path.join(home, '.cargo', 'bin', 'uv'),
    '/opt/homebrew/bin/uv',
    '/usr/local/bin/uv'
  ];
}

/** 問い合わせの stdout(JSON)を module ごとの答えに読む。形が違えば理由を返す。 */
export function parseLocateOutput(stdout: string, modules: readonly string[]): LocateBatch {
  let raw: unknown;
  try {
    raw = JSON.parse(stdout.trim().split('\n').pop() ?? '');
  } catch (error) {
    return { tag: 'failed', reason: `答えを JSON として読めない: ${String(error)}` };
  }
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    return { tag: 'failed', reason: '答えが object でない' };
  }
  const answers = new Map<string, unknown>(Object.entries(raw));
  const locations = new Map<string, ModuleLocation>();
  for (const module of modules) {
    const answer = answers.get(module);
    if (typeof answer !== 'object' || answer === null) {
      locations.set(module, { tag: 'error', reason: `答えに ${module} が無い` });
      continue;
    }
    const fields = new Map<string, unknown>(Object.entries(answer));
    const origin = fields.get('origin');
    const error = fields.get('error');
    if (typeof origin === 'string') {
      locations.set(module, { tag: 'found', origin });
    } else if (fields.get('missing') === true) {
      locations.set(module, { tag: 'missing' });
    } else if (typeof error === 'string') {
      locations.set(module, { tag: 'error', reason: `${module} の場所を引けない: ${error}` });
    } else {
      locations.set(module, { tag: 'error', reason: `${module} の答えの形が違う: ${JSON.stringify(answer)}` });
    }
  }
  return { tag: 'ok', locations };
}

/** `uv run --no-sync --project <root> python -c …` で module の置き場所をまとめて聞く handler。 */
export class UvModuleLocator implements ModuleLocator {
  /** module の置き場所を子 process 1 回で聞く(uv が PATH に無ければ既知の場所を順に試す)。 */
  async locate(root: string, modules: readonly string[]): Promise<LocateBatch> {
    if (modules.length === 0) {
      return { tag: 'ok', locations: new Map() };
    }
    const args = ['run', '--no-sync', '--project', root, 'python', '-c', FIND_SPEC_SCRIPT, ...modules];
    for (const uv of uvCandidates()) {
      const result = await runProcess(uv, args, root, undefined, UV_TIMEOUT_MS);
      if (result.tag === 'error') {
        if (result.commandMissing) {
          continue;
        }
        return { tag: 'failed', reason: `uv (${uv}): ${result.reason}` };
      }
      if (result.code !== 0) {
        const detail = result.stderr.trim().split('\n').slice(-3).join(' / ');
        return { tag: 'failed', reason: `uv が終了コード ${String(result.code)} で終わった: ${detail}` };
      }
      return parseLocateOutput(result.stdout, modules);
    }
    return { tag: 'failed', reason: `uv が見つからない(試した場所: ${uvCandidates().join(', ')})` };
  }
}

/** file の更新時刻を文字列にする(無い file は "-"。無い以外の失敗は投げる)。 */
async function mtimeOf(filePath: string): Promise<string> {
  try {
    return String((await fs.promises.stat(filePath)).mtimeMs);
  } catch (error) {
    const code = error instanceof Error && 'code' in error ? error.code : undefined;
    if (code === 'ENOENT' || code === 'ENOTDIR') {
      return '-';
    }
    throw error;
  }
}

/** 環境が変わった合図 = uv.lock と pyproject.toml の時刻。file の合図 = その file の時刻。 */
export const FS_CHANGE_STAMPS: ChangeStamps = {
  projectFingerprint: async (root) =>
    `${await mtimeOf(path.join(root, 'uv.lock'))}|${await mtimeOf(path.join(root, 'pyproject.toml'))}`,
  fileStamp: (filePath) => mtimeOf(filePath)
};
