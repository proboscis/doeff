// 何にどの icon を当てるか — 定義の kind・層・service・linter の違反・file の状態から icon の名前を決める純粋な関数。
// 判定はしない(違反か・層はどれかは linter の出力、kind は hy-index の出力のまま)。同じ語はエディタ・linter の欄・
// hover・状態バーで必ず同じ icon になるよう、当て方の持ち主をこの 1 か所にする。VS Code には触らない。

import { NONE_VALUE, UNKNOWN_VALUE, type Axis } from '../hy/browse';
import { isHyDefinitionKind, type HyDefinition, type HyDefinitionKind } from '../hy/contract';
import type { LintModule, LintRuleFamily, LintSeverity, LintViolation } from '../lint/contract';
import { worstSeverity } from '../lint/view';
import { chooseFlag, flagPixels } from './flags';
import { composeTinted, LARGE, overlayBadge, SMALL, type ColorIndex, type GlyphSet, type Pixels } from './glyphs';

/** linter の印の icon(閉じた集合)。 */
export const MARK_GLYPHS = ['lint-error', 'lint-warning', 'lint-info', 'lint-registered', 'jev', 'jev-unjudged'] as const;
export type MarkGlyph = (typeof MARK_GLYPHS)[number];

/** 状態バーの doe の表情(閉じた集合)。 */
export type MoodGlyph = 'doe-calm' | 'doe-worried' | 'doe-surprised';

/** 定義の kind の icon(icon の無い kind は undefined — 木は codicon のまま)。 */
const KIND_GLYPHS: Readonly<Partial<Record<HyDefinitionKind, string>>> = {
  defk: 'defk',
  defp: 'program',
  defhandler: 'defhandler',
  defeffect: 'defeffect',
  'effect-clause': 'defeffect',
  defrecord: 'defrecord',
  deftest: 'deftest',
  law: 'law'
};

/** 定義の kind の icon の名前。 */
export function kindGlyph(kind: HyDefinitionKind): string | undefined {
  return KIND_GLYPHS[kind];
}

/** effect の種類の装置の icon(閉じた集合)— defk の見出しと文字の置き換えの印が effect の名の前に出す絵。 */
export const EFFECT_GLYPHS = [
  'effect-ask',
  'effect-read',
  'effect-write',
  'effect-settle',
  'effect-time',
  'effect-wait',
  'effect-launch',
  'effect-door',
  'effect-send',
  'effect-delete',
  'effect-raise',
  'effect-absent',
  'effect-http',
  'effect-device'
] as const;
export type EffectGlyph = (typeof EFFECT_GLYPHS)[number];

/**
 * effect の名 → 装置の絵の決まった表(当て方の唯一の持ち主 — 見出しの描画と文字の置き換えがここを引く)。
 * 先に名の全体で引き(`names`)、無ければ名の頭の語(CamelCase の最初の語・例 `ReadBoard` の `Read`)で引く(`verbs`)。
 * どちらにも無い effect は一般の装置(effect-device)。種類の判定はしない — 名の字面だけで絵を選ぶ。
 */
export const EFFECT_GLYPH_TABLE: {
  readonly names: Readonly<Record<string, EffectGlyph>>;
  readonly verbs: Readonly<Record<string, EffectGlyph>>;
} = {
  names: { Ask: 'effect-ask', GetTime: 'effect-time', Now: 'effect-time', Raise: 'effect-raise', Absent: 'effect-absent' },
  verbs: {
    // 問い合わせ・読み — 受話器と読み取り機
    Ask: 'effect-ask',
    Read: 'effect-read',
    Fetch: 'effect-read',
    Get: 'effect-read',
    List: 'effect-read',
    Observe: 'effect-read',
    Inspect: 'effect-read',
    Count: 'effect-read',
    Watch: 'effect-read',
    Await: 'effect-read',
    Awaiting: 'effect-read',
    Evaluate: 'effect-read',
    Ping: 'effect-read',
    // 書き込み — 印刷機
    Write: 'effect-write',
    Put: 'effect-write',
    Append: 'effect-write',
    Insert: 'effect-write',
    Create: 'effect-write',
    Update: 'effect-write',
    Revise: 'effect-write',
    Print: 'effect-write',
    Publish: 'effect-write',
    Export: 'effect-write',
    Migrate: 'effect-write',
    // 確定・登録 — 判子
    Settle: 'effect-settle',
    Record: 'effect-settle',
    Mark: 'effect-settle',
    Submit: 'effect-settle',
    Attach: 'effect-settle',
    Relate: 'effect-settle',
    Tombstone: 'effect-settle',
    Declared: 'effect-settle',
    Resolve: 'effect-settle',
    // 時刻 — 時計
    Time: 'effect-time',
    Clock: 'effect-time',
    // 待つ — 砂時計
    Sleep: 'effect-wait',
    Wait: 'effect-wait',
    // 起動と停止 — 起動のレバー
    Launch: 'effect-launch',
    Start: 'effect-launch',
    Run: 'effect-launch',
    Stop: 'effect-launch',
    Interrupt: 'effect-launch',
    // 開く・閉じる — エアロックの扉
    Open: 'effect-door',
    Close: 'effect-door',
    // 送る — 気送管の筒
    Send: 'effect-send',
    Post: 'effect-send',
    Deliver: 'effect-send',
    // 消す — 廃棄の口
    Delete: 'effect-delete',
    Remove: 'effect-delete',
    Cancel: 'effect-delete',
    Drop: 'effect-delete',
    // 外部との通信(HTTP・socket)— 無線の塔
    Http: 'effect-http',
    Forward: 'effect-http',
    Socket: 'effect-http',
    Serve: 'effect-http',
    // 失敗・無い
    Raise: 'effect-raise',
    Fail: 'effect-raise',
    Absent: 'effect-absent'
  }
};

/** effect の名の頭の語(CamelCase の最初の語。大文字で始まらない名は名の全体)。 */
function leadingWord(name: string): string {
  return /^[A-Z][a-z0-9]*/.exec(name)?.[0] ?? name;
}

/** effect の名から装置の絵を選ぶ(名の全体 → 頭の語 → 一般の装置)。 */
export function effectGlyph(name: string): EffectGlyph {
  return EFFECT_GLYPH_TABLE.names[name] ?? EFFECT_GLYPH_TABLE.verbs[leadingWord(name)] ?? 'effect-device';
}

/** 層のタイルの icon を持つ層(linter の層の名前)。 */
const LAYER_NAMES = ['core', 'intent', 'protocol', 'foundation', 'entry'];

/** 層のタイルの icon の名前(知らない層・層の外は undefined)。 */
export function layerGlyph(layer: string | null): string | undefined {
  return layer !== null && LAYER_NAMES.includes(layer) ? `layer-${layer}` : undefined;
}

/** service の旗の icon の名前(旗は名前の hash から作るので、どの名前にもある)。 */
export function serviceGlyph(service: string): string {
  return `service-${service}`;
}

/**
 * 「タグで閲覧」の束の icon — kind の軸は kind の icon、層と role の軸は層のタイル、service の軸は旗。
 * 値の無い束(「(不明)」「なし」)と、icon の無い軸は undefined(codicon のまま)。
 */
export function axisValueGlyph(axis: Axis, value: string): string | undefined {
  if (axis.tag === 'tag' || value === UNKNOWN_VALUE || value === NONE_VALUE) {
    return undefined;
  }
  switch (axis.name) {
    case 'kind':
      return isHyDefinitionKind(value) ? kindGlyph(value) : undefined;
    case 'layer':
    case 'role':
      return layerGlyph(value);
    case 'service':
      return /^[a-z][a-z0-9-]*$/.test(value) ? serviceGlyph(value) : undefined;
    case 'context':
    case 'raw':
    case 'violation':
      return undefined;
    default: {
      const unreachable: never = axis.name;
      throw new Error(`網羅されていない軸: ${JSON.stringify(unreachable)}`);
    }
  }
}

/**
 * 違反 1 件の印 — Jev の判定はふくろう、登録簿に載った既知の破れは足場、それ以外は重さ(error = 火・warning = 黄色の旗・
 * info = 青い旗)。
 */
export function violationMark(violation: LintViolation): MarkGlyph {
  if (violation.source === 'jev') {
    return 'jev';
  }
  if (violation.registered) {
    return 'lint-registered';
  }
  return severityMark(violation.severity);
}

/** 重さの印。 */
function severityMark(severity: LintSeverity): MarkGlyph {
  switch (severity) {
    case 'error':
      return 'lint-error';
    case 'warning':
      return 'lint-warning';
    case 'info':
      return 'lint-info';
    default: {
      const unreachable: never = severity;
      throw new Error(`網羅されていない重さ: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** Jev が判定した違反の束に使う、家族とふくろうの組の sprite(組を描いた家族だけ。他の家族は家族の sprite のまま)。 */
const JEV_PAIRS: Readonly<Partial<Record<LintRuleFamily, string>>> = { smell: 'rule-smell-jev', class: 'rule-class-jev' };

/**
 * 違反の欄の行(規則の束)の sprite — 規則の家族ごとに違う物(どの規則がどの家族かは linter が決める)。Python の規則は
 * ADR の決まりと同じ天秤。家族を出さない古い linter の出力(null)も天秤にする(絵が無いと行が codicon と混ざるため)。
 * Jev が判定した束は、組を描いた家族なら組の sprite(臭いの DOEFF205 = ふくろうの顔のドローン・class の DOEFF204 =
 * ふくろうが乗った木箱)。
 */
export function ruleFamilyGlyph(family: LintRuleFamily | null, judgedByJev = false): string {
  switch (family) {
    case 'layer':
    case 'tags':
    case 'raw':
    case 'naming':
    case 'place':
    case 'definition':
    case 'class':
    case 'wire':
    case 'smell':
    case 'jev':
    case 'law':
      return (judgedByJev ? JEV_PAIRS[family] : undefined) ?? `rule-${family}`;
    case 'python':
    case null:
      return 'rule-law';
    default: {
      const unreachable: never = family;
      throw new Error(`網羅されていない規則の家族: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 灯の色(色の組の番号)— 違反の重さを sprite に付いた灯の色で出すため(error = 赤・warning = 琥珀・info = 青。色は元の定義の lamps)。 */
export function severityLamp(set: GlyphSet, severity: LintSeverity): ColorIndex {
  return set.lamps[severity];
}

/**
 * 灯を灯した sprite 1 つ — gutter・木・違反の欄が使う唯一の画の形。灯(`L` の点)は違反の重さの色(重さが無ければ消えた
 * 灯)。`flag` は層と service の gutter だけが使う、右下に重ねる service の旗(重さの印ではない)。
 */
export interface LitIcon {
  readonly glyph: string;
  readonly severity: LintSeverity | null;
  readonly flag: string | null;
}

/** 違反の欄の行の画 — sprite は家族(Jev の判定なら組の sprite)、灯は束の最も重い重さ。 */
export function ruleIcon(family: LintRuleFamily | null, severity: LintSeverity | null, violations: readonly LintViolation[]): LitIcon {
  return { glyph: ruleFamilyGlyph(family, violations.some((v) => v.source === 'jev')), severity, flag: null };
}

/** 画の鍵(同じ画を使い回すため)。 */
export function litKey(icon: LitIcon): string {
  return `${icon.glyph}|${icon.severity ?? '-'}|${icon.flag ?? '-'}`;
}

/**
 * 灯を灯した大きい sprite(32×32)の格子 — sprite の `L` の点を重さの色にし(重さが無ければ消えた灯の色)、service の旗があれば右下に
 * 重ねる。service の旗(`service-<名>`)は旗の決まりから作る。知らない名前は undefined。拡張と見本の HTML が同じ関数で作る。
 */
export function litPixels(set: GlyphSet, icon: LitIcon): Pixels | undefined {
  const lamp = icon.severity === null ? undefined : severityLamp(set, icon.severity);
  const base = spritePixels(set, icon.glyph, lamp);
  if (base === undefined || icon.flag === null) {
    return base;
  }
  const flag = icon.flag.startsWith('service-') ? flagPixels(set, chooseFlag(set, icon.flag.slice('service-'.length)), SMALL) : composeTinted(set, icon.flag, {})?.[SMALL];
  return flag === undefined ? base : overlayBadge(base, flag, set.outline[0]);
}

/** sprite 1 つの大きい格子(灯の色の上書きつき。service の旗も同じ口で)。 */
function spritePixels(set: GlyphSet, name: string, lamp: ColorIndex | undefined): Pixels | undefined {
  if (name.startsWith('service-')) {
    return flagPixels(set, chooseFlag(set, name.slice('service-'.length)), LARGE, lamp);
  }
  return composeTinted(set, name, lamp === undefined ? {} : { L: lamp })?.[LARGE];
}

/** 印の強さの順(小さいほど先に目に入れたい)— 火 → 旗 → ふくろう → 足場 → 青い旗 → 霧。 */
const MARK_RANK: Readonly<Record<MarkGlyph, number>> = {
  'lint-error': 0,
  'lint-warning': 1,
  jev: 2,
  'lint-registered': 3,
  'lint-info': 4,
  'jev-unjudged': 5
};

/** 違反の束で最も強い印(違反が無ければ undefined)。 */
export function worstMark(violations: readonly LintViolation[]): MarkGlyph | undefined {
  let worst: MarkGlyph | undefined;
  for (const v of violations) {
    const mark = violationMark(v);
    if (worst === undefined || MARK_RANK[mark] < MARK_RANK[worst]) {
      worst = mark;
    }
  }
  return worst;
}

/** 今の file の違反の数え(状態バーの doe と数)。 */
export interface FileTally {
  readonly errors: number;
  readonly warnings: number;
  readonly registered: number;
  readonly jev: number;
  readonly info: number;
}

/** file の違反を印ごとに数える。 */
export function tallyViolations(violations: readonly LintViolation[]): FileTally {
  const tally = { errors: 0, warnings: 0, registered: 0, jev: 0, info: 0 };
  for (const v of violations) {
    const mark = violationMark(v);
    switch (mark) {
      case 'lint-error':
        tally.errors += 1;
        break;
      case 'lint-warning':
        tally.warnings += 1;
        break;
      case 'lint-registered':
        tally.registered += 1;
        break;
      case 'jev':
        tally.jev += 1;
        break;
      case 'lint-info':
      case 'jev-unjudged':
        tally.info += 1;
        break;
      default: {
        const unreachable: never = mark;
        throw new Error(`網羅されていない印: ${JSON.stringify(unreachable)}`);
      }
    }
  }
  return tally;
}

/** doe の表情 — error(新しい破れ)があれば驚き、他の違反だけなら困り、違反 0 なら落ち着き。 */
export function fileMood(tally: FileTally): MoodGlyph {
  if (tally.errors > 0) {
    return 'doe-surprised';
  }
  const others = tally.warnings + tally.registered + tally.jev + tally.info;
  return others > 0 ? 'doe-worried' : 'doe-calm';
}

/** 状態バーの doe の中身 — 表情・文(`$(doeff-…)` の字体つき)・tooltip の行。 */
export interface DoeStatus {
  readonly mood: MoodGlyph;
  readonly text: string;
  readonly lines: ReadonlyArray<{ readonly glyph: MarkGlyph | MoodGlyph; readonly label: string }>;
}

/** 今の file の数えから状態バーの doe を作る(数の 0 の印は出さない)。 */
export function doeStatus(tally: FileTally): DoeStatus {
  const mood = fileMood(tally);
  const counts: Array<{ readonly glyph: MarkGlyph; readonly count: number; readonly label: string }> = [
    { glyph: 'lint-error', count: tally.errors, label: 'error(新しい違反)' },
    { glyph: 'lint-warning', count: tally.warnings, label: 'warning' },
    { glyph: 'lint-registered', count: tally.registered, label: '登録済み(既知の違反)' },
    { glyph: 'jev', count: tally.jev, label: 'Jev の判定' },
    { glyph: 'lint-info', count: tally.info, label: 'info' }
  ];
  const shown = counts.filter((c) => c.count > 0);
  const text = [`$(doeff-${mood})`, ...shown.map((c) => `$(doeff-${c.glyph}) ${c.count}`)].join(' ');
  const lines =
    shown.length === 0
      ? [{ glyph: mood, label: 'この file の linter の違反は 0' }]
      : shown.map((c) => ({ glyph: c.glyph, label: `${c.label} ${c.count} 件` }));
  return { mood, text: shown.length === 0 ? `${text} 0` : text, lines };
}

/** gutter の出し方の設定の名前(pixel art の gutter と linter の左端の丸の両方が読む)。 */
export const GUTTER_SETTING = 'doeff-runner.pixel.gutter';

/** gutter の出し方 — 種類と違反(既定)・層と service・出さない。 */
export const GUTTER_MODES = ['kind', 'layer', 'off'] as const;
export type GutterMode = (typeof GUTTER_MODES)[number];

/** 設定の gutter の出し方を読む(知らない値は undefined — 呼ぶ側が理由を出す)。 */
export function parseGutterMode(value: unknown): GutterMode | undefined {
  return GUTTER_MODES.find((m) => m === value);
}

/** gutter の 1 行。 */
export interface GutterLine {
  readonly line: number;
  readonly icon: LitIcon;
}

/** 位置が定義の範囲に入るか(違反を定義へ割り当てるため)。 */
function within(definition: HyDefinition, line: number, character: number): boolean {
  const r = definition.fullRange;
  const afterStart = line > r.start.line || (line === r.start.line && character >= r.start.character);
  const beforeEnd = line < r.end.line || (line === r.end.line && character <= r.end.character);
  return afterStart && beforeEnd;
}

/** 違反の印の sprite だけの画(警報灯・足場・霧・ふくろう — 印そのものが重さの色を持つので灯は灯さない)。 */
function markOnly(violations: readonly LintViolation[]): LitIcon | undefined {
  const mark = worstMark(violations);
  return mark === undefined ? undefined : { glyph: mark, severity: null, flag: null };
}

/**
 * file 1 つの gutter の行 — 入れ物の外の定義の行に sprite(灯 = その定義の範囲の違反の最も重い重さ)、定義の行でない
 * 違反の行に違反の印の sprite。
 * - kind: sprite = 定義の kind(kind に sprite が無い定義 — defn など — は違反があれば印の sprite)
 * - layer: sprite = file の層の建物、右下に file の service の旗(層の外の file は旗だけ)
 */
export function gutterLines(
  definitions: readonly HyDefinition[],
  violations: readonly LintViolation[],
  module: LintModule | undefined,
  mode: GutterMode
): GutterLine[] {
  if (mode === 'off') {
    return [];
  }
  const byLine = new Map<number, LitIcon>();
  const service = module === undefined || module.service === null ? null : serviceGlyph(module.service);
  const layer = layerGlyph(module?.layer ?? null) ?? null;
  for (const definition of definitions) {
    if (definition.container !== null) {
      continue;
    }
    const inside = violations.filter((v) => within(definition, v.range.start.line, v.range.start.character));
    const severity = worstSeverity(inside) ?? null;
    const kind = kindGlyph(definition.kind);
    const icon: LitIcon | undefined =
      mode === 'kind'
        ? kind === undefined
          ? markOnly(inside)
          : { glyph: kind, severity, flag: null }
        : layer !== null
          ? { glyph: layer, severity, flag: service }
          : service === null
            ? undefined
            : { glyph: service, severity, flag: null };
    if (icon !== undefined) {
      byLine.set(definition.range.start.line, icon);
    }
  }
  const violationLines = new Map<number, LintViolation[]>();
  for (const v of violations) {
    violationLines.set(v.range.start.line, [...(violationLines.get(v.range.start.line) ?? []), v]);
  }
  for (const [line, onLine] of violationLines) {
    const icon = byLine.has(line) ? undefined : markOnly(onLine);
    if (icon !== undefined) {
      byLine.set(line, icon);
    }
  }
  return [...byLine.entries()].sort(([a], [b]) => a - b).map(([line, icon]) => ({ line, icon }));
}
