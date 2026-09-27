// Hy の索引の唯一の置き場。file path をキーに 1 file 1 件を持ち、provider はすべてここから読む。
// 外の世界には触らない(子 process の結果を入れるのは indexService の役目)。

import * as path from 'path';
import type { HyFileIndex } from './contract';
import { mangleDotted } from './mangle';

/** 置き場の 1 件 — どの workspace root の索引として取ったかを添える(module 名の基準)。 */
export interface HyStoreEntry {
  readonly root: string;
  readonly file: HyFileIndex;
}

/** 置き場を読むだけの面。解決の論理はこの面だけを受け取る。 */
export interface HyIndexView {
  get(filePath: string): HyStoreEntry | undefined;
  entries(): readonly HyStoreEntry[];
  byModule(module: string): readonly HyStoreEntry[];
}

/** path の比べ方を 1 つに決める(区切り・`..` の揺れを消す)。 */
export function normalizeKey(filePath: string): string {
  return path.normalize(filePath);
}

/** 索引の置き場。書き換えのたびに購読者へ知らせる。 */
export class HyIndexStore implements HyIndexView {
  private readonly files = new Map<string, HyStoreEntry>();
  private readonly listeners = new Set<() => void>();

  /** 1 file の索引を引く。 */
  get(filePath: string): HyStoreEntry | undefined {
    return this.files.get(normalizeKey(filePath));
  }

  /** 全 file の索引を返す。 */
  entries(): readonly HyStoreEntry[] {
    return [...this.files.values()];
  }

  /** module 名(mangle して比べる)が一致する file を返す。 */
  byModule(module: string): readonly HyStoreEntry[] {
    const wanted = mangleDotted(module);
    return this.entries().filter((entry) => mangleDotted(entry.file.module) === wanted);
  }

  /** root 1 つの全体の索引で置き換える — 結果に無い、その root の古い file は消す。 */
  replaceRoot(root: string, files: readonly HyFileIndex[]): void {
    const rootKey = normalizeKey(root);
    for (const [key, entry] of this.files) {
      if (normalizeKey(entry.root) === rootKey) {
        this.files.delete(key);
      }
    }
    for (const file of files) {
      this.files.set(normalizeKey(file.path), { root, file });
    }
    this.emit();
  }

  /** 何 file かの索引を足す・差し替える。 */
  upsert(root: string, files: readonly HyFileIndex[]): void {
    for (const file of files) {
      this.files.set(normalizeKey(file.path), { root, file });
    }
    this.emit();
  }

  /** 消えた file の索引を落とす。 */
  remove(filePath: string): void {
    if (this.files.delete(normalizeKey(filePath))) {
      this.emit();
    }
  }

  /** dir の下の索引を全部落とす(dir ごとの削除・改名の元)。 */
  removeUnder(dirPath: string): void {
    const prefix = normalizeKey(dirPath) + path.sep;
    let changed = false;
    for (const key of [...this.files.keys()]) {
      if (key.startsWith(prefix)) {
        this.files.delete(key);
        changed = true;
      }
    }
    if (changed) {
      this.emit();
    }
  }

  /** 書き換えの知らせを購読する。戻り値で購読をやめる。 */
  onDidChange(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** 購読者へ書き換えを知らせる。 */
  private emit(): void {
    for (const listener of this.listeners) {
      listener();
    }
  }
}
