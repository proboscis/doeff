// effect・handler・defk の行き来の中身を決める純粋な関数 — 定義へ移動に足す handler の節、実装へ移動の行き先、
// コード上の注記の中身、hover に足す行。表(EffectGraph)と解決の結果だけを見る。

import type { DefRef, CallSite, EffectGraph } from './effects';
import { PROGRAM_KINDS } from './effects';
import type { DefinitionResolution, DefinitionTarget } from './resolve';

/** 定義の参照を定義へ移動の行き先にする。 */
function refTarget(ref: DefRef): DefinitionTarget {
  return { tag: 'hy-definition', path: ref.path, module: ref.module, definition: ref.definition };
}

/** 行き先を重ねずに並べるためのキー。 */
function definitionTargetKey(target: DefinitionTarget): string {
  switch (target.tag) {
    case 'hy-definition':
      return `${target.path}:${target.definition.range.start.line}:${target.definition.range.start.character}`;
    case 'python-definition':
      return `${target.path}:${target.range.start.line}:${target.range.start.character}`;
    case 'hy-module':
    case 'python-module':
      return `${target.path}:module`;
    default: {
      const unreachable: never = target;
      throw new Error(`網羅されていない行き先: ${JSON.stringify(unreachable)}`);
    }
  }
}

/**
 * Cmd+クリックの行き先 — effect の名前なら、解決した定義(クラス)に、その effect を扱う全 handler の節を足す
 * (複数なら VS Code が peek で選ばせる)。effect でなければ解決の結果のまま。
 */
export function definitionTargetsWithHandlers(
  graph: EffectGraph,
  resolution: DefinitionResolution,
  mangled: string
): DefinitionTarget[] {
  const targets = [...resolution.targets];
  if (!graph.isEffect(mangled)) {
    return targets;
  }
  const seen = new Set(targets.map(definitionTargetKey));
  for (const clause of graph.clausesFor(mangled)) {
    const target = refTarget(clause);
    const key = definitionTargetKey(target);
    if (!seen.has(key)) {
      seen.add(key);
      targets.push(target);
    }
  }
  return targets;
}

/**
 * 実装へ移動(Cmd+F12)の行き先 — effect の名前の上ならその effect を扱う全 handler の節、
 * handler の名前の上ならその handler の節の一覧。どちらでもなければ空。
 */
export function implementationTargets(
  graph: EffectGraph,
  resolution: DefinitionResolution,
  mangled: string
): DefRef[] {
  if (graph.isEffect(mangled)) {
    return [...graph.clausesFor(mangled)];
  }
  const clauses: DefRef[] = [];
  for (const target of resolution.targets) {
    if (target.tag !== 'hy-definition' || target.definition.kind !== 'defhandler') {
      continue;
    }
    const handler = graph.refOf(target.path, target.definition);
    if (handler !== undefined) {
      clauses.push(...graph.handlerClauses(handler));
    }
  }
  return clauses;
}

/** コード上の注記 1 つの中身(呼び出し元の数は解決が要るので、開いた時に数える)。 */
export type LensSpec =
  | { readonly tag: 'effect-handlers'; readonly ref: DefRef; readonly clauses: readonly DefRef[] }
  | { readonly tag: 'effect-sites'; readonly ref: DefRef; readonly sites: readonly CallSite[] }
  | { readonly tag: 'handler-effects'; readonly ref: DefRef; readonly clauses: readonly DefRef[] }
  | { readonly tag: 'program-performs'; readonly ref: DefRef; readonly sites: readonly CallSite[]; readonly effects: readonly string[] }
  | { readonly tag: 'program-callers'; readonly ref: DefRef };

/** file の注記の中身 — effect のクラス・defhandler・defk / deff / defp の上に置く物。 */
export function lensSpecs(graph: EffectGraph, filePath: string): LensSpec[] {
  const specs: LensSpec[] = [];
  for (const ref of graph.definitionsIn(filePath)) {
    const kind = ref.definition.kind;
    if (graph.isEffectClass(ref)) {
      specs.push({ tag: 'effect-handlers', ref, clauses: graph.clausesFor(ref.definition.mangled) });
      specs.push({ tag: 'effect-sites', ref, sites: graph.performSites(ref.definition.mangled) });
    } else if (kind === 'defhandler') {
      specs.push({ tag: 'handler-effects', ref, clauses: graph.handlerClauses(ref) });
    } else if (PROGRAM_KINDS.includes(kind)) {
      // defk は見出しの effect の行が撃つ effect を出すので、注記は呼び出し元だけにする(同じ情報を 2 か所に出さない —
      // coordinator の決定 2026-09-28・agora-redesign #849)。他の定義(defp など)は今までどおり
      if (kind !== 'defk') {
        const performed = graph.performedEffects(ref);
        specs.push({
          tag: 'program-performs',
          ref,
          sites: performed.flatMap((p) => p.sites),
          effects: performed.map((p) => p.name)
        });
      }
      specs.push({ tag: 'program-callers', ref });
    }
  }
  return specs;
}

/** 注記の見出し(呼び出し元の数は program-callers の時だけ渡す)。 */
export function lensTitle(spec: LensSpec, callerCount: number | undefined): string {
  switch (spec.tag) {
    case 'effect-handlers':
      return `handler ${spec.clauses.length} 個`;
    case 'effect-sites':
      return `撃つ場所 ${spec.sites.length} 箇所`;
    case 'handler-effects':
      return spec.clauses.length === 0
        ? '扱う effect なし'
        : `扱う effect: ${spec.clauses.map((c) => c.definition.name).join(', ')}`;
    case 'program-performs':
      return `撃つ effect ${spec.effects.length} 個`;
    case 'program-callers':
      return callerCount === undefined ? '呼び出し元を数えています…' : `呼び出し元 ${callerCount} 箇所`;
    default: {
      const unreachable: never = spec;
      throw new Error(`網羅されていない注記: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** hover に足す行 — effect なら扱う handler の数、プログラムなら撃つ effect、handler なら扱う effect。 */
export function hoverExtras(graph: EffectGraph, ref: DefRef): string[] {
  if (graph.isEffectClass(ref)) {
    const clauses = graph.clausesFor(ref.definition.mangled);
    const handlers = clauses.map((c) => c.definition.container ?? '?');
    return [`effect — handler ${clauses.length} 個${handlers.length > 0 ? `(${handlers.join(', ')})` : ''}`];
  }
  if (ref.definition.kind === 'defhandler') {
    const clauses = graph.handlerClauses(ref);
    return [`扱う effect: ${clauses.length === 0 ? 'なし' : clauses.map((c) => c.definition.name).join(', ')}`];
  }
  if (PROGRAM_KINDS.includes(ref.definition.kind)) {
    const performed = graph.performedEffects(ref);
    return [`撃つ effect: ${performed.length === 0 ? 'なし' : performed.map((p) => p.name).join(', ')}`];
  }
  return [];
}
