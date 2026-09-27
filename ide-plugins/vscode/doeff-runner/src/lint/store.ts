// linter の結果の置き場 — workspace の root ごとに直前の全体の実行の結果を持ち、編集中の file は 1 file の実行の結果で
// 差し替える。表示(波線・パネル・地図)はすべてここから読む。外の世界には触らない。

import * as path from 'path';
import type { LintLayer, LintModule, LintReport, LintRule, LintViolation } from './contract';

/** root 1 つの状態 — 直前の全体の結果と、file ごとの差し替え。 */
interface RootState {
  readonly report: LintReport;
  readonly overrides: Map<string, { readonly violations: readonly LintViolation[]; readonly module: LintModule | undefined }>;
}

/** path の比べ方を 1 つに決める。 */
function key(filePath: string): string {
  return path.normalize(filePath);
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

  /** root の全体の実行の結果で置き換える(それまでの file の差し替えは捨てる)。 */
  replaceRoot(root: string, report: LintReport): void {
    this.roots.set(key(root), { report, overrides: new Map() });
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
    this.emit();
  }

  /** root の結果を落とす(folder が workspace から外れた時)。 */
  removeRoot(root: string): void {
    if (this.roots.delete(key(root))) {
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
        violations.set(k, [...(violations.get(k) ?? []), violation]);
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
