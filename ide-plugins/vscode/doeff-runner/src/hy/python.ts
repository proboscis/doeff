// Hy から import した Python の module(索引に無い物)の中の定義を探す。
// 外の世界(file の探索と読み込み)は PythonModuleSource の口に閉じ、ここの関数は文字列だけを見る。

/** Python の source を探して読む口(effect)。実 I/O は pythonSource.ts の handler が持つ。 */
export interface PythonModuleSource {
  /** module の dotted 名から、その module の file(`a/b.py` か `a/b/__init__.py`)の絶対 path を返す。無ければ []。 */
  findModuleFiles(module: string): Promise<readonly string[]>;
  /** file の中身を読む。読めない時は理由を返す(「無い」と「読めない」を混ぜない)。 */
  readText(filePath: string): Promise<PythonReadResult>;
}

/** Python の file を読んだ結果。 */
export type PythonReadResult =
  | { readonly tag: 'text'; readonly text: string }
  | { readonly tag: 'unreadable'; readonly reason: string };

/** Python の file の中の名前の位置(0 始まりの行と、UTF-16 の列)。 */
export interface PythonNameLocation {
  readonly line: number;
  readonly character: number;
  readonly length: number;
  /** 行頭から始まる(module の top level の)定義か */
  readonly topLevel: boolean;
}

/** dotted の module 名から、root からの相対 path の候補を作る(`a.b` → `a/b.py`・`a/b/__init__.py`)。 */
export function pythonModuleRelativePaths(module: string): readonly string[] {
  const parts = module.split('.').filter((p) => p !== '');
  if (parts.length === 0) {
    return [];
  }
  const joined = parts.join('/');
  return [`${joined}.py`, `${joined}/__init__.py`];
}

/** 正規表現の特殊文字を逃がす。 */
function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Python の source の中から `def name` / `async def name` / `class name` / `name =`(`name: T =` を含む)の
 * 名前の位置を返す。top level の物があればそれだけを、無ければ字下げされた def / class を返す。
 */
export function findPythonDefinitions(text: string, name: string): readonly PythonNameLocation[] {
  const n = escapeRegExp(name);
  const defLike = new RegExp(`^(\\s*)(?:async\\s+def|def|class)\\s+(${n})\\b`);
  const assign = new RegExp(`^(${n})\\s*(?::[^=]*)?=(?!=)`);
  const found: PythonNameLocation[] = [];
  const lines = text.split(/\r?\n/);
  lines.forEach((line, lineNo) => {
    const def = defLike.exec(line);
    if (def) {
      const character = def[0].length - name.length;
      found.push({ line: lineNo, character, length: name.length, topLevel: def[1].length === 0 });
      return;
    }
    if (assign.test(line)) {
      found.push({ line: lineNo, character: 0, length: name.length, topLevel: true });
    }
  });
  const topLevel = found.filter((loc) => loc.topLevel);
  return topLevel.length > 0 ? topLevel : found;
}
