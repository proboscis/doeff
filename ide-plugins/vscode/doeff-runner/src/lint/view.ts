// linter の結果の見せ方を決める純粋な関数 — 波線の中身、違反の木(law → file → 違反)、規則の一覧、層の地図の木。
// 判定はしない(何が違反かも、地図の層・色も linter の出力のまま)。VS Code には触らない。

import * as path from 'path';
import type { LintModule, LintRange, LintRule, LintSeverity, LintViolation } from './contract';
import { violationExplanationLines } from './layers';

/** 波線 1 本の中身。 */
export interface LintDiagnostic {
  readonly path: string;
  readonly violation: LintViolation;
  readonly severity: LintSeverity;
  /** 波線の文 — 違反の文・直し方・規則の ID と ADR の law の名 */
  readonly message: string;
  /** 問題の一覧の「コード」欄(規則の ID) */
  readonly code: string;
}

/** 違反を波線の中身にする(重さも文も linter の出力のまま)。 */
export function diagnosticOf(violation: LintViolation): LintDiagnostic {
  // 文は linter の出力だけから作る(これは何か・なぜ違反か・law の :statement・直し方)
  const lines = [violation.message, ...violationExplanationLines(violation)];
  const law = violation.law === null ? '' : ` · law ${violation.law}`;
  const adr = violation.adr === null ? '' : ` · ${violation.adr}`;
  const registered = violation.registered ? '(登録簿に載った既知の破れ)' : '';
  lines.push(`規則 ${violation.rule}${law}${adr}${registered}`);
  return { path: violation.path, violation, severity: violation.severity, message: lines.join('\n'), code: violation.rule };
}

/** パネルの木の節。 */
export type LintNode =
  /** 違反を law(無ければ規則の ID)でまとめた束 */
  | { readonly tag: 'law'; readonly label: string; readonly violations: readonly LintViolation[] }
  /** 束の中の file */
  | { readonly tag: 'file'; readonly path: string; readonly label: string; readonly violations: readonly LintViolation[] }
  | { readonly tag: 'violation'; readonly violation: LintViolation }
  | { readonly tag: 'rule'; readonly rule: LintRule }
  /** 地図の層の束 */
  | { readonly tag: 'layer'; readonly label: string; readonly entries: readonly MapEntry[] }
  /** 地図の dir */
  | { readonly tag: 'dir'; readonly label: string; readonly prefix: string; readonly entries: readonly MapEntry[] }
  /** 地図の file(module) */
  | { readonly tag: 'module'; readonly entry: MapEntry }
  | { readonly tag: 'message'; readonly label: string };

/** 地図の 1 file — root からの相対 path と linter の要約。 */
export interface MapEntry {
  readonly relative: string;
  readonly module: LintModule;
  /** その file の今の違反(展開すると 1 件ずつ出す) */
  readonly violations: readonly LintViolation[];
}

/** 束の中の違反の数(地図の色は違反の有無だけで決める)。 */
export function violationCount(node: LintNode): number {
  switch (node.tag) {
    case 'law':
    case 'file':
      return node.violations.length;
    case 'violation':
      return 1;
    case 'layer':
    case 'dir':
      return node.entries.reduce((n, e) => n + e.module.violations, 0);
    case 'module':
      return node.entry.module.violations;
    case 'rule':
    case 'message':
      return 0;
    default: {
      const unreachable: never = node;
      throw new Error(`網羅されていない節: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 違反の束の見出し — ADR の law の名があればそれ、無ければ規則の ID。 */
function groupLabel(violation: LintViolation): string {
  return violation.law ?? violation.rule;
}

/** 違反の木の最上段 — law(か規則の ID)ごとの束(名前の順)。違反が無ければ札。 */
export function violationRoots(violations: readonly LintViolation[]): LintNode[] {
  if (violations.length === 0) {
    return [{ tag: 'message', label: 'linter の違反はありません' }];
  }
  const byLaw = new Map<string, LintViolation[]>();
  for (const violation of violations) {
    const label = groupLabel(violation);
    byLaw.set(label, [...(byLaw.get(label) ?? []), violation]);
  }
  return [...byLaw.keys()].sort().map((label) => ({ tag: 'law', label, violations: byLaw.get(label) ?? [] }));
}

/** 規則の一覧 — 針のつながった規則が先、つながっていない規則(見ていない物)は後。 */
export function ruleNodes(rules: readonly LintRule[]): LintNode[] {
  if (rules.length === 0) {
    return [{ tag: 'message', label: 'linter の規則の一覧がまだありません(全体の実行の後に出ます)' }];
  }
  const sorted = [...rules].sort((a, b) => Number(b.wired) - Number(a.wired) || a.rule.localeCompare(b.rule));
  return sorted.map((rule) => ({ tag: 'rule', rule }));
}

/** 地図の層の順(linter の層の名前。この外の名前は名前の順で後に、層の外は最後)。 */
const LAYER_ORDER = ['core', 'intent', 'protocol', 'foundation', 'entry'];
/** 層の無い module の束の見出し。 */
export const OUTSIDE_LAYERS = '(層の外)';

/** 地図の最上段 — 層ごとの束。 */
export function mapRoots(
  modules: ReadonlyArray<{ readonly root: string; readonly module: LintModule }>,
  violations: readonly LintViolation[]
): LintNode[] {
  if (modules.length === 0) {
    return [{ tag: 'message', label: 'linter の地図の材料(modules)がまだありません' }];
  }
  const roots = new Set(modules.map((m) => m.root));
  const byLayer = new Map<string, MapEntry[]>();
  for (const { root, module } of modules) {
    const inRoot = path.relative(root, module.path);
    const relative = roots.size > 1 ? path.join(path.basename(root), inRoot) : inRoot;
    const layer = module.layer ?? OUTSIDE_LAYERS;
    const own = violations.filter((v) => path.normalize(v.path) === path.normalize(module.path));
    byLayer.set(layer, [...(byLayer.get(layer) ?? []), { relative, module, violations: own }]);
  }
  // 層を決まった順(core → entry、知らない層、層の外)に並べるための順位
  const rank = (layer: string): number => {
    if (layer === OUTSIDE_LAYERS) {
      return LAYER_ORDER.length + 2;
    }
    const i = LAYER_ORDER.indexOf(layer);
    return i >= 0 ? i : LAYER_ORDER.length + 1;
  };
  return [...byLayer.keys()]
    .sort((a, b) => rank(a) - rank(b) || a.localeCompare(b))
    .map((label) => ({ tag: 'layer', label, entries: byLayer.get(label) ?? [] }));
}

/** dir の中身 — 直下の dir(名前の順)と直下の file(名前の順)。 */
function dirChildren(prefix: string, entries: readonly MapEntry[]): LintNode[] {
  const subdirs = new Map<string, MapEntry[]>();
  const files: MapEntry[] = [];
  for (const entry of entries) {
    const rest = prefix === '' ? entry.relative : entry.relative.slice(prefix.length + 1);
    const parts = rest.split(path.sep);
    if (parts.length <= 1) {
      files.push(entry);
    } else {
      subdirs.set(parts[0], [...(subdirs.get(parts[0]) ?? []), entry]);
    }
  }
  const dirs: LintNode[] = [...subdirs.keys()].sort().map((name) => ({
    tag: 'dir',
    label: name,
    prefix: prefix === '' ? name : path.join(prefix, name),
    entries: subdirs.get(name) ?? []
  }));
  const modules: LintNode[] = files
    .sort((a, b) => a.relative.localeCompare(b.relative))
    .map((entry) => ({ tag: 'module', entry }));
  return [...dirs, ...modules];
}

/** 違反を行の順の節にする(file の子・地図の file の子)。 */
function violationsByLine(violations: readonly LintViolation[]): LintNode[] {
  return [...violations]
    .sort((a, b) => a.range.start.line - b.range.start.line || a.range.start.character - b.range.start.character)
    .map((violation) => ({ tag: 'violation', violation }));
}

/** 節の子を作る(展開した時に呼ぶ)。 */
export function lintChildren(node: LintNode): LintNode[] {
  switch (node.tag) {
    case 'law': {
      const byFile = new Map<string, LintViolation[]>();
      for (const violation of node.violations) {
        byFile.set(violation.path, [...(byFile.get(violation.path) ?? []), violation]);
      }
      return [...byFile.keys()].sort().map((filePath) => ({
        tag: 'file',
        path: filePath,
        label: path.basename(filePath),
        violations: byFile.get(filePath) ?? []
      }));
    }
    case 'file':
      return violationsByLine(node.violations);
    case 'module':
      return violationsByLine(node.entry.violations);
    case 'layer':
      return dirChildren('', node.entries);
    case 'dir':
      return dirChildren(node.prefix, node.entries);
    case 'violation':
    case 'rule':
    case 'message':
      return [];
    default: {
      const unreachable: never = node;
      throw new Error(`網羅されていない節: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 行の長さが分からない時に、行全体とみなす列(VS Code は行の長さに切り詰める)。 */
export const WHOLE_LINE = 10_000;

/** 表示に使う範囲 — linter の範囲が空(0 幅)なら、その行の全体に広げる(見える波線と選択にする)。 */
export function displayRange(range: LintRange, lineLength: number | undefined): LintRange {
  const empty = range.start.line === range.end.line && range.start.character === range.end.character;
  if (!empty) {
    return range;
  }
  return {
    start: { line: range.start.line, character: 0 },
    end: { line: range.start.line, character: lineLength ?? WHOLE_LINE }
  };
}

/** 重さの強い順(同じ行の注記の色は最も強い物で決める)。 */
const SEVERITY_RANK: Readonly<Record<LintSeverity, number>> = { error: 0, warning: 1, info: 2 };

/** 行末の注記 1 つ — 行・その行の最も強い重さ・短い文。 */
export interface InlineAnnotation {
  readonly line: number;
  readonly severity: LintSeverity;
  readonly text: string;
  readonly count: number;
}

/** 注記の文に載せる文の長さの上限(長い文は … で切る)。 */
const INLINE_MESSAGE_LIMIT = 90;

/**
 * 1 つの file の違反から行末の注記を作る — 行ごとに 1 つ、最も強い重さの先頭の違反を「● 規則 文」、
 * 同じ行に複数あれば「(他 N 件)」を添える。行の順に返す。
 */
export function inlineAnnotations(violations: readonly LintViolation[]): InlineAnnotation[] {
  const byLine = new Map<number, LintViolation[]>();
  for (const violation of violations) {
    const line = violation.range.start.line;
    byLine.set(line, [...(byLine.get(line) ?? []), violation]);
  }
  return [...byLine.keys()]
    .sort((a, b) => a - b)
    .map((line) => {
      const onLine = [...(byLine.get(line) ?? [])].sort(
        (a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity] || a.range.start.character - b.range.start.character
      );
      const first = onLine[0];
      const message =
        first.message.length > INLINE_MESSAGE_LIMIT ? `${first.message.slice(0, INLINE_MESSAGE_LIMIT - 1)}…` : first.message;
      const more = onLine.length > 1 ? `(他 ${onLine.length - 1} 件)` : '';
      return { line, severity: first.severity, text: `● ${first.rule} ${message}${more}`, count: onLine.length };
    });
}
