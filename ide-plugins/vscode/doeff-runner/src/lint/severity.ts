// 違反の重大さの見せ方を決める純粋な関数 — 重大さ(critical・major・minor・info)ごと・立場(新しい・登録簿の既知・照合中)ごとの
// 数え、重大さと立場の絞り込み、前回からの増減、欄の見出しと状態バーの文。判定はしない(重大さと立場は linter の出力のまま —
// 重大さは repo が規則ごとに宣言し、登録簿で下げない)。VS Code には触らない。

import { LINT_LEVELS, type LintLevel, type LintStanding, type LintViolation } from './contract';

/** 重大さで絞る — critical だけ・major 以上・全部。 */
export const LEVEL_FILTERS = ['critical', 'major', 'all'] as const;
export type LevelFilter = (typeof LEVEL_FILTERS)[number];

/** 立場で絞る — 新しい分だけ・既知の分も。 */
export const STANDING_FILTERS = ['new', 'all'] as const;
export type StandingFilter = (typeof STANDING_FILTERS)[number];

/** 違反の欄の絞り込み。 */
export interface PanelFilter {
  readonly level: LevelFilter;
  readonly standing: StandingFilter;
}

/** 既定の絞り込み(全部・既知も)。 */
export const ALL_VIOLATIONS: PanelFilter = { level: 'all', standing: 'all' };

/** 重大さの順位(小さいほど重い)。 */
const RANK: Readonly<Record<LintLevel, number>> = { critical: 0, major: 1, minor: 2, info: 3 };

/** 重大さ 1 つの色(読む面の札の地と字・印の下線)。 */
export interface LevelColors {
  /** 札の地 */
  readonly background: string;
  /** 札の字 */
  readonly foreground: string;
  /** 定義の名・source の範囲に引く下線 */
  readonly underline: string;
}

/** 重大さの色の表 — 読む面の札・下線・file の帯は、この 1 つの表からだけ色を引く(面に色の表を増やさない・agora-redesign #1685)。 */
export const LEVEL_COLORS: Readonly<Record<LintLevel, LevelColors>> = {
  critical: { background: '#5a1d1d', foreground: '#ffb0b0', underline: '#f14c4c' },
  major: { background: '#5a3a1d', foreground: '#ffd0a0', underline: '#e5a03c' },
  minor: { background: '#3a3a1d', foreground: '#e6e0a0', underline: '#c9c26a' },
  info: { background: '#1d3a5a', foreground: '#a0c8ff', underline: '#5aa2ff' }
};

/** 重大さを比べる(重い方が先)— 違反の欄を critical から並べるため。 */
export function byLevel(a: LintLevel, b: LintLevel): number {
  return RANK[a] - RANK[b];
}

/** 違反が絞り込みを通るか(重大さは登録簿で下げない — 既知の critical も critical で数える)。 */
export function passes(violation: LintViolation, filter: PanelFilter): boolean {
  const levelOk = filter.level === 'all' || RANK[violation.level] <= RANK[filter.level];
  const standingOk = filter.standing === 'all' || violation.standing === 'new';
  return levelOk && standingOk;
}

/** 絞り込みを当てる。 */
export function filterViolations(violations: readonly LintViolation[], filter: PanelFilter): LintViolation[] {
  return violations.filter((v) => passes(v, filter));
}

/** 立場ごとの数。 */
export interface StandingCounts {
  readonly total: number;
  readonly new: number;
  readonly registered: number;
  readonly reconciling: number;
}

/** 重大さごとの立場の数。 */
export type LevelTally = Readonly<Record<LintLevel, StandingCounts>>;

/** 立場を数える — 束の行の内訳と要約の材料。 */
export function standingCounts(violations: readonly LintViolation[]): StandingCounts {
  const counts: Record<LintStanding, number> = { new: 0, registered: 0, reconciling: 0 };
  for (const v of violations) {
    counts[v.standing] += 1;
  }
  return { total: violations.length, ...counts };
}

/** 重大さごとに立場を数える — 欄の一番上の要約と状態バーの材料。 */
export function levelTally(violations: readonly LintViolation[]): LevelTally {
  const by: Record<LintLevel, LintViolation[]> = { critical: [], major: [], minor: [], info: [] };
  for (const v of violations) {
    by[v.level].push(v);
  }
  return { critical: standingCounts(by.critical), major: standingCounts(by.major), minor: standingCounts(by.minor), info: standingCounts(by.info) };
}

/** 覚えておく前回の数え(重大さごとの新しい分と全部)— workspace の状態に JSON で置く形。 */
export type SavedTally = Readonly<Record<LintLevel, { readonly new: number; readonly total: number }>>;

/** 数えを覚えておく形にする(次に開いた時の増減の元)。 */
export function saveTally(tally: LevelTally): SavedTally {
  const pick = (c: StandingCounts): { new: number; total: number } => ({ new: c.new, total: c.total });
  return { critical: pick(tally.critical), major: pick(tally.major), minor: pick(tally.minor), info: pick(tally.info) };
}

/** 覚えておいた値を読む — 形が違えば(古い版の拡張が置いた値など)undefined にして増減を出さない。 */
export function readSavedTally(value: unknown): SavedTally | undefined {
  if (typeof value !== 'object' || value === null) {
    return undefined;
  }
  const record = value as Record<string, unknown>;
  const read: Partial<Record<LintLevel, { new: number; total: number }>> = {};
  for (const level of LINT_LEVELS) {
    const entry = record[level];
    if (typeof entry !== 'object' || entry === null) {
      return undefined;
    }
    const n = (entry as Record<string, unknown>).new;
    const t = (entry as Record<string, unknown>).total;
    if (typeof n !== 'number' || typeof t !== 'number' || !Number.isInteger(n) || !Number.isInteger(t)) {
      return undefined;
    }
    read[level] = { new: n, total: t };
  }
  const { critical, major, minor, info } = read;
  return critical === undefined || major === undefined || minor === undefined || info === undefined ? undefined : { critical, major, minor, info };
}

/** 重大さ 1 つの前回からの増減(新しい分)。前回が無ければ undefined。 */
export function newDelta(tally: LevelTally, previous: SavedTally | undefined, level: LintLevel): number | undefined {
  return previous === undefined ? undefined : tally[level].new - previous[level].new;
}

/** 重大さの札(大文字 — 灯の色に頼らず文字で読めるように)。 */
export function levelTag(level: LintLevel): string {
  return level.toUpperCase();
}

/** 立場の内訳の文(例: `新しい 44 · 既知 308 · 照合中 15` — 照合中は 0 なら省く)。 */
export function standingText(counts: StandingCounts): string {
  const parts = [`新しい ${counts.new}`, `既知 ${counts.registered}`];
  if (counts.reconciling > 0) {
    parts.push(`照合中 ${counts.reconciling}`);
  }
  return parts.join(' · ');
}

/** 増減の文(例: `+3`・`-5`・`±0`)。 */
export function deltaText(delta: number): string {
  return delta > 0 ? `+${delta}` : delta < 0 ? `${delta}` : '±0';
}

/** 要約の行の見出し(例: `CRITICAL 452`)。 */
export function summaryLabel(level: LintLevel, counts: StandingCounts): string {
  return `${levelTag(level)} ${counts.total}`;
}

/** 要約の行の説明(例: `新しい 0 · 既知 452 · 前回から新しい ±0`)。 */
export function summaryDescription(counts: StandingCounts, delta: number | undefined): string {
  const tail = delta === undefined ? '' : ` · 前回から新しい ${deltaText(delta)}`;
  return `${standingText(counts)}${tail}`;
}

/** 絞り込みの短い文(欄の見出しの横に出す — 今どこまで見ているかを取り違えないため)。 */
export function filterText(filter: PanelFilter): string {
  const level = filter.level === 'critical' ? 'critical だけ' : filter.level === 'major' ? 'major 以上' : '全部の重大さ';
  const standing = filter.standing === 'new' ? '新しい分だけ' : '既知の分も';
  return `${level} · ${standing}`;
}

/** 重大さの絞り込みを次へ回す(全部 → critical だけ → major 以上 → 全部)— 欄の見出しの 1 つのボタンで切り替えるため。 */
export function nextLevelFilter(current: LevelFilter): LevelFilter {
  switch (current) {
    case 'all':
      return 'critical';
    case 'critical':
      return 'major';
    case 'major':
      return 'all';
    default: {
      const unreachable: never = current;
      throw new Error(`網羅されていない絞り込み: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 状態バーの中身。 */
export interface LevelStatus {
  readonly text: string;
  readonly tooltip: string;
  /** 新しい critical があるか(背景を error の色にする) */
  readonly alarming: boolean;
}

/**
 * 手つかずの critical が何件残るかを常に見せるための状態バーの文
 * (例: `$(doeff-lint-error) CRITICAL 452(新しい 0) · MAJOR 新しい 44`)。
 */
export function levelStatus(tally: LevelTally, previous: SavedTally | undefined): LevelStatus {
  const critical = tally.critical;
  const major = tally.major;
  const deltaOf = (level: LintLevel): string => {
    const d = newDelta(tally, previous, level);
    return d === undefined || d === 0 ? '' : ` ${deltaText(d)}`;
  };
  const lines = LINT_LEVELS.map((l) => `${summaryLabel(l, tally[l])} — ${summaryDescription(tally[l], newDelta(tally, previous, l))}`);
  return {
    text: `$(doeff-lint-error) CRITICAL ${critical.total}(新しい ${critical.new}${deltaOf('critical')}) · MAJOR 新しい ${major.new}${deltaOf('major')}`,
    tooltip: [
      'doeff-linter の違反(重大さは repo が規則ごとに宣言し、登録簿で下げない。既知 = 登録簿に載った分)',
      ...lines,
      ...(previous === undefined ? [] : ['前回 = この workspace で前に VS Code を開いていた時の最後の数']),
      '押すと違反の欄を critical だけにして開く'
    ].join('\n'),
    alarming: critical.new > 0
  };
}
