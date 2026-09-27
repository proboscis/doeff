// PythonModuleSource の handler — workspace の root の下の file を実際に探して読む。
// VS Code の glob 検索は register.ts から後ろ盾として渡す(ここは node の fs だけに依る)。

import * as fs from 'fs';
import * as path from 'path';
import { pythonModuleRelativePaths, type PythonModuleSource, type PythonReadResult } from './python';

/** root からの相対 path(`a/b.py`)で workspace を広く探す口(VS Code の findFiles 等)。 */
export type GlobFinder = (relativePath: string) => Promise<readonly string[]>;

/** 候補の path に file が在るかを見る(無い = ENOENT・ENOTDIR だけを「無い」とし、他の失敗は投げる)。 */
async function isFile(filePath: string): Promise<boolean> {
  try {
    return (await fs.promises.stat(filePath)).isFile();
  } catch (error) {
    const code = error instanceof Error && 'code' in error ? error.code : undefined;
    if (code === 'ENOENT' || code === 'ENOTDIR') {
      return false;
    }
    throw error;
  }
}

/**
 * workspace の root の直下と `src/` の下を先に見て、無ければ glob で workspace 全体から探す handler。
 * roots は呼ぶたびに読み直す(workspace の folder の増減に追いつくため)。
 */
export class FsPythonModuleSource implements PythonModuleSource {
  constructor(
    private readonly roots: () => readonly string[],
    private readonly glob: GlobFinder | undefined
  ) {}

  /** module の file を探す(root 直下 → root/src → glob の順。見つかった段で止める)。 */
  async findModuleFiles(module: string): Promise<readonly string[]> {
    const relatives = pythonModuleRelativePaths(module);
    const direct: string[] = [];
    for (const root of this.roots()) {
      for (const base of [root, path.join(root, 'src')]) {
        for (const rel of relatives) {
          const candidate = path.join(base, rel);
          if (await isFile(candidate)) {
            direct.push(candidate);
          }
        }
      }
    }
    if (direct.length > 0 || this.glob === undefined) {
      return direct;
    }
    const globbed: string[] = [];
    for (const rel of relatives) {
      globbed.push(...(await this.glob(rel)));
    }
    return globbed;
  }

  /** file を utf-8 で読む(読めない時は理由を返す)。 */
  async readText(filePath: string): Promise<PythonReadResult> {
    try {
      return { tag: 'text', text: await fs.promises.readFile(filePath, 'utf8') };
    } catch (error) {
      return { tag: 'unreadable', reason: `${filePath} を読めない: ${String(error)}` };
    }
  }
}
