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

/** root 1 つの状態 — 直前の全体の結果と、file ごとの差し替え。 */
interface RootState {
  readonly report: LintReport;
  readonly overrides: Map<string, { readonly violations: readonly LintViolation[]; readonly module: LintModule | undefined }>;
}

/** path の比べ方を 1 つに決める。 */
function key(filePath: string): string {
  return path.normalize(filePath);
}

/** root の全体の実行が失敗した事と理由(次に成功するまで残す — 失敗を 0 件の結果と見分けるため)。 */
export interface LintFailure {
  readonly root: string;
  readonly reason: string;
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
  /** root → 直前の全体の実行の失敗(成功すれば消す) */
  private readonly rootFailures = new Map<string, LintFailure>();

  /** root の全体の実行の結果で置き換える(それまでの file の差し替えと、失敗の記録は捨てる)。 */
  replaceRoot(root: string, report: LintReport): void {
    this.roots.set(key(root), { report, overrides: new Map() });
    this.rootFailures.delete(key(root));
    this.noteUnknown(root, report);
    this.emit();
  }

  /**
   * 1 file の実行の結果で、その file の違反と module の要約を差し替える。全体の結果がまだ無い root には何もしない
   * (規則の一覧と地図は全体の実行から作るため)。
   */
  replaceFile(root: string, filePath: string, report: LintReport): void {
    const state = this.roots.get(key(root));
    if (state === undefined) {
      return;
    }
    const wanted = key(filePath);
    state.overrides.set(wanted, {
      violations: report.violations.filter((v) => key(v.path) === wanted),
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
   * root の全体の実行が失敗した事を置く(前の成功の結果は残す — 表は古い結果と失敗の理由を並べて出す)。表示が失敗を
   * 「違反はありません」と取り違えないため(agora-redesign #1631)。
   */
  failRoot(root: string, reason: string): void {
    const previous = this.rootFailures.get(key(root));
    if (previous?.reason !== reason) {
      this.rootFailures.set(key(root), { root, reason });
      this.emit();
    }
  }

  /** 全体の実行が失敗したままの root と理由の全部。 */
  failures(): LintFailure[] {
    return [...this.rootFailures.values()];
  }

  /** root の結果を落とす(folder が workspace から外れた時・linter を切った時)。 */
  removeRoot(root: string): void {
    const hadFailure = this.rootFailures.delete(key(root));
    if (this.roots.delete(key(root)) || hadFailure) {
      this.emit();
    }
  }

  /** 結果のある root の一覧。 */
  rootPaths(): string[] {
    return [...this.roots.values()].map((s) => s.report.root);
  }

  /** 今の違反の全部(差し替えた file はその結果、それ以外は全体の結果)。 */
  violations(): LintViolation[] {
    const found: LintViolation[] = [];
    for (const state of this.roots.values()) {
      found.push(...state.report.violations.filter((v) => !state.overrides.has(key(v.path))));
      for (const override of state.overrides.values()) {
        found.push(...override.violations);
      }
    }
    return found;
  }

  /** 今の module の要約の全部(root と組にして返す)。 */
  modules(): Array<{ readonly root: string; readonly module: LintModule }> {
    const found: Array<{ readonly root: string; readonly module: LintModule }> = [];
    for (const state of this.roots.values()) {
      for (const module of state.report.modules) {
        const override = state.overrides.get(key(module.path));
        found.push({ root: state.report.root, module: override?.module ?? module });
      }
    }
    return found;
  }

  /** 走らせた規則の一覧(root をまたいで規則の ID で重ねない)。 */
  rules(): LintRule[] {
    const byId = new Map<string, LintRule>();
    for (const state of this.roots.values()) {
      for (const rule of state.report.rules) {
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
      for (const layer of state.report.layers) {
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
    return [...this.roots.values()].flatMap((s) => s.report.errors);
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
