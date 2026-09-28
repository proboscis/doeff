// 定義を読む面のカードを 1 行に畳む状態と、1 行に出す欄の設定(v4 2 節・operator 2026-09-28 "i want to be able to fold a card
// to oneline form where i can toggle what to show so i can glance at")。純粋な関数だけ(VS Code に触らない)。
// 既定: カードは畳む(一目で見渡すため)・1 行に出すのは args / return type・effects・tags。面は VS Code の状態に覚える。

import type { HyDefinition, HyFileIndex } from '../hy/contract';

/** 1 行に出せる欄(v4 の表の順)。 */
export const LINE_FIELDS = ['args', 'effects', 'tags', 'doc', 'relations', 'location'] as const;
export type LineField = (typeof LINE_FIELDS)[number];

/** 1 行に出す欄の既定(v4 の表の「既定 = 出す」)。 */
export const DEFAULT_LINE_FIELDS: readonly LineField[] = ['args', 'effects', 'tags'];

/** 畳む状態 — 開いたカード(畳むのが既定なので開いた物だけ持つ)と、1 行に出す欄。 */
export interface FoldState {
  /** 開いたカードの鍵(cardKey) */
  readonly open: ReadonlySet<string>;
  readonly line: ReadonlySet<LineField>;
}

/** 何も覚えていない時の状態。 */
export const INITIAL_FOLD: FoldState = { open: new Set(), line: new Set(DEFAULT_LINE_FIELDS) };

/** カードの鍵 — 開き直しても同じカードを指すため、完全修飾名と頭の行で作る(同じ file の同名の再定義を分ける)。 */
export function cardKey(definition: HyDefinition): string {
  return `${definition.qualifiedName}@${definition.fullRange.start.line}`;
}

/** カード 1 枚の開閉を入れ替える。 */
export function toggleOpen(state: FoldState, key: string): FoldState {
  const open = new Set(state.open);
  if (open.has(key)) {
    open.delete(key);
  } else {
    open.add(key);
  }
  return { ...state, open };
}

/** 全部畳む。 */
export function foldAll(state: FoldState): FoldState {
  return { ...state, open: new Set() };
}

/** 全部開く(渡したカードの鍵の全部)。 */
export function unfoldAll(state: FoldState, keys: readonly string[]): FoldState {
  return { ...state, open: new Set([...state.open, ...keys]) };
}

/** 1 行に出す欄を入れ替える。 */
export function toggleLineField(state: FoldState, field: LineField): FoldState {
  const line = new Set(state.line);
  if (line.has(field)) {
    line.delete(field);
  } else {
    line.add(field);
  }
  return { ...state, line };
}

/** 欄の名を読む(知らない名は undefined)。 */
export function parseLineField(text: string): LineField | undefined {
  return LINE_FIELDS.find((f) => f === text);
}

/** VS Code の状態に書く形。 */
export interface SavedFold {
  readonly open: string[];
  readonly line: string[];
}

/** 状態を書く形にする。 */
export function saveFold(state: FoldState): SavedFold {
  return { open: [...state.open].sort(), line: LINE_FIELDS.filter((f) => state.line.has(f)) };
}

/** 覚えていた形を読む(形の違う値・知らない欄は捨てて既定へ — 拡張の版が変わっても面が開けるように)。 */
export function loadFold(saved: unknown): FoldState {
  if (typeof saved !== 'object' || saved === null || Array.isArray(saved)) {
    return INITIAL_FOLD;
  }
  const fields = new Map<string, unknown>(Object.entries(saved));
  const open = fields.get('open');
  const line = fields.get('line');
  const strings = (value: unknown): string[] => (Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : []);
  return {
    open: new Set(strings(open)),
    line: Array.isArray(line) ? new Set(strings(line).flatMap((t) => parseLineField(t) ?? [])) : new Set(DEFAULT_LINE_FIELDS)
  };
}

/** 定義の関係の数(1 行の callers / tests)— 呼び手の定義と、そのうち deftest の数。 */
export interface RelationCount {
  readonly callers: number;
  readonly tests: number;
}

/**
 * 索引の全 file の呼び出しを呼び先の完全修飾名で逆に引く表(U7 の決定: 逆引きは索引に持たず読む側で作る)。
 * 呼び手 = 呼びを含む一番内側の定義。同じ呼び手からの何度もの呼びは 1 つに数える。
 */
export function relationCounts(files: readonly HyFileIndex[]): ReadonlyMap<string, RelationCount> {
  const callers = new Map<string, Set<string>>();
  const tests = new Map<string, Set<string>>();
  for (const file of files) {
    for (const call of file.calls) {
      if (call.target === null || call.caller === null) {
        continue;
      }
      const caller = file.definitions[call.caller];
      if (caller === undefined) {
        continue;
      }
      const who = `${file.path}#${call.caller}`;
      const bucket = caller.kind === 'deftest' ? tests : callers;
      const seen = bucket.get(call.target) ?? new Set<string>();
      seen.add(who);
      bucket.set(call.target, seen);
    }
  }
  const counts = new Map<string, RelationCount>();
  for (const target of new Set([...callers.keys(), ...tests.keys()])) {
    counts.set(target, { callers: callers.get(target)?.size ?? 0, tests: tests.get(target)?.size ?? 0 });
  }
  return counts;
}
