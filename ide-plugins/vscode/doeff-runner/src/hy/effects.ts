// effect・handler・defk の関係の索引 — 何が effect か(判定の唯一の場所)、effect を扱う handler の節、
// effect を撃つ場所、定義の中の呼び出し。索引の置き場(workspace)と外の cache の file から作る純粋な表で、
// 実装へ移動・呼び出し階層・注記・ナビゲーションパネルはすべてここから読む。

import type { HyCall, HyDefinition, HyDefinitionKind, HyFileIndex } from './contract';
import type { ExternalFileView } from './external';
import { mangle } from './mangle';
import { normalizeKey, type HyIndexView } from './store';

/** 定義 1 件への参照 — どの file の何番目の定義か。 */
export interface DefRef {
  readonly path: string;
  readonly module: string;
  readonly file: HyFileIndex;
  /** file.definitions の添字 */
  readonly index: number;
  readonly definition: HyDefinition;
  /** workspace の外(依存 package の cache)の file か */
  readonly external: boolean;
}

/** 呼び出し 1 件と、それを含む定義(top level なら null)。 */
export interface CallSite {
  readonly path: string;
  readonly module: string;
  readonly file: HyFileIndex;
  readonly call: HyCall;
  readonly caller: DefRef | null;
}

/** 名前で束ねた effect 1 つ — クラスの定義(見えれば)と、扱う節。 */
export interface EffectEntry {
  readonly mangled: string;
  /** 表示の名前(クラスか節の書かれたとおりの名前) */
  readonly name: string;
  readonly classes: readonly DefRef[];
}

/** 索引の file 1 つと、workspace の外かどうか。 */
export interface GraphFile {
  readonly file: HyFileIndex;
  readonly external: boolean;
}

/** effect の根の基底(dotted の最後の区切りで比べる)。 */
const EFFECT_ROOT = 'EffectBase';

/** 呼び出し階層・パネルで「プログラム」として並べる kind。 */
export const PROGRAM_KINDS: readonly HyDefinitionKind[] = ['defk', 'deff', 'defp'];

/** 定義を一意に指すキー(file の path と添字)。 */
export function defKey(ref: { readonly path: string; readonly index: number }): string {
  return `${normalizeKey(ref.path)}#${ref.index}`;
}

/** 基底の記号(dotted も可)の最後の区切りを mangle した物 — effect の判定の照合用。 */
function baseName(base: string): string {
  const parts = base.split('.');
  return mangle(parts[parts.length - 1]);
}

/** 表に 1 件足す(同じキーの並びへ積む)。 */
function push<K, V>(table: Map<K, V[]>, key: K, value: V): void {
  const list = table.get(key);
  if (list === undefined) {
    table.set(key, [value]);
  } else {
    list.push(value);
  }
}

/** effect・handler・呼び出しの関係の表(作った後は変わらない)。 */
export class EffectGraph {
  private readonly effects = new Set<string>();
  private readonly classesByName = new Map<string, DefRef[]>();
  private readonly clausesByName = new Map<string, DefRef[]>();
  private readonly callsByName = new Map<string, CallSite[]>();
  private readonly callsByCaller = new Map<string, CallSite[]>();
  private readonly refsByPath = new Map<string, DefRef[]>();
  private readonly allRefs: DefRef[] = [];
  private readonly definedNames = new Set<string>();

  /** file の並びから表を作る(同じ path が 2 度あれば先の物を使う)。 */
  constructor(files: readonly GraphFile[]) {
    const seen = new Set<string>();
    const classBases: Array<{ readonly ref: DefRef; readonly bases: readonly string[] }> = [];
    for (const { file, external } of files) {
      const pathKey = normalizeKey(file.path);
      if (seen.has(pathKey)) {
        continue;
      }
      seen.add(pathKey);
      const refs = file.definitions.map(
        (definition, index): DefRef => ({ path: file.path, module: file.module, file, index, definition, external })
      );
      this.refsByPath.set(pathKey, refs);
      this.allRefs.push(...refs);
      for (const ref of refs) {
        this.definedNames.add(ref.definition.mangled);
      }
      for (const ref of refs) {
        const kind = ref.definition.kind;
        if (kind === 'defclass' || kind === 'defrecord' || kind === 'defeffect') {
          push(this.classesByName, ref.definition.mangled, ref);
          classBases.push({ ref, bases: ref.definition.bases.map(baseName) });
        } else if (kind === 'effect-clause') {
          push(this.clausesByName, ref.definition.mangled, ref);
          this.effects.add(ref.definition.mangled); // (b) どこかの handler が節を持つ名前は effect
        }
      }
      for (const call of file.calls) {
        const caller = call.caller === null ? null : refs[call.caller];
        const site: CallSite = { path: file.path, module: file.module, file, call, caller };
        push(this.callsByName, call.mangled, site);
        if (caller !== null) {
          push(this.callsByCaller, defKey(caller), site);
        }
      }
    }
    // (a) 基底に EffectBase か、既に effect と分かったクラスを持つクラスは effect(増えなくなるまで繰り返す)
    let grew = true;
    while (grew) {
      grew = false;
      for (const { ref, bases } of classBases) {
        const name = ref.definition.mangled;
        if (!this.effects.has(name) && bases.some((b) => b === EFFECT_ROOT || this.effects.has(b))) {
          this.effects.add(name);
          grew = true;
        }
      }
    }
  }

  /** workspace の置き場と外の cache から表を作る。 */
  static fromIndex(index: HyIndexView, external: ExternalFileView): EffectGraph {
    return new EffectGraph([
      ...index.entries().map((entry): GraphFile => ({ file: entry.file, external: false })),
      ...external.cachedFiles().map((file): GraphFile => ({ file, external: true }))
    ]);
  }

  /** 表のどこかにこの名前(mangled)の定義があるか(組み込みの呼び出しを解決しに行かないための絞り込み)。 */
  hasDefinitionNamed(mangled: string): boolean {
    return this.definedNames.has(mangled);
  }

  /** 名前(mangled)が effect かを答える — 判定の唯一の場所。 */
  isEffect(mangled: string): boolean {
    return this.effects.has(mangled);
  }

  /** 定義が effect のクラスかを答える。 */
  isEffectClass(ref: DefRef): boolean {
    const kind = ref.definition.kind;
    return (kind === 'defclass' || kind === 'defrecord' || kind === 'defeffect') && this.effects.has(ref.definition.mangled);
  }

  /** effect の名前の束(クラスの定義つき)を返す。effect でなければ undefined。 */
  effect(mangled: string): EffectEntry | undefined {
    if (!this.effects.has(mangled)) {
      return undefined;
    }
    const classes = (this.classesByName.get(mangled) ?? []).filter((ref) => this.isEffectClass(ref));
    const name = classes[0]?.definition.name ?? this.clausesByName.get(mangled)?.[0]?.definition.name ?? mangled;
    return { mangled, name, classes };
  }

  /** 全 effect の束(名前の順)。 */
  effectEntries(): EffectEntry[] {
    return [...this.effects]
      .sort()
      .map((m) => this.effect(m))
      .filter((e): e is EffectEntry => e !== undefined);
  }

  /** effect を扱う全 handler の節。 */
  clausesFor(mangled: string): readonly DefRef[] {
    return this.clausesByName.get(mangled) ?? [];
  }

  /** handler が持つ effect の節(同じ file の、container がその handler の名前の物)。 */
  handlerClauses(handler: DefRef): DefRef[] {
    return (this.refsByPath.get(normalizeKey(handler.path)) ?? []).filter(
      (ref) => ref.definition.kind === 'effect-clause' && ref.definition.container === handler.definition.name
    );
  }

  /** 節を持つ handler(同じ file の、節の container の名前の defhandler)。 */
  handlerOfClause(clause: DefRef): DefRef | undefined {
    return (this.refsByPath.get(normalizeKey(clause.path)) ?? []).find(
      (ref) => ref.definition.kind === 'defhandler' && ref.definition.name === clause.definition.container
    );
  }

  /** effect を撃つ場所 = その名前の呼び出し(生成も `<-` も)。 */
  performSites(mangled: string): readonly CallSite[] {
    return this.callsByName.get(mangled) ?? [];
  }

  /** 名前が一致する呼び出しの全部(呼び出し元を探す時の、解決の前の絞り込み)。 */
  callsNamed(mangled: string): readonly CallSite[] {
    return this.callsByName.get(mangled) ?? [];
  }

  /** 定義の中の呼び出し(caller がその定義の物)。 */
  callsFrom(ref: DefRef): readonly CallSite[] {
    return this.callsByCaller.get(defKey(ref)) ?? [];
  }

  /** 呼び出しが effect を撃つものか(`<-` 等で撃たれているか、callee が effect)。 */
  isEffectCall(call: HyCall): boolean {
    return call.performed || this.effects.has(call.mangled);
  }

  /** 定義の中で撃つ effect を、名前ごとに撃つ場所を束ねて返す(書いた順)。 */
  performedEffects(ref: DefRef): Array<{ readonly mangled: string; readonly name: string; readonly sites: CallSite[] }> {
    const byName = new Map<string, { readonly mangled: string; readonly name: string; readonly sites: CallSite[] }>();
    for (const site of this.callsFrom(ref)) {
      if (!this.isEffectCall(site.call)) {
        continue;
      }
      const known = byName.get(site.call.mangled);
      if (known === undefined) {
        byName.set(site.call.mangled, { mangled: site.call.mangled, name: site.call.callee, sites: [site] });
      } else {
        known.sites.push(site);
      }
    }
    return [...byName.values()];
  }

  /** file の定義の全部。 */
  definitionsIn(filePath: string): readonly DefRef[] {
    return this.refsByPath.get(normalizeKey(filePath)) ?? [];
  }

  /** 定義の object から参照を引く(解決の結果の定義を表の参照へ戻す)。 */
  refOf(filePath: string, definition: HyDefinition): DefRef | undefined {
    return this.definitionsIn(filePath).find((ref) => ref.definition === definition);
  }

  /** 名前の位置の始まりで定義を引く(VS Code から戻ってきた項目を表の参照へ戻す)。 */
  defAt(filePath: string, line: number, character: number): DefRef | undefined {
    return this.definitionsIn(filePath).find(
      (ref) => ref.definition.range.start.line === line && ref.definition.range.start.character === character
    );
  }

  /** 指定の kind の定義の全部(workspace の中だけか、外も含めるか)。 */
  definitionsOfKind(kinds: readonly HyDefinitionKind[], includeExternal: boolean): DefRef[] {
    return this.allRefs.filter((ref) => kinds.includes(ref.definition.kind) && (includeExternal || !ref.external));
  }
}

/** 置き場と外の cache の版が変わった時だけ表を作り直す係(provider・木・注記が共有する)。 */
export class EffectGraphSource {
  private cached: { readonly key: string; readonly graph: EffectGraph } | undefined;

  constructor(
    private readonly index: HyIndexView,
    private readonly external: ExternalFileView
  ) {}

  /** 今の表を返す(版が同じなら作り直さない)。 */
  current(): EffectGraph {
    const key = `${this.index.version}:${this.external.version}`;
    if (this.cached === undefined || this.cached.key !== key) {
      this.cached = { key, graph: EffectGraph.fromIndex(this.index, this.external) };
    }
    return this.cached.graph;
  }
}
