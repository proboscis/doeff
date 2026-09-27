// ExternalModuleSource の実装のうち「何をいつ聞き直すか」を持つ部分 — module の置き場所を workspace root 単位で、
// 外の Hy の file の索引を file 単位で cache する。外の世界(uv・子 process・file の時刻)は口で受け取る。
// 外の file の索引は workspace の置き場(HyIndexStore)に入れない(目次の検索・参照の一覧に混ぜない)。

import type { HyFileIndex } from './contract';
import {
  sysPathEntry,
  type ExternalFileView,
  type ExternalModuleQuery,
  type ExternalModuleResult,
  type ExternalModuleSource
} from './external';
import type { HyIndexer } from './indexer';
import { normalizeKey } from './store';

/** module 1 つの置き場所の答え。 */
export type ModuleLocation =
  | { readonly tag: 'found'; readonly origin: string }
  | { readonly tag: 'missing' }
  | { readonly tag: 'error'; readonly reason: string };

/** 何 module かをまとめて聞いた結果(子 process 1 回分)。 */
export type LocateBatch =
  | { readonly tag: 'ok'; readonly locations: ReadonlyMap<string, ModuleLocation> }
  | { readonly tag: 'failed'; readonly reason: string };

/** workspace の Python 環境に module の置き場所を聞く口(実物は uv を通す)。 */
export interface ModuleLocator {
  locate(root: string, modules: readonly string[]): Promise<LocateBatch>;
}

/** cache を捨てる合図を読む口 — root の環境の指紋(lock file・pyproject の時刻)と、file の時刻。 */
export interface ChangeStamps {
  projectFingerprint(root: string): Promise<string>;
  fileStamp(filePath: string): Promise<string>;
}

/** 環境に聞けなかった後、聞き直すまで待つ時間。 */
export const LOCATE_RETRY_AFTER_MS = 60_000;

/** module の置き場所の答え(まとめて聞いた子 process 自体の失敗を含む)。 */
type PendingLocation = ModuleLocation | { readonly tag: 'batch-failed'; readonly reason: string };

/** workspace root 1 つ分の cache。指紋が変われば丸ごと作り直す。 */
interface RootCache {
  readonly fingerprint: string;
  readonly modules: Map<string, Promise<PendingLocation>>;
  failedAt: number | undefined;
}

/** 外の Hy の file 1 つの索引の cache(file の時刻が同じ間だけ使う)。 */
interface HyFileCache {
  readonly stamp: string;
  readonly result: Promise<ExternalModuleResult>;
}

/** 外の module の問い合わせを cache し、同じ module を何度も聞かない ExternalModuleSource。 */
export class ExternalModuleCache implements ExternalModuleSource, ExternalFileView {
  private readonly roots = new Map<string, RootCache>();
  private readonly hyFiles = new Map<string, HyFileCache>();
  private readonly indexed = new Map<string, HyFileIndex>();
  private readonly reported = new Set<string>();
  private readonly listeners = new Set<() => void>();
  private changes = 0;

  constructor(
    private readonly locator: ModuleLocator,
    private readonly indexer: HyIndexer,
    private readonly stamps: ChangeStamps,
    private readonly now: () => number
  ) {}

  /** 外の module を聞く — 置き場所を引き、Hy なら 1 file の索引を取って返す。 */
  async lookup(query: ExternalModuleQuery): Promise<ExternalModuleResult> {
    const cache = await this.rootCache(query.root);
    if (cache.failedAt !== undefined && this.now() - cache.failedAt < LOCATE_RETRY_AFTER_MS) {
      return { tag: 'skipped' };
    }
    const location = await this.locationOf(cache, query);
    switch (location.tag) {
      case 'batch-failed':
        return this.onBatchFailed(cache, location.reason);
      case 'missing':
        return { tag: 'not-found' };
      case 'error':
        return this.reportOnce(`${cache.fingerprint}:${query.root}:${query.module}`, location.reason);
      case 'found':
        return this.fromOrigin(location.origin, query.module);
      default: {
        const unreachable: never = location;
        throw new Error(`網羅されていない置き場所: ${JSON.stringify(unreachable)}`);
      }
    }
  }

  /** 前に取った外の Hy の file の索引(その file を開いた時の目次用)。workspace の置き場とは別。 */
  cachedFile(filePath: string): HyFileIndex | undefined {
    return this.indexed.get(normalizeKey(filePath));
  }

  /** 取った外の Hy の file の索引の全部。 */
  cachedFiles(): readonly HyFileIndex[] {
    return [...this.indexed.values()];
  }

  /** 外の索引が変わるたびに増える数。 */
  get version(): number {
    return this.changes;
  }

  /** 外の索引が変わった知らせを購読する(注記・木の更新用)。戻り値で購読をやめる。 */
  onDidChange(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** root の cache を返す(環境の指紋が変わっていれば作り直す)。 */
  private async rootCache(root: string): Promise<RootCache> {
    const key = normalizeKey(root);
    const fingerprint = await this.stamps.projectFingerprint(root);
    const known = this.roots.get(key);
    if (known !== undefined && known.fingerprint === fingerprint) {
      return known;
    }
    const fresh: RootCache = { fingerprint, modules: new Map(), failedAt: undefined };
    this.roots.set(key, fresh);
    return fresh;
  }

  /** module の置き場所を引く — まだ聞いていなければ prefetch の分と合わせて子 process 1 回で聞く。 */
  private locationOf(cache: RootCache, query: ExternalModuleQuery): Promise<PendingLocation> {
    const known = cache.modules.get(query.module);
    if (known !== undefined) {
      return known;
    }
    const batch = [...new Set([query.module, ...query.prefetch])].filter((m) => !cache.modules.has(m));
    const run = this.locator.locate(query.root, batch);
    for (const module of batch) {
      cache.modules.set(
        module,
        run.then((result): PendingLocation => {
          if (result.tag === 'failed') {
            cache.modules.delete(module); // 答えを持たないので、待ちが明けたら聞き直す
            return { tag: 'batch-failed', reason: result.reason };
          }
          return result.locations.get(module) ?? { tag: 'error', reason: `環境の答えに ${module} が無い` };
        })
      );
    }
    const mine = cache.modules.get(query.module);
    if (mine === undefined) {
      throw new Error(`問い合わせに ${query.module} を積めなかった`);
    }
    return mine;
  }

  /** まとめた問い合わせ自体が失敗した — しばらく聞き直さず、理由は 1 度だけ返す。 */
  private onBatchFailed(cache: RootCache, reason: string): ExternalModuleResult {
    if (cache.failedAt !== undefined && this.now() - cache.failedAt < LOCATE_RETRY_AFTER_MS) {
      return { tag: 'skipped' };
    }
    cache.failedAt = this.now();
    return { tag: 'unavailable', reason: `Python 環境に module の場所を聞けない: ${reason}` };
  }

  /** 同じ理由を何度も Output に出さないよう、初めての時だけ理由を返す。 */
  private reportOnce(key: string, reason: string): ExternalModuleResult {
    if (this.reported.has(key)) {
      return { tag: 'skipped' };
    }
    this.reported.add(key);
    return { tag: 'unavailable', reason };
  }

  /** 置き場所の拡張子で Hy / Python を分ける(.so 等の読めない実体は見せる物が無いので not-found)。 */
  private fromOrigin(origin: string, module: string): Promise<ExternalModuleResult> {
    if (/\.(hy|hyk|hyp)$/.test(origin)) {
      return this.hyFile(origin, module);
    }
    if (origin.endsWith('.py')) {
      return Promise.resolve({ tag: 'python', path: origin });
    }
    return Promise.resolve({ tag: 'not-found' });
  }

  /** 外の Hy の file 1 つの索引を、file の時刻が変わった時だけ hy-index で取り直す。 */
  private async hyFile(origin: string, module: string): Promise<ExternalModuleResult> {
    const key = normalizeKey(origin);
    const stamp = await this.stamps.fileStamp(origin);
    const known = this.hyFiles.get(key);
    if (known !== undefined && known.stamp === stamp) {
      const reused = await known.result;
      // 失敗の理由は初めの 1 回で報告済み
      return reused.tag === 'unavailable' ? { tag: 'skipped' } : reused;
    }
    const result = this.indexHyFile(origin, module, `${key}@${stamp}`);
    this.hyFiles.set(key, { stamp, result });
    return result;
  }

  /** hy-index を sys.path の入口を root にして 1 file だけ走らせる。 */
  private async indexHyFile(origin: string, module: string, reportKey: string): Promise<ExternalModuleResult> {
    const root = sysPathEntry(origin, module);
    const outcome = await this.indexer.index({ tag: 'files', root, files: [origin] });
    switch (outcome.tag) {
      case 'failed':
      case 'unsupported':
        return this.reportOnce(reportKey, `外の Hy の file ${origin} の索引を取れない: ${outcome.reason}`);
      case 'ok': {
        const file = outcome.document.files.find((f) => normalizeKey(f.path) === normalizeKey(origin));
        if (file === undefined) {
          return this.reportOnce(reportKey, `外の Hy の file ${origin} が hy-index の結果に出なかった`);
        }
        this.indexed.set(normalizeKey(origin), file);
        this.changes += 1;
        for (const listener of this.listeners) {
          listener();
        }
        return { tag: 'hy', path: origin, file };
      }
      default: {
        const unreachable: never = outcome;
        throw new Error(`網羅されていない結果: ${JSON.stringify(unreachable)}`);
      }
    }
  }
}
