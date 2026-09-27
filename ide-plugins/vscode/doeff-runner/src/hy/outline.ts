// 目次(DocumentSymbol)・workspace の記号の検索・hover の中身を、索引から VS Code に依らない形で作る。
// VS Code の型への写しは providers.ts が 1 か所で行う。

import type { HyDefinition, HyDefinitionKind, HyFileIndex, HyRange } from './contract';
import type { HyIndexView } from './store';

/** 目次で使う記号の種類(VS Code の SymbolKind のうち使う物だけの閉じた集合)。 */
export type OutlineSymbolKind =
  | 'Function'
  | 'Method'
  | 'Class'
  | 'Enum'
  | 'EnumMember'
  | 'Field'
  | 'Object'
  | 'Event'
  | 'Struct'
  | 'Variable'
  | 'Module'
  | 'Namespace'
  | 'Property'
  | 'Constant'
  | 'TypeParameter';

/** 検査の網羅を compiler に確かめさせるための行き止まり。 */
export function assertNever(value: never): never {
  throw new Error(`網羅されていない値: ${JSON.stringify(value)}`);
}

/** 契約の kind を目次の記号の種類へ対応づける(kind の一覧が増えたら compiler が赤にする)。 */
export function outlineKindOf(kind: HyDefinitionKind): OutlineSymbolKind {
  switch (kind) {
    case 'defn':
    case 'defn/a':
    case 'defk':
    case 'deff':
    case 'defp':
    case 'defpp':
    case 'fnk-binding':
    case 'defmacro':
    case 'deftest':
    case 'defmain':
    case 'defmcp-tool':
      return 'Function';
    case 'method':
      return 'Method';
    case 'defclass':
    case 'defrecord':
      return 'Class';
    case 'deftype':
      return 'TypeParameter';
    case 'defenum':
      return 'Enum';
    case 'enum-member':
      return 'EnumMember';
    case 'field':
      return 'Field';
    case 'defhandler':
      return 'Object';
    case 'defeffect':
      return 'Struct';
    case 'effect-clause':
      return 'Event';
    case 'variable':
      return 'Variable';
    case 'defadr':
      return 'Module';
    case 'defpipeline':
    case 'defworkflow':
    case 'defphase':
      return 'Namespace';
    case 'law':
      return 'Property';
    case 'defsemgrep':
      return 'Constant';
    default:
      return assertNever(kind);
  }
}

/** 目次の横に出す説明 — kind と引数(deftest は "test")。 */
export function definitionDetail(def: HyDefinition): string {
  const label = def.kind === 'deftest' ? 'test' : def.kind;
  return def.params.length > 0 ? `${label} [${def.params.join(' ')}]` : label;
}

/** 目次の 1 項目(入れ子を持つ)。 */
export interface OutlineNode {
  readonly name: string;
  readonly detail: string;
  readonly kind: OutlineSymbolKind;
  /** form 全体(VS Code の range) */
  readonly range: HyRange;
  /** 名前(VS Code の selectionRange — range の内側に収まる) */
  readonly selectionRange: HyRange;
  readonly children: OutlineNode[];
}

/** 位置 a が b より前(か同じ)かを見る。 */
function beforeOrEqual(a: HyRange['start'], b: HyRange['start']): boolean {
  return a.line < b.line || (a.line === b.line && a.character <= b.character);
}

/** outer が inner を含むかを見る。 */
function contains(outer: HyRange, inner: HyRange): boolean {
  return beforeOrEqual(outer.start, inner.start) && beforeOrEqual(inner.end, outer.end);
}

/**
 * file の目次 — definitions を container の名前で入れ子にする。入れ物が同じ file に無い項目は top level に置く。
 * VS Code は selectionRange が range の内側でないと項目を拒むので、full_range が名前を含まない時は名前の範囲を使う。
 */
export function buildOutline(file: HyFileIndex): OutlineNode[] {
  const roots: OutlineNode[] = [];
  const topByName = new Map<string, OutlineNode>();
  const nodes = file.definitions.map((def) => {
    const node: OutlineNode = {
      name: def.name,
      detail: definitionDetail(def),
      kind: outlineKindOf(def.kind),
      range: contains(def.fullRange, def.range) ? def.fullRange : def.range,
      selectionRange: def.range,
      children: []
    };
    if (def.container === null && !topByName.has(def.name)) {
      topByName.set(def.name, node);
    }
    return { def, node };
  });
  for (const { def, node } of nodes) {
    const parent = def.container === null ? undefined : topByName.get(def.container);
    if (parent !== undefined && parent !== node) {
      parent.children.push(node);
    } else {
      roots.push(node);
    }
  }
  return roots;
}

/** workspace の記号の検索の 1 件。 */
export interface WorkspaceSymbolHit {
  readonly path: string;
  readonly definition: HyDefinition;
}

/** query の文字が順に名前に現れるか(大文字小文字を見ない)を見る — VS Code の記号検索と同じ緩さ。 */
export function fuzzyContains(nameLower: string, queryLower: string): boolean {
  let i = 0;
  for (const ch of nameLower) {
    if (i < queryLower.length && ch === queryLower[i]) {
      i += 1;
    }
  }
  return i === queryLower.length;
}

/** 全定義から query に合う物を集める(名前か mangled のどちらかに合えばよい)。 */
export function searchWorkspaceSymbols(index: HyIndexView, query: string, limit: number): WorkspaceSymbolHit[] {
  const q = query.toLowerCase().replace(/\s+/g, '');
  const hits: WorkspaceSymbolHit[] = [];
  for (const entry of index.entries()) {
    for (const def of entry.file.definitions) {
      if (fuzzyContains(def.name.toLowerCase(), q) || fuzzyContains(def.mangled.toLowerCase(), q)) {
        hits.push({ path: entry.file.path, definition: def });
        if (hits.length >= limit) {
          return hits;
        }
      }
    }
  }
  return hits;
}

/** hover に出す Markdown — kind・名前・引数・入れ物・module・docstring。 */
export function hoverMarkdown(def: HyDefinition, module: string): string {
  const params = def.params.length > 0 ? ` [${def.params.join(' ')}]` : '';
  const owner = def.container !== null ? `${def.container} の ` : '';
  const lines = ['```hy', `(${def.kind} ${def.name}${params})`, '```', `${owner}\`${module}\``];
  if (def.docstring !== null && def.docstring.trim() !== '') {
    lines.push('', def.docstring);
  }
  return lines.join('\n');
}
