// doeff-linter のエディタ向け出力(`--output-format editor-json`・契約 版 2)の型と、読み込みの唯一の検査。
// 版 2 = defk / deff の見出し(signatures)と束縛の型(bindings)を足した(agora-redesign #849)。
// linter が規則の判定の唯一の正本で、拡張はこの JSON を表示するだけ(自分では判定しない)。
// 版が違う・欄が欠けた・型が違う JSON は理由つきで捨て、既定値で埋めない。
// ただし閉じた集合のうち linter が先に語を足しうる物(規則の家族・見出しの種類・束縛の形と出どころ・型の式の種類)は、知らない語でも
// 出力を捨てない — その項目だけ既定の見た目(家族は null = 一般の印・見出しと束縛は描かない・型は読めない式)にして、`unknown` に
// 「拡張が古い」の理由として控える(agora-redesign #848 — linter が先に進むと違反の欄が全部消えていた)。

export const LINT_CONTRACT_VERSION = 2;
/** 読める版 — 版 1(見出しと束縛の無い古い linter)も読み、見出しと束縛を空とする(linter の置き場が本線に追いつくまでの間、違反の欄を消さないため)。 */
export const LINT_READABLE_VERSIONS: readonly number[] = [1, 2];

/** 違反の重さ(契約の閉じた集合)。`severity` は登録簿と照合中で下げた後の重さ、`baseSeverity` は規則そのものの重さ。 */
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
  /** 登録簿と照合中で下げる前の規則そのものの重さ(更新 7。古い linter の出力には無く、その時は severity) */
  readonly baseSeverity: LintSeverity;
  /** 新しい・登録簿の既知・照合中(更新 7。古い linter の出力には無く、その時は registered から new か registered) */
  readonly standing: LintStanding;
  /** 規則の重大さ(更新 7。古い linter の出力には無く、その時は baseSeverity から linter の既定の決め方で) */
  readonly level: LintLevel;
  /** これは何か・なぜ違反か・law の :statement(更新 3。古い linter の出力には無く null) */
  readonly explanation: LintExplanation | null;
  /** 出どころ(更新 5。古い linter の出力には無く、その時は決定的な規則 = linter とみなす) */
  readonly source: LintSource;
  /** Jev の判定の確率(Jev の違反だけ。他は null) */
  readonly probability: number | null;
  /** doc-linter が検査した文章の種類。関数の説明は読む面の docstring にも印を付ける。 */
  readonly documentKind?: 'function' | 'comment' | 'document';
}

/** 規則の重大さ(更新 7)— repo が規則ごとに宣言する方針(無い規則は linter が規則そのものの重さから決める)。登録簿で下げない。 */
export const LINT_LEVELS = ['critical', 'major', 'minor', 'info'] as const;
export type LintLevel = (typeof LINT_LEVELS)[number];

/** 重大さの欄の無い古い linter の出力の時の重大さ — linter の既定の決め方(error = major・warning = minor・info = info)の写し。 */
function levelOfSeverity(severity: LintSeverity): LintLevel {
  switch (severity) {
    case 'error':
      return 'major';
    case 'warning':
      return 'minor';
    case 'info':
      return 'info';
    default: {
      const unreachable: never = severity;
      throw new Error(`網羅されていない重さ: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 違反の立場(更新 7)— new = 登録簿に無い新しい破れ、registered = 登録簿に載った既知の破れ、reconciling = 照合中で info に下げた。 */
export const LINT_STANDINGS = ['new', 'registered', 'reconciling'] as const;
export type LintStanding = (typeof LINT_STANDINGS)[number];

/** 違反の出どころ(更新 5)— 決定的な規則か、Jev の意味の判定か。 */
export const LINT_SOURCES = ['linter', 'jev', 'doc-linter'] as const;
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

/** 規則の家族(更新 6)— 違反の欄の行の絵を選ぶ閉じた集合。どの規則がどの家族かは linter が決める。 */
export const LINT_RULE_FAMILIES = ['layer', 'tags', 'raw', 'naming', 'place', 'definition', 'class', 'wire', 'smell', 'jev', 'python', 'law'] as const;
export type LintRuleFamily = (typeof LINT_RULE_FAMILIES)[number];

/** 有効な規則の一覧の 1 件(何を見ているか・何を見ていないか)。この実行で判じたかは LintReport.judgedRules が名乗る(更新 8)。 */
export interface LintRule {
  readonly rule: string;
  readonly adr: string | null;
  readonly statement: string;
  /** 針(判定)がつながっているか。false の規則は違反を出さない */
  readonly wired: boolean;
  /** 短い日本語の名 — 違反の形で書いた物(例: defn を使っている)。更新 6。古い linter の出力には無く null */
  readonly title: string | null;
  /** 規則の家族(更新 6。古い linter の出力には無く null) */
  readonly family: LintRuleFamily | null;
}

/** 定義の位置(押して飛ぶ先)。 */
export interface LintLocation {
  readonly path: string;
  readonly range: LintRange;
}

/** 型の式(版 2 — 閉じた集合)。name の definition は repo の中の定義(組み込みと解けない名は null)。 */
export type LintTypeRef =
  | { readonly kind: 'name'; readonly name: string; readonly definition: LintLocation | null }
  | { readonly kind: 'union'; readonly members: readonly LintTypeRef[] }
  | { readonly kind: 'apply'; readonly head: LintTypeRef; readonly args: readonly LintTypeRef[] }
  | { readonly kind: 'unknown'; readonly text: string };

/** effect 1 つ(宣言か推論)。 */
export interface LintEffectRef {
  readonly name: string;
  readonly definition: LintLocation | null;
  readonly answer: LintTypeRef | null;
  readonly absent: readonly LintTypeRef[];
  readonly failure: readonly LintTypeRef[];
}

/** 見出しを持つ定義の種類。 */
export const LINT_SIGNATURE_KINDS = ['defk', 'deff'] as const;
export type LintSignatureKind = (typeof LINT_SIGNATURE_KINDS)[number];

/** defk / deff 1 つの見出し(版 2)。 */
export interface LintSignature {
  readonly kind: LintSignatureKind;
  readonly name: string;
  readonly path: string;
  /** 名の範囲 */
  readonly range: LintRange;
  readonly fullRange: LintRange;
  /** 契約の辞書 `{:pre … :post …}` の範囲(無ければ null) */
  readonly contractRange: LintRange | null;
  readonly params: readonly { readonly name: string; readonly type: LintTypeRef | null }[];
  /** `:post` の型(無ければ null)。Maybe の包みは absent */
  readonly answer: LintTypeRef | null;
  readonly absent: boolean;
  readonly raises: readonly LintTypeRef[];
  /** 宣言(`:effects` が無ければ null)と推論 */
  readonly declared: readonly LintEffectRef[] | null;
  readonly inferred: readonly LintEffectRef[];
  /** 推論が追いきれたか(追えない呼びを撃っていれば false — inferred は見えた分だけ)。欄の無い古い linter は true */
  readonly inferenceComplete: boolean;
  readonly tags: ReadonlyMap<string, string>;
}

/** 束縛の形。 */
export const LINT_BINDING_FORMS = ['<-', 'val', 'var', 'setv', ':='] as const;
export type LintBindingForm = (typeof LINT_BINDING_FORMS)[number];

/** 束縛の型をどこから読んだか(unknown の時は type が null)。 */
export const LINT_BINDING_ORIGINS = ['annotation', 'effect', 'call', 'literal', 'constructor', 'var', 'unknown'] as const;
export type LintBindingOrigin = (typeof LINT_BINDING_ORIGINS)[number];

/** `val` / `var` の前の語(`(lazy val …)`・`(session var …)`)。 */
export const LINT_BINDING_MODIFIERS = ['lazy', 'session'] as const;
export type LintBindingModifier = (typeof LINT_BINDING_MODIFIERS)[number];

/** 束縛 1 つ(版 2)。 */
export interface LintBinding {
  readonly form: LintBindingForm;
  /** `lazy` / `session`(無い・古い linter・知らない語は null) */
  readonly modifier: LintBindingModifier | null;
  readonly name: string;
  readonly path: string;
  /** 名の範囲 */
  readonly range: LintRange;
  readonly formRange: LintRange;
  readonly headRange: LintRange;
  readonly annotationRange: LintRange | null;
  readonly valueRange: LintRange | null;
  readonly type: LintTypeRef | null;
  readonly origin: LintBindingOrigin;
  readonly absent: boolean;
  readonly raises: readonly LintTypeRef[];
}

/** 呼びの表示の置き換えの種類(閉じた集合 — 描き方は edit だけで決まるので、知らない種類も描く)。 */
export const LINT_REWRITE_KINDS = ['call', 'method', 'infix', 'prefix', 'perform', 'bind', 'subscript', 'attribute'] as const;
export type LintRewriteKind = (typeof LINT_REWRITE_KINDS)[number];

/** 置き換えた式の部品(呼びの頭)の種類。 */
export const LINT_REWRITE_ROLES = ['effect', 'defk', 'deff', 'type', 'function', 'builtin', 'local', 'method'] as const;
export type LintRewriteRole = (typeof LINT_REWRITE_ROLES)[number];

/** 元の文字の範囲を隠して text を見せる(範囲が空なら挿すだけ)。effect があれば text の前に装置の絵。 */
export interface LintRewriteEdit {
  readonly range: LintRange;
  readonly text: string;
  readonly effect: string | null;
}

/** 部品 1 つ(hover の型と定義への link)。 */
export interface LintRewritePart {
  readonly range: LintRange;
  readonly name: string;
  /** 知らない種類は null */
  readonly role: LintRewriteRole | null;
  readonly definition: LintLocation | null;
  readonly answer: LintTypeRef | null;
}

/** 呼びを `f(a, b)` の形で見せる置き換え 1 つ(括弧の組 1 つ — 版 2 への欄の追加・agora-redesign #849)。 */
export interface LintRewrite {
  /** 知らない種類は null(edit だけで描ける) */
  readonly kind: LintRewriteKind | null;
  readonly path: string;
  readonly range: LintRange;
  readonly original: string;
  readonly text: string;
  readonly edits: readonly LintRewriteEdit[];
  readonly parts: readonly LintRewritePart[];
  /** 外側の置き換えの番号(同じ出力の rewrites の中の位置) */
  readonly parent: number | null;
}

/** 本体の文字の字の役(閉じた集合 — 読む面の本体の行・agora-redesign #910)。 */
export const LINT_BODY_ROLES = [
  'keyword',
  'type',
  'unknown-type',
  'name',
  'bind',
  'assign',
  'effect',
  'call',
  'text',
  'lisp',
  'comment'
] as const;
export type LintBodyRole = (typeof LINT_BODY_ROLES)[number];

/** 本体の行の警告の種類。 */
export const LINT_BODY_WARNING_KINDS = ['setv'] as const;
export type LintBodyWarningKind = (typeof LINT_BODY_WARNING_KINDS)[number];

/** 本体の行の字の範囲 1 つ(行の字は text をつないだ物)。 */
export interface LintBodySegment {
  readonly text: string;
  /** 知らない役は null(ただの字として描く) */
  readonly role: LintBodyRole | null;
  /** 元の source の範囲(source に無い字は null) */
  readonly range: LintRange | null;
  /** effect の役の時の effect の名(絵を選ぶ) */
  readonly effect: string | null;
  readonly definition: LintLocation | null;
}

/** 本体の行 1 つ。 */
export interface LintBodyLine {
  /** source の行(0 始まり) */
  readonly line: number;
  /** 字下げの段 */
  readonly depth: number;
  /** 段の字下げの後ろに足す空白の数(描く字 = "  " × depth + " " × pad + segments) */
  readonly pad: number;
  readonly segments: readonly LintBodySegment[];
  /** この行が描く束縛の番号(同じ report の bindings の中の位置 — 知らない語で落とした束縛を指していれば null) */
  readonly binding: number | null;
  readonly warning: { readonly kind: LintBodyWarningKind | null; readonly message: string } | null;
}

/** 定義 1 つの本体の文字(版 2 への欄の追加)。 */
export interface LintBody {
  readonly kind: LintSignatureKind;
  readonly name: string;
  readonly path: string;
  /** 名の範囲(signatures の同じ定義と同じ) */
  readonly range: LintRange;
  readonly fullRange: LintRange;
  readonly lines: readonly LintBodyLine[];
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
  /** `--stdin` の file の defk / deff の見出し(版 2・全体の実行では空) */
  readonly signatures: readonly LintSignature[];
  /** `--stdin` の file の束縛の型(版 2・全体の実行では空) */
  readonly bindings: readonly LintBinding[];
  /** `--stdin` の file の呼びの表示の置き換え(版 2 への欄の追加 — 古い linter の出力には無く []) */
  readonly rewrites: readonly LintRewrite[];
  /** `--stdin` の file の定義ごとの本体の文字の行(版 2 への欄の追加 — 古い linter の出力には無く []) */
  readonly bodies: readonly LintBody[];
  /**
   * この実行で判じた規則の ID(更新 8・agora-redesign #2163)。1 file の実行(stdin)は repo 全体でだけ判じる規則(DOEFF166・141 など)を
   * 走らせないので、置き場はこの規則の違反だけを 1 file の結果で差し替え、ほかの規則の違反は全体の実行の結果のまま残す。古い linter の
   * 出力には無く null(その時は今までどおり file の違反を全部差し替える)。
   */
  readonly judgedRules: readonly string[] | null;
  /** linter 自身が読めなかった file など */
  readonly errors: readonly string[];
  /** 拡張の知らない語(linter の方が新しい)— その項目だけ既定の見た目にした理由。空なら無し */
  readonly unknown: readonly string[];
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
function violation(value: unknown, where: string, notes: Notes): LintViolation {
  const obj = asObject(value, where);
  const current = severity(obj, where);
  const registered = bool(obj, 'registered', where);
  // 知らない語(linter の方が新しい)は落とさず、下げる前の重さを今の重さ・立場を registered から、として控える
  const base = optional(obj, 'base_severity', where, (v, at) => lenient(v, at, LINT_SEVERITIES, notes) ?? null) ?? current;
  const standing =
    optional(obj, 'standing', where, (v, at) => lenient(v, at, LINT_STANDINGS, notes) ?? null) ?? (registered ? 'registered' : 'new');
  const level = optional(obj, 'level', where, (v, at) => lenient(v, at, LINT_LEVELS, notes) ?? null) ?? levelOfSeverity(base);
  return {
    rule: str(obj, 'rule', where),
    law: strOrNull(obj, 'law', where),
    adr: strOrNull(obj, 'adr', where),
    severity: current,
    baseSeverity: base,
    standing,
    level,
    path: str(obj, 'path', where),
    range: range(obj, where),
    message: str(obj, 'message', where),
    hint: strOrNull(obj, 'hint', where),
    key: strOrNull(obj, 'key', where),
    registered,
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
function lintRule(value: unknown, where: string, notes: Notes): LintRule {
  const obj = asObject(value, where);
  return {
    rule: str(obj, 'rule', where),
    adr: strOrNull(obj, 'adr', where),
    statement: str(obj, 'statement', where),
    wired: bool(obj, 'wired', where),
    title: optional(obj, 'title', where, text),
    family: optional(obj, 'family', where, (v, at) => lenient(v, at, LINT_RULE_FAMILIES, notes) ?? null)
  };
}

/** 範囲の値を検める。 */
function rangeValue(value: unknown, where: string): LintRange {
  const obj = asObject(value, where);
  return { start: position(obj, 'start', where), end: position(obj, 'end', where) };
}

/** 範囲か null の欄を検める(欄そのものは必須)。 */
function rangeOrNull(obj: JsonObject, key: string, where: string): LintRange | null {
  const value = field(obj, key, where);
  return value === null ? null : rangeValue(value, `${where}.${key}`);
}

/** 定義の位置か null を検める。 */
function locationOrNull(obj: JsonObject, key: string, where: string): LintLocation | null {
  const value = field(obj, key, where);
  if (value === null) {
    return null;
  }
  const loc = asObject(value, `${where}.${key}`);
  return { path: str(loc, 'path', `${where}.${key}`), range: range(loc, `${where}.${key}`) };
}

/** 型の式を検める(閉じた集合)。 */
function typeRef(value: unknown, where: string, notes: Notes): LintTypeRef {
  const obj = asObject(value, where);
  const kind = lenient(field(obj, 'kind', where), `${where}.kind`, ['name', 'union', 'apply', 'unknown'] as const, notes);
  const each = (v: unknown, at: string): LintTypeRef => typeRef(v, at, notes);
  switch (kind) {
    case undefined:
      return { kind: 'unknown', text: '?' };
    case 'name':
      return { kind, name: str(obj, 'name', where), definition: locationOrNull(obj, 'definition', where) };
    case 'union':
      return { kind, members: list(obj, 'members', where, each) };
    case 'apply':
      return { kind, head: typeRef(field(obj, 'head', where), `${where}.head`, notes), args: list(obj, 'args', where, each) };
    case 'unknown':
      return { kind, text: str(obj, 'text', where) };
    default: {
      const unreachable: never = kind;
      throw new LintContractViolation(`${where}.kind: 網羅されていない ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 型の式か null の欄を検める(欄そのものは必須)。 */
function typeOrNull(obj: JsonObject, key: string, where: string, notes: Notes): LintTypeRef | null {
  const value = field(obj, key, where);
  return value === null ? null : typeRef(value, `${where}.${key}`, notes);
}

/** effect の参照を検める。 */
function effectRef(value: unknown, where: string, notes: Notes): LintEffectRef {
  const obj = asObject(value, where);
  const each = (v: unknown, at: string): LintTypeRef => typeRef(v, at, notes);
  return {
    name: str(obj, 'name', where),
    definition: locationOrNull(obj, 'definition', where),
    answer: typeOrNull(obj, 'answer', where, notes),
    absent: list(obj, 'absent', where, each),
    failure: list(obj, 'failure', where, each)
  };
}

/** 見出し 1 つを検める(知らない種類の見出しは undefined — 描かない)。 */
function signature(value: unknown, where: string, notes: Notes): LintSignature | undefined {
  const obj = asObject(value, where);
  const kind = lenient(field(obj, 'kind', where), `${where}.kind`, LINT_SIGNATURE_KINDS, notes);
  if (kind === undefined) {
    return undefined;
  }
  const types = (v: unknown, at: string): LintTypeRef => typeRef(v, at, notes);
  const effect = (v: unknown, at: string): LintEffectRef => effectRef(v, at, notes);
  const effects = asObject(field(obj, 'effects', where), `${where}.effects`);
  const declared = field(effects, 'declared', `${where}.effects`);
  const tags = asObject(field(obj, 'tags', where), `${where}.tags`);
  return {
    kind,
    name: str(obj, 'name', where),
    path: str(obj, 'path', where),
    range: range(obj, where),
    fullRange: rangeValue(field(obj, 'full_range', where), `${where}.full_range`),
    contractRange: rangeOrNull(obj, 'contract_range', where),
    params: list(obj, 'params', where, (v, at) => {
      const param = asObject(v, at);
      return { name: str(param, 'name', at), type: typeOrNull(param, 'type', at, notes) };
    }),
    answer: typeOrNull(obj, 'answer', where, notes),
    absent: bool(obj, 'absent', where),
    raises: list(obj, 'raises', where, types),
    declared: declared === null ? null : list(effects, 'declared', `${where}.effects`, effect),
    inferred: list(effects, 'inferred', `${where}.effects`, effect),
    inferenceComplete: optional(effects, 'complete', `${where}.effects`, (v, at) => {
      if (typeof v !== 'boolean') {
        throw new LintContractViolation(`${at}: 真偽値でない`);
      }
      return v;
    }) ?? true,
    tags: new Map(Object.keys(tags).map((k) => [k, text(tags[k], `${where}.tags.${k}`)]))
  };
}

/** 束縛 1 つを検める(知らない形・出どころの束縛は undefined — 描かない)。 */
function binding(value: unknown, where: string, notes: Notes): LintBinding | undefined {
  const obj = asObject(value, where);
  const form = lenient(field(obj, 'form', where), `${where}.form`, LINT_BINDING_FORMS, notes);
  const origin = lenient(field(obj, 'origin', where), `${where}.origin`, LINT_BINDING_ORIGINS, notes);
  if (form === undefined || origin === undefined) {
    return undefined;
  }
  return {
    form,
    modifier: optional(obj, 'modifier', where, (v, at) => lenient(v, at, LINT_BINDING_MODIFIERS, notes) ?? null),
    name: str(obj, 'name', where),
    path: str(obj, 'path', where),
    range: range(obj, where),
    formRange: rangeValue(field(obj, 'form_range', where), `${where}.form_range`),
    headRange: rangeValue(field(obj, 'head_range', where), `${where}.head_range`),
    annotationRange: rangeOrNull(obj, 'annotation_range', where),
    valueRange: rangeOrNull(obj, 'value_range', where),
    type: typeOrNull(obj, 'type', where, notes),
    origin,
    absent: bool(obj, 'absent', where),
    raises: list(obj, 'raises', where, (v, at) => typeRef(v, at, notes))
  };
}

/** 呼びの表示の置き換え 1 つを検める。 */
function rewrite(value: unknown, where: string, notes: Notes): LintRewrite {
  const obj = asObject(value, where);
  const kindValue = field(obj, 'kind', where);
  const parent = field(obj, 'parent', where);
  if (parent !== null && (typeof parent !== 'number' || !Number.isInteger(parent) || parent < 0)) {
    throw new LintContractViolation(`${where}.parent: 0 以上の整数でも null でもない`);
  }
  return {
    kind: lenient(kindValue, `${where}.kind`, LINT_REWRITE_KINDS, notes) ?? null,
    path: str(obj, 'path', where),
    range: range(obj, where),
    original: str(obj, 'original', where),
    text: str(obj, 'text', where),
    edits: list(obj, 'edits', where, (v, at) => {
      const edit = asObject(v, at);
      return { range: range(edit, at), text: str(edit, 'text', at), effect: strOrNull(edit, 'effect', at) };
    }),
    parts: list(obj, 'parts', where, (v, at) => {
      const part = asObject(v, at);
      return {
        range: range(part, at),
        name: str(part, 'name', at),
        role: lenient(field(part, 'role', at), `${at}.role`, LINT_REWRITE_ROLES, notes) ?? null,
        definition: locationOrNull(part, 'definition', at),
        answer: typeOrNull(part, 'answer', at, notes)
      };
    }),
    parent
  };
}

/** 0 以上の整数か null の欄を検める(欄そのものは必須)。 */
function indexOrNull(obj: JsonObject, key: string, where: string): number | null {
  const value = field(obj, key, where);
  if (value !== null && (typeof value !== 'number' || !Number.isInteger(value) || value < 0)) {
    throw new LintContractViolation(`${where}.${key}: 0 以上の整数でも null でもない`);
  }
  return value;
}

/**
 * 本体 1 つを検める(知らない種類の定義は undefined — 描かない)。`bindingIndex` は linter の bindings の番号 → 読んだ
 * bindings の番号(知らない語で落とした束縛は undefined)。
 */
function body(value: unknown, where: string, notes: Notes, bindingIndex: readonly (number | undefined)[]): LintBody | undefined {
  const obj = asObject(value, where);
  const kind = lenient(field(obj, 'kind', where), `${where}.kind`, LINT_SIGNATURE_KINDS, notes);
  if (kind === undefined) {
    return undefined;
  }
  return {
    kind,
    name: str(obj, 'name', where),
    path: str(obj, 'path', where),
    range: range(obj, where),
    fullRange: rangeValue(field(obj, 'full_range', where), `${where}.full_range`),
    lines: list(obj, 'lines', where, (v, at) => {
      const line = asObject(v, at);
      const binding = indexOrNull(line, 'binding', at);
      const warning = field(line, 'warning', at);
      return {
        line: nat(line, 'line', at),
        depth: nat(line, 'depth', at),
        // pad は U3 で足した欄 — 無い(U2 の linter)なら 0
        pad:
          optional(line, 'pad', at, (v, vat) => {
            if (typeof v !== 'number' || !Number.isInteger(v) || v < 0) {
              throw new LintContractViolation(`${vat}: 0 以上の整数でない`);
            }
            return v;
          }) ?? 0,
        segments: list(line, 'segments', at, (s, sat) => {
          const segment = asObject(s, sat);
          return {
            text: str(segment, 'text', sat),
            role: lenient(field(segment, 'role', sat), `${sat}.role`, LINT_BODY_ROLES, notes) ?? null,
            range: rangeOrNull(segment, 'range', sat),
            effect: strOrNull(segment, 'effect', sat),
            definition: locationOrNull(segment, 'definition', sat)
          };
        }),
        binding: binding === null ? null : bindingIndex[binding] ?? null,
        warning:
          warning === null
            ? null
            : (() => {
                const w = asObject(warning, `${at}.warning`);
                return {
                  kind: lenient(field(w, 'kind', `${at}.warning`), `${at}.warning.kind`, LINT_BODY_WARNING_KINDS, notes) ?? null,
                  message: str(w, 'message', `${at}.warning`)
                };
              })()
      };
    })
  };
}

/** 読みの途中で控える、拡張の知らない語。 */
interface Notes {
  readonly unknown: string[];
}

/** 閉じた集合の語を読む — 知らなければ控えて undefined(呼ぶ側が既定の見た目にする)。文字列でない値は契約違反。 */
function lenient<T extends string>(value: unknown, where: string, allowed: readonly T[], notes: Notes): T | undefined {
  if (typeof value !== 'string') {
    throw new LintContractViolation(`${where}: 文字列でない`);
  }
  const found = allowed.find((a) => a === value);
  if (found === undefined) {
    notes.unknown.push(`${where}: 知らない語 ${JSON.stringify(value)}`);
  }
  return found;
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
    if (typeof version !== 'number' || !LINT_READABLE_VERSIONS.includes(version)) {
      return { tag: 'rejected', reason: `契約の版が違う(期待 ${LINT_READABLE_VERSIONS.join(' か ')}、実際 ${JSON.stringify(version)})` };
    }
    const hasSignatures = version >= 2;
    const notes: Notes = { unknown: [] };
    const present = <T>(items: readonly (T | undefined)[]): T[] => items.filter((i): i is T => i !== undefined);
    const rawBindings = hasSignatures ? list(obj, 'bindings', '$', (v, at) => binding(v, at, notes)) : [];
    // 知らない語の束縛を落とすので、本体の行の束縛の番号を読んだ bindings の番号へ付け替える
    let kept = 0;
    const bindingIndex = rawBindings.map((b) => (b === undefined ? undefined : kept++));
    return {
      tag: 'ok',
      report: {
        version,
        root: str(obj, 'root', '$'),
        violations: list(obj, 'violations', '$', (v, at) => violation(v, at, notes)),
        modules: list(obj, 'modules', '$', lintModule),
        rules: list(obj, 'rules', '$', (v, at) => lintRule(v, at, notes)),
        layers: Object.prototype.hasOwnProperty.call(obj, 'layers') ? list(obj, 'layers', '$', lintLayer) : [],
        semantic: optional(obj, 'semantic', '$', semanticSummary),
        signatures: hasSignatures ? present(list(obj, 'signatures', '$', (v, at) => signature(v, at, notes))) : [],
        bindings: present(rawBindings),
        rewrites: Object.prototype.hasOwnProperty.call(obj, 'rewrites') ? list(obj, 'rewrites', '$', (v, at) => rewrite(v, at, notes)) : [],
        bodies: Object.prototype.hasOwnProperty.call(obj, 'bodies')
          ? present(list(obj, 'bodies', '$', (v, at) => body(v, at, notes, bindingIndex)))
          : [],
        judgedRules: Object.prototype.hasOwnProperty.call(obj, 'judged_rules') ? list(obj, 'judged_rules', '$', text) : null,
        errors: list(obj, 'errors', '$', text),
        unknown: notes.unknown
      }
    };
  } catch (error) {
    if (error instanceof LintContractViolation) {
      return { tag: 'rejected', reason: error.message };
    }
    throw error;
  }
}
