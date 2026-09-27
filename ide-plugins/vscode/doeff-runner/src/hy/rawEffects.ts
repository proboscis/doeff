// 生の副作用に触る定義の判定 — 目録(rawCatalog)と索引(imports・references・calls・full_range)だけから、
// 定義ごとの証拠(直接)と、呼ぶ定義を通した証拠(経由・経路つき)を作る。VS Code にも子 process にも触らない。

import type { HyFileIndex, HyRange, HyReference } from './contract';
import type { Resolve } from './callGraph';
import { defKey, type DefRef, type EffectGraph } from './effects';
import { mangle, mangleDotted } from './mangle';
import { RAW_CATEGORIES, RAW_IGNORED_NAMES, type RawCatalogEntry, type RawCategory } from './rawCatalog';

/** 証拠の強さ — 強い = import を通した名前・組み込み、弱い = method 名だけ。 */
export type RawStrength = 'strong' | 'weak';

/** 何で見つけたか。 */
export type RawEvidenceKind = 'name' | 'builtin' | 'method';

/** 生の副作用の証拠 1 件(参照 1 つの位置)。 */
export interface RawEvidence {
  readonly category: RawCategory;
  /** 表示の名前 — import を通した完全な名前(`httpx.post`)か、組み込み・method の名前 */
  readonly name: string;
  readonly kind: RawEvidenceKind;
  readonly strength: RawStrength;
  readonly path: string;
  readonly range: HyRange;
}

/** 呼ぶ定義を通した証拠 — 経路(呼ぶ順の定義)と、行き着いた先の直接の証拠。 */
export interface ViaEvidence {
  readonly through: readonly DefRef[];
  readonly evidence: RawEvidence;
}

/** 定義 1 つの判定の結果。 */
export interface RawMark {
  readonly direct: readonly RawEvidence[];
  readonly via: readonly ViaEvidence[];
}

/** 経由の伝播の深さの上限。 */
export const RAW_VIA_MAX_DEPTH = 4;
/** 例外・警告の型の名前(`httpx.ReadTimeout`)— 型を参照するだけで副作用ではないので数えない。 */
const EXCEPTION_NAME = /(Error|Exception|Timeout|Warning)$/;

/** 1 つの定義に持つ経由の証拠の上限(表示が溢れないように)。 */
const RAW_VIA_MAX_ITEMS = 50;

/** 参照を import で完全な名前に直した結果。 */
type ExpandedName =
  /** import を通した名前(`time.sleep`・`pathlib.Path`) */
  | { readonly tag: 'import'; readonly full: string }
  /** import されていない修飾の無い名前(組み込みか、書いた file の外で決まる名前) */
  | { readonly tag: 'unbound'; readonly name: string }
  /** 局所の値の上の属性(`p.read-text`)や、file の中の定義の名前 */
  | { readonly tag: 'local'; readonly name: string };

/** 範囲 a が位置 b を含むか。 */
function containsPosition(range: HyRange, pos: HyRange['start']): boolean {
  const afterStart = pos.line > range.start.line || (pos.line === range.start.line && pos.character >= range.start.character);
  const beforeEnd = pos.line < range.end.line || (pos.line === range.end.line && pos.character <= range.end.character);
  return afterStart && beforeEnd;
}

/** dotted の名前が型の名前 pattern に合うか(区切りの境目での前方一致・末尾 `*` は区切りの中の前方一致)。 */
export function matchesPattern(full: string, pattern: string): boolean {
  const name = mangleDotted(full);
  if (pattern.endsWith('*')) {
    return name.startsWith(mangleDotted(pattern.slice(0, -1)));
  }
  const p = mangleDotted(pattern);
  return name === p || name.startsWith(p + '.');
}

/** 参照の書かれた dotted(修飾 + 名前)を mangle した物。 */
function chainOf(ref: HyReference): string {
  return mangleDotted(ref.qualifier === null ? ref.name : `${ref.qualifier}.${ref.name}`);
}

/**
 * 参照を file の import で完全な名前に直す。最も長く一致する import を使う
 * (`(import importlib.util)` の `importlib.util.find-spec`、`(import pathlib [Path])` の `Path.home`)。
 */
function expand(file: HyFileIndex, ref: HyReference, localNames: ReadonlySet<string>): ExpandedName {
  const segments = chainOf(ref).split('.');
  let best: { readonly length: number; readonly full: string } | undefined;
  for (const imp of file.imports) {
    if (imp.module.startsWith('.')) {
      continue; // 相対 import は workspace の中の module(生の副作用の目録の外)
    }
    const module = mangleDotted(imp.module);
    const bound = mangleDotted(imp.alias ?? imp.name ?? imp.module).split('.');
    const matched = bound.every((seg, i) => segments[i] === seg);
    if (!matched || (best !== undefined && best.length >= bound.length)) {
      continue;
    }
    const rest = segments.slice(bound.length);
    const head = imp.name === null ? module : `${module}.${mangle(imp.name)}`;
    best = { length: bound.length, full: [head, ...rest].join('.') };
  }
  if (best !== undefined) {
    return { tag: 'import', full: best.full };
  }
  const name = mangle(ref.name);
  if (ref.qualifier === null && !localNames.has(name)) {
    return { tag: 'unbound', name };
  }
  return { tag: 'local', name };
}

/** file の生の副作用の証拠の全部と、method の文脈になる module が見える位置。 */
export interface FileRawScan {
  readonly evidence: readonly RawEvidence[];
  /** method の証拠 1 件ごとに要る文脈の module(同じ添字) */
  readonly methodContexts: ReadonlyMap<RawEvidence, readonly string[]>;
  /** file の import が満たす文脈の module */
  readonly importedContexts: ReadonlySet<string>;
  /** 定義の中で文脈の module を参照している位置(module の名前ごと) */
  readonly contextRefs: ReadonlyMap<string, readonly HyRange[]>;
}

/**
 * file の参照を 1 度だけ走査して、生の副作用の証拠を集める。dotted の途中の区切り(`os.environ.get` の
 * `os`・`environ`)は数えず、終端の区切りだけを完全な名前に直して目録と比べる。
 */
export function scanFile(file: HyFileIndex, catalog: readonly RawCatalogEntry[]): FileRawScan {
  const localNames = new Set(file.definitions.filter((d) => d.container === null).map((d) => d.mangled));
  // 組み込みは呼び出しの頭の位置だけ数える(`(setv open …)` の局所の変数を拾わない。`(.open p)` は calls に入らない)
  const callHeads = new Set(
    file.calls.filter((c) => c.qualifier === null).map((c) => `${c.range.start.line}:${c.range.start.character}`)
  );
  // 後ろに `.x` が続く参照(dotted の途中)を見分けるための表
  const continued = new Set(
    file.references
      .filter((r) => r.qualifier !== null)
      .map((r) => `${r.range.start.line}:${r.range.start.character - 1}:${mangleDotted(r.qualifier ?? '')}`)
  );
  const contextModules = [...new Set(catalog.flatMap((e) => e.methods.flatMap((m) => m.context)))];
  const importedContexts = new Set(
    contextModules.filter((m) => file.imports.some((imp) => matchesPattern(imp.module, m)))
  );
  const contextRefs = new Map<string, HyRange[]>();
  const evidence: RawEvidence[] = [];
  const methodContexts = new Map<RawEvidence, readonly string[]>();
  for (const ref of file.references) {
    const expanded = expand(file, ref, localNames);
    if (expanded.tag === 'import') {
      for (const m of contextModules) {
        if (matchesPattern(expanded.full, m)) {
          contextRefs.set(m, [...(contextRefs.get(m) ?? []), ref.range]);
        }
      }
    }
    if (continued.has(`${ref.range.end.line}:${ref.range.end.character}:${chainOf(ref)}`)) {
      continue; // dotted の途中の区切り
    }
    const atCallHead = callHeads.has(`${ref.range.start.line}:${ref.range.start.character}`);
    for (const entry of catalog) {
      const found = matchEntry(entry, expanded, atCallHead, file.path, ref.range);
      if (found !== undefined) {
        evidence.push(found.evidence);
        if (found.context !== undefined) {
          methodContexts.set(found.evidence, found.context);
        }
      }
    }
  }
  return { evidence, methodContexts, importedContexts, contextRefs };
}

/** 参照 1 つを目録の分類 1 つと比べる(強い証拠を先に、method は弱い証拠)。 */
function matchEntry(
  entry: RawCatalogEntry,
  expanded: ExpandedName,
  atCallHead: boolean,
  path: string,
  range: HyRange
): { readonly evidence: RawEvidence; readonly context: readonly string[] | undefined } | undefined {
  const base = { category: entry.category, path, range };
  switch (expanded.tag) {
    case 'import': {
      const last = expanded.full.split('.').pop() ?? '';
      if (EXCEPTION_NAME.test(last) || RAW_IGNORED_NAMES.some((n) => matchesPattern(expanded.full, n))) {
        return undefined;
      }
      return entry.patterns.some((p) => matchesPattern(expanded.full, p))
        ? { evidence: { ...base, name: expanded.full, kind: 'name', strength: 'strong' }, context: undefined }
        : undefined;
    }
    case 'unbound':
      if (atCallHead && entry.builtins.some((b) => mangle(b) === expanded.name)) {
        return { evidence: { ...base, name: expanded.name, kind: 'builtin', strength: 'strong' }, context: undefined };
      }
      return methodMatch(entry, expanded.name, base);
    case 'local':
      return methodMatch(entry, expanded.name, base);
    default: {
      const unreachable: never = expanded;
      throw new Error(`網羅されていない名前: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** method 名だけの一致(弱い証拠)と、それに要る文脈。 */
function methodMatch(
  entry: RawCatalogEntry,
  name: string,
  base: { readonly category: RawCategory; readonly path: string; readonly range: HyRange }
): { readonly evidence: RawEvidence; readonly context: readonly string[] } | undefined {
  const method = entry.methods.find((m) => mangle(m.name) === name);
  return method === undefined
    ? undefined
    : { evidence: { ...base, name: `.${name}`, kind: 'method', strength: 'weak' }, context: method.context };
}

/** 定義の範囲の中の直接の証拠(method の証拠は文脈の module が file の import か定義の中の参照に見える時だけ)。 */
export function directEvidence(scan: FileRawScan, ref: DefRef): RawEvidence[] {
  const range = ref.definition.fullRange;
  const contextVisible = (module: string): boolean =>
    scan.importedContexts.has(module) ||
    (scan.contextRefs.get(module) ?? []).some((r) => containsPosition(range, r.start));
  return scan.evidence.filter((e) => {
    if (!containsPosition(range, e.range.start)) {
      return false;
    }
    const context = scan.methodContexts.get(e);
    return context === undefined || context.length === 0 || context.some(contextVisible);
  });
}

/** 分類ごとの要約 1 件 — 強い証拠があるか(無ければ「?」を付ける)。 */
export interface CategorySummary {
  readonly category: RawCategory;
  readonly weakOnly: boolean;
}

/** 証拠を分類ごとに要約する(目録の分類の順)。 */
export function summarize(evidence: readonly RawEvidence[]): CategorySummary[] {
  return RAW_CATEGORIES.filter((c) => evidence.some((e) => e.category === c)).map((category) => ({
    category,
    weakOnly: evidence.filter((e) => e.category === category).every((e) => e.strength === 'weak')
  }));
}

/** 要約を「http, time?」の形の文字列にする。 */
export function summaryText(summary: readonly CategorySummary[]): string {
  return summary.map((s) => `${s.category}${s.weakOnly ? '?' : ''}`).join(', ');
}

/** 定義ごとの判定を持つ係 — file の走査・直接の証拠・呼び出しの解決・経由の結果を表の版ごとに覚える。 */
export class RawEffectIndex {
  private readonly scans = new Map<string, FileRawScan>();
  private readonly directs = new Map<string, RawEvidence[]>();
  private readonly marks = new Map<string, Promise<RawMark>>();
  private readonly callTargets = new Map<string, Promise<DefRef[]>>();

  constructor(
    private readonly graph: EffectGraph,
    private readonly catalog: readonly RawCatalogEntry[],
    private readonly resolve: Resolve
  ) {}

  /** 定義の直接の証拠。 */
  direct(ref: DefRef): readonly RawEvidence[] {
    const key = defKey(ref);
    let known = this.directs.get(key);
    if (known === undefined) {
      let scan = this.scans.get(ref.path);
      if (scan === undefined) {
        scan = scanFile(ref.file, this.catalog);
        this.scans.set(ref.path, scan);
      }
      known = directEvidence(scan, ref);
      this.directs.set(key, known);
    }
    return known;
  }

  /** 定義の判定(直接 + 経由)。同じ定義は 1 度だけ計算する。 */
  mark(ref: DefRef): Promise<RawMark> {
    const key = defKey(ref);
    let known = this.marks.get(key);
    if (known === undefined) {
      known = this.computeMark(ref);
      this.marks.set(key, known);
    }
    return known;
  }

  /** 直接の証拠と、呼ぶ定義を深さ RAW_VIA_MAX_DEPTH まで辿った経由の証拠を作る。 */
  private async computeMark(ref: DefRef): Promise<RawMark> {
    const via: ViaEvidence[] = [];
    const seen = new Set<string>();
    await this.collectVia(ref, [], new Set([defKey(ref)]), via, seen);
    return { direct: this.direct(ref), via };
  }

  /** 経由の証拠を深さ優先で集める(循環は経路の中の定義で止め、同じ証拠の位置は 1 度だけ)。 */
  private async collectVia(
    ref: DefRef,
    through: readonly DefRef[],
    visiting: ReadonlySet<string>,
    out: ViaEvidence[],
    seen: Set<string>
  ): Promise<void> {
    if (through.length >= RAW_VIA_MAX_DEPTH || out.length >= RAW_VIA_MAX_ITEMS) {
      return;
    }
    for (const callee of await this.calleesOf(ref)) {
      const key = defKey(callee);
      if (visiting.has(key)) {
        continue;
      }
      const path = [...through, callee];
      for (const evidence of this.direct(callee)) {
        const at = `${evidence.path}:${evidence.range.start.line}:${evidence.range.start.character}`;
        if (!seen.has(at) && out.length < RAW_VIA_MAX_ITEMS) {
          seen.add(at);
          out.push({ through: path, evidence });
        }
      }
      await this.collectVia(callee, path, new Set([...visiting, key]), out, seen);
    }
  }

  /**
   * 定義の範囲の中の呼び出しが行き着く定義(自分の中の入れ子は除く)。解決の前に、表のどこかに同じ名前の定義が
   * ある呼び出しだけに絞る(`str`・`list` のような組み込みを解決しに行かない)。行き先が同じ file・import で
   * 決まった物だけを使い、workspace 全体の同名で当てた解決(不確か)は経由の根拠にしない。
   */
  private async calleesOf(ref: DefRef): Promise<DefRef[]> {
    const range = ref.definition.fullRange;
    const found = new Map<string, DefRef>();
    for (const call of ref.file.calls) {
      if (!containsPosition(range, call.range.start) || !this.graph.hasDefinitionNamed(call.mangled)) {
        continue;
      }
      const callKey = `${ref.path}:${call.range.start.line}:${call.range.start.character}`;
      let targets = this.callTargets.get(callKey);
      if (targets === undefined) {
        targets = this.resolve({ filePath: ref.path, name: call.callee, qualifier: call.qualifier }).then((resolution) =>
          resolution.tier === 'workspace'
            ? []
            : resolution.targets.flatMap((t) => {
                if (t.tag !== 'hy-definition') {
                  return [];
                }
                const target = this.graph.refOf(t.path, t.definition);
                return target === undefined ? [] : [target];
              })
        );
        this.callTargets.set(callKey, targets);
      }
      for (const target of await targets) {
        const inside = target.path === ref.path && containsPosition(range, target.definition.range.start);
        if (!inside) {
          found.set(defKey(target), target);
        }
      }
    }
    return [...found.values()];
  }
}

/** 今の表と目録に合った判定の係を配る(表か目録が変わった時だけ作り直し、それまでの計算を使い回す)。 */
export class RawEffectSource {
  private cached:
    | { readonly graph: EffectGraph; readonly catalog: readonly RawCatalogEntry[]; readonly index: RawEffectIndex }
    | undefined;

  constructor(
    private readonly graphs: { current(): EffectGraph },
    private readonly catalog: () => readonly RawCatalogEntry[],
    private readonly resolve: Resolve
  ) {}

  /** 今の判定の係。 */
  current(): RawEffectIndex {
    const graph = this.graphs.current();
    const catalog = this.catalog();
    if (this.cached === undefined || this.cached.graph !== graph || this.cached.catalog !== catalog) {
      this.cached = { graph, catalog, index: new RawEffectIndex(graph, catalog, this.resolve) };
    }
    return this.cached.index;
  }
}
