// Hy の索引の唯一の置き場。file path をキーに 1 file 1 件を持ち、provider はすべてここから読む。
// 外の世界には触らない(子 process の結果を入れるのは indexService の役目)。

import * as path from 'path';
import type { HyDefinition, HyFileIndex } from './contract';
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
  /** 名前(mangled)の定義を全 file から引く(解決の最後の段と参照の数え上げ用) */
  definitionsNamed(mangled: string): readonly NamedDefinition[];
  /** 書き換えのたびに増える数(派生の表を作り直すかの判定用) */
  readonly version: number;
}

/** 名前で引いた定義 1 件と、その file の索引。 */
export interface NamedDefinition {
  readonly entry: HyStoreEntry;
  readonly definition: HyDefinition;
}

/** 定義を file の版をまたいで同一視するキー(kind・入れ物・名前 — 同じキーが複数あれば順に対応させる)。 */
function definitionKey(definition: HyDefinition): string {
  return `${definition.kind}|${definition.container ?? ''}|${definition.name}`;
}

/**
 * 部分の実行(1 file・--file)の結果に、直前の全体の実行の経由の証拠(file をまたぐ事実)を引き継ぐ。
 * 直接の証拠は新しい結果のまま使う。引き継いだ経由の証拠は次の全体の実行まで古いことがある。
 */
export function carryOverCrossFile(previous: HyFileIndex | undefined, next: HyFileIndex): HyFileIndex {
  if (previous === undefined) {
    return next;
  }
  const byKey = new Map<string, HyDefinition[]>();
  for (const definition of previous.definitions) {
    const key = definitionKey(definition);
    byKey.set(key, [...(byKey.get(key) ?? []), definition]);
  }
  const definitions = next.definitions.map((definition) => {
    const earlier = byKey.get(definitionKey(definition))?.shift();
    if (earlier === undefined || definition.raw.via.length > 0) {
      return definition;
    }
    return { ...definition, raw: { direct: definition.raw.direct, via: earlier.raw.via } };
  });
  return { ...next, definitions };
}

/** path の比べ方を 1 つに決める(区切り・`..` の揺れを消す)。 */
export function normalizeKey(filePath: string): string {
  return path.normalize(filePath);
}

/** 索引の置き場。書き換えのたびに購読者へ知らせる。 */
export class HyIndexStore implements HyIndexView {
  private readonly files = new Map<string, HyStoreEntry>();
  private readonly listeners = new Set<() => void>();
  private changes = 0;
  private moduleTable: { readonly version: number; readonly table: Map<string, HyStoreEntry[]> } | undefined;
  private nameTable: { readonly version: number; readonly table: Map<string, NamedDefinition[]> } | undefined;

  /** 書き換えのたびに増える数。 */
  get version(): number {
    return this.changes;
  }

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
    if (this.moduleTable === undefined || this.moduleTable.version !== this.changes) {
      // 解決は呼び出しの数だけ module を引くので、版ごとに 1 度だけ表を作る
      const table = new Map<string, HyStoreEntry[]>();
      for (const entry of this.files.values()) {
        const key = mangleDotted(entry.file.module);
        table.set(key, [...(table.get(key) ?? []), entry]);
      }
      this.moduleTable = { version: this.changes, table };
    }
    return this.moduleTable.table.get(mangleDotted(module)) ?? [];
  }

  /** 名前(mangled)の定義を全 file から引く(版ごとに 1 度だけ表を作る)。 */
  definitionsNamed(mangled: string): readonly NamedDefinition[] {
    if (this.nameTable === undefined || this.nameTable.version !== this.changes) {
      const table = new Map<string, NamedDefinition[]>();
      for (const entry of this.files.values()) {
        for (const definition of entry.file.definitions) {
          const list = table.get(definition.mangled);
          if (list === undefined) {
            table.set(definition.mangled, [{ entry, definition }]);
          } else {
            list.push({ entry, definition });
          }
        }
      }
      this.nameTable = { version: this.changes, table };
    }
    return this.nameTable.table.get(mangled) ?? [];
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
      const key = normalizeKey(file.path);
      // 部分の実行は file をまたぐ経由の証拠を持たないので、直前の全体の結果を引き継ぐ
      this.files.set(key, { root, file: carryOverCrossFile(this.files.get(key)?.file, file) });
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
    this.changes += 1;
    for (const listener of this.listeners) {
      listener();
    }
  }
}
