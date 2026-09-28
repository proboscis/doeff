// editor が使っている `.hy` の文法と、有効な color theme の file を、入っている拡張の宣言から選ぶ(純粋 — 拡張の一覧は外から渡す)。
// 面が文法や theme を自前で持たず、editor と同じ file を使うため(agora-redesign #910 U16)。

import * as path from 'path';

/** 入っている拡張 1 つ(vscode.Extension の要る所だけ)。 */
export interface InstalledExtension {
  readonly id: string;
  readonly extensionPath: string;
  readonly packageJSON: unknown;
}

/** 選んだ文法の file。 */
export interface GrammarLocation {
  readonly extensionId: string;
  readonly scopeName: string;
  readonly path: string;
}

/** object かを見る(拡張の package.json の値の形を確かめるため)。 */
function isRecord(value: unknown): value is { readonly [key: string]: unknown } {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** 拡張の `contributes` の中の配列の欄(無ければ空)。 */
function contributed(extension: InstalledExtension, key: string): readonly unknown[] {
  const pkg = extension.packageJSON;
  if (!isRecord(pkg) || !isRecord(pkg.contributes)) {
    return [];
  }
  const list = pkg.contributes[key];
  return Array.isArray(list) ? list : [];
}

/**
 * 言語 `languageId` の文法を出している拡張の文法の file(editor が色付けに使う物)。複数あれば一覧の後の方
 * (VS Code は後から登録した文法で先の物を置き換える)。無ければ null。
 */
export function locateGrammar(extensions: readonly InstalledExtension[], languageId: string): GrammarLocation | null {
  let found: GrammarLocation | null = null;
  for (const extension of extensions) {
    for (const grammar of contributed(extension, 'grammars')) {
      if (!isRecord(grammar) || grammar.language !== languageId) {
        continue;
      }
      const { scopeName, path: relative } = grammar;
      if (typeof scopeName === 'string' && typeof relative === 'string') {
        found = { extensionId: extension.id, scopeName, path: path.join(extension.extensionPath, relative) };
      }
    }
  }
  return found;
}

/**
 * 設定 `workbench.colorTheme` の値の theme の file。VS Code と同じく theme の `id`(無ければ `label`)で照らす。
 * 見つからなければ null(その時は customizations の規則だけで塗る)。
 */
export function locateTheme(extensions: readonly InstalledExtension[], themeName: string): string | null {
  for (const extension of extensions) {
    for (const theme of contributed(extension, 'themes')) {
      if (!isRecord(theme) || typeof theme.path !== 'string') {
        continue;
      }
      const settingsId = typeof theme.id === 'string' ? theme.id : theme.label;
      if (settingsId === themeName) {
        return path.join(extension.extensionPath, theme.path);
      }
    }
  }
  return null;
}
