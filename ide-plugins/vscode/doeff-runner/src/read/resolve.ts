// 実体の名 → 定義の解決(v12・operator 2026-09-29 "jump to definition by clicking each entities like effect/class/record etc from
// reading view and the source view... every location of plugin")の純粋な関数(VS Code に触らない)。読む面の中で実体の名を
// 描く場所は全部ここを通す — 場所ごとに別の解決を書かないため(agora-redesign #910 U19)。
// 解き方の順: 1. linter の definition(path と名の範囲)→ 2. 索引の target(呼び出し・型の名・effect の名の完全修飾名)→
// 3. 名だけ(索引の最上位の定義の名で引く — 候補が複数なら全部返し、押した時に選ばせる)。面が自分で file を歩かない(v1 制約 5)。

import type { HyNameRef, HyRange } from '../hy/contract';
import type { LintLocation, LintTypeRef } from '../lint/contract';
import { escapeHtml } from './html';
import type { CallGraph } from './tree';

/** 名 1 つの手がかり — 何で引けるか。 */
export type EntityRef =
  /** linter が解いた定義の位置(null = 組み込みか解けない名 — 名では引かない) */
  | { readonly tag: 'location'; readonly location: LintLocation | null }
  /** 索引が解いた完全修飾名(null = 解けない名 — 名では引かない) */
  | { readonly tag: 'target'; readonly target: string | null }
  /** その位置の呼び出しの頭の記号(索引の calls の target で引く — linter が位置を解かなかった呼び・effect) */
  | { readonly tag: 'call-at'; readonly path: string; readonly line: number; readonly character: number }
  /** 名だけ(linter も索引も位置を持たない名 — bases など。最上位の定義の名で引く) */
  | { readonly tag: 'name'; readonly name: string }
  /** effect の名だけ(同名の defeffect で引く — effect の札に他の種類の同名の定義を出さない) */
  | { readonly tag: 'effect-name'; readonly name: string }
  /** 手がかりを順に試し、最初に当たった物(linter の位置 → 索引の target → 名の順に並べて渡す) */
  | { readonly tag: 'first'; readonly refs: readonly EntityRef[] };

/** 定義の名の範囲の頭の位置の鍵(linter の definition の path と範囲を索引の定義に当てるため)。 */
export function locationKey(filePath: string, line: number, character: number): string {
  return `${filePath}\u0000${line}:${character}`;
}

/** dotted な名の最後の区切り(`models.Row` の `Row` — 名で引く時)。 */
function lastPart(name: string): string {
  const parts = name.split('.');
  return parts[parts.length - 1];
}

/**
 * 名の手がかりを定義の完全修飾名の候補にする。空 = 押せない(組み込み・repo の外・解けない名)、1 つ = その定義、
 * 複数 = 同名の定義が複数(押した時に選ばせる)。
 */
export function resolveEntity(ref: EntityRef, graph: CallGraph): readonly string[] {
  switch (ref.tag) {
    case 'location': {
      if (ref.location === null) {
        return [];
      }
      const { path, range } = ref.location;
      const found = graph.locations.get(locationKey(path, range.start.line, range.start.character));
      return found === undefined ? [] : [found];
    }
    case 'target':
      return ref.target !== null && graph.definitions.has(ref.target) ? [ref.target] : [];
    case 'call-at': {
      const found = graph.callTargets.get(locationKey(ref.path, ref.line, ref.character));
      return found === undefined ? [] : [found];
    }
    case 'name':
      return graph.byName.get(lastPart(ref.name)) ?? [];
    case 'effect-name':
      return graph.effectsByName.get(lastPart(ref.name)) ?? [];
    case 'first': {
      for (const inner of ref.refs) {
        const found = resolveEntity(inner, graph);
        if (found.length > 0) {
          return found;
        }
      }
      return [];
    }
    default: {
      const unreachable: never = ref;
      throw new Error(`網羅されていない手がかり: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** effect の名の手がかり — 位置か target で引け、引けなければ同名の defeffect(名だけで引く時も effect に限る)。 */
export function effectRef(name: string, known: EntityRef): EntityRef {
  return { tag: 'first', refs: [known, { tag: 'effect-name', name }] };
}

/** 索引の名(書いた名と完全修飾名)の手がかり。 */
export function nameRefOf(ref: HyNameRef): EntityRef {
  return { tag: 'target', target: ref.target };
}

/**
 * 実体の名を描く唯一の関数 — 定義に当たれば押せる印(`data-reveal` に候補の完全修飾名を空白で区切って)を付け、当たらなければ
 * 今の見た目のまま。inner は描いた HTML(逃がし済み)。押した時の動き(素の click はカード・Cmd / Ctrl は editor)は頁の script と面が持つ。
 */
export function entityLink(inner: string, candidates: readonly string[], cls = '', title?: string): string {
  const titleAttr = title === undefined ? '' : ` title="${escapeHtml(title)}"`;
  if (candidates.length === 0) {
    return cls === '' && title === undefined ? inner : `<span${cls === '' ? '' : ` class="${cls}"`}${titleAttr}>${inner}</span>`;
  }
  const classes = cls === '' ? 'ent' : `${cls} ent`;
  return `<span class="${classes}" data-reveal="${escapeHtml(candidates.join(' '))}"${titleAttr}>${inner}</span>`;
}

/** linter の型の式を描く(typeText と同じ綴り — 名の型は定義に当たれば押せる)。 */
export function typeHtml(type: LintTypeRef | null, graph: CallGraph): string {
  if (type === null) {
    return '?';
  }
  switch (type.kind) {
    case 'name':
      return entityLink(escapeHtml(type.name), resolveEntity({ tag: 'location', location: type.definition }, graph));
    case 'union':
      return type.members.map((m) => typeHtml(m, graph)).join(' | ');
    case 'apply':
      return `${typeHtml(type.head, graph)}[${type.args.map((a) => typeHtml(a, graph)).join(', ')}]`;
    case 'unknown':
      return escapeHtml(type.text);
    default: {
      const unreachable: never = type;
      throw new Error(`網羅されていない型の式: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 型の綴りの中の名(Hy の識別子と dotted な名)。 */
const TYPE_NAME = /([A-Za-z_][\w\-?!*.]*)/;

/**
 * 索引の型の綴り(indexTypeText で直した文字)を描く — 綴りの中の名のうち、索引の型の注記の名(書いた名と target)に当たる物を押せる
 * ようにする。注記に無い名(組み込み・解けない名)は文字のまま。
 */
export function indexTypeHtml(text: string, names: readonly HyNameRef[], graph: CallGraph): string {
  const targets = new Map(names.map((n) => [n.name, n.target]));
  return text
    .split(TYPE_NAME)
    .map((piece, i) => {
      const target = i % 2 === 1 ? targets.get(piece) : undefined;
      return target === undefined ? escapeHtml(piece) : entityLink(escapeHtml(piece), resolveEntity({ tag: 'target', target }, graph));
    })
    .join('');
}

/** 押した名 1 つの行き先 — その定義のカード(入れ子の定義は入れ物のカードのその行)か、text editor の定義の名の位置。 */
export type EntityDestination =
  | { readonly tag: 'card'; readonly path: string; readonly qualifiedName: string; readonly line: number | undefined }
  | { readonly tag: 'editor'; readonly path: string; readonly range: HyRange };

/**
 * 候補から選んだ定義 1 つの行き先(素の click はカード、Cmd / Ctrl + click は editor — v12)。入れ子の定義(method・欄・
 * effect 節)は入れ物のカードへ行き、その定義の行を光らせる。索引に無い名は undefined。
 */
export function destinationOf(graph: CallGraph, qualifiedName: string, editor: boolean): EntityDestination | undefined {
  const found = graph.definitions.get(qualifiedName);
  if (found === undefined) {
    return undefined;
  }
  if (editor) {
    return { tag: 'editor', path: found.path, range: found.definition.range };
  }
  const owner = graph.owners.get(qualifiedName);
  return owner === undefined
    ? { tag: 'card', path: found.path, qualifiedName, line: undefined }
    : { tag: 'card', path: found.path, qualifiedName: owner, line: found.definition.fullRange.start.line };
}
