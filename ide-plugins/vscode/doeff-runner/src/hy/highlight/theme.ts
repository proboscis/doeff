// 有効な color theme の token の色の規則を、VS Code と同じ順で組む(純粋 — file の読み込みは外から渡す)。
//
// VS Code の組み方(colorThemeData.ts)に合わせる: theme の file の `include` を先に読み、その後に自分の `tokenColors`、
// 最後に設定 `editor.tokenColorCustomizations`(全体 → `[theme の名]` の順)を足す。後に足した規則が同じ scope で勝つ。
// `tokenColors` が `.tmTheme`(plist)を指す theme は読まず、理由を problems に積む(色の無い token は既定の文字色で描く)。

import { parse as parseJsonc, type ParseError } from 'jsonc-parser';

/** TextMate の規則 1 つ(vscode-textmate の IRawThemeSetting と同じ形)。scope が無い規則は既定の色。 */
export interface ThemeRule {
  readonly scope?: string | readonly string[];
  readonly settings: { readonly foreground?: string; readonly background?: string; readonly fontStyle?: string };
}

/** theme の file を path で読む口(読めなければ理由を返す)。 */
export type ReadThemeFile = (path: string) => Promise<{ readonly tag: 'ok'; readonly text: string } | { readonly tag: 'error'; readonly reason: string }>;

/** path の結合(`include` は theme の file からの相対)。 */
export type JoinPath = (fromFile: string, relative: string) => string;

/** 組んだ規則と、読めなかった理由。 */
export interface ThemeRules {
  readonly rules: readonly ThemeRule[];
  readonly problems: readonly string[];
}

/** `editor.tokenColorCustomizations` の簡単な鍵 → scope(VS Code の colorThemeData.ts の tokenGroupToScopesMap と同じ)。 */
const CUSTOMIZATION_SCOPES: Readonly<Record<string, readonly string[]>> = {
  comments: ['comment', 'punctuation.definition.comment'],
  strings: ['string', 'meta.embedded.assembly'],
  keywords: ['keyword - keyword.operator', 'keyword.control', 'storage', 'storage.type'],
  numbers: ['constant.numeric'],
  types: ['entity.name.type', 'entity.name.class', 'support.type', 'support.class'],
  functions: ['entity.name.function', 'support.function'],
  variables: ['variable', 'entity.name.variable']
};

/** include の深さの上限(循環の止め)。 */
const MAX_INCLUDE_DEPTH = 8;

type JsonObject = { readonly [key: string]: unknown };

/** JSON の object かを見る(設定と theme の file の値の形を確かめるため)。 */
function isObject(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** 文字列の列かを見る(scope の配列の形を確かめるため)。 */
function isStringArray(value: unknown): value is readonly string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string');
}

/** 規則 1 つを JSON から読む(形が違えば null)。 */
function ruleOf(value: unknown): ThemeRule | null {
  if (!isObject(value) || !isObject(value.settings)) {
    return null;
  }
  const settings = value.settings;
  const text = (key: string): string | undefined => {
    const found = settings[key];
    return typeof found === 'string' ? found : undefined;
  };
  const scope = value.scope;
  const scopes = typeof scope === 'string' || isStringArray(scope) ? scope : undefined;
  const picked = { foreground: text('foreground'), background: text('background'), fontStyle: text('fontStyle') };
  return scopes === undefined ? { settings: picked } : { scope: scopes, settings: picked };
}

/** theme の file 1 つ(と、その include)の規則を順に積む。 */
async function collect(path: string, read: ReadThemeFile, join: JoinPath, depth: number, out: ThemeRule[], problems: string[]): Promise<void> {
  if (depth > MAX_INCLUDE_DEPTH) {
    problems.push(`${path}: include が深すぎる`);
    return;
  }
  const loaded = await read(path);
  if (loaded.tag === 'error') {
    problems.push(`${path}: ${loaded.reason}`);
    return;
  }
  const errors: ParseError[] = [];
  const doc: unknown = parseJsonc(loaded.text, errors, { allowTrailingComma: true });
  if (!isObject(doc)) {
    problems.push(`${path}: JSON の object でない`);
    return;
  }
  if (typeof doc.include === 'string') {
    await collect(join(path, doc.include), read, join, depth + 1, out, problems);
  }
  const tokenColors = doc.tokenColors;
  if (typeof tokenColors === 'string') {
    problems.push(`${path}: tokenColors が ${tokenColors} を指す(.tmTheme は読まない)`);
  } else if (Array.isArray(tokenColors)) {
    for (const item of tokenColors) {
      const rule = ruleOf(item);
      if (rule !== null) {
        out.push(rule);
      }
    }
  }
}

/** customizations の 1 段(全体か `[theme の名]`)を規則にする。 */
function customizationRules(value: unknown): ThemeRule[] {
  if (!isObject(value)) {
    return [];
  }
  const out: ThemeRule[] = [];
  for (const [key, scopes] of Object.entries(CUSTOMIZATION_SCOPES)) {
    const setting = value[key];
    if (typeof setting === 'string') {
      out.push({ scope: scopes, settings: { foreground: setting } });
    } else if (isObject(setting)) {
      const rule = ruleOf({ scope: [...scopes], settings: setting });
      if (rule !== null) {
        out.push(rule);
      }
    }
  }
  const textMateRules = value.textMateRules;
  if (Array.isArray(textMateRules)) {
    for (const item of textMateRules) {
      const rule = ruleOf(item);
      if (rule !== null) {
        out.push(rule);
      }
    }
  }
  return out;
}

/**
 * theme の file(`themePath` — 無ければ規則は customizations だけ)と、設定 `editor.tokenColorCustomizations` の値から規則を組む。
 * `themeName` は `[theme の名]` の段を引くための名(設定 `workbench.colorTheme` の値)。
 */
export async function resolveThemeRules(
  themePath: string | null,
  themeName: string,
  customizations: unknown,
  read: ReadThemeFile,
  join: JoinPath
): Promise<ThemeRules> {
  const rules: ThemeRule[] = [];
  const problems: string[] = [];
  if (themePath !== null) {
    await collect(themePath, read, join, 0, rules, problems);
  }
  rules.push(...customizationRules(customizations));
  if (isObject(customizations)) {
    rules.push(...customizationRules(customizations[`[${themeName}]`]));
  }
  return { rules, problems };
}
