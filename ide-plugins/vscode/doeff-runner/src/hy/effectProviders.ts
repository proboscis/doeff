// 呼び出し階層とコード上の注記の provider — callGraph・navigation の純粋な結果を VS Code の型へ写すだけの層。
// 既存の Python の CodeLens(実行ボタン)とは別の provider で、Hy の file にだけ出す。

import * as vscode from 'vscode';
import {
  callSitesOf,
  incomingCalls,
  outgoingCalls,
  type CallTarget,
  type CallerScope,
  type Resolve
} from './callGraph';
import { symbolAt } from './cursor';
import { PROGRAM_KINDS, type CallSite, type DefRef, type EffectGraph, type EffectGraphSource } from './effects';
import { lensSpecs, lensTitle, type LensSpec } from './navigation';
import { outlineKindOf } from './outline';
import { toRange, toSymbolKind } from './providers';
import type { RawEffectSource } from './rawEffects';
import { rawEvidenceLocations, rawLensTitle, rawRoleOf } from './rawView';

/** 呼び出し階層の項目にする定義の kind(effect のクラスは別に足す)。 */
const HIERARCHY_KINDS = [...PROGRAM_KINDS, 'defn', 'defn/a', 'defhandler', 'effect-clause'] as const;

/** 定義を呼び出し階層の項目にできるか。 */
function isHierarchyDefinition(graph: EffectGraph, ref: DefRef): boolean {
  return (HIERARCHY_KINDS as readonly string[]).includes(ref.definition.kind) || graph.isEffectClass(ref);
}

/** 定義の detail(effect のクラスと節には「effect」を出す)。 */
function definitionDetail(graph: EffectGraph, ref: DefRef, effect: boolean): string {
  const handler = ref.definition.kind === 'effect-clause' ? ` · ${ref.definition.container ?? '?'}` : '';
  const mark = effect || graph.isEffectClass(ref) ? 'effect · ' : '';
  return `${mark}${ref.definition.kind}${handler} · ${ref.module}`;
}

/** 呼び出しの行き先を呼び出し階層の項目にする。 */
function targetItem(graph: EffectGraph, target: CallTarget, effect: boolean): vscode.CallHierarchyItem {
  switch (target.tag) {
    case 'definition': {
      const def = target.ref.definition;
      return new vscode.CallHierarchyItem(
        toSymbolKind(outlineKindOf(def.kind)),
        def.name,
        definitionDetail(graph, target.ref, effect),
        vscode.Uri.file(target.ref.path),
        toRange(def.fullRange),
        toRange(def.range)
      );
    }
    case 'python':
      return new vscode.CallHierarchyItem(
        vscode.SymbolKind.Function,
        target.name,
        `python · ${target.module}`,
        vscode.Uri.file(target.path),
        toRange(target.range),
        toRange(target.range)
      );
    case 'effect-name':
      return new vscode.CallHierarchyItem(
        vscode.SymbolKind.Event,
        target.name,
        'effect(定義が索引に無い)',
        vscode.Uri.file(target.site.path),
        toRange(target.site.call.range),
        toRange(target.site.call.range)
      );
    default: {
      const unreachable: never = target;
      throw new Error(`網羅されていない行き先: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 呼び出し元を項目にする(top level なら file の module の項目)。 */
function scopeItem(graph: EffectGraph, scope: CallerScope, ranges: readonly vscode.Range[]): vscode.CallHierarchyItem {
  if (scope.tag === 'definition') {
    return targetItem(graph, { tag: 'definition', ref: scope.ref }, false);
  }
  const at = ranges[0] ?? new vscode.Range(0, 0, 0, 0);
  return new vscode.CallHierarchyItem(vscode.SymbolKind.Module, scope.module, 'top level', vscode.Uri.file(scope.path), at, at);
}

/** 呼び出し階層 — 項目は defk / deff / defp / defn / defhandler / effect の節 / effect のクラス。 */
export class HyCallHierarchyProvider implements vscode.CallHierarchyProvider {
  // VS Code は返した項目の object を次の問いにそのまま渡すので、項目から行き先を引ける
  private readonly targets = new WeakMap<vscode.CallHierarchyItem, CallTarget>();

  constructor(
    private readonly graphs: EffectGraphSource,
    private readonly resolve: Resolve
  ) {}

  /** 項目を作り、行き先を覚える。 */
  private remember(item: vscode.CallHierarchyItem, target: CallTarget): vscode.CallHierarchyItem {
    this.targets.set(item, target);
    return item;
  }

  /** 項目から行き先を引く(覚えていなければ位置から表を引き直す)。 */
  private targetOf(item: vscode.CallHierarchyItem): CallTarget | undefined {
    const known = this.targets.get(item);
    if (known !== undefined) {
      return known;
    }
    const ref = this.graphs
      .current()
      .defAt(item.uri.fsPath, item.selectionRange.start.line, item.selectionRange.start.character);
    return ref === undefined ? undefined : { tag: 'definition', ref };
  }

  /** カーソルの記号から項目を作る(解決の行き先のうち項目にできる定義)。 */
  async prepareCallHierarchy(
    document: vscode.TextDocument,
    position: vscode.Position
  ): Promise<vscode.CallHierarchyItem[]> {
    const symbol = symbolAt(document.lineAt(position.line).text, position.character);
    if (symbol === undefined) {
      return [];
    }
    const graph = this.graphs.current();
    const here = graph.defAt(document.uri.fsPath, position.line, symbol.start);
    if (here !== undefined && isHierarchyDefinition(graph, here)) {
      // 定義の名前の上(節の頭を含む)ならその定義そのもの
      return [this.remember(targetItem(graph, { tag: 'definition', ref: here }, false), { tag: 'definition', ref: here })];
    }
    const resolution = await this.resolve({ filePath: document.uri.fsPath, name: symbol.name, qualifier: symbol.qualifier });
    const items: vscode.CallHierarchyItem[] = [];
    for (const target of resolution.targets) {
      if (target.tag !== 'hy-definition') {
        continue;
      }
      const ref = graph.refOf(target.path, target.definition);
      if (ref !== undefined && isHierarchyDefinition(graph, ref)) {
        items.push(this.remember(targetItem(graph, { tag: 'definition', ref }, false), { tag: 'definition', ref }));
      }
    }
    return items;
  }

  /** 出ていく呼び出し — 行き先ごとに束ね、effect の生成には detail に「effect」を出す。 */
  async provideCallHierarchyOutgoingCalls(item: vscode.CallHierarchyItem): Promise<vscode.CallHierarchyOutgoingCall[]> {
    const target = this.targetOf(item);
    if (target === undefined) {
      return [];
    }
    const graph = this.graphs.current();
    const groups = await outgoingCalls(graph, this.resolve, target);
    return groups.map(
      (g) =>
        new vscode.CallHierarchyOutgoingCall(
          this.remember(targetItem(graph, g.target, g.effect), g.target),
          g.fromRanges.map(toRange)
        )
    );
  }

  /** 入ってくる呼び出し — 名前で絞ってから解決し、行き先がこの定義になる物を呼び出し元ごとに束ねる。 */
  async provideCallHierarchyIncomingCalls(item: vscode.CallHierarchyItem): Promise<vscode.CallHierarchyIncomingCall[]> {
    const target = this.targetOf(item);
    if (target === undefined) {
      return [];
    }
    const graph = this.graphs.current();
    const groups = await incomingCalls(graph, this.resolve, target);
    return groups.map((g) => {
      const ranges = g.fromRanges.map(toRange);
      const from = scopeItem(graph, g.from, ranges);
      if (g.from.tag === 'definition') {
        this.remember(from, { tag: 'definition', ref: g.from.ref });
      }
      return new vscode.CallHierarchyIncomingCall(from, ranges);
    });
  }
}

/** 注記を押した時の命令 — 1 件なら直接移動、複数なら peek で一覧、0 件なら押せない注記。 */
export function locationsCommand(
  title: string,
  anchor: vscode.Location,
  locations: readonly vscode.Location[]
): vscode.Command {
  if (locations.length === 0) {
    return { title, command: '' };
  }
  if (locations.length === 1) {
    const only = locations[0];
    return { title, command: 'vscode.open', arguments: [only.uri, { selection: only.range }] };
  }
  return {
    title,
    command: 'editor.action.peekLocations',
    arguments: [anchor.uri, anchor.range.start, [...locations], 'peek']
  };
}

/** 定義の名前の位置。 */
function refLocation(ref: DefRef): vscode.Location {
  return new vscode.Location(vscode.Uri.file(ref.path), toRange(ref.definition.range));
}

/** 呼び出しの位置。 */
function siteLocation(site: CallSite): vscode.Location {
  return new vscode.Location(vscode.Uri.file(site.path), toRange(site.call.range));
}

/** 注記 1 つ(中身の種類を持つ)。 */
class HyCodeLens extends vscode.CodeLens {
  constructor(readonly spec: LensSpec) {
    super(toRange(spec.ref.definition.range));
  }
}

/** コード上の注記 — effect のクラス・defhandler・defk / deff / defp の上。置き場が変わったら出し直す。 */
export class HyEffectCodeLensProvider implements vscode.CodeLensProvider<vscode.CodeLens>, vscode.Disposable {
  private readonly changed = new vscode.EventEmitter<void>();
  readonly onDidChangeCodeLenses = this.changed.event;

  constructor(
    private readonly graphs: EffectGraphSource,
    private readonly resolve: Resolve,
    private readonly raw: RawEffectSource
  ) {}

  /** 置き場か外の cache が変わった時に呼ぶ(注記を出し直させる)。 */
  refresh(): void {
    this.changed.fire();
  }

  /** 購読を止める。 */
  dispose(): void {
    this.changed.dispose();
  }

  /**
   * file の注記を並べる(呼び出し元の数だけは開いた時に数える)。生の副作用の注記は、印のある
   * defhandler・effect の節・直接触る defk / deff / defp にだけ、見出しと移動先を決めて出す。
   */
  async provideCodeLenses(document: vscode.TextDocument): Promise<vscode.CodeLens[]> {
    const graph = this.graphs.current();
    const lenses: vscode.CodeLens[] = lensSpecs(graph, document.uri.fsPath).map((spec) => new HyCodeLens(spec));
    const raw = this.raw.current();
    for (const ref of graph.definitionsIn(document.uri.fsPath)) {
      const role = rawRoleOf(ref.definition.kind);
      if (role === undefined) {
        continue;
      }
      const mark = await raw.mark(ref);
      const title = rawLensTitle(mark, role);
      if (title === undefined) {
        continue;
      }
      const locations = rawEvidenceLocations(mark, role).map(
        (loc) => new vscode.Location(vscode.Uri.file(loc.path), toRange(loc.range))
      );
      lenses.push(new vscode.CodeLens(toRange(ref.definition.range), locationsCommand(title, refLocation(ref), locations)));
    }
    return lenses;
  }

  /** 注記の見出しと、押した時の移動先を決める(生の副作用の注記は並べた時に決まっている)。 */
  async resolveCodeLens(lens: vscode.CodeLens): Promise<vscode.CodeLens> {
    if (!(lens instanceof HyCodeLens)) {
      return lens;
    }
    const spec = lens.spec;
    const anchor = refLocation(spec.ref);
    switch (spec.tag) {
      case 'effect-handlers':
      case 'handler-effects':
        lens.command = locationsCommand(lensTitle(spec, undefined), anchor, spec.clauses.map(refLocation));
        return lens;
      case 'effect-sites':
      case 'program-performs':
        lens.command = locationsCommand(lensTitle(spec, undefined), anchor, spec.sites.map(siteLocation));
        return lens;
      case 'program-callers': {
        const sites = await callSitesOf(this.graphs.current(), this.resolve, spec.ref);
        lens.command = locationsCommand(lensTitle(spec, sites.length), anchor, sites.map(siteLocation));
        return lens;
      }
      default: {
        const unreachable: never = spec;
        throw new Error(`網羅されていない注記: ${JSON.stringify(unreachable)}`);
      }
    }
  }
}
