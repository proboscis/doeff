// Hy の索引の対象になる path の決まり — 拡張子と、索引の対象から外す dir の一覧を 1 か所に持つ。
// 起動時の root 全体(glob)も、disk の変化の通知(watcher)も、この一覧だけを読む(vscode に依らない純粋な関数)。

/** 索引の対象から外す dir の名(契約の --root の除外と同じ)。 */
export const EXCLUDED_DIRS = ['.venv', 'node_modules', 'target', '.git', '__pycache__'] as const;

/** Hy の file の拡張子(languageId `hy` と同じ集合)。 */
export const HY_FILE_GLOB = '**/*.{hy,hyk,hyp}';
/** 索引の対象から外す dir の glob。 */
export const HY_EXCLUDE_GLOB = `**/{${EXCLUDED_DIRS.join(',')}}/**`;

/** path が Hy の file かを拡張子で見る。 */
export function isHyPath(filePath: string): boolean {
  return /\.(hy|hyk|hyp)$/.test(filePath);
}

/** path が除外の dir の中(または除外の dir そのもの)にあるか。区切りは / と \ のどちらも読む。 */
export function isExcludedPath(filePath: string): boolean {
  const parts = filePath.split(/[\\/]/);
  return parts.some((part) => (EXCLUDED_DIRS as readonly string[]).includes(part));
}
