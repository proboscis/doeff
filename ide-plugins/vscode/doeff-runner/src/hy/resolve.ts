// 「どの定義に飛ぶか」「どこが参照か」を決める解決の論理。索引の読む面と Python の source・workspace の外の module の口だけを受け取り、
// VS Code にも子 process にも触らない(テストは fixture の索引と一時 dir の .py で撃つ)。

import * as path from 'path';
import type { HyDefinition, HyFileIndex, HyImport, HyRange } from './contract';
import { mangle, mangleDotted } from './mangle';
import type { ExternalModuleSource } from './external';
import { findPythonDefinitions, type PythonModuleSource } from './python';
import type { HyIndexView, HyStoreEntry } from './store';

/** 定義へ移動の行き先 1 件。 */
export type DefinitionTarget =
  | { readonly tag: 'hy-definition'; readonly path: string; readonly module: string; readonly definition: HyDefinition }
  | { readonly tag: 'hy-module'; readonly path: string; readonly module: string }
  | {
      readonly tag: 'python-definition';
      readonly path: string;
      readonly module: string;
      readonly range: HyRange;
    }
  | { readonly tag: 'python-module'; readonly path: string; readonly module: string };

/** どの段で見つけたか(a 同じ file・b import 先の Hy・c import 先の Python・c' workspace の外・d workspace 全体)。 */
export type ResolutionTier =
  | 'same-file'
  | 'import-hy'
  | 'import-python'
  | 'import-external'
  | 'workspace'
  | 'module-only'
  | 'none';

export interface DefinitionResolution {
  readonly tier: ResolutionTier;
  readonly targets: readonly DefinitionTarget[];
  /** 行き先が 1 つの module に定まった時の module 名(参照の絞り込みに使う)。定まらなければ null。 */
  readonly module: string | null;
  /** 探す途中で読めなかった file 等(Output に出す)。 */
  readonly problems: readonly string[];
}

/** 解決の依頼 — どの file のどの記号か。 */
export interface SymbolQuery {
  readonly filePath: string;
  /** 書かれたとおりの名前(dotted の 1 区切り) */
  readonly name: string;
  /** dotted の前の区切り(無ければ null) */
  readonly qualifier: string | null;
}

/** 参照の一覧の 1 件。 */
export interface ReferenceLocation {
  readonly path: string;
  readonly range: HyRange;
}

/** import が束ねる先 — module(と、その中の名前。module そのものなら null)。 */
interface ImportBinding {
  readonly module: string;
  readonly symbol: string | null;
  /** `(import m [Name])` の Name 経由の dotted(`Name.member`)なら Name(入れ物の名前) */
  readonly container: string | null;
}

/** file が package の __init__ かを見る(相対 import の基準が変わる)。 */
function isPackageInit(filePath: string): boolean {
  return /^__init__\.(hy|hyk|hyp)$/.test(path.basename(filePath));
}

/** 相対 import(`.foo`・`..foo`)を、書いた file の module を基準に絶対の dotted 名へ直す。 */
export function absoluteModule(file: HyFileIndex, module: string): string {
  const dots = /^\.*/.exec(module)?.[0].length ?? 0;
  if (dots === 0) {
    return module;
  }
  const base = file.module.split('.').filter((p) => p !== '');
  const pkg = isPackageInit(file.path) ? base : base.slice(0, -1);
  const up = pkg.slice(0, Math.max(0, pkg.length - (dots - 1)));
  const rest = module.slice(dots);
  return [...up, ...(rest === '' ? [] : rest.split('.'))].join('.');
}

/** import が file の中に作る名前(別名 > 名前 > module)。 */
function boundName(imp: HyImport): string {
  return imp.alias ?? imp.name ?? imp.module;
}

/** 修飾の無い名前 m が、file の import のどれで束ねられているかを返す。 */
function bindingForName(file: HyFileIndex, mangled: string): ImportBinding | undefined {
  for (const imp of file.imports) {
    if (mangleDotted(boundName(imp)) !== mangled) {
      continue;
    }
    const module = absoluteModule(file, imp.module);
    return imp.name === null
      ? { module, symbol: null, container: null }
      : { module, symbol: mangle(imp.name), container: null };
  }
  return undefined;
}

/** dotted の `q.m` の q を file の import で解き、m の在る module(と入れ物)を返す。 */
function bindingForQualified(file: HyFileIndex, qualifier: string, mangled: string): ImportBinding[] {
  const q = mangleDotted(qualifier);
  const found: ImportBinding[] = [];
  for (const imp of file.imports) {
    const bound = mangleDotted(boundName(imp));
    const module = absoluteModule(file, imp.module);
    if (imp.name === null) {
      if (q === bound) {
        found.push({ module, symbol: mangled, container: null });
      } else if (q.startsWith(bound + '.')) {
        found.push({ module: module + q.slice(bound.length), symbol: mangled, container: null });
      }
      continue;
    }
    if (q === bound) {
      // `(import pkg [sub])` の sub.fn か、`(import m [Class])` の Class.member
      found.push({ module: `${module}.${imp.name}`, symbol: mangled, container: null });
      found.push({ module, symbol: mangled, container: mangle(imp.name) });
    }
  }
  return found;
}

/** module の Hy の file から、名前(と入れ物)が一致する定義を集める。 */
function hyDefinitionsInModule(
  index: HyIndexView,
  module: string,
  symbol: string,
  container: string | null
): DefinitionTarget[] {
  const targets: DefinitionTarget[] = [];
  for (const entry of index.byModule(module)) {
    for (const def of entry.file.definitions) {
      if (isMember(def, symbol, container)) {
        targets.push(hyTarget(entry, def));
      }
    }
  }
  return targets;
}

/** 定義が「入れ物 container(null は top level)の中の名前 symbol」かを見る。 */
function isMember(def: HyDefinition, symbol: string, container: string | null): boolean {
  const containerMatches =
    container === null ? def.container === null : def.container !== null && mangle(def.container) === container;
  return def.mangled === symbol && containerMatches;
}

/** 解決の途中の 1 段の答え — 定義・(名前は無いが見つかった)module の file・報告すべき問題。 */
interface StepTargets {
  readonly definitions: DefinitionTarget[];
  readonly modules: DefinitionTarget[];
  readonly problems: string[];
}

/**
 * c'. workspace の外(uv の git / path 依存の package 等)の module を口に聞く。
 * Hy なら取った 1 file の索引の定義、Python なら Python の名前探しで行き先を作る。
 */
async function externalTargets(
  external: ExternalModuleSource,
  source: PythonModuleSource,
  index: HyIndexView,
  current: HyStoreEntry,
  binding: ImportBinding
): Promise<StepTargets> {
  const prefetch = current.file.imports
    .map((imp) => absoluteModule(current.file, imp.module))
    .filter((module) => index.byModule(module).length === 0);
  const result = await external.lookup({ root: current.root, module: binding.module, prefetch });
  switch (result.tag) {
    case 'hy': {
      const moduleTarget: DefinitionTarget = { tag: 'hy-module', path: result.path, module: result.file.module };
      if (binding.symbol === null) {
        return { definitions: [moduleTarget], modules: [], problems: [] };
      }
      const symbol = binding.symbol;
      const definitions = result.file.definitions
        .filter((def) => isMember(def, symbol, binding.container))
        .map((definition): DefinitionTarget => ({
          tag: 'hy-definition',
          path: result.path,
          module: result.file.module,
          definition
        }));
      return { definitions, modules: [moduleTarget], problems: [] };
    }
    case 'python':
      if (binding.container !== null) {
        // Python の class の中の member までは追わない
        return { definitions: [], modules: [], problems: [] };
      }
      if (binding.symbol === null) {
        return {
          definitions: [{ tag: 'python-module', path: result.path, module: binding.module }],
          modules: [],
          problems: []
        };
      }
      return pythonTargetsIn(source, [result.path], binding.module, binding.symbol);
    case 'unavailable':
      return { definitions: [], modules: [], problems: [result.reason] };
    case 'not-found':
    case 'skipped':
      return { definitions: [], modules: [], problems: [] };
    default: {
      const unreachable: never = result;
      throw new Error(`網羅されていない答え: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 索引の 1 件と定義から行き先を作る。 */
function hyTarget(entry: HyStoreEntry, definition: HyDefinition): DefinitionTarget {
  return { tag: 'hy-definition', path: entry.file.path, module: entry.file.module, definition };
}

/** Python の module の中の名前を探す。見つからなくても module の file があればそれを別に返す。 */
async function pythonTargets(
  source: PythonModuleSource,
  module: string,
  symbol: string | null
): Promise<StepTargets> {
  return pythonTargetsIn(source, await source.findModuleFiles(module), module, symbol);
}

/** 場所の分かった Python の module の file の中から名前を探す(workspace の中と外で共用)。 */
async function pythonTargetsIn(
  source: PythonModuleSource,
  files: readonly string[],
  module: string,
  symbol: string | null
): Promise<StepTargets> {
  const definitions: DefinitionTarget[] = [];
  const modules: DefinitionTarget[] = files.map((file) => ({ tag: 'python-module', path: file, module }));
  const problems: string[] = [];
  if (symbol === null) {
    return { definitions, modules, problems };
  }
  for (const file of files) {
    const read = await source.readText(file);
    if (read.tag === 'unreadable') {
      problems.push(read.reason);
      continue;
    }
    for (const loc of findPythonDefinitions(read.text, symbol)) {
      definitions.push({
        tag: 'python-definition',
        path: file,
        module,
        range: {
          start: { line: loc.line, character: loc.character },
          end: { line: loc.line, character: loc.character + loc.length }
        }
      });
    }
  }
  return { definitions, modules, problems };
}

/** 行き先がすべて同じ module なら、その名前を返す。 */
function commonModule(targets: readonly DefinitionTarget[]): string | null {
  const modules = new Set(targets.map((t) => t.module));
  return modules.size === 1 ? [...modules][0] : null;
}

/** 行き先を段の名前と合わせて解決の結果にする。 */
function resolution(
  tier: ResolutionTier,
  targets: readonly DefinitionTarget[],
  problems: readonly string[]
): DefinitionResolution {
  return { tier, targets, module: commonModule(targets), problems };
}

/**
 * 定義へ移動の解決。順は a 同じ file → b import 先の Hy の module → c import 先の Python の module →
 * c' workspace の外の module(Python 環境に聞く)→ d workspace 全体の同名の定義
 * (最後に、名前は無くても module の file だけ見つかった時はその file)。
 */
export async function resolveDefinition(
  index: HyIndexView,
  source: PythonModuleSource,
  external: ExternalModuleSource,
  query: SymbolQuery
): Promise<DefinitionResolution> {
  const mangled = mangle(query.name);
  const current = index.get(query.filePath);
  const problems: string[] = [];

  // a. 同じ file の top level の定義(修飾つきなら、同じ file の入れ物の member)
  if (current !== undefined) {
    const local = current.file.definitions.filter((def) => {
      if (def.mangled !== mangled) {
        return false;
      }
      if (query.qualifier === null) {
        return def.container === null;
      }
      return def.container !== null && mangle(def.container) === mangleDotted(query.qualifier);
    });
    if (local.length > 0) {
      return resolution('same-file', local.map((def) => hyTarget(current, def)), problems);
    }
  }

  // b・c. import から module を決め、Hy の索引 → Python の source の順に探す
  const moduleOnly: DefinitionTarget[] = [];
  if (current !== undefined) {
    const bindings =
      query.qualifier === null
        ? [bindingForName(current.file, mangled)].filter((b): b is ImportBinding => b !== undefined)
        : bindingForQualified(current.file, query.qualifier, mangled);
    for (const binding of bindings) {
      const hyModule = index.byModule(binding.module);
      if (hyModule.length > 0) {
        if (binding.symbol === null) {
          return resolution(
            'import-hy',
            hyModule.map((entry) => ({ tag: 'hy-module', path: entry.file.path, module: entry.file.module })),
            problems
          );
        }
        const found = hyDefinitionsInModule(index, binding.module, binding.symbol, binding.container);
        if (found.length > 0) {
          return resolution('import-hy', found, problems);
        }
        continue;
      }
      if (binding.container === null) {
        // 入れ物の member は workspace の Python の class の中まで追わない
        const py = await pythonTargets(source, binding.module, binding.symbol);
        problems.push(...py.problems);
        if (py.definitions.length > 0) {
          return resolution('import-python', py.definitions, problems);
        }
        if (binding.symbol === null && py.modules.length > 0) {
          return resolution('import-python', py.modules, problems);
        }
        if (py.modules.length > 0) {
          moduleOnly.push(...py.modules);
          continue;
        }
      }
      // c'. workspace の索引にも workspace の中の .py にも無い module は、workspace の Python 環境に聞く
      const outside = await externalTargets(external, source, index, current, binding);
      problems.push(...outside.problems);
      if (outside.definitions.length > 0) {
        return resolution('import-external', outside.definitions, problems);
      }
      moduleOnly.push(...outside.modules);
    }
  }

  // d. workspace 全体の同名の定義
  const everywhere: DefinitionTarget[] = index
    .definitionsNamed(mangled)
    .map(({ entry, definition }) => hyTarget(entry, definition));
  if (everywhere.length > 0) {
    return resolution('workspace', everywhere, problems);
  }
  if (moduleOnly.length > 0) {
    return resolution('module-only', moduleOnly, problems);
  }
  return { tier: 'none', targets: [], module: null, problems };
}

/** 位置の範囲を文字列のキーにする(重複を落とす用)。 */
function locationKey(filePath: string, range: HyRange): string {
  return `${filePath}:${range.start.line}:${range.start.character}:${range.end.line}:${range.end.character}`;
}

/**
 * 参照 1 件が target の module を指すかを判定する。
 * true = 指す・false = 別の module を指すと分かる・'unknown' = 絞れない(名前だけで数える)。
 */
function referencePointsTo(
  file: HyFileIndex,
  mangled: string,
  qualifier: string | null,
  target: string
): boolean | 'unknown' {
  const wanted = mangleDotted(target);
  if (qualifier === null) {
    if (mangleDotted(file.module) === wanted) {
      return true;
    }
    const binding = bindingForName(file, mangled);
    if (binding !== undefined) {
      return mangleDotted(binding.module) === wanted;
    }
    if (file.definitions.some((def) => def.container === null && def.mangled === mangled)) {
      return false; // この file の同名の定義が名前を覆っている
    }
    return 'unknown';
  }
  const bindings = bindingForQualified(file, qualifier, mangled);
  if (bindings.length === 0) {
    return 'unknown';
  }
  return bindings.some((b) => mangleDotted(b.module) === wanted);
}

/**
 * 参照の一覧 — 全 file の references と definitions から、同じ mangled 名の位置を集める。
 * module が定まっていれば qualifier と import で絞り、絞れない参照と module 不定の時は名前だけで集める。
 */
export function collectReferences(
  index: HyIndexView,
  name: string,
  targetModule: string | null,
  includeDeclaration: boolean
): readonly ReferenceLocation[] {
  const mangled = mangle(name);
  const seen = new Set<string>();
  const found: ReferenceLocation[] = [];
  // 同じ位置を 2 度数えないように、一覧へ 1 度だけ足す
  const add =(filePath: string, range: HyRange): void => {
    const key = locationKey(filePath, range);
    if (!seen.has(key)) {
      seen.add(key);
      found.push({ path: filePath, range });
    }
  };
  const declarationKeys = new Set<string>();
  for (const entry of index.entries()) {
    const file = entry.file;
    for (const def of file.definitions) {
      if (def.mangled !== mangled) {
        continue;
      }
      const inTarget = targetModule === null || mangleDotted(file.module) === mangleDotted(targetModule);
      declarationKeys.add(locationKey(file.path, def.range));
      if (includeDeclaration && inTarget) {
        add(file.path, def.range);
      }
    }
  }
  for (const entry of index.entries()) {
    const file = entry.file;
    for (const ref of file.references) {
      if (ref.mangled !== mangled) {
        continue;
      }
      if (declarationKeys.has(locationKey(file.path, ref.range))) {
        continue; // 定義の名前の位置は上で扱った
      }
      if (targetModule !== null && referencePointsTo(file, mangled, ref.qualifier, targetModule) === false) {
        continue;
      }
      add(file.path, ref.range);
    }
  }
  return found;
}
