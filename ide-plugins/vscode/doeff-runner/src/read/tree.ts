// 呼び出しの依存の木(v7 3 節・operator 2026-09-29 "it would be great if there's also call dependency tree visualization")の
// 純粋なモデル(VS Code に触らない)。材料は hy-index の全 file(版 4 の呼び出しの target・版 5 の宣言した effect と effect 節の
// handles)だけで、面が自分で file を歩かない(v1 制約 5)。逆引き(呼び手)は索引に持たず、ここで引く(U7 の決定)。
// 既定(席の既定・戻せる): 向きは callees・深さ 3・同じ実体の 2 度目は ↺ で開かない・deftest は隠す。

import type { HyDefinition, HyFileIndex, HyRange, HyTypeNote } from '../hy/contract';

/** 木の向き — 根が呼ぶ物を下へ(callees)か、根を呼ぶ物を上へ(callers)。 */
export type TreeDirection = 'callees' | 'callers';

/** 深さの既定。 */
export const DEFAULT_TREE_DEPTH = 3;

/** 索引の中の定義 1 つ(どの file の物か)。 */
export interface GraphDefinition {
  readonly definition: HyDefinition;
  readonly path: string;
}

/** 索引の全 file から作る呼び出しの表 — 完全修飾名で引く。 */
export interface CallGraph {
  readonly definitions: ReadonlyMap<string, GraphDefinition>;
  /** 呼び先(Hy の定義だけ・書いた順・重ねない) */
  readonly callees: ReadonlyMap<string, readonly string[]>;
  /** 呼び手(Hy の定義だけ・重ねない) */
  readonly callers: ReadonlyMap<string, readonly string[]>;
  /** effect の完全修飾名 → それを解く effect 節の数(handled by N) */
  readonly handlers: ReadonlyMap<string, number>;
  /** effect の完全修飾名 → それを解く defhandler の完全修飾名(帯の handlers の名) */
  readonly handlerDefinitions: ReadonlyMap<string, readonly string[]>;
  /** 型の完全修飾名 → その型を答えに書いた定義(returned by) */
  readonly returnedBy: ReadonlyMap<string, readonly string[]>;
  /** 型の完全修飾名 → その型を引数に書いた定義(accepted by) */
  readonly acceptedBy: ReadonlyMap<string, readonly string[]>;
  /** 型の完全修飾名 → その型を欄の型に書いた型と effect(field of — v9 の used by) */
  readonly fieldOf: ReadonlyMap<string, readonly string[]>;
}

/** 欄を持つ種類(索引の param_types が欄の型 — v9: defclass の `#^ T x` も defrecord と同じ読み手で載る)。 */
const FIELD_KINDS: ReadonlySet<HyDefinition['kind']> = new Set<HyDefinition['kind']>(['defrecord', 'deftype', 'defclass', 'defeffect']);

/** 位置が範囲に入るか。 */
function within(range: HyRange, line: number, character: number): boolean {
  const afterStart = line > range.start.line || (line === range.start.line && character >= range.start.character);
  const beforeEnd = line < range.end.line || (line === range.end.line && character <= range.end.character);
  return afterStart && beforeEnd;
}

/** 表に足す(重ねない・書いた順)。 */
function push(table: Map<string, string[]>, key: string, value: string): void {
  const list = table.get(key) ?? [];
  if (!list.includes(value)) {
    list.push(value);
  }
  table.set(key, list);
}

/**
 * 索引の全 file から呼び出しの表を作る。定義の呼び出し = その定義の範囲の中の呼び出しの全部(入れ子の定義の呼び出しも
 * 外側に数える — U7 の決定「外側で出すなら full_range の包含で足す」)。呼び先が Hy の定義でない呼び(Python の関数など)は数えない。
 * 同じ完全修飾名の定義が複数(同じ file の再定義)なら最初の 1 つを使う。
 */
export function buildCallGraph(files: readonly HyFileIndex[]): CallGraph {
  const definitions = new Map<string, GraphDefinition>();
  const handlers = new Map<string, number>();
  const returnedBy = new Map<string, string[]>();
  const handlerDefinitions = new Map<string, string[]>();
  const acceptedBy = new Map<string, string[]>();
  const fieldOf = new Map<string, string[]>();
  for (const file of files) {
    for (const definition of file.definitions) {
      if (!definitions.has(definition.qualifiedName)) {
        definitions.set(definition.qualifiedName, { definition, path: file.path });
      }
      const handled = definition.handles?.target ?? null;
      if (handled !== null) {
        handlers.set(handled, (handlers.get(handled) ?? 0) + 1);
        const owner = file.definitions.find((d) => d.kind === 'defhandler' && d.name === definition.container);
        if (owner !== undefined) {
          push(handlerDefinitions, handled, owner.qualifiedName);
        }
      }
      // 型の逆引きは関数の見出し(defk など)だけから — 欄の型(defrecord の #^)は「受ける」ではないため
      if (TREE_KINDS.has(definition.kind) && definition.kind !== 'defeffect') {
        for (const name of definition.answerType?.names ?? []) {
          if (name.target !== null) {
            push(returnedBy, name.target, definition.qualifiedName);
          }
        }
        for (const param of definition.paramTypes) {
          for (const name of param.type.names) {
            if (name.target !== null) {
              push(acceptedBy, name.target, definition.qualifiedName);
            }
          }
        }
      }
      if (FIELD_KINDS.has(definition.kind)) {
        for (const field of definition.paramTypes) {
          for (const name of field.type.names) {
            if (name.target !== null && name.target !== definition.qualifiedName) {
              push(fieldOf, name.target, definition.qualifiedName);
            }
          }
        }
      }
    }
  }
  const callees = new Map<string, string[]>();
  const callers = new Map<string, string[]>();
  for (const file of files) {
    const top = file.definitions
      .filter((d) => d.container === null)
      .slice()
      .sort((a, b) => a.fullRange.start.line - b.fullRange.start.line || a.fullRange.start.character - b.fullRange.start.character);
    for (const call of file.calls) {
      if (call.target === null || !definitions.has(call.target)) {
        continue;
      }
      const owner = ownerOf(top, call.range.start.line, call.range.start.character);
      if (owner !== undefined && owner.qualifiedName !== call.target) {
        push(callees, owner.qualifiedName, call.target);
        push(callers, call.target, owner.qualifiedName);
      }
    }
  }
  return { definitions, callees, callers, handlers, handlerDefinitions, returnedBy, acceptedBy, fieldOf };
}

/** 位置を含む最上位の定義(位置の順に並べた列を二分探索 — 大きな repo でも呼びごとに全定義を回さないため)。 */
function ownerOf(sortedTop: readonly HyDefinition[], line: number, character: number): HyDefinition | undefined {
  let lo = 0;
  let hi = sortedTop.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const start = sortedTop[mid].fullRange.start;
    if (start.line < line || (start.line === line && start.character <= character)) {
      found = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  const candidate = found < 0 ? undefined : sortedTop[found];
  return candidate !== undefined && within(candidate.fullRange, line, character) ? candidate : undefined;
}

/**
 * 木の節にする定義の種類 — 関数と effect と handler と test(見本 artifacts/v7/call-tree.html の通り。defrecord などの型の
 * 構築は呼びでも節にしない — 木が型の生成で埋まって呼び出しの流れが読めなくなるため。席の既定・戻せる)。
 */
const TREE_KINDS: ReadonlySet<HyDefinition['kind']> = new Set<HyDefinition['kind']>([
  'defk',
  'deff',
  'defn',
  'defn/a',
  'defp',
  'defpp',
  'defeffect',
  'defhandler',
  'deftest'
]);

/** 木の節にする定義か。 */
function isTreeKind(graph: CallGraph, qualifiedName: string): boolean {
  const found = graph.definitions.get(qualifiedName);
  return found !== undefined && TREE_KINDS.has(found.definition.kind);
}

/** 木の節にする種類の呼び先(帯の callees と木で同じ物を数えるため)。 */
export function calleesInTree(graph: CallGraph, qualifiedName: string): string[] {
  return (graph.callees.get(qualifiedName) ?? []).filter((qn) => isTreeKind(graph, qn));
}

/** 定義の関係の数(カードの帯と 1 行の callers / tests)。 */
export interface RelationCount {
  /** 呼び手(deftest を除く) */
  readonly callers: number;
  readonly callees: number;
  /** 呼び手のうち deftest */
  readonly tests: number;
}

/** 定義の関係の数を呼び出しの表から数える(カードの帯と木が同じ表を使うため)。 */
export function relationOf(graph: CallGraph, qualifiedName: string): RelationCount {
  const callers = graph.callers.get(qualifiedName) ?? [];
  const tests = callers.filter((qn) => graph.definitions.get(qn)?.definition.kind === 'deftest').length;
  return { callers: callers.length - tests, callees: calleesInTree(graph, qualifiedName).length, tests };
}

/** 木の節 1 つ。 */
export interface TreeNode {
  readonly qualifiedName: string;
  readonly definition: HyDefinition;
  readonly path: string;
  /** その向きの隣の数(callees N / callers N — 畳んだ節でも見える) */
  readonly count: number;
  /** 同じ実体の 2 度目('repeat')・祖先に同じ実体がある 2 度目('cycle')は開かない */
  readonly seen: 'first' | 'repeat' | 'cycle';
  /** 深さの上限で切った(`+` で広げられる) */
  readonly truncated: boolean;
  readonly children: readonly TreeNode[];
}

/** 木の全体と、根の下に出す数え。 */
export interface CallTree {
  readonly root: TreeNode;
  readonly direction: TreeDirection;
  readonly depth: number;
  /** この木の下で使う effect の和(宣言した effect の名・出た順) */
  readonly effects: readonly string[];
  readonly nodes: number;
  readonly repeats: number;
  readonly cycles: number;
}

/** 木を作る条件。 */
export interface TreeQuery {
  readonly root: string;
  readonly direction: TreeDirection;
  readonly depth: number;
  readonly showTests: boolean;
}

/** 根から木を作る(根が索引に無ければ undefined)。 */
export function buildCallTree(graph: CallGraph, query: TreeQuery): CallTree | undefined {
  const rootDef = graph.definitions.get(query.root);
  if (rootDef === undefined) {
    return undefined;
  }
  const neighbours = query.direction === 'callees' ? graph.callees : graph.callers;
  const visible = (qn: string): boolean =>
    isTreeKind(graph, qn) && (query.showTests || graph.definitions.get(qn)?.definition.kind !== 'deftest');
  const seen = new Set<string>();
  const effects: string[] = [];
  let nodes = 0;
  let repeats = 0;
  let cycles = 0;
  const visit = (qn: string, level: number, ancestors: ReadonlySet<string>): TreeNode => {
    const found = graph.definitions.get(qn);
    if (found === undefined) {
      throw new Error(`木の節が索引に無い: ${qn}`);
    }
    nodes += 1;
    const next = (neighbours.get(qn) ?? []).filter(visible);
    const base = { qualifiedName: qn, definition: found.definition, path: found.path, count: next.length };
    if (seen.has(qn)) {
      const cycle = ancestors.has(qn);
      if (cycle) {
        cycles += 1;
      } else {
        repeats += 1;
      }
      return { ...base, seen: cycle ? 'cycle' : 'repeat', truncated: false, children: [] };
    }
    seen.add(qn);
    for (const effect of found.definition.effects ?? []) {
      if (!effects.includes(effect.name)) {
        effects.push(effect.name);
      }
    }
    if (found.definition.kind === 'defeffect' && !effects.includes(found.definition.name)) {
      effects.push(found.definition.name);
    }
    // 深さ d = 根から d 段下までの節を開き、その 1 段下の節は畳んだまま見せる(見本の深さ 3 と同じ数え方)
    if (level > query.depth) {
      return { ...base, seen: 'first', truncated: next.length > 0, children: [] };
    }
    const inner = new Set(ancestors).add(qn);
    return { ...base, seen: 'first', truncated: false, children: next.map((child) => visit(child, level + 1, inner)) };
  };
  const root = visit(query.root, 0, new Set());
  return { root, direction: query.direction, depth: query.depth, effects, nodes, repeats, cycles };
}

/** 型の綴りの読み — 名・呼びの形 `( … )`・tuple `#( … )`・list `[ … ]`。 */
type TypeForm =
  | { readonly tag: 'atom'; readonly text: string }
  | { readonly tag: 'call' | 'tuple' | 'list'; readonly items: readonly TypeForm[] };

/** 型の綴りを字の並びに切る(文字列の literal は 1 つの字)。 */
function typeTokens(text: string): string[] {
  return text.match(/#\(|[()[\]]|"(?:[^"\\]|\\.)*"|[^\s()[\]"]+/g) ?? [];
}

/** 字の並びから型の形を 1 つ読む(読めなければ undefined — 書かれたままに戻すため)。 */
function readTypeForm(tokens: readonly string[], at: number): { readonly form: TypeForm; readonly next: number } | undefined {
  const token = tokens[at];
  if (token === undefined || token === ')' || token === ']') {
    return undefined;
  }
  if (token !== '(' && token !== '#(' && token !== '[') {
    return { form: { tag: 'atom', text: token }, next: at + 1 };
  }
  const close = token === '[' ? ']' : ')';
  const items: TypeForm[] = [];
  let i = at + 1;
  while (tokens[i] !== close) {
    const item = readTypeForm(tokens, i);
    if (item === undefined) {
      return undefined;
    }
    items.push(item.form);
    i = item.next;
  }
  return { form: { tag: token === '(' ? 'call' : token === '#(' ? 'tuple' : 'list', items }, next: i + 1 };
}

/** 型の形を Python の型の書き方へ(描けない形なら undefined)。 */
function typeFormText(form: TypeForm): string | undefined {
  const all = (items: readonly TypeForm[]): string[] | undefined => {
    const texts = items.map(typeFormText);
    return texts.every((t): t is string => t !== undefined) ? texts : undefined;
  };
  switch (form.tag) {
    case 'atom':
      return form.text;
    case 'list':
      return all(form.items)?.join(', ').replace(/^/, '[').concat(']');
    case 'tuple':
      return all(form.items)?.join(', ').replace(/^/, '(').concat(')');
    case 'call': {
      const [head, ...args] = form.items;
      if (head?.tag === 'atom' && head.text === '|' && args.length > 0) {
        return all(args)?.join(' | ');
      }
      // (get T X) = T[X]、(get T #(A B)) = T[A, B]。添字が 2 つ以上の (get T A B) は Hy では T[A][B] なので型としては読まない
      if (head?.tag === 'atom' && head.text === 'get' && args.length === 2) {
        const base = typeFormText(args[0]);
        const index = args[1].tag === 'tuple' ? all(args[1].items)?.join(', ') : typeFormText(args[1]);
        return base === undefined || index === undefined ? undefined : `${base}[${index}]`;
      }
      return undefined;
    }
    default: {
      const unreachable: never = form;
      throw new Error(`網羅されていない型の形: ${JSON.stringify(unreachable)}`);
    }
  }
}

/**
 * 索引の型の綴りを 1 行に出す形 — Hy の型の書き方を Python の書き方へ: union `(| A B)` は `A | B`、添字 `(get dict #(str X))` は
 * `dict[str, X]`、`[A B]` は `[A, B]`。描けない形は書かれたまま(型の読み方の正本は linter。木の節と欄は linter の見出しが無い
 * 定義も出すので、よくある形だけを直す — 決定の記録は #910)。
 */
export function indexTypeText(note: HyTypeNote | null): string {
  if (note === null) {
    return '?';
  }
  const form = typeFormOf(note);
  const text = form === undefined ? undefined : typeFormText(form);
  return text ?? note.text;
}

/** 型の綴り全体を 1 つの形として読む(余りがあれば読めない)。 */
function typeFormOf(note: HyTypeNote): TypeForm | undefined {
  const tokens = typeTokens(note.text.trim());
  const read = readTypeForm(tokens, 0);
  return read === undefined || read.next !== tokens.length ? undefined : read.form;
}

/** 型の union の候補ごとの綴り(縦の表で候補ごとのチップにする — union でなければ 1 つ)。 */
export function indexUnionMembers(note: HyTypeNote): string[] {
  const form = typeFormOf(note);
  if (form !== undefined && form.tag === 'call' && form.items[0]?.tag === 'atom' && form.items[0].text === '|') {
    const members = form.items.slice(1).map(typeFormText);
    if (members.length > 0 && members.every((m): m is string => m !== undefined)) {
      return members;
    }
  }
  return [indexTypeText(note)];
}
