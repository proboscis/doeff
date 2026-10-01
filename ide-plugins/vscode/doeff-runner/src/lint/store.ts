// linter の結果の置き場 — workspace の root ごとに直前の全体の実行の結果を持ち、編集中の file は 1 file の実行の結果で
// 差し替える。表示(波線・パネル・地図)はすべてここから読む。外の世界には触らない。
// 知らせは 2 種類 — 違反の側(違反・module・規則・層・root の実行の状態・知らない語)が変わった時の onDidChange と、見出しの側
// (1 file の見出し・束縛・置き換え・本体の行)が変わった時の onDidChangeSignatures。木は違反の側だけを聞く(見出しだけの変化や
// 前と同じ違反の差し替えで木を出し直すと、展開と選択が初期に戻る — agora-redesign #2162)。

import * as path from 'path';
import type {
  LintBinding,
  LintBody,
  LintExplanation,
  LintLayer,
  LintModule,
  LintPosition,
  LintRange,
  LintReport,
  LintRewrite,
  LintRule,
  LintSignature,
  LintViolation
} from './contract';

/**
 * 見出しを聞いた時の document の印 — 版と中身の短い hash。VS Code は document を閉じて開き直すと版を 1 から数え直すので、
 * 版だけでは「閉じる前の版 1」と「開き直した版 1」の別の中身を見分けられない(閉じても見出しを捨てないため、両方で比べる)。
 */
export interface DocumentStamp {
  readonly version: number;
  readonly hash: string;
}

/** 文字の短い hash(FNV-1a 32 bit を 16 進 8 桁)— 同じ版の別の中身を見分ける・違反の文を節の id に縮める。暗号の強さは要らない。 */
export function contentHash(text: string): string {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16).padStart(8, '0');
}

/** 印を取れる document の面(VS Code の TextDocument はこの形を満たす)。 */
export interface StampSource {
  readonly version: number;
  getText(): string;
}

/**
 * document の object ごとの直前の印 — 1 つの document の object の中身は版が同じ間は変わらないので、同じ版なら hash を
 * 数え直さない(editor の飾りは cursor が動くたびに印を問う)。
 */
const stamps = new WeakMap<StampSource, DocumentStamp>();

/** 版と中身の文字から印を作る(linter に渡した stdin の文字の印)。 */
export function textStamp(version: number, text: string): DocumentStamp {
  return { version, hash: contentHash(text) };
}

/** document の今の印(版と中身の hash)。 */
export function stampOf(document: StampSource): DocumentStamp {
  const known = stamps.get(document);
  if (known !== undefined && known.version === document.version) {
    return known;
  }
  const stamp = textStamp(document.version, document.getText());
  stamps.set(document, stamp);
  return stamp;
}

/** 2 つの印が同じ中身を指すか(版と hash の両方が同じ)。 */
export function sameStamp(a: DocumentStamp, b: DocumentStamp): boolean {
  return a.version === b.version && a.hash === b.hash;
}

/** 見出しを聞き直すか — まだ聞いていないか、置いた見出しの印が document の今の印と違う時だけ。 */
export function needsSignatures(seen: FileSignatures | undefined, now: DocumentStamp): boolean {
  return seen === undefined || !sameStamp(seen, now);
}

/** 欄ごとの比べ方の表 — 契約の型に欄を 1 つ足すと、ここに比べ方を書くまで compile が通らない(比べ忘れを型で塞ぐ)。 */
type FieldEquality<T> = { readonly [K in keyof T]-?: (a: T[K], b: T[K]) => boolean };

/** 値で比べる(文字列・数・真偽・null・閉じた集合の語)。 */
function sameValue<V>(a: V, b: V): boolean {
  return a === b;
}

/** 欄の表の全部の欄が同じか。 */
function sameByFields<T>(fields: FieldEquality<T>, a: T, b: T): boolean {
  for (const name in fields) {
    if (!fields[name](a[name], b[name])) {
      return false;
    }
  }
  return true;
}

/** null を許す欄の比べ方(両方 null なら同じ・片方だけ null なら違う)。 */
function nullable<V>(equal: (a: V, b: V) => boolean): (a: V | null, b: V | null) => boolean {
  return (a, b) => (a === null || b === null ? a === b : equal(a, b));
}

const POSITION_FIELDS: FieldEquality<LintPosition> = { line: sameValue, character: sameValue };
const RANGE_FIELDS: FieldEquality<LintRange> = {
  start: (a, b) => sameByFields(POSITION_FIELDS, a, b),
  end: (a, b) => sameByFields(POSITION_FIELDS, a, b)
};
const EXPLANATION_FIELDS: FieldEquality<LintExplanation> = { subject: sameValue, reason: sameValue, lawStatement: sameValue };
const VIOLATION_FIELDS: FieldEquality<LintViolation> = {
  rule: sameValue,
  law: sameValue,
  adr: sameValue,
  severity: sameValue,
  path: sameValue,
  range: (a, b) => sameByFields(RANGE_FIELDS, a, b),
  message: sameValue,
  hint: sameValue,
  key: sameValue,
  registered: sameValue,
  baseSeverity: sameValue,
  standing: sameValue,
  level: sameValue,
  explanation: nullable((a, b) => sameByFields(EXPLANATION_FIELDS, a, b)),
  source: sameValue,
  probability: sameValue
};
const MODULE_FIELDS: FieldEquality<LintModule> = {
  path: sameValue,
  layer: sameValue,
  service: sameValue,
  context: sameValue,
  role: sameValue,
  violations: sameValue,
  layerReason: sameValue
};

/** 違反の列が構造として同じか(契約の型の欄を全部・順も含めて比べる)。 */
export function sameViolations(a: readonly LintViolation[], b: readonly LintViolation[]): boolean {
  return a.length === b.length && a.every((violation, i) => sameByFields(VIOLATION_FIELDS, violation, b[i]));
}

/** module の要約が構造として同じか(両方無ければ同じ)。 */
export function sameModule(a: LintModule | undefined, b: LintModule | undefined): boolean {
  return a === undefined || b === undefined ? a === b : sameByFields(MODULE_FIELDS, a, b);
}

/** file 1 つに見せる物 — その file の違反と module の要約。 */
interface FileView {
  readonly violations: readonly LintViolation[];
  readonly module: LintModule | undefined;
}

/** file 1 つに見せる物が同じか。 */
function sameView(a: FileView, b: FileView): boolean {
  return sameModule(a.module, b.module) && sameViolations(a.violations, b.violations);
}

/**
 * 違反を位置・規則・鍵・文の順に並べた写し。1 file の差し替えの合成は残した違反(判じていない規則の分)を先に並べるので、
 * 全体の結果と同じ中身でも linter の順(file ごとに位置と規則の順)と違いうる — 見せる物が変わったかは順を揃えてから比べる(#2163)。
 */
function inPositionOrder(view: FileView): FileView {
  const text = (value: string | null): string => value ?? '';
  const ordered = [...view.violations].sort(
    (a, b) =>
      a.range.start.line - b.range.start.line ||
      a.range.start.character - b.range.start.character ||
      a.rule.localeCompare(b.rule) ||
      text(a.key).localeCompare(text(b.key)) ||
      a.message.localeCompare(b.message)
  );
  return { violations: ordered, module: view.module };
}

/** 文字の列が同じか(順も含めて)。 */
function sameStrings(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((s, i) => s === b[i]);
}

/**
 * 1 file の見出しと束縛(linter に渡した document の印つき — 印が document の今の印と違えば古い位置なので描かない)。
 * 閉じた file の分も捨てずに持つ — 持つのは開いたことのある file の数だけで、1 file は file の文字と同じ程度の大きさ
 * (見出し・束縛・置き換え・本体の行)。捨てると読む面の tab を開き直すたびに linter に聞き直し、木が出し直されていた(#2162)。
 */
export interface FileSignatures extends DocumentStamp {
  readonly signatures: readonly LintSignature[];
  readonly bindings: readonly LintBinding[];
  /** 呼びを `f(a, b)` の形で見せる置き換え(parent は この列の中の番号) */
  readonly rewrites: readonly LintRewrite[];
  /** 定義ごとの本体の文字の行(定義を読む面が描く・agora-redesign #910 U2 / U5) */
  readonly bodies: readonly LintBody[];
}

/**
 * root 1 つの全体の実行の状態(agora-redesign #1650) — 実行中(前の結果があれば保つ)・測定済み・失敗(前の結果が
 * あれば保つ)の閉じた型に畳む。「まだ全体の実行を始めていない」は roots に entry が無い事で表す。
 */
export type RootRun =
  | { readonly tag: 'running'; readonly since: number; readonly previous: LintReport | undefined }
  | { readonly tag: 'measured'; readonly report: LintReport }
  | { readonly tag: 'failed'; readonly reason: string; readonly previous: LintReport | undefined };

/** RootRun から今の表示に使う report を取り出す(running・failed は前回の物 — 前回も無ければ undefined)。 */
function reportOf(run: RootRun): LintReport | undefined {
  switch (run.tag) {
    case 'running':
      return run.previous;
    case 'measured':
      return run.report;
    case 'failed':
      return run.previous;
    default: {
      const unreachable: never = run;
      throw new Error(`網羅されていない状態: ${JSON.stringify(unreachable)}`);
    }
  }
}

/**
 * 1 file の実行の結果が差し替える範囲(agora-redesign #2163) — linter が判じた規則を名乗れば(judged_rules)その規則の違反だけ、
 * 名乗らない古い linter の出力は file の違反の全部。名乗らない規則(repo 全体でだけ判じる DOEFF166 など)の違反は、全体の実行の
 * 結果のまま残す(以前は全部を差し替え、項目を押した・保存した file からその違反が次の全体の実行まで消えていた)。
 */
type FileScope = { readonly tag: 'every-rule' } | { readonly tag: 'judged'; readonly rules: ReadonlySet<string> };

/** 範囲が規則 rule の違反を差し替えるか。 */
function replaces(scope: FileScope, rule: string): boolean {
  switch (scope.tag) {
    case 'every-rule':
      return true;
    case 'judged':
      return scope.rules.has(rule);
    default: {
      const unreachable: never = scope;
      throw new Error(`網羅されていない範囲: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** file 1 つの差し替え — 範囲と、その file の範囲の規則の違反(1 file の実行の結果)と module の要約。 */
interface FileOverride extends FileView {
  readonly scope: FileScope;
}

/** root 1 つの状態 — 今の全体の実行の状態(RootRun)と、file ごとの差し替え。 */
interface RootState {
  /** report が 1 度も無い間(初回の running・前の結果の無い failed)に表へ出す root の path。 */
  readonly root: string;
  readonly run: RootRun;
  readonly overrides: Map<string, FileOverride>;
}

/** path の比べ方を 1 つに決める。 */
function key(filePath: string): string {
  return path.normalize(filePath);
}

/** root 1 つの今の違反 — 全体の結果のうち差し替えの範囲の外の物と、差し替えた file の 1 file の結果。 */
function composedViolations(base: LintReport, overrides: ReadonlyMap<string, FileOverride>): LintViolation[] {
  const kept = base.violations.filter((v) => {
    const override = overrides.get(key(v.path));
    return override === undefined || !replaces(override.scope, v.rule);
  });
  return [...kept, ...[...overrides.values()].flatMap((override) => override.violations)];
}

/**
 * file 1 つに今見せる物 — 差し替えが無ければ全体の結果の物、在れば範囲の外の規則の違反を全体の結果から残して 1 file の結果と
 * 合成した物。module の要約は 1 file の結果の物(無ければ全体の物)に合成した違反の数を載せる — 判じていない規則の違反は全体の
 * 結果に残るので、1 file の結果の数では足りない(#2163)。全体の結果に module の無い file は要約を見せない(modules() と同じ)。
 */
function shownFile(base: LintReport, wanted: string, override: FileOverride | undefined): FileView {
  const whole = base.violations.filter((v) => key(v.path) === wanted);
  const module = base.modules.find((m) => key(m.path) === wanted);
  if (override === undefined) {
    return { violations: whole, module };
  }
  const violations = [...whole.filter((v) => !replaces(override.scope, v.rule)), ...override.violations];
  return { violations, module: module === undefined ? undefined : { ...(override.module ?? module), violations: violations.length } };
}

/** root の全体の実行が失敗した事と理由(次に成功するまで残す — 失敗を 0 件の結果と見分けるため)。 */
export interface LintFailure {
  readonly root: string;
  readonly reason: string;
}

/** rootRuns() が返す、root 1 つぶんの今の実行の状態。 */
export interface RootRunEntry {
  readonly root: string;
  readonly run: RootRun;
}

/** path で引く表。 */
interface PathTables {
  readonly modules: ReadonlyMap<string, LintModule>;
  readonly violations: ReadonlyMap<string, readonly LintViolation[]>;
}

/** linter の結果の置き場。書き換えのたびに、変わった側(違反か見出し)の購読者へ知らせる。 */
export class LintStore {
  private readonly roots = new Map<string, RootState>();
  /** 違反の側の購読者 */
  private readonly listeners = new Set<() => void>();
  /** 見出しの側の購読者 */
  private readonly signatureListeners = new Set<() => void>();
  private pathTables: PathTables | undefined;
  /** path → 直前の 1 file の実行の見出しと束縛(契約 版 2) */
  private readonly signatures = new Map<string, FileSignatures>();
  /** 結果の出どころ(root か file の path)→ 拡張の知らない語(linter の方が新しい) */
  private readonly unknownBySource = new Map<string, readonly string[]>();

  /**
   * root の全体の実行を始めた事を置く(agora-redesign #1650) — 前の結果(measured の report か、failed の previous)
   * があれば running へ持ち越す。表は「実行中」の札を出しつつ、前の結果があればその違反と波線を保つ(消して 0 件の
   * 「違反はありません」に見せない — 起動直後と再実行の間の取り違え)。
   */
  beginRoot(root: string): void {
    const k = key(root);
    const existing = this.roots.get(k);
    const previous = existing === undefined ? undefined : reportOf(existing.run);
    this.roots.set(k, {
      root: existing?.root ?? root,
      run: { tag: 'running', since: Date.now(), previous },
      overrides: existing?.overrides ?? new Map()
    });
    this.emit();
  }

  /** root の全体の実行の結果で置き換える(それまでの file の差し替えと、失敗の記録は捨てる)。 */
  replaceRoot(root: string, report: LintReport): void {
    this.roots.set(key(root), { root: report.root, run: { tag: 'measured', report }, overrides: new Map() });
    this.noteUnknown(root, report);
    this.emit();
  }

  /**
   * 1 file の実行の結果で、その file の違反と module の要約を差し替える(違反は 1 file の実行が判じた規則の分だけ — 判じて
   * いない規則の違反は全体の結果のまま残す・#2163)。全体の結果が 1 度も無い root(実行中の初回・前の結果の無い失敗)には
   * 何もしない(規則の一覧と地図は全体の実行から作るため)。差し替えた後にその file に見せる物が今見せている物と構造として
   * 同じなら(知らない語も同じなら)違反の側の購読者へ知らせない — 読む面を開いた時の 1 file の実行が前と同じ違反を返す
   * たびに木が出し直されていた(#2162)。
   */
  replaceFile(root: string, filePath: string, report: LintReport): void {
    const state = this.roots.get(key(root));
    const base = state === undefined ? undefined : reportOf(state.run);
    if (state === undefined || base === undefined) {
      return;
    }
    const wanted = key(filePath);
    const scope: FileScope = report.judgedRules === null ? { tag: 'every-rule' } : { tag: 'judged', rules: new Set(report.judgedRules) };
    const shown = shownFile(base, wanted, state.overrides.get(wanted));
    const next: FileOverride = {
      scope,
      violations: report.violations.filter((v) => key(v.path) === wanted && replaces(scope, v.rule)),
      module: report.modules.find((m) => key(m.path) === wanted)
    };
    state.overrides.set(wanted, next);
    const unknownChanged = this.noteUnknown(filePath, report);
    if (sameView(inPositionOrder(shown), inPositionOrder(shownFile(base, wanted, next))) && !unknownChanged) {
      // 見せる物は同じ — path で引く表だけ新しい object で作り直させ、購読者は起こさない
      this.pathTables = undefined;
      return;
    }
    this.emit();
  }

  /**
   * 1 file の実行(stdin)の見出しと束縛を、渡した document の印(版と中身の hash)と組で置く(全体の結果が無い root でも
   * 置く)。知らせは見出しの側だけ(違反の側の購読者 = 木・波線は起こさない)。
   */
  replaceSignatures(filePath: string, stamp: DocumentStamp, report: LintReport): void {
    // 1 file の実行(stdin)の結果は全部その file の物 — path では絞らない。linter は symlink を解いた path を名乗り
    // (macOS の /tmp は /private/tmp)、絞ると見出しが 1 つも出なかった(実測 2026-09-28)
    this.signatures.set(key(filePath), {
      version: stamp.version,
      hash: stamp.hash,
      signatures: report.signatures,
      bindings: report.bindings,
      rewrites: report.rewrites,
      bodies: report.bodies
    });
    this.noteUnknown(filePath, report);
    this.emitSignatures();
  }

  /**
   * 1 file の実行(stdin)の結果を置く — 違反と module の差し替え(前と同じなら鳴らない)と、見出しの差し替え(見出しの側だけ
   * 鳴る)。1 回の実行で違反の側の購読者(木・波線)が呼ばれるのは多くて 1 回(#2162 — 前は 2 回鳴っていた)。
   */
  replaceFileRun(root: string, filePath: string, stamp: DocumentStamp, report: LintReport): void {
    this.replaceFile(root, filePath, report);
    this.replaceSignatures(filePath, stamp, report);
  }

  /** file の見出しと束縛(まだ聞いていなければ undefined — 印は document の今の印と違うことがある)。 */
  signaturesFor(filePath: string): FileSignatures | undefined {
    return this.signatures.get(key(filePath));
  }

  /** document の今の印(版と中身の hash)で聞いた見出しと束縛(無い・古ければ undefined)— 描く側はここから読む。 */
  currentSignatures(filePath: string, now: DocumentStamp): FileSignatures | undefined {
    const seen = this.signatures.get(key(filePath));
    return seen !== undefined && sameStamp(seen, now) ? seen : undefined;
  }

  /** 拡張の知らない語の全部(空なら拡張は linter に追いついている)。 */
  unknownVocabulary(): string[] {
    return [...new Set([...this.unknownBySource.values()].flat())];
  }

  /** 出どころごとの知らない語を置き換える(前と変わったかを返す)。 */
  private noteUnknown(source: string, report: LintReport): boolean {
    const before = this.unknownBySource.get(key(source)) ?? [];
    if (report.unknown.length === 0) {
      this.unknownBySource.delete(key(source));
    } else {
      this.unknownBySource.set(key(source), report.unknown);
    }
    return !sameStrings(before, report.unknown);
  }

  /**
   * root の全体の実行が失敗した事を置く(前の結果は running と同じく持ち越す — 表は古い結果と失敗の理由を並べて
   * 出す)。表示が失敗を「違反はありません」と取り違えないため(agora-redesign #1631)。同じ理由が続く間は書き換え
   * ない(実行のたびに毎回 emit しない)。
   */
  failRoot(root: string, reason: string): void {
    const k = key(root);
    const existing = this.roots.get(k);
    if (existing?.run.tag === 'failed' && existing.run.reason === reason) {
      return;
    }
    const previous = existing === undefined ? undefined : reportOf(existing.run);
    this.roots.set(k, {
      root: existing?.root ?? root,
      run: { tag: 'failed', reason, previous },
      overrides: existing?.overrides ?? new Map()
    });
    this.emit();
  }

  /** 全体の実行が失敗したままの root と理由の全部(実体は rootRuns() の failed から導く)。 */
  failures(): LintFailure[] {
    const found: LintFailure[] = [];
    for (const entry of this.rootRuns()) {
      if (entry.run.tag === 'failed') {
        found.push({ root: entry.root, reason: entry.run.reason });
      }
    }
    return found;
  }

  /** root ごとの今の全体の実行の状態の一覧(running・measured・failed)。表(view.ts の panelViolationRoots)はここから読む。 */
  rootRuns(): RootRunEntry[] {
    return [...this.roots.values()].map((state) => ({ root: reportOf(state.run)?.root ?? state.root, run: state.run }));
  }

  /** root の結果を落とす(folder が workspace から外れた時・linter を切った時)。 */
  removeRoot(root: string): void {
    if (this.roots.delete(key(root))) {
      this.emit();
    }
  }

  /** 結果のある root の一覧(実行中で前の結果も無い root はまだ数えない — 測定済みか、前の結果を持ち越した物だけ)。 */
  rootPaths(): string[] {
    return [...this.roots.values()]
      .map((s) => reportOf(s.run)?.root)
      .filter((r): r is string => r !== undefined);
  }

  /**
   * 今の違反の全部(差し替えた file は 1 file の結果が判じた規則の分をその結果、判じていない規則の分と ほかの file は全体の結果。
   * 前の結果も無い実行中・失敗の root は数えない)。
   */
  violations(): LintViolation[] {
    const found: LintViolation[] = [];
    for (const state of this.roots.values()) {
      const base = reportOf(state.run);
      if (base === undefined) {
        continue;
      }
      found.push(...composedViolations(base, state.overrides));
    }
    return found;
  }

  /** 今の module の要約の全部(root と組にして返す)。 */
  modules(): Array<{ readonly root: string; readonly module: LintModule }> {
    const found: Array<{ readonly root: string; readonly module: LintModule }> = [];
    for (const state of this.roots.values()) {
      const base = reportOf(state.run);
      if (base === undefined) {
        continue;
      }
      for (const module of base.modules) {
        const k = key(module.path);
        const override = state.overrides.get(k);
        // 差し替えた file の要約は合成した物(違反の数は全体の結果に残した分を含む — shownFile)。
        found.push({ root: base.root, module: override === undefined ? module : (shownFile(base, k, override).module ?? module) });
      }
    }
    return found;
  }

  /** 走らせた規則の一覧(root をまたいで規則の ID で重ねない)。 */
  rules(): LintRule[] {
    const byId = new Map<string, LintRule>();
    for (const state of this.roots.values()) {
      const base = reportOf(state.run);
      if (base === undefined) {
        continue;
      }
      for (const rule of base.rules) {
        if (!byId.has(rule.rule)) {
          byId.set(rule.rule, rule);
        }
      }
    }
    return [...byId.values()];
  }

  /** linter が出した層の説明(root をまたいで層の名前で重ねない・linter の順)。 */
  layers(): LintLayer[] {
    const byName = new Map<string, LintLayer>();
    for (const state of this.roots.values()) {
      const base = reportOf(state.run);
      if (base === undefined) {
        continue;
      }
      for (const layer of base.layers) {
        if (!byName.has(layer.name)) {
          byName.set(layer.name, layer);
        }
      }
    }
    return [...byName.values()];
  }

  /** file の module の要約(linter の結果に無ければ undefined)。 */
  moduleFor(filePath: string): LintModule | undefined {
    return this.tables().modules.get(key(filePath));
  }

  /** file の今の違反。 */
  violationsIn(filePath: string): readonly LintViolation[] {
    return this.tables().violations.get(key(filePath)) ?? [];
  }

  /** path で引く表(置き場が変わるまで使い回す — 定義 2 万件の閲覧が 1 件ずつ引くため)。 */
  private tables(): PathTables {
    if (this.pathTables === undefined) {
      const modules = new Map<string, LintModule>();
      for (const { module } of this.modules()) {
        modules.set(key(module.path), module);
      }
      const violations = new Map<string, LintViolation[]>();
      for (const violation of this.violations()) {
        const k = key(violation.path);
        const list = violations.get(k);
        if (list === undefined) {
          violations.set(k, [violation]);
        } else {
          list.push(violation);
        }
      }
      this.pathTables = { modules, violations };
    }
    return this.pathTables;
  }

  /** linter 自身が読めなかった file などの理由。 */
  errors(): string[] {
    return [...this.roots.values()].flatMap((s) => reportOf(s.run)?.errors ?? []);
  }

  /**
   * 違反の側(違反・module・規則・層・root の実行の状態・知らない語)の書き換えの知らせを購読する。戻り値で購読をやめる。
   * 見出しだけの変化では鳴らない(見出しも読む購読者は onDidChangeSignatures も聞く)。
   */
  onDidChange(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** 見出しの側(1 file の見出し・束縛・置き換え・本体の行)の書き換えの知らせを購読する。戻り値で購読をやめる。 */
  onDidChangeSignatures(listener: () => void): () => void {
    this.signatureListeners.add(listener);
    return () => this.signatureListeners.delete(listener);
  }

  /** 違反の側の購読者へ書き換えを知らせる。 */
  private emit(): void {
    this.pathTables = undefined;
    for (const listener of this.listeners) {
      listener();
    }
  }

  /** 見出しの側の購読者へ書き換えを知らせる。 */
  private emitSignatures(): void {
    for (const listener of this.signatureListeners) {
      listener();
    }
  }
}
