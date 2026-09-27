// doeff-linter のエディタ向け出力(`--output-format editor-json`・契約 lint-contract-v1.md 版 1)の型と、読み込みの唯一の検査。
// linter が規則の判定の唯一の正本で、拡張はこの JSON を表示するだけ(自分では判定しない)。
// 版が違う・欄が欠けた・型が違う JSON は理由つきで捨て、既定値で埋めない。

export const LINT_CONTRACT_VERSION = 1;

/** 違反の重さ(契約の閉じた集合)。error = 新しい破れ、warning = 登録簿に載った既知の破れ、info。 */
export const LINT_SEVERITIES = ['error', 'warning', 'info'] as const;
export type LintSeverity = (typeof LINT_SEVERITIES)[number];

export interface LintPosition {
  readonly line: number;
  readonly character: number;
}

export interface LintRange {
  readonly start: LintPosition;
  readonly end: LintPosition;
}

/** 違反 1 件。 */
export interface LintViolation {
  /** doeff-linter の規則の ID(`DOEFF101` など) */
  readonly rule: string;
  /** 規則に結びつけた ADR の law の名(無ければ null) */
  readonly law: string | null;
  /** ADR の名(無ければ null) */
  readonly adr: string | null;
  readonly severity: LintSeverity;
  readonly path: string;
  readonly range: LintRange;
  readonly message: string;
  /** 直し方の 1 行(無ければ null) */
  readonly hint: string | null;
  /** 登録簿の鍵(無ければ null) */
  readonly key: string | null;
  /** 登録簿に載っている既知の破れか */
  readonly registered: boolean;
  /** これは何か・なぜ違反か・law の :statement(更新 3。古い linter の出力には無く null) */
  readonly explanation: LintExplanation | null;
  /** 出どころ(更新 5。古い linter の出力には無く、その時は決定的な規則 = linter とみなす) */
  readonly source: LintSource;
  /** Jev の判定の確率(Jev の違反だけ。他は null) */
  readonly probability: number | null;
}

/** 違反の出どころ(更新 5)— 決定的な規則か、Jev の意味の判定か。 */
export const LINT_SOURCES = ['linter', 'jev'] as const;
export type LintSource = (typeof LINT_SOURCES)[number];

/** 較正の見張りの結果(更新 5)。 */
export const LINT_CALIBRATIONS = ['not-run', 'ok', 'drifted', 'failed'] as const;
export type LintCalibration = (typeof LINT_CALIBRATIONS)[number];

/** 意味の規則の要約(更新 5)— unjudged は cache に答えの無い定義の数(未判定 — 合格ではない)。 */
export interface LintSemantic {
  readonly model: string;
  readonly wire: string;
  readonly judged: number;
  readonly unjudged: number;
  readonly asked: number;
  readonly costUsd: number;
  readonly inputTokens: number;
  readonly servedModel: string | null;
  readonly calibration: LintCalibration;
}

/** 違反の説明(更新 3)— 文は linter が作る。 */
export interface LintExplanation {
  /** これは何か(例: この file は層 core — path が controllers/core/ の下) */
  readonly subject: string;
  /** なぜ違反か */
  readonly reason: string;
  /** ADR の law の :statement の逐語(無ければ null) */
  readonly lawStatement: string | null;
}

/** 層 1 つの説明(更新 3)— 中身は repo ごとの linter の設定から。設定に無い欄は null。 */
export interface LintLayer {
  readonly name: string;
  readonly summary: string | null;
  readonly knows: string | null;
  readonly doesNotKnow: string | null;
  readonly question: string | null;
}

/** 地図の材料 — module(file)ごとの層・文脈・役割と違反の数(linter の要約)。 */
export interface LintModule {
  readonly path: string;
  /** 層(core・intent・protocol・foundation・entry …)。層の外なら null */
  readonly layer: string | null;
  /** service(dir の service の段・更新 3 の後の追加。古い linter の出力には無く null) */
  readonly service: string | null;
  readonly context: string | null;
  readonly role: string | null;
  readonly violations: number;
  /** その file の層を何で決めたか(更新 3。古い linter の出力には無く null) */
  readonly layerReason: string | null;
}

/** 走らせた規則の一覧の 1 件(何を見ているか・何を見ていないか)。 */
export interface LintRule {
  readonly rule: string;
  readonly adr: string | null;
  readonly statement: string;
  /** 針(判定)がつながっているか。false の規則は違反を出さない */
  readonly wired: boolean;
}

/** linter の出力の全体。 */
export interface LintReport {
  readonly version: number;
  readonly root: string;
  readonly violations: readonly LintViolation[];
  readonly modules: readonly LintModule[];
  readonly rules: readonly LintRule[];
  /** 層の説明(更新 3。古い linter の出力には無く []。並びは linter の層の順) */
  readonly layers: readonly LintLayer[];
  /** 意味の規則の要約(更新 5。設定が無い・古い linter なら null) */
  readonly semantic: LintSemantic | null;
  /** linter 自身が読めなかった file など */
  readonly errors: readonly string[];
}

export type LintParseResult =
  | { readonly tag: 'ok'; readonly report: LintReport }
  | { readonly tag: 'rejected'; readonly reason: string };

/** 検査の途中で契約違反を見つけた時に投げる内部の例外(parse の外へは出さない)。 */
class LintContractViolation extends Error {}

type JsonObject = { readonly [key: string]: unknown };

/** 値が JSON の object(配列でない)であるかを見る。 */
function isObject(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** 必須の欄を取り出す(欠けていれば契約違反)。 */
function field(obj: JsonObject, key: string, where: string): unknown {
  if (!Object.prototype.hasOwnProperty.call(obj, key)) {
    throw new LintContractViolation(`${where}: 欄 "${key}" が無い`);
  }
  return obj[key];
}

/** 文字列の欄を検める。 */
function str(obj: JsonObject, key: string, where: string): string {
  const value = field(obj, key, where);
  if (typeof value !== 'string') {
    throw new LintContractViolation(`${where}.${key}: 文字列でない`);
  }
  return value;
}

/** 文字列か null の欄を検める(欄そのものは必須)。 */
function strOrNull(obj: JsonObject, key: string, where: string): string | null {
  const value = field(obj, key, where);
  if (value !== null && typeof value !== 'string') {
    throw new LintContractViolation(`${where}.${key}: 文字列でも null でもない`);
  }
  return value;
}

/** 真偽値の欄を検める。 */
function bool(obj: JsonObject, key: string, where: string): boolean {
  const value = field(obj, key, where);
  if (typeof value !== 'boolean') {
    throw new LintContractViolation(`${where}.${key}: 真偽値でない`);
  }
  return value;
}

/** 0 以上の整数の欄を検める。 */
function nat(obj: JsonObject, key: string, where: string): number {
  const value = field(obj, key, where);
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 0) {
    throw new LintContractViolation(`${where}.${key}: 0 以上の整数でない`);
  }
  return value;
}

/** 配列の欄の各要素を検める。 */
function list<T>(obj: JsonObject, key: string, where: string, each: (value: unknown, at: string) => T): T[] {
  const value = field(obj, key, where);
  if (!Array.isArray(value)) {
    throw new LintContractViolation(`${where}.${key}: 配列でない`);
  }
  return value.map((item, i) => each(item, `${where}.${key}[${i}]`));
}

/** object の要素であることを検める。 */
function asObject(value: unknown, where: string): JsonObject {
  if (!isObject(value)) {
    throw new LintContractViolation(`${where}: object でない`);
  }
  return value;
}

/** 位置を検める。 */
function position(obj: JsonObject, key: string, where: string): LintPosition {
  const value = asObject(field(obj, key, where), `${where}.${key}`);
  return { line: nat(value, 'line', `${where}.${key}`), character: nat(value, 'character', `${where}.${key}`) };
}

/** 範囲を検める。 */
function range(obj: JsonObject, where: string): LintRange {
  const value = asObject(field(obj, 'range', where), `${where}.range`);
  return { start: position(value, 'start', `${where}.range`), end: position(value, 'end', `${where}.range`) };
}

/** 重さを検める(閉じた集合)。 */
function severity(obj: JsonObject, where: string): LintSeverity {
  const value = str(obj, 'severity', where);
  const found = LINT_SEVERITIES.find((s) => s === value);
  if (found === undefined) {
    throw new LintContractViolation(`${where}.severity: 契約に無い値 "${value}"`);
  }
  return found;
}

/** 更新 3 で足した欄を読む — 無ければ null(古い linter)、在れば検める。 */
function optional<T>(obj: JsonObject, key: string, where: string, read: (value: unknown, at: string) => T): T | null {
  if (!Object.prototype.hasOwnProperty.call(obj, key) || obj[key] === null) {
    return null;
  }
  return read(obj[key], `${where}.${key}`);
}

/** 文字列か null の値を検める。 */
function textOrNull(value: unknown, where: string): string | null {
  if (value !== null && typeof value !== 'string') {
    throw new LintContractViolation(`${where}: 文字列でも null でもない`);
  }
  return value;
}

/** 違反の説明を検める。 */
function explanation(value: unknown, where: string): LintExplanation {
  const obj = asObject(value, where);
  return {
    subject: str(obj, 'subject', where),
    reason: str(obj, 'reason', where),
    lawStatement: strOrNull(obj, 'law_statement', where)
  };
}

/** 閉じた集合の文字列を検める。 */
function closed<T extends string>(value: unknown, where: string, allowed: readonly T[]): T {
  const found = allowed.find((a) => a === value);
  if (found === undefined) {
    throw new LintContractViolation(`${where}: 契約に無い値 ${JSON.stringify(value)}`);
  }
  return found;
}

/** 確率(0〜1 の数)を検める。 */
function probability(value: unknown, where: string): number {
  if (typeof value !== 'number' || !(value >= 0 && value <= 1)) {
    throw new LintContractViolation(`${where}: 0〜1 の数でない`);
  }
  return value;
}

/** 0 以上の数(費用)を検める。 */
function nonNegative(obj: JsonObject, key: string, where: string): number {
  const value = field(obj, key, where);
  if (typeof value !== 'number' || !(value >= 0)) {
    throw new LintContractViolation(`${where}.${key}: 0 以上の数でない`);
  }
  return value;
}

/** 意味の規則の要約を検める。 */
function semanticSummary(value: unknown, where: string): LintSemantic {
  const obj = asObject(value, where);
  return {
    model: str(obj, 'model', where),
    wire: str(obj, 'wire', where),
    judged: nat(obj, 'judged', where),
    unjudged: nat(obj, 'unjudged', where),
    asked: nat(obj, 'asked', where),
    costUsd: nonNegative(obj, 'cost_usd', where),
    inputTokens: nat(obj, 'input_tokens', where),
    servedModel: strOrNull(obj, 'served_model', where),
    calibration: closed(field(obj, 'calibration', where), `${where}.calibration`, LINT_CALIBRATIONS)
  };
}

/** 層の説明 1 件を検める。 */
function lintLayer(value: unknown, where: string): LintLayer {
  const obj = asObject(value, where);
  return {
    name: str(obj, 'name', where),
    summary: strOrNull(obj, 'summary', where),
    knows: strOrNull(obj, 'knows', where),
    doesNotKnow: strOrNull(obj, 'does_not_know', where),
    question: strOrNull(obj, 'question', where)
  };
}

/** 違反 1 件を検める。 */
function violation(value: unknown, where: string): LintViolation {
  const obj = asObject(value, where);
  return {
    rule: str(obj, 'rule', where),
    law: strOrNull(obj, 'law', where),
    adr: strOrNull(obj, 'adr', where),
    severity: severity(obj, where),
    path: str(obj, 'path', where),
    range: range(obj, where),
    message: str(obj, 'message', where),
    hint: strOrNull(obj, 'hint', where),
    key: strOrNull(obj, 'key', where),
    registered: bool(obj, 'registered', where),
    explanation: optional(obj, 'explanation', where, explanation),
    source: optional(obj, 'source', where, (v, at) => closed(v, at, LINT_SOURCES)) ?? 'linter',
    probability: optional(obj, 'probability', where, probability)
  };
}

/** module の要約 1 件を検める。 */
function lintModule(value: unknown, where: string): LintModule {
  const obj = asObject(value, where);
  return {
    path: str(obj, 'path', where),
    layer: strOrNull(obj, 'layer', where),
    service: optional(obj, 'service', where, textOrNull),
    context: strOrNull(obj, 'context', where),
    role: strOrNull(obj, 'role', where),
    violations: nat(obj, 'violations', where),
    layerReason: optional(obj, 'layer_reason', where, textOrNull)
  };
}

/** 規則 1 件を検める。 */
function lintRule(value: unknown, where: string): LintRule {
  const obj = asObject(value, where);
  return {
    rule: str(obj, 'rule', where),
    adr: strOrNull(obj, 'adr', where),
    statement: str(obj, 'statement', where),
    wired: bool(obj, 'wired', where)
  };
}

/** 文字列 1 つを検める。 */
function text(value: unknown, where: string): string {
  if (typeof value !== 'string') {
    throw new LintContractViolation(`${where}: 文字列でない`);
  }
  return value;
}

/** linter の stdout(文字列)を契約の型に読む唯一の入口。どこかが契約に合わなければ全体を理由つきで捨てる。 */
export function parseLintJson(stdout: string): LintParseResult {
  let raw: unknown;
  try {
    raw = JSON.parse(stdout);
  } catch (error) {
    return { tag: 'rejected', reason: `JSON として読めない: ${String(error)}` };
  }
  try {
    const obj = asObject(raw, '$');
    const version = field(obj, 'version', '$');
    if (version !== LINT_CONTRACT_VERSION) {
      return { tag: 'rejected', reason: `契約の版が違う(期待 ${LINT_CONTRACT_VERSION}、実際 ${JSON.stringify(version)})` };
    }
    return {
      tag: 'ok',
      report: {
        version: LINT_CONTRACT_VERSION,
        root: str(obj, 'root', '$'),
        violations: list(obj, 'violations', '$', violation),
        modules: list(obj, 'modules', '$', lintModule),
        rules: list(obj, 'rules', '$', lintRule),
        layers: Object.prototype.hasOwnProperty.call(obj, 'layers') ? list(obj, 'layers', '$', lintLayer) : [],
        semantic: optional(obj, 'semantic', '$', semanticSummary),
        errors: list(obj, 'errors', '$', text)
      }
    };
  } catch (error) {
    if (error instanceof LintContractViolation) {
      return { tag: 'rejected', reason: error.message };
    }
    throw error;
  }
}
