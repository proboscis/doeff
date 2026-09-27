// linter の結果の見せ方を決める純粋な関数 — 波線の中身、違反の木(law → file → 違反)、規則の一覧、層の地図の木。
// 判定はしない(何が違反かも、地図の層・色も linter の出力のまま)。VS Code には触らない。

import * as path from 'path';
import type { LintModule, LintRange, LintRule, LintRuleFamily, LintSeverity, LintViolation } from './contract';
import { violationExplanationLines } from './layers';

/** 表の値の並びへ 1 件足す(数千件でも線形に束ねる)。 */
export function pushTo<K, V>(table: Map<K, V[]>, key: K, value: V): void {
  const list = table.get(key);
  if (list === undefined) {
    table.set(key, [value]);
  } else {
    list.push(value);
  }
}

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

/**
 * 違反の束の見出しに使う規則の中身 — linter の規則の一覧から引いた短い名・家族・文。linter は 1 つの規則を結びついた
 * law ごとに並べるので、同じ ID の項目をまとめる(名と家族は規則ごとに 1 つ)。古い linter の出力は名も家族も null。
 */
export interface RuleSummary {
  readonly title: string | null;
  readonly family: LintRuleFamily | null;
  /** 規則の文(law が結びついていれば `law の名: law の文`)— 重ならない物を一覧の順に */
  readonly statements: readonly string[];
}

/** 規則の一覧を ID ごとの中身にまとめる。 */
export function ruleSummaries(rules: readonly LintRule[]): Map<string, RuleSummary> {
  const table = new Map<string, { title: string | null; family: LintRuleFamily | null; statements: string[] }>();
  for (const rule of rules) {
    const found = table.get(rule.rule);
    if (found === undefined) {
      table.set(rule.rule, { title: rule.title, family: rule.family, statements: [rule.statement] });
      continue;
    }
    found.title = found.title ?? rule.title;
    found.family = found.family ?? rule.family;
    if (!found.statements.includes(rule.statement)) {
      found.statements.push(rule.statement);
    }
  }
  return table;
}

/** 規則の一覧に無い ID の中身(規則の一覧は全体の実行の後にしか無い — 名と家族は出さない)。 */
const UNKNOWN_RULE: RuleSummary = { title: null, family: null, statements: [] };

/** パネルの木の節。 */
export type LintNode =
  /** 違反を規則の ID でまとめた束(law の名は hover に出す) */
  | { readonly tag: 'group'; readonly rule: string; readonly summary: RuleSummary; readonly violations: readonly LintViolation[] }
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
    case 'group':
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

/**
 * 違反の木の最上段 — 規則の ID ごとの束(ID の順)。見出しの名と家族は linter の規則の一覧から引く(拡張は写しを
 * 持たない)。違反が無ければ札。
 */
export function violationRoots(violations: readonly LintViolation[], rules: readonly LintRule[]): LintNode[] {
  if (violations.length === 0) {
    return [{ tag: 'message', label: 'linter の違反はありません' }];
  }
  const byRule = new Map<string, LintViolation[]>();
  for (const violation of violations) {
    pushTo(byRule, violation.rule, violation);
  }
  const summaries = ruleSummaries(rules);
  return [...byRule.keys()].sort().map((rule) => ({
    tag: 'group',
    rule,
    summary: summaries.get(rule) ?? UNKNOWN_RULE,
    violations: byRule.get(rule) ?? []
  }));
}

/** 束の見出し — 規則の ID と短い名(名の無い古い linter の出力は ID だけ)。 */
export function groupLabel(rule: string, summary: RuleSummary): string {
  return summary.title === null ? rule : `${rule} ${summary.title}`;
}

/** 違反の束で最も重い重さ(束の絵の縁の色)。 */
export function worstSeverity(violations: readonly LintViolation[]): LintSeverity | undefined {
  let worst: LintSeverity | undefined;
  for (const v of violations) {
    if (worst === undefined || SEVERITY_RANK[v.severity] < SEVERITY_RANK[worst]) {
      worst = v.severity;
    }
  }
  return worst;
}

/** 束の件数の文 — 件数と、重さが混ざる時はその内訳(例: `335 件(error 3・warning 332)`)。 */
export function groupDescription(violations: readonly LintViolation[]): string {
  const counts = (['error', 'warning', 'info'] as const)
    .map((s) => ({ s, n: violations.filter((v) => v.severity === s).length }))
    .filter((c) => c.n > 0);
  const detail = counts.length > 1 ? `(${counts.map((c) => `${c.s} ${c.n}`).join('・')})` : '';
  return `${violations.length} 件${detail}`;
}

/** 束の hover の行 — 見出し・件数・結びついた law の名と ADR・規則の文(文はすべて linter の出力から)。 */
export function groupTooltipLines(rule: string, summary: RuleSummary, violations: readonly LintViolation[]): string[] {
  const laws = new Map<string, string | null>();
  for (const v of violations) {
    if (v.law !== null && !laws.has(v.law)) {
      laws.set(v.law, v.adr);
    }
  }
  const lines = [`${groupLabel(rule, summary)} — ${groupDescription(violations)}`];
  for (const [law, adr] of laws) {
    lines.push(`law: ${law}${adr === null ? '' : `(${adr})`}`);
  }
  lines.push(...summary.statements);
  if (summary.title === null) {
    lines.push('(この linter の出力には規則の短い名が無い — doeff-linter を新しくすると名が出る)');
  }
  return lines;
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
  const violationsByPath = new Map<string, LintViolation[]>();
  for (const violation of violations) {
    pushTo(violationsByPath, path.normalize(violation.path), violation);
  }
  for (const { root, module } of modules) {
    const inRoot = path.relative(root, module.path);
    const relative = roots.size > 1 ? path.join(path.basename(root), inRoot) : inRoot;
    const layer = module.layer ?? OUTSIDE_LAYERS;
    const own = violationsByPath.get(path.normalize(module.path)) ?? [];
    pushTo(byLayer, layer, { relative, module, violations: own });
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
        pushTo(subdirs, parts[0], entry);
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
    case 'group': {
      const byFile = new Map<string, LintViolation[]>();
      for (const violation of node.violations) {
        pushTo(byFile, violation.path, violation);
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
    pushTo(byLine, line, violation);
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

/** 違反を file ごとの波線の中身にまとめる(数千件の全体の実行でも線形)。 */
export function diagnosticsByPath(violations: readonly LintViolation[]): Map<string, LintDiagnostic[]> {
  const byPath = new Map<string, LintDiagnostic[]>();
  for (const violation of violations) {
    pushTo(byPath, violation.path, diagnosticOf(violation));
  }
  return byPath;
}

/** 重さの順位(小さいほど重い)。 */
export function severityRank(severity: LintSeverity): number {
  return SEVERITY_RANK[severity];
}

/** 行末の注記と左端の印に出す違反 — 最小の重さ以上だけ(波線と問題の一覧は全部出す)。 */
export function atLeast(violations: readonly LintViolation[], minimum: LintSeverity): LintViolation[] {
  return violations.filter((v) => SEVERITY_RANK[v.severity] <= SEVERITY_RANK[minimum]);
}

/** 設定の最小の重さを読む(知らない値は既定の warning にせず undefined — 呼ぶ側が理由を出す)。 */
export function parseMinSeverity(value: unknown): LintSeverity | undefined {
  return value === 'error' || value === 'warning' || value === 'info' ? value : undefined;
}
