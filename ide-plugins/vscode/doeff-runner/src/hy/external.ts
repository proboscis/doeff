// workspace の外の module(uv の git / path 依存の package 等)の置き場所と中身を返す口(effect)。
// 解決の論理(resolve.ts)はこの口だけを受け取り、uv や子 process を知らない。実 I/O は externalSource.ts の handler。

import * as path from 'path';
import type { HyFileIndex } from './contract';

/** 外の module 1 つを聞いた結果。 */
export type ExternalModuleResult =
  /** Hy の module — その 1 file の索引(workspace の置き場とは別の cache から) */
  | { readonly tag: 'hy'; readonly path: string; readonly file: HyFileIndex }
  /** Python の module — file の場所だけ(名前は Python の名前探しで探す) */
  | { readonly tag: 'python'; readonly path: string }
  /** 環境に無い module(組み込み・名前空間 package を含む) */
  | { readonly tag: 'not-found' }
  /** 聞けなかった(uv が無い・環境が無い・時間切れ・索引の失敗)— 理由は初めの 1 度だけ運ぶ */
  | { readonly tag: 'unavailable'; readonly reason: string }
  /** 同じ理由で聞けないことが既に分かっていて、理由は報告済み */
  | { readonly tag: 'skipped' };

/** 外の module の問い合わせ。 */
export interface ExternalModuleQuery {
  /** 問い合わせる Python 環境の workspace root(uv の --project) */
  readonly root: string;
  readonly module: string;
  /** 同じ子 process でまとめて聞いておく module(書いた file の import 全部) */
  readonly prefetch: readonly string[];
}

/** 外の module を聞く口。 */
export interface ExternalModuleSource {
  lookup(query: ExternalModuleQuery): Promise<ExternalModuleResult>;
}

/** 前に取った外の Hy の file の索引を読む面(その file を開いた時の目次・hover 用。workspace の置き場とは別)。 */
export interface ExternalFileView {
  cachedFile(filePath: string): HyFileIndex | undefined;
  /** 取った外の Hy の file の索引の全部(effect の判定と handler の数え上げは、ここに入った物までを見る) */
  cachedFiles(): readonly HyFileIndex[];
  /** 外の索引が増えた・変わったたびに増える数 */
  readonly version: number;
}

/** 外の file を 1 つも持たない面(テストの既定)。 */
export const NO_EXTERNAL_FILES: ExternalFileView = {
  cachedFile: () => undefined,
  cachedFiles: () => [],
  version: 0
};

/** 外の module を使わない口(workspace の外を見ない時・テストの既定)。 */
export const NO_EXTERNAL_MODULES: ExternalModuleSource = {
  lookup: async () => ({ tag: 'not-found' })
};

/**
 * module の file(origin)から、その module が import される sys.path の入口の dir を求める。
 * hy-index の --root に渡し、索引の module 名が import の名前と一致するようにするため。
 * `a.b` の `/x/a/b.hy` → `/x`、package の `/x/a/b/__init__.hy` → `/x`。
 */
export function sysPathEntry(origin: string, module: string): string {
  const segments = module.split('.').filter((s) => s !== '').length;
  const isInit = /^__init__\.[A-Za-z]+$/.test(path.basename(origin));
  let dir = path.dirname(origin);
  const ups = segments - 1 + (isInit ? 1 : 0);
  for (let i = 0; i < ups; i += 1) {
    dir = path.dirname(dir);
  }
  return dir;
}
