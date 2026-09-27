// `doeff-indexer hy-index` の出力 JSON(契約 版 3)の型と、読み込みの唯一の検査。
// 契約の正本 = experiments/hy-highlighter/hy-index-contract.md(版 1)+ -v2.md(版 2)+ -v3.md(版 3 = 生の副作用の判定)。欄が欠けた・型が違う・版が違う JSON は
// 理由つきで捨て、既定値で埋めない。

export const HY_INDEX_CONTRACT_VERSION = 3;

/** 生の副作用の分類(契約の閉じた集合 — 判定と目録は hy-index が持ち、拡張は読むだけ)。 */
export const RAW_CATEGORIES = ['http', 'async', 'time', 'random', 'file', 'process', 'env', 'network', 'db', 'thread'] as const;
export type RawCategory = (typeof RAW_CATEGORIES)[number];

/** 証拠の見つけ方。 */
export const RAW_EVIDENCE_KINDS = ['name', 'builtin', 'method'] as const;
export type RawEvidenceKind = (typeof RAW_EVIDENCE_KINDS)[number];

/** 証拠の強さ。 */
export const RAW_STRENGTHS = ['strong', 'weak'] as const;
export type RawStrength = (typeof RAW_STRENGTHS)[number];

/** 経由の証拠を計算したか。 */
export const RAW_VIA_SCOPES = ['computed', 'not-computed'] as const;
export type RawViaScope = (typeof RAW_VIA_SCOPES)[number];

/** 契約の kind の一覧(閉じた集合)。足す時は契約と同時に直す。 */
export const HY_DEFINITION_KINDS = [
  'defn',
  'defn/a',
  'defmacro',
  'defk',
  'deff',
  'defp',
  'defpp',
  'fnk-binding',
  'defclass',
  'defrecord',
  'defenum',
  'enum-member',
  'field',
  'method',
  'defhandler',
  'defeffect',
  'effect-clause',
  'deftest',
  'defadr',
  'defsemgrep',
  'law',
  'defpipeline',
  'defworkflow',
  'defphase',
  'defmcp-tool',
  'deftype',
  'defmain',
  'variable'
] as const;

export type HyDefinitionKind = (typeof HY_DEFINITION_KINDS)[number];

export interface HyPosition {
  /** 0 始まりの行 */
  readonly line: number;
  /** UTF-16 の code unit での列(VS Code の Position と同じ) */
  readonly character: number;
}

export interface HyRange {
  readonly start: HyPosition;
  readonly end: HyPosition;
}

export interface HyDefinition {
  readonly name: string;
  readonly mangled: string;
  readonly kind: HyDefinitionKind;
  readonly range: HyRange;
  readonly fullRange: HyRange;
  readonly container: string | null;
  readonly docstring: string | null;
  readonly params: readonly string[];
  /** defclass / defrecord の基底の記号(書かれたとおり、dotted も 1 つの文字列)と、defeffect の ["EffectBase"]。他の kind は常に [] */
  readonly bases: readonly string[];
  /** 生の副作用の証拠(版 3 — 事実であって規則の判定ではない。判定の正本は linter) */
  readonly raw: HyRawMark;
  /** 定義が名乗ったタグ(契約の辞書の :tags と defeffect の :tags・文字列の値の鍵だけ)。無ければ null。版 3 への追加の欄なので、欄が無い出力は null として読む */
  readonly tags: Readonly<Record<string, string>> | null;
}

/** 生の副作用の証拠 1 件。 */
export interface HyRawEvidence {
  readonly category: RawCategory;
  readonly name: string;
  readonly kind: RawEvidenceKind;
  readonly strength: RawStrength;
  readonly path: string;
  readonly range: HyRange;
}

/** 経路の 1 段(呼んだ定義)。 */
export interface HyRawStep {
  readonly path: string;
  readonly index: number;
  readonly name: string;
}

/** 呼ぶ定義を通した証拠。 */
export interface HyRawVia {
  readonly through: readonly HyRawStep[];
  readonly evidence: HyRawEvidence;
}

/** 定義 1 つの証拠。 */
export interface HyRawMark {
  readonly direct: readonly HyRawEvidence[];
  readonly via: readonly HyRawVia[];
}

/** 呼び出し 1 件(版 2)— `(` の直後の記号。effect の生成も関数の呼び出しもここに入る。 */
export interface HyCall {
  /** 呼び出しの頭の記号の最後の区切り(書かれたとおり) */
  readonly callee: string;
  readonly mangled: string;
  readonly qualifier: string | null;
  readonly range: HyRange;
  /** この呼び出しを含む最も内側の定義の、同じ file の definitions の添字。top level の式なら null */
  readonly caller: number | null;
  /** `<-` / yield / yield-from で撃たれている */
  readonly performed: boolean;
}

export interface HyImport {
  readonly module: string;
  readonly name: string | null;
  readonly alias: string | null;
  readonly range: HyRange;
  readonly isRequire: boolean;
}

export interface HyReference {
  readonly name: string;
  readonly mangled: string;
  readonly qualifier: string | null;
  readonly range: HyRange;
}

export interface HyFileIndex {
  readonly path: string;
  readonly module: string;
  readonly definitions: readonly HyDefinition[];
  readonly imports: readonly HyImport[];
  readonly references: readonly HyReference[];
  readonly calls: readonly HyCall[];
  readonly errors: readonly string[];
}

export interface HyIndexDocument {
  readonly version: number;
  readonly root: string;
  readonly files: readonly HyFileIndex[];
  readonly rawVia: RawViaScope;
  readonly rawCatalogProblems: readonly string[];
}

/** 捨てた file の path(読めた時)と理由。 */
export interface RejectedFile {
  readonly path: string | null;
  readonly reason: string;
}

export type HyIndexParseResult =
  | { readonly tag: 'ok'; readonly document: HyIndexDocument; readonly rejected: readonly RejectedFile[] }
  | { readonly tag: 'rejected'; readonly reason: string };

/** 検査の途中で契約違反を見つけた時に投げる内部の例外(parse の外へは出さない)。 */
class ContractViolation extends Error {}

type JsonObject = { readonly [key: string]: unknown };

/** 値が JSON の object(配列でない)であるかを見る。 */
function isObject(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** object から必須の欄を取り出す(欠けていれば契約違反)。 */
function field(obj: JsonObject, key: string, where: string): unknown {
  if (!Object.prototype.hasOwnProperty.call(obj, key)) {
    throw new ContractViolation(`${where}: 欄 "${key}" が無い`);
  }
  return obj[key];
}

/** 文字列の欄を検める。 */
function str(obj: JsonObject, key: string, where: string): string {
  const value = field(obj, key, where);
  if (typeof value !== 'string') {
    throw new ContractViolation(`${where}.${key}: 文字列でない`);
  }
  return value;
}

/** 文字列か null の欄を検める(欄そのものは必須)。 */
function strOrNull(obj: JsonObject, key: string, where: string): string | null {
  const value = field(obj, key, where);
  if (value !== null && typeof value !== 'string') {
    throw new ContractViolation(`${where}.${key}: 文字列でも null でもない`);
  }
  return value;
}

/** 真偽値の欄を検める。 */
function bool(obj: JsonObject, key: string, where: string): boolean {
  const value = field(obj, key, where);
  if (typeof value !== 'boolean') {
    throw new ContractViolation(`${where}.${key}: 真偽値でない`);
  }
  return value;
}

/** 配列の欄を検める。 */
function arr(obj: JsonObject, key: string, where: string): readonly unknown[] {
  const value = field(obj, key, where);
  if (!Array.isArray(value)) {
    throw new ContractViolation(`${where}.${key}: 配列でない`);
  }
  return value;
}

/** object の欄を検める。 */
function obj(parent: JsonObject, key: string, where: string): JsonObject {
  const value = field(parent, key, where);
  if (!isObject(value)) {
    throw new ContractViolation(`${where}.${key}: object でない`);
  }
  return value;
}

/** 0 以上の整数の欄を検める(行・列)。 */
function nat(parent: JsonObject, key: string, where: string): number {
  const value = field(parent, key, where);
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 0) {
    throw new ContractViolation(`${where}.${key}: 0 以上の整数でない`);
  }
  return value;
}

/** 位置 {line, character} を検める。 */
function parsePosition(value: JsonObject, where: string): HyPosition {
  return { line: nat(value, 'line', where), character: nat(value, 'character', where) };
}

/** 範囲 {start, end} を検める。 */
function parseRange(parent: JsonObject, key: string, where: string): HyRange {
  const value = obj(parent, key, where);
  const at = `${where}.${key}`;
  return {
    start: parsePosition(obj(value, 'start', at), `${at}.start`),
    end: parsePosition(obj(value, 'end', at), `${at}.end`)
  };
}

/** 文字列が契約の kind のどれかであるかを見る。 */
export function isHyDefinitionKind(value: string): value is HyDefinitionKind {
  return (HY_DEFINITION_KINDS as readonly string[]).includes(value);
}

/** 文字列の配列の欄を検める。 */
function strArray(obj: JsonObject, key: string, where: string): string[] {
  return arr(obj, key, where).map((item, i) => {
    if (typeof item !== 'string') {
      throw new ContractViolation(`${where}.${key}[${i}]: 文字列でない`);
    }
    return item;
  });
}

/** 閉じた集合の文字列の欄を検める。 */
function oneOf<T extends string>(parent: JsonObject, key: string, where: string, allowed: readonly T[]): T {
  const value = str(parent, key, where);
  const found = allowed.find((a) => a === value);
  if (found === undefined) {
    throw new ContractViolation(`${where}.${key}: 契約に無い値 "${value}"`);
  }
  return found;
}

/** 0 以上の整数の値を検める(配列の添字)。 */
function index(parent: JsonObject, key: string, where: string): number {
  return nat(parent, key, where);
}

/** 証拠 1 件を検める。 */
function parseEvidence(value: unknown, where: string): HyRawEvidence {
  if (!isObject(value)) {
    throw new ContractViolation(`${where}: object でない`);
  }
  return {
    category: oneOf(value, 'category', where, RAW_CATEGORIES),
    name: str(value, 'name', where),
    kind: oneOf(value, 'kind', where, RAW_EVIDENCE_KINDS),
    strength: oneOf(value, 'strength', where, RAW_STRENGTHS),
    path: str(value, 'path', where),
    range: parseRange(value, 'range', where)
  };
}

/** 定義の raw の欄を検める。 */
function parseRawMark(parent: JsonObject, where: string): HyRawMark {
  const raw = obj(parent, 'raw', where);
  const at = `${where}.raw`;
  return {
    direct: arr(raw, 'direct', at).map((e, i) => parseEvidence(e, `${at}.direct[${i}]`)),
    via: arr(raw, 'via', at).map((v, i) => {
      const w = `${at}.via[${i}]`;
      if (!isObject(v)) {
        throw new ContractViolation(`${w}: object でない`);
      }
      return {
        through: arr(v, 'through', w).map((step, j) => {
          const sw = `${w}.through[${j}]`;
          if (!isObject(step)) {
            throw new ContractViolation(`${sw}: object でない`);
          }
          return { path: str(step, 'path', sw), index: index(step, 'index', sw), name: str(step, 'name', sw) };
        }),
        evidence: parseEvidence(field(v, 'evidence', w), `${w}.evidence`)
      };
    })
  };
}

/** 定義 1 件を検める。 */
function parseDefinition(value: unknown, where: string): HyDefinition {
  if (!isObject(value)) {
    throw new ContractViolation(`${where}: object でない`);
  }
  const kind = str(value, 'kind', where);
  if (!isHyDefinitionKind(kind)) {
    throw new ContractViolation(`${where}.kind: 契約に無い kind "${kind}"`);
  }
  const params = strArray(value, 'params', where);
  const bases = strArray(value, 'bases', where);
  if (bases.length > 0 && kind !== 'defclass' && kind !== 'defrecord' && kind !== 'defeffect') {
    throw new ContractViolation(`${where}.bases: ${kind} は基底を持たない`);
  }
  return {
    name: str(value, 'name', where),
    mangled: str(value, 'mangled', where),
    kind,
    range: parseRange(value, 'range', where),
    fullRange: parseRange(value, 'full_range', where),
    container: strOrNull(value, 'container', where),
    docstring: strOrNull(value, 'docstring', where),
    params,
    bases,
    raw: parseRawMark(value, where),
    tags: parseTags(value, where)
  };
}

/** 定義の tags の欄を検める(欄が無い・null は null。在れば文字列の値だけの object)。 */
function parseTags(parent: JsonObject, where: string): Readonly<Record<string, string>> | null {
  const value = parent.tags;
  if (value === undefined || value === null) {
    return null;
  }
  if (!isObject(value)) {
    throw new ContractViolation(`${where}.tags: object でも null でもない`);
  }
  const tags: Record<string, string> = {};
  for (const [key, text] of Object.entries(value)) {
    if (typeof text !== 'string') {
      throw new ContractViolation(`${where}.tags.${key}: 文字列でない`);
    }
    tags[key] = text;
  }
  return tags;
}

/** 呼び出し 1 件を検める(caller は同じ file の definitions の添字の範囲に入ること)。 */
function parseCall(value: unknown, where: string, definitionCount: number): HyCall {
  if (!isObject(value)) {
    throw new ContractViolation(`${where}: object でない`);
  }
  const rawCaller = field(value, 'caller', where);
  let caller: number | null;
  if (rawCaller === null) {
    caller = null;
  } else if (typeof rawCaller === 'number' && Number.isInteger(rawCaller) && rawCaller >= 0 && rawCaller < definitionCount) {
    caller = rawCaller;
  } else {
    throw new ContractViolation(`${where}.caller: definitions の添字でない(${JSON.stringify(rawCaller)})`);
  }
  return {
    callee: str(value, 'callee', where),
    mangled: str(value, 'mangled', where),
    qualifier: strOrNull(value, 'qualifier', where),
    range: parseRange(value, 'range', where),
    caller,
    performed: bool(value, 'performed', where)
  };
}

/** import 1 件を検める。 */
function parseImport(value: unknown, where: string): HyImport {
  if (!isObject(value)) {
    throw new ContractViolation(`${where}: object でない`);
  }
  return {
    module: str(value, 'module', where),
    name: strOrNull(value, 'name', where),
    alias: strOrNull(value, 'alias', where),
    range: parseRange(value, 'range', where),
    isRequire: bool(value, 'is_require', where)
  };
}

/** 参照 1 件を検める。 */
function parseReference(value: unknown, where: string): HyReference {
  if (!isObject(value)) {
    throw new ContractViolation(`${where}: object でない`);
  }
  return {
    name: str(value, 'name', where),
    mangled: str(value, 'mangled', where),
    qualifier: strOrNull(value, 'qualifier', where),
    range: parseRange(value, 'range', where)
  };
}

/** file 1 件を検める。 */
function parseFile(value: JsonObject, where: string): HyFileIndex {
  const errors = arr(value, 'errors', where).map((e, i) => {
    if (typeof e !== 'string') {
      throw new ContractViolation(`${where}.errors[${i}]: 文字列でない`);
    }
    return e;
  });
  const definitions = arr(value, 'definitions', where).map((d, i) =>
    parseDefinition(d, `${where}.definitions[${i}]`)
  );
  return {
    path: str(value, 'path', where),
    module: str(value, 'module', where),
    definitions,
    imports: arr(value, 'imports', where).map((d, i) => parseImport(d, `${where}.imports[${i}]`)),
    references: arr(value, 'references', where).map((d, i) =>
      parseReference(d, `${where}.references[${i}]`)
    ),
    calls: arr(value, 'calls', where).map((d, i) => parseCall(d, `${where}.calls[${i}]`, definitions.length)),
    errors
  };
}

/**
 * hy-index の stdout(文字列)を契約どおりの型に読む唯一の入口。
 * 全体の形・版が違えば全部を捨て、file 単位の契約違反はその file だけを理由つきで捨てる。
 */
export function parseHyIndexJson(text: string): HyIndexParseResult {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (error) {
    return { tag: 'rejected', reason: `JSON として読めない: ${String(error)}` };
  }
  if (!isObject(raw)) {
    return { tag: 'rejected', reason: '最上位が object でない' };
  }
  try {
    const version = field(raw, 'version', '$');
    if (version !== HY_INDEX_CONTRACT_VERSION) {
      return {
        tag: 'rejected',
        reason: `契約の版が違う(期待 ${HY_INDEX_CONTRACT_VERSION}、実際 ${JSON.stringify(version)})`
      };
    }
    const root = str(raw, 'root', '$');
    const rawFiles = arr(raw, 'files', '$');
    const files: HyFileIndex[] = [];
    const rejected: RejectedFile[] = [];
    rawFiles.forEach((rawFile, i) => {
      const where = `$.files[${i}]`;
      if (!isObject(rawFile)) {
        rejected.push({ path: null, reason: `${where}: object でない` });
        return;
      }
      const maybePath = typeof rawFile.path === 'string' ? rawFile.path : null;
      try {
        files.push(parseFile(rawFile, where));
      } catch (error) {
        if (error instanceof ContractViolation) {
          rejected.push({ path: maybePath, reason: error.message });
          return;
        }
        throw error;
      }
    });
    const rawVia = oneOf(raw, 'raw_via', '$', RAW_VIA_SCOPES);
    const rawCatalogProblems = arr(raw, 'raw_catalog_problems', '$').map((p, i) => {
      if (typeof p !== 'string') {
        throw new ContractViolation(`$.raw_catalog_problems[${i}]: 文字列でない`);
      }
      return p;
    });
    return { tag: 'ok', document: { version, root, files, rawVia, rawCatalogProblems }, rejected };
  } catch (error) {
    if (error instanceof ContractViolation) {
      return { tag: 'rejected', reason: error.message };
    }
    throw error;
  }
}
