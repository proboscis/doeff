// linter の結果の置き場 — workspace の root ごとに直前の全体の実行の結果を持ち、編集中の file は 1 file の実行の結果で
// 差し替える。表示(波線・パネル・地図)はすべてここから読む。外の世界には触らない。

import * as path from 'path';
import type { LintBinding, LintBody, LintLayer, LintModule, LintReport, LintRewrite, LintRule, LintSignature, LintViolation } from './contract';

/** 1 file の見出しと束縛(linter に渡した document の版つき — 版が進んだら古い位置なので描かない)。 */
export interface FileSignatures {
  readonly version: number;
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
interface FileOverride {
  readonly scope: FileScope;
  readonly violations: readonly LintViolation[];
  readonly module: LintModule | undefined;
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

/** linter の結果の置き場。書き換えのたびに購読者へ知らせる。 */
export class LintStore {
  private readonly roots = new Map<string, RootState>();
  private readonly listeners = new Set<() => void>();
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
   * 1 file の実行の結果で、その file の違反と module の要約を差し替える。全体の結果が 1 度も無い root(実行中の
   * 初回・前の結果の無い失敗)には何もしない(規則の一覧と地図は全体の実行から作るため)。
   */
  replaceFile(root: string, filePath: string, report: LintReport): void {
    const state = this.roots.get(key(root));
    const base = state === undefined ? undefined : reportOf(state.run);
    if (state === undefined || base === undefined) {
      return;
    }
    const wanted = key(filePath);
    const scope: FileScope = report.judgedRules === null ? { tag: 'every-rule' } : { tag: 'judged', rules: new Set(report.judgedRules) };
    state.overrides.set(wanted, {
      scope,
      violations: report.violations.filter((v) => key(v.path) === wanted && replaces(scope, v.rule)),
      module: report.modules.find((m) => key(m.path) === wanted)
    });
    this.noteUnknown(filePath, report);
    this.emit();
  }

  /** 1 file の実行(stdin)の見出しと束縛を、渡した document の版と組で置く(全体の結果が無い root でも置く)。 */
  replaceSignatures(filePath: string, version: number, report: LintReport): void {
    // 1 file の実行(stdin)の結果は全部その file の物 — path では絞らない。linter は symlink を解いた path を名乗り
    // (macOS の /tmp は /private/tmp)、絞ると見出しが 1 つも出なかった(実測 2026-09-28)
    this.signatures.set(key(filePath), {
      version,
      signatures: report.signatures,
      bindings: report.bindings,
      rewrites: report.rewrites,
      bodies: report.bodies
    });
    this.noteUnknown(filePath, report);
    this.emit();
  }

  /** file の見出しと束縛(まだ聞いていなければ undefined)。 */
  signaturesFor(filePath: string): FileSignatures | undefined {
    return this.signatures.get(key(filePath));
  }

  /** 閉じた file の見出しを捨てる。 */
  forgetSignatures(filePath: string): void {
    this.signatures.delete(key(filePath));
  }

  /** 拡張の知らない語の全部(空なら拡張は linter に追いついている)。 */
  unknownVocabulary(): string[] {
    return [...new Set([...this.unknownBySource.values()].flat())];
  }

  /** 出どころごとの知らない語を置き換える。 */
  private noteUnknown(source: string, report: LintReport): void {
    if (report.unknown.length === 0) {
      this.unknownBySource.delete(key(source));
    } else {
      this.unknownBySource.set(key(source), report.unknown);
    }
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
      // 差し替えた file の要約は 1 file の結果の物(無ければ全体の物)に、合成した違反の数を載せる — 判じていない規則の違反は全体の
      // 結果に残るので、1 file の結果の数では足りない(#2163)。
      const counts = new Map<string, number>();
      if (state.overrides.size > 0) {
        for (const violation of composedViolations(base, state.overrides)) {
          const k = key(violation.path);
          counts.set(k, (counts.get(k) ?? 0) + 1);
        }
      }
      for (const module of base.modules) {
        const k = key(module.path);
        const override = state.overrides.get(k);
        found.push({ root: base.root, module: override === undefined ? module : { ...(override.module ?? module), violations: counts.get(k) ?? 0 } });
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

  /** 書き換えの知らせを購読する。戻り値で購読をやめる。 */
  onDidChange(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** 購読者へ書き換えを知らせる。 */
  private emit(): void {
    this.pathTables = undefined;
    for (const listener of this.listeners) {
      listener();
    }
  }
}
