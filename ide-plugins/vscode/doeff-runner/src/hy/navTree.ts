// ナビゲーションパネル(Effects・Handlers・Programs・Current file)の木の中身。VS Code に依らない節の型と、
// 節の子を作る関数だけを持つ。子は展開した時に作り(大きな workspace でも最上段は module の束だけ)、
// 既に開いた経路に戻る子は「循環」で止める。

import type { HyRange } from './contract';
import { outgoingCalls, incomingCalls, type CallTarget, type Resolve } from './callGraph';
import { defKey, PROGRAM_KINDS, type CallSite, type DefRef, type EffectEntry, type EffectGraph } from './effects';
import { fuzzyContains } from './outline';

/** パネルの view の種類。 */
export type NavView = 'effects' | 'handlers' | 'programs' | 'current-file';

/** 束の子の見出し。 */
export type NavSection = 'handlers' | 'performed-by' | 'performs' | 'calls' | 'called-by' | 'other-handlers';

/** 開いた経路(循環を止めるためのキーの並び)。 */
export type Trail = readonly string[];

/** 木の節 1 つ。 */
export type NavNode =
  | { readonly tag: 'group'; readonly label: string; readonly children: readonly NavNode[] }
  | { readonly tag: 'effect'; readonly entry: EffectEntry; readonly trail: Trail }
  | { readonly tag: 'handler'; readonly ref: DefRef; readonly trail: Trail }
  | { readonly tag: 'clause'; readonly ref: DefRef; readonly trail: Trail }
  | { readonly tag: 'program'; readonly ref: DefRef; readonly trail: Trail }
  | { readonly tag: 'section'; readonly section: NavSection; readonly owner: SectionOwner; readonly trail: Trail }
  | { readonly tag: 'site'; readonly site: CallSite }
  | { readonly tag: 'target'; readonly target: CallTarget }
  | { readonly tag: 'top-level'; readonly path: string; readonly module: string; readonly ranges: readonly HyRange[] }
  | { readonly tag: 'cycle'; readonly label: string; readonly at: DefRef | undefined }
  | { readonly tag: 'empty'; readonly label: string };

/** 束の持ち主 — effect か定義。 */
export type SectionOwner =
  | { readonly tag: 'effect'; readonly entry: EffectEntry }
  | { readonly tag: 'definition'; readonly ref: DefRef };

/** effect の束のキー(経路の記録用)。 */
function effectKey(entry: EffectEntry): string {
  return `effect:${entry.mangled}`;
}

/** 節の子を作る時に使う物 — 今の表と解決の口。 */
export interface NavContext {
  readonly graph: EffectGraph;
  readonly resolve: Resolve;
}

/** 名前が絞り込みの文字に合うか(空なら全部)。 */
function matches(name: string, filter: string): boolean {
  const q = filter.toLowerCase().replace(/\s+/g, '');
  return q === '' || fuzzyContains(name.toLowerCase(), q);
}

/** 要素を module ごとの束にする(module の名前の順、束の中は名前の順)。 */
function groupByModule<T>(items: readonly T[], moduleOf: (item: T) => string, nameOf: (item: T) => string, node: (item: T) => NavNode): NavNode[] {
  const byModule = new Map<string, T[]>();
  for (const item of items) {
    const module = moduleOf(item);
    const list = byModule.get(module);
    if (list === undefined) {
      byModule.set(module, [item]);
    } else {
      list.push(item);
    }
  }
  return [...byModule.keys()].sort().map((module) => ({
    tag: 'group' as const,
    label: module,
    children: (byModule.get(module) ?? []).sort((a, b) => nameOf(a).localeCompare(nameOf(b))).map(node)
  }));
}

/** effect の束の module(クラスが見えればその module、見えなければ見出しの札)。 */
function effectModule(entry: EffectEntry): string {
  const first = entry.classes[0];
  if (first === undefined) {
    return '(クラスの定義が索引に無い effect)';
  }
  return first.external ? `${first.module}(外)` : first.module;
}

/** workspace に関わる effect(workspace にクラスか節がある物)。 */
function workspaceEffects(graph: EffectGraph): EffectEntry[] {
  return graph
    .effectEntries()
    .filter(
      (entry) => entry.classes.some((c) => !c.external) || graph.clausesFor(entry.mangled).some((c) => !c.external)
    );
}

/** view の最上段 — module ごとの束(Current file は今の file の effect・handler・プログラム)。 */
export function rootNodes(graph: EffectGraph, view: NavView, filter: string, currentFile: string | undefined): NavNode[] {
  switch (view) {
    case 'effects':
      return groupByModule(
        workspaceEffects(graph).filter((e) => matches(e.name, filter)),
        effectModule,
        (e) => e.name,
        (entry) => ({ tag: 'effect', entry, trail: [] })
      );
    case 'handlers':
      return groupByModule(
        graph.definitionsOfKind(['defhandler'], false).filter((r) => matches(r.definition.name, filter)),
        (r) => r.module,
        (r) => r.definition.name,
        (ref) => ({ tag: 'handler', ref, trail: [] })
      );
    case 'programs':
      return groupByModule(
        graph.definitionsOfKind(PROGRAM_KINDS, false).filter((r) => matches(r.definition.name, filter)),
        (r) => r.module,
        (r) => r.definition.name,
        (ref) => ({ tag: 'program', ref, trail: [] })
      );
    case 'current-file':
      return currentFileNodes(graph, filter, currentFile);
    default: {
      const unreachable: never = view;
      throw new Error(`網羅されていない view: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 今の file の effect・handler・プログラムを 3 つの束にする。 */
function currentFileNodes(graph: EffectGraph, filter: string, currentFile: string | undefined): NavNode[] {
  if (currentFile === undefined) {
    return [{ tag: 'empty', label: 'Hy の file を開くと、ここにその file の effect・handler・プログラムが出ます' }];
  }
  const refs = graph.definitionsIn(currentFile).filter((r) => matches(r.definition.name, filter));
  const effects = refs
    .filter((r) => graph.isEffectClass(r))
    .map((r) => graph.effect(r.definition.mangled))
    .filter((e): e is EffectEntry => e !== undefined)
    .map((entry): NavNode => ({ tag: 'effect', entry, trail: [] }));
  const handlers = refs
    .filter((r) => r.definition.kind === 'defhandler')
    .map((ref): NavNode => ({ tag: 'handler', ref, trail: [] }));
  const programs = refs
    .filter((r) => PROGRAM_KINDS.includes(r.definition.kind))
    .map((ref): NavNode => ({ tag: 'program', ref, trail: [] }));
  const groups: NavNode[] = [];
  if (effects.length > 0) groups.push({ tag: 'group', label: 'Effects', children: effects });
  if (handlers.length > 0) groups.push({ tag: 'group', label: 'Handlers', children: handlers });
  if (programs.length > 0) groups.push({ tag: 'group', label: 'Programs', children: programs });
  return groups.length > 0 ? groups : [{ tag: 'empty', label: 'この file には effect・handler・プログラムがありません' }];
}

/** 定義を種類に合った節にする(経路に既にあれば循環)。 */
export function definitionNode(graph: EffectGraph, ref: DefRef, trail: Trail): NavNode {
  if (trail.includes(defKey(ref))) {
    return { tag: 'cycle', label: ref.definition.name, at: ref };
  }
  if (graph.isEffectClass(ref)) {
    const entry = graph.effect(ref.definition.mangled);
    if (entry !== undefined) {
      return effectNode(entry, trail, ref);
    }
  }
  switch (ref.definition.kind) {
    case 'defhandler':
      return { tag: 'handler', ref, trail };
    case 'effect-clause':
      return { tag: 'clause', ref, trail };
    default:
      return { tag: 'program', ref, trail };
  }
}

/** effect の節(経路に既にあれば循環)。 */
function effectNode(entry: EffectEntry, trail: Trail, at: DefRef | undefined): NavNode {
  return trail.includes(effectKey(entry))
    ? { tag: 'cycle', label: entry.name, at: at ?? entry.classes[0] }
    : { tag: 'effect', entry, trail };
}

/** 子が 1 つも無い時の札を添える。 */
function orEmpty(nodes: NavNode[], label: string): NavNode[] {
  return nodes.length > 0 ? nodes : [{ tag: 'empty', label }];
}

/** 節の子を作る(展開した時に呼ぶ)。 */
export async function childNodes(ctx: NavContext, node: NavNode): Promise<NavNode[]> {
  const { graph } = ctx;
  switch (node.tag) {
    case 'group':
      return [...node.children];
    case 'effect': {
      const trail = [...node.trail, effectKey(node.entry)];
      const owner: SectionOwner = { tag: 'effect', entry: node.entry };
      return [
        { tag: 'section', section: 'handlers', owner, trail },
        { tag: 'section', section: 'performed-by', owner, trail }
      ];
    }
    case 'handler': {
      const trail = [...node.trail, defKey(node.ref)];
      return orEmpty(
        graph.handlerClauses(node.ref).map((clause) => definitionNode(graph, clause, trail)),
        '扱う effect の節がありません'
      );
    }
    case 'clause': {
      const trail = [...node.trail, defKey(node.ref)];
      const others = graph
        .clausesFor(node.ref.definition.mangled)
        .filter((c) => defKey(c) !== defKey(node.ref));
      const children: NavNode[] = [];
      const entry = graph.effect(node.ref.definition.mangled);
      if (entry !== undefined) {
        children.push(effectNode(entry, trail, undefined));
      }
      if (others.length > 0) {
        children.push({ tag: 'section', section: 'other-handlers', owner: { tag: 'definition', ref: node.ref }, trail });
      }
      return orEmpty(children, 'この effect を扱う他の handler はありません');
    }
    case 'program': {
      const trail = [...node.trail, defKey(node.ref)];
      const owner: SectionOwner = { tag: 'definition', ref: node.ref };
      return [
        { tag: 'section', section: 'performs', owner, trail },
        { tag: 'section', section: 'calls', owner, trail },
        { tag: 'section', section: 'called-by', owner, trail }
      ];
    }
    case 'section':
      return sectionChildren(ctx, node.section, node.owner, node.trail);
    case 'site':
    case 'target':
    case 'top-level':
    case 'cycle':
    case 'empty':
      return [];
    default: {
      const unreachable: never = node;
      throw new Error(`網羅されていない節: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 束(Handlers・Performed by・Performs・Calls・Called by・他の handler)の子を作る。 */
async function sectionChildren(ctx: NavContext, section: NavSection, owner: SectionOwner, trail: Trail): Promise<NavNode[]> {
  const { graph, resolve } = ctx;
  const mangled = owner.tag === 'effect' ? owner.entry.mangled : owner.ref.definition.mangled;
  switch (section) {
    case 'handlers':
    case 'other-handlers': {
      const self = owner.tag === 'definition' ? defKey(owner.ref) : undefined;
      return orEmpty(
        graph
          .clausesFor(mangled)
          .filter((c) => defKey(c) !== self)
          .map((clause) => definitionNode(graph, clause, trail)),
        'この effect を扱う handler はありません'
      );
    }
    case 'performed-by':
      return orEmpty(
        graph.performSites(mangled).map((site): NavNode => ({ tag: 'site', site })),
        'この effect を撃つ場所はありません'
      );
    case 'performs': {
      if (owner.tag !== 'definition') {
        return [];
      }
      return orEmpty(
        graph.performedEffects(owner.ref).map((performed): NavNode => {
          const entry = graph.effect(performed.mangled);
          return entry !== undefined
            ? effectNode(entry, trail, undefined)
            : { tag: 'site', site: performed.sites[0] }; // 撃っているが effect と判定できない名前(関数が返す effect 等)
        }),
        '撃つ effect はありません'
      );
    }
    case 'calls': {
      if (owner.tag !== 'definition') {
        return [];
      }
      const groups = await outgoingCalls(graph, resolve, { tag: 'definition', ref: owner.ref });
      return orEmpty(
        groups
          .filter((g) => !g.effect)
          .map((g): NavNode => (g.target.tag === 'definition' ? definitionNode(graph, g.target.ref, trail) : { tag: 'target', target: g.target })),
        '呼ぶ定義はありません'
      );
    }
    case 'called-by': {
      if (owner.tag !== 'definition') {
        return [];
      }
      const groups = await incomingCalls(graph, resolve, { tag: 'definition', ref: owner.ref });
      return orEmpty(
        groups.map((g): NavNode =>
          g.from.tag === 'definition'
            ? definitionNode(graph, g.from.ref, trail)
            : { tag: 'top-level', path: g.from.path, module: g.from.module, ranges: g.fromRanges }
        ),
        '呼び出し元はありません'
      );
    }
    default: {
      const unreachable: never = section;
      throw new Error(`網羅されていない束: ${JSON.stringify(unreachable)}`);
    }
  }
}
