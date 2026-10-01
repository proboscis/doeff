// linter の結果の見せ方を決める純粋な関数 — 波線の中身、違反の木(law → file → 違反)、規則の一覧、層の地図の木と、木の節の
// 固定の id。判定はしない(何が違反かも、地図の層・色も linter の出力のまま)。VS Code には触らない。

import * as path from 'path';
import type { LintModule, LintRange, LintRule, LintRuleFamily, LintSeverity, LintViolation } from './contract';
import { violationExplanationLines } from './layers';
import {
  ALL_VIOLATIONS,
  byLevel,
  filterViolations,
  newDelta,
  levelTag,
  levelTally,
  standingCounts,
  standingText,
  type PanelFilter,
  type SavedTally,
  type StandingCounts
} from './severity';
import { LINT_LEVELS, type LintLevel } from './contract';
import { contentHash, type RootRunEntry } from './store';
import type { ViolationRef } from '../read/locate';

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
  /** 重大さ 1 つの要約の行 — 件数と、新しい分・既知の分・照合中の内訳、前回からの増減(前回が無ければ undefined) */
  | { readonly tag: 'summary'; readonly level: LintLevel; readonly counts: StandingCounts; readonly delta: number | undefined }
  /** 違反を (重大さ, 規則の ID) でまとめた束(law の名は hover に出す) */
  | {
      readonly tag: 'group';
      readonly level: LintLevel;
      readonly rule: string;
      readonly summary: RuleSummary;
      readonly violations: readonly LintViolation[];
    }
  /** 束の中の file(束の重大さと規則を持つ — 違う束の同じ file を別の節にするため) */
  | {
      readonly tag: 'file';
      readonly level: LintLevel;
      readonly rule: string;
      readonly path: string;
      readonly label: string;
      readonly violations: readonly LintViolation[];
    }
  /**
   * 違反 1 件。parentId = 親の節(束の file か地図の module)の id、occurrence = 同じ親の下で同じ規則・位置・文の違反の
   * 何番目か(linter が同じ違反を 2 度出しても id を重ねないため)
   */
  | { readonly tag: 'violation'; readonly violation: LintViolation; readonly parentId: string; readonly occurrence: number }
  | { readonly tag: 'rule'; readonly rule: LintRule }
  /** 地図の層の束 */
  | { readonly tag: 'layer'; readonly label: string; readonly entries: readonly MapEntry[] }
  /** 地図の dir(layer = その dir の層の束の名 — 違う層の同じ dir を別の節にするため) */
  | { readonly tag: 'dir'; readonly layer: string; readonly label: string; readonly prefix: string; readonly entries: readonly MapEntry[] }
  /** 地図の file(module) */
  | { readonly tag: 'module'; readonly layer: string; readonly entry: MapEntry }
  | { readonly tag: 'message'; readonly label: string };

/**
 * 節の固定の id — 同じ置き場から 2 度作った節は同じ id、違う節は違う id(1 つの木の中で重ならない)。VS Code の TreeView は
 * id の同じ節の展開と選択を出し直しの後も保つ(id が無いと、出し直すたびに全部畳まれ選択も消えた — agora-redesign #2162)。
 * 数や文の変わる欄(件数・前回からの増減・束の見出しの名)は入れない — 変わっても同じ節のまま。
 */
export function nodeId(node: LintNode): string {
  switch (node.tag) {
    case 'summary':
      return idOf('summary', [node.level]);
    case 'group':
      return idOf('group', [node.level, node.rule]);
    case 'file':
      return idOf('file', [node.level, node.rule, node.path]);
    case 'violation':
      return idOf('violation', [...violationKey(node.parentId, node.violation), node.occurrence]);
    case 'rule':
      return idOf('rule', [node.rule.rule]);
    case 'layer':
      return idOf('layer', [node.label]);
    case 'dir':
      return idOf('dir', [node.layer, node.prefix]);
    case 'module':
      // relative も入れる — 入れ子の workspace の folder では同じ file が 2 つの root の下に(違う relative で)出る
      return idOf('module', [node.layer, node.entry.relative, node.entry.module.path]);
    case 'message':
      return idOf('message', [node.label]);
    default: {
      const unreachable: never = node;
      throw new Error(`網羅されていない節: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 節の種類と欄から id を作る(欄の区切りは JSON の配列の綴り — path に `:` や `/` があっても別の欄と混ざらない)。 */
function idOf(tag: LintNode['tag'], parts: ReadonlyArray<string | number>): string {
  return JSON.stringify([tag, ...parts]);
}

/** 違反の節の id の欄のうち、何番目か(occurrence)の前まで — 親・規則・path・範囲・文の hash。 */
function violationKey(parentId: string, v: LintViolation): ReadonlyArray<string | number> {
  const { start, end } = v.range;
  return [parentId, v.rule, v.path, start.line, start.character, end.line, end.character, contentHash(v.message)];
}

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
    case 'summary':
      return node.counts.total;
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
 * 違反の木の最上段 — 重大さごとの要約の行(critical・major・minor・info — 絞り込みによらず全部の数)と、その下に (重大さ, 規則) ごとの束。
 * 手つかずの critical が何件残るかを一目で読むため、束は重い順 → 新しい分の多い順 → 件数の多い順 → ID の順に並べる。重大さは repo が
 * 規則ごとに宣言した物で、登録簿で下げない。見出しの名と家族は linter の規則の一覧から引く(拡張は写しを持たない)。
 * 違反が無ければ札、絞り込みで 0 になれば要約の下に札。
 */
export function violationRoots(
  violations: readonly LintViolation[],
  rules: readonly LintRule[],
  filter: PanelFilter = ALL_VIOLATIONS,
  previous?: SavedTally
): LintNode[] {
  if (violations.length === 0) {
    return [{ tag: 'message', label: 'linter の違反はありません' }];
  }
  const tally = levelTally(violations);
  const summaryRows: LintNode[] = LINT_LEVELS.map((level) => ({
    tag: 'summary',
    level,
    counts: tally[level],
    delta: newDelta(tally, previous, level)
  }));
  const shown = filterViolations(violations, filter);
  if (shown.length === 0) {
    return [...summaryRows, { tag: 'message', label: '絞り込みに当たる違反はありません' }];
  }
  const byKey = new Map<string, LintViolation[]>();
  for (const violation of shown) {
    pushTo(byKey, `${violation.level}\u0000${violation.rule}`, violation);
  }
  const summaries = ruleSummaries(rules);
  const groups = [...byKey.values()].map((list) => ({
    group: {
      tag: 'group' as const,
      level: list[0].level,
      rule: list[0].rule,
      summary: summaries.get(list[0].rule) ?? UNKNOWN_RULE,
      violations: list
    },
    fresh: list.filter((v) => v.standing === 'new').length
  }));
  groups.sort(
    (a, b) =>
      byLevel(a.group.level, b.group.level) ||
      b.fresh - a.fresh ||
      b.group.violations.length - a.group.violations.length ||
      a.group.rule.localeCompare(b.group.rule)
  );
  return [...summaryRows, ...groups.map((g) => g.group)];
}

/**
 * 違反の表の最上段 — root ごとの今の全体の実行の状態(running・measured・failed)を先に見る。実行中の root は
 * 「実行中」の札(agora-redesign #1650 — 起動直後と再実行の間、結果が無いだけで「違反はありません」と出ない
 * ようにする)、失敗した root は理由の札(#1631)を先頭に出す。実行中か失敗の root が 1 つでもあれば、violations
 * が 0 件でも「違反はありません」は出さない(前の結果が無ければ札だけ、前の結果があればその violations が summary
 * 行以下に出る — その violations は store.violations() が running・failed の previous からすでに拾っている)。
 * 「違反はありません」が出るのは、全 root が measured で violations が 0 件の時だけ。
 */
export function panelViolationRoots(
  runs: readonly RootRunEntry[],
  violations: readonly LintViolation[],
  rules: readonly LintRule[],
  filter: PanelFilter = ALL_VIOLATIONS,
  previous?: SavedTally
): LintNode[] {
  const header: LintNode[] = [];
  for (const { root, run } of runs) {
    switch (run.tag) {
      case 'running':
        header.push({ tag: 'message', label: `linter を実行中(${root})…` });
        break;
      case 'failed':
        header.push({ tag: 'message', label: `linter が失敗(${root}): ${run.reason}` });
        break;
      case 'measured':
        break;
      default: {
        const unreachable: never = run;
        throw new Error(`網羅されていない状態: ${JSON.stringify(unreachable)}`);
      }
    }
  }
  if (header.length > 0 && violations.length === 0) {
    return header;
  }
  return [...header, ...violationRoots(violations, rules, filter, previous)];
}

/** 束の見出し — 規則の ID と短い名(名の無い古い linter の出力は ID だけ)。 */
export function groupLabel(rule: string, summary: RuleSummary): string {
  return summary.title === null ? rule : `${rule} ${summary.title}`;
}

/** 束の行の見出し — 重大さの札と件数を先に(例: `CRITICAL 3 · DOEFF126 defk を素で呼んで答えに使う`)。 */
export function groupHeading(level: LintLevel, rule: string, summary: RuleSummary, count: number): string {
  return `${levelTag(level)} ${count} · ${groupLabel(rule, summary)}`;
}

/** 束の行の説明 — 新しい分・既知の分・照合中の内訳(例: `新しい 16 · 既知 303`)。 */
export function groupStanding(violations: readonly LintViolation[]): string {
  return standingText(standingCounts(violations));
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

/** dir の中身 — 直下の dir(名前の順)と直下の file(名前の順)。layer = この dir の層の束の名。 */
function dirChildren(layer: string, prefix: string, entries: readonly MapEntry[]): LintNode[] {
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
    layer,
    label: name,
    prefix: prefix === '' ? name : path.join(prefix, name),
    entries: subdirs.get(name) ?? []
  }));
  const modules: LintNode[] = files
    .sort((a, b) => a.relative.localeCompare(b.relative))
    .map((entry) => ({ tag: 'module', layer, entry }));
  return [...dirs, ...modules];
}

/** 違反を行の順の節にする(file の子・地図の file の子)。parentId = 親の節の id。 */
function violationsByLine(violations: readonly LintViolation[], parentId: string): LintNode[] {
  const seen = new Map<string, number>();
  return [...violations]
    .sort((a, b) => a.range.start.line - b.range.start.line || a.range.start.character - b.range.start.character)
    .map((violation) => {
      // 同じ親の下で id の欄(規則・範囲・文の hash)が同じ違反の何番目か — 文の hash が偶然重なっても id は重ならない
      const same = idOf('violation', violationKey(parentId, violation));
      const occurrence = seen.get(same) ?? 0;
      seen.set(same, occurrence + 1);
      return { tag: 'violation', violation, parentId, occurrence };
    });
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
        level: node.level,
        rule: node.rule,
        path: filePath,
        label: path.basename(filePath),
        violations: byFile.get(filePath) ?? []
      }));
    }
    case 'file':
      return violationsByLine(node.violations, nodeId(node));
    case 'module':
      return violationsByLine(node.entry.violations, nodeId(node));
    case 'layer':
      return dirChildren(node.label, '', node.entries);
    case 'dir':
      return dirChildren(node.layer, node.prefix, node.entries);
    case 'violation':
    case 'summary':
    case 'rule':
    case 'message':
      return [];
    default: {
      const unreachable: never = node;
      throw new Error(`網羅されていない節: ${JSON.stringify(unreachable)}`);
    }
  }
}

/**
 * 違反の表の中の、目印(規則の ID と位置)の違反の節までの道 — 束 → file → 違反(読む面の吹き出しの「show in violations」から
 * 表の項目を見せるため・agora-redesign #1685)。子は渡された口で引く(表が作った同じ節を VS Code の reveal に渡すため)。
 * 表に無ければ undefined(絞り込みで隠れた・規則の一覧を出している・linter を走らせ直して消えた)。
 */
export function violationTrail(
  roots: readonly LintNode[],
  ref: ViolationRef,
  childrenOf: (node: LintNode) => readonly LintNode[]
): readonly LintNode[] | undefined {
  const same = (v: LintViolation): boolean =>
    v.rule === ref.rule &&
    path.normalize(v.path) === path.normalize(ref.place.path) &&
    v.range.start.line === ref.place.start.line &&
    v.range.start.character === ref.place.start.character &&
    v.range.end.line === ref.place.end.line &&
    v.range.end.character === ref.place.end.character;
  for (const group of roots) {
    if (group.tag !== 'group' || group.rule !== ref.rule || !group.violations.some(same)) {
      continue;
    }
    for (const file of childrenOf(group)) {
      if (file.tag !== 'file' || path.normalize(file.path) !== path.normalize(ref.place.path)) {
        continue;
      }
      const hit = childrenOf(file).find((n) => n.tag === 'violation' && same(n.violation));
      if (hit !== undefined) {
        return [group, file, hit];
      }
    }
  }
  return undefined;
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
