// 呼び出し階層の中身 — 定義から出ていく呼び出し(行き先ごと)と、定義へ入ってくる呼び出し(呼び出し元ごと)。
// 行き先は定義へ移動と同じ resolveDefinition で決める(解決の論理を 2 つ持たない)。effect のクラスからは
// その effect を扱う handler の節へ降りられるようにする。VS Code には触らない。

import type { HyRange } from './contract';
import { defKey, type CallSite, type DefRef, type EffectGraph } from './effects';
import type { DefinitionResolution, SymbolQuery } from './resolve';

/** 解決の口 — 名前と修飾から行き先を決める(実物は resolveDefinition に索引と Python と外の口を束ねた物)。 */
export type Resolve = (query: SymbolQuery) => Promise<DefinitionResolution>;

/** 呼び出しの行き先。 */
export type CallTarget =
  | { readonly tag: 'definition'; readonly ref: DefRef }
  | { readonly tag: 'python'; readonly path: string; readonly module: string; readonly name: string; readonly range: HyRange }
  /** 定義の見えない effect(外の未索引の package 等)— 撃つ場所を項目の位置にし、名前で handler の節へ降りる */
  | { readonly tag: 'effect-name'; readonly mangled: string; readonly name: string; readonly site: CallSite };

/** 出ていく呼び出しの 1 行き先と、呼んでいる位置。 */
export interface OutgoingGroup {
  readonly target: CallTarget;
  readonly fromRanges: HyRange[];
  /** effect の生成(撃つ・クラスの生成)か、effect から handler の節へ降りる行 */
  readonly effect: boolean;
}

/** 呼び出し元 — 定義か、file の top level。 */
export type CallerScope =
  | { readonly tag: 'definition'; readonly ref: DefRef }
  | { readonly tag: 'top-level'; readonly path: string; readonly module: string };

/** 入ってくる呼び出しの 1 呼び出し元と、呼んでいる位置。 */
export interface IncomingGroup {
  readonly from: CallerScope;
  readonly fromRanges: HyRange[];
}

/** 行き先を束ねるキー。 */
export function targetKey(target: CallTarget): string {
  switch (target.tag) {
    case 'definition':
      return defKey(target.ref);
    case 'python':
      return `py:${target.path}:${target.range.start.line}:${target.range.start.character}`;
    case 'effect-name':
      return `effect:${target.mangled}`;
    default: {
      const unreachable: never = target;
      throw new Error(`網羅されていない行き先: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 呼び出し 1 件の行き先を解決する(effect で行き先が見えない時は名前の行き先にする)。 */
export async function resolveCall(graph: EffectGraph, resolve: Resolve, site: CallSite): Promise<CallTarget[]> {
  const resolution = await resolve({ filePath: site.path, name: site.call.callee, qualifier: site.call.qualifier });
  const targets: CallTarget[] = [];
  for (const target of resolution.targets) {
    switch (target.tag) {
      case 'hy-definition': {
        const ref = graph.refOf(target.path, target.definition);
        if (ref !== undefined) {
          targets.push({ tag: 'definition', ref });
        }
        break;
      }
      case 'python-definition':
        targets.push({ tag: 'python', path: target.path, module: target.module, name: site.call.callee, range: target.range });
        break;
      case 'hy-module':
      case 'python-module':
        break; // module そのものは呼び出しの行き先にしない
      default: {
        const unreachable: never = target;
        throw new Error(`網羅されていない行き先: ${JSON.stringify(unreachable)}`);
      }
    }
  }
  if (targets.length === 0 && graph.isEffectCall(site.call)) {
    targets.push({ tag: 'effect-name', mangled: site.call.mangled, name: site.call.callee, site });
  }
  return targets;
}

/** 行き先ごとに位置を束ねる。 */
function addOutgoing(groups: Map<string, OutgoingGroup>, target: CallTarget, range: HyRange, effect: boolean): void {
  const key = targetKey(target);
  const known = groups.get(key);
  if (known === undefined) {
    groups.set(key, { target, fromRanges: [range], effect });
  } else {
    known.fromRanges.push(range);
  }
}

/** effect の名前から、それを扱う handler の節への行(effect → handler へ降りる)。 */
function clauseGroups(graph: EffectGraph, mangled: string, from: HyRange): OutgoingGroup[] {
  return graph.clausesFor(mangled).map((clause) => ({
    target: { tag: 'definition', ref: clause },
    fromRanges: [from],
    effect: true
  }));
}

/**
 * 項目から出ていく呼び出し。effect のクラス(か名前だけの effect)ならそれを扱う節、
 * handler なら自分の中の呼び出しと自分の節、それ以外は自分を caller に持つ呼び出しを行き先ごとに束ねる。
 */
export async function outgoingCalls(graph: EffectGraph, resolve: Resolve, item: CallTarget): Promise<OutgoingGroup[]> {
  switch (item.tag) {
    case 'python':
      return [];
    case 'effect-name':
      return clauseGroups(graph, item.mangled, item.site.call.range);
    case 'definition':
      break;
    default: {
      const unreachable: never = item;
      throw new Error(`網羅されていない項目: ${JSON.stringify(unreachable)}`);
    }
  }
  const ref = item.ref;
  if (graph.isEffectClass(ref)) {
    return clauseGroups(graph, ref.definition.mangled, ref.definition.range);
  }
  const groups = new Map<string, OutgoingGroup>();
  for (const site of graph.callsFrom(ref)) {
    const effect = graph.isEffectCall(site.call);
    for (const target of await resolveCall(graph, resolve, site)) {
      addOutgoing(groups, target, site.call.range, effect);
    }
  }
  if (ref.definition.kind === 'defhandler') {
    for (const clause of graph.handlerClauses(ref)) {
      addOutgoing(groups, { tag: 'definition', ref: clause }, clause.definition.range, false);
    }
  }
  return [...groups.values()];
}

/** 呼び出し元のキー。 */
function scopeKey(scope: CallerScope): string {
  return scope.tag === 'definition' ? defKey(scope.ref) : `top:${scope.path}`;
}

/** 呼び出しの場所を呼び出し元ごとに束ねる。 */
function groupByCaller(sites: readonly CallSite[]): IncomingGroup[] {
  const groups = new Map<string, IncomingGroup>();
  for (const site of sites) {
    const from: CallerScope =
      site.caller === null ? { tag: 'top-level', path: site.path, module: site.module } : { tag: 'definition', ref: site.caller };
    const key = scopeKey(from);
    const known = groups.get(key);
    if (known === undefined) {
      groups.set(key, { from, fromRanges: [site.call.range] });
    } else {
      known.fromRanges.push(site.call.range);
    }
  }
  return [...groups.values()];
}

/** 名前の一致する呼び出しのうち、解決の行き先が target になる物(まず名前で絞ってから解決する)。 */
export async function callSitesOf(graph: EffectGraph, resolve: Resolve, target: DefRef): Promise<CallSite[]> {
  const wanted = defKey(target);
  const found: CallSite[] = [];
  for (const site of graph.callsNamed(target.definition.mangled)) {
    const targets = await resolveCall(graph, resolve, site);
    if (targets.some((t) => t.tag === 'definition' && defKey(t.ref) === wanted)) {
      found.push(site);
    }
  }
  return found;
}

/**
 * 項目へ入ってくる呼び出し。handler の節なら、その effect を撃つ場所(名前で束ねる effect の規則どおり)、
 * 名前だけの effect も同じ。それ以外は、呼び出しの行き先がこの定義になる物を呼び出し元ごとに束ねる。
 */
export async function incomingCalls(graph: EffectGraph, resolve: Resolve, item: CallTarget): Promise<IncomingGroup[]> {
  switch (item.tag) {
    case 'python':
      return [];
    case 'effect-name':
      return groupByCaller(graph.performSites(item.mangled));
    case 'definition':
      if (item.ref.definition.kind === 'effect-clause') {
        return groupByCaller(graph.performSites(item.ref.definition.mangled));
      }
      return groupByCaller(await callSitesOf(graph, resolve, item.ref));
    default: {
      const unreachable: never = item;
      throw new Error(`網羅されていない項目: ${JSON.stringify(unreachable)}`);
    }
  }
}
