// VS Code の拡張の追随(dotfiles の agentcli/src/agentcli/vsix_follow.py・ai land arm の側 loop)が書く状態 file の契約と、
// そこから出す報せの判定(agora-redesign #1043)。この file は `vscode` を import しない純関数だけ(単体の検が VS Code 無しで撃てる)。
//
// 責務の分け方: 「入れた版と commit・直近の失敗」を知っているのは追随だけなので追随が状態 file に書く。「いま動いている拡張の commit」を
// 知っているのは動いている拡張の側だけ(入った dir の out/vsix-follow-build.json)なので、Reload が要るかはこちらが判じる。
// 追随は Reload を撃たない — Reload は operator が通知のボタンで行う。
//
// 状態 file の形は 2 つの repo の間の契約。知らない版・欠けた欄・読めない file では黙る(警告を出さない・落ちない)。

import * as path from 'path';

/** 状態 file の形の版(書き手の `STATUS_SCHEMA` と同じ数)。 */
export const FOLLOW_STATUS_SCHEMA = 1;
/** 状態の根からの状態 file の相対 path(書き手の `STATE_SUBDIR` + `STATUS_NAME`)。 */
export const FOLLOW_STATUS_RELPATH: readonly string[] = ['ai', 'vsix-follow', 'status.json'];
/** 組み立ての印の置き場(拡張の dir からの相対・書き手の `MARK_RELPATH`)— 追随が組む前に書き、vsix に同梱する。 */
export const BUILD_MARK_RELPATH = path.join('out', 'vsix-follow-build.json');

/** 追随が VS Code に入れた版(組んだ commit は入った dir の印の commit と同じ)。 */
export interface InstalledBuild {
  readonly version: string;
  readonly commit: string;
  readonly installedAt: string;
}

/** 直近の落ちた組み立て(前の版のまま)— 理由は 1 行。 */
export interface LastFailure {
  readonly commit: string;
  readonly reason: string;
  readonly at: string;
}

/** 拡張 1 つの状態(状態 file の行)— identity は VS Code の身元 `<publisher>.<name>`(小文字)。 */
export interface ExtensionStatus {
  readonly name: string;
  readonly identity: string;
  readonly installed: InstalledBuild | null;
  readonly failure: LastFailure | null;
}

export interface FollowStatus {
  readonly extensions: readonly ExtensionStatus[];
}

/** 状態 file を読んだ結果 — silent は報せを出さない理由(output の log にだけ書く)。 */
export type FollowStatusRead =
  | { readonly tag: 'ok'; readonly status: FollowStatus; readonly skipped: readonly string[] }
  | { readonly tag: 'silent'; readonly reason: string };

/** いま動いている拡張の組み立て — marked = 入った dir の印を読めた・unmarked = 印の無い dir(追随が印を書く前に入れた物・手で
 *  入れた物)で版だけ分かる・absent = この窓の拡張の一覧に居ない。 */
export type RunningBuild =
  | { readonly tag: 'marked'; readonly commit: string; readonly version: string }
  | { readonly tag: 'unmarked'; readonly version: string }
  | { readonly tag: 'absent' };

/** 出す報せ — reload = 新しい版が入ったが動いているのは前の版・failure = 組み立てが落ちて前の版のまま。key は同じ報せを繰り返さない鍵。 */
export type FollowNotice =
  | {
      readonly tag: 'reload';
      readonly key: string;
      readonly name: string;
      readonly identity: string;
      readonly version: string;
      readonly commit: string;
    }
  | {
      readonly tag: 'failure';
      readonly key: string;
      readonly name: string;
      readonly identity: string;
      readonly commit: string;
      readonly reason: string;
    };

/** 出した報せの控え — reloads = この窓で出した Reload の報せの鍵・failures = 身元ごとの、出した失敗の警告の鍵(窓をまたいで共有する)。 */
export interface NoticeLedger {
  readonly reloads: readonly string[];
  readonly failures: Readonly<Record<string, string>>;
}

export const EMPTY_LEDGER: NoticeLedger = { reloads: [], failures: {} };

/** 状態 file の path — 書き手の `state_root` と同じ解き方(env XDG_STATE_HOME、無ければ `<home>/.local/state`)。 */
export function followStatusPath(env: Readonly<Record<string, string | undefined>>, home: string): string {
  const stateHome = env.XDG_STATE_HOME || path.join(home, '.local', 'state');
  return path.join(stateHome, ...FOLLOW_STATUS_RELPATH);
}

/** 契約の JSON の表(object)かを確かめる — 欄を引く前の型の絞り。 */
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** 表の欄を空でない文字列として引く — 欠けた・型の違う欄を undefined にして、既定値で埋めない。 */
function text(record: Record<string, unknown>, key: string): string | undefined {
  const value = record[key];
  return typeof value === 'string' && value !== '' ? value : undefined;
}

/** installed の欄(null か 版・commit・入れた時刻の表)を読む。形が違えば undefined(その行を読み飛ばす)。 */
function readInstalled(value: unknown): InstalledBuild | null | undefined {
  if (value === null) {
    return null;
  }
  if (!isRecord(value)) {
    return undefined;
  }
  const version = text(value, 'version');
  const commit = text(value, 'commit');
  const installedAt = text(value, 'installed_at');
  return version && commit && installedAt ? { version, commit, installedAt } : undefined;
}

/** failure の欄(null か commit・理由・時刻の表)を読む。形が違えば undefined(その行を読み飛ばす)。 */
function readFailure(value: unknown): LastFailure | null | undefined {
  if (value === null) {
    return null;
  }
  if (!isRecord(value)) {
    return undefined;
  }
  const commit = text(value, 'commit');
  const reason = text(value, 'reason');
  const at = text(value, 'at');
  return commit && reason && at ? { commit, reason, at } : undefined;
}

/** 拡張の行 1 つを読む。欄が欠けた・形が違う行は undefined(その拡張だけ黙る)。 */
function readEntry(value: unknown): ExtensionStatus | undefined {
  if (!isRecord(value)) {
    return undefined;
  }
  const name = text(value, 'name');
  const identity = text(value, 'identity');
  const installed = readInstalled(value.installed);
  const failure = readFailure(value.failure);
  if (!name || !identity || installed === undefined || failure === undefined) {
    return undefined;
  }
  return { name, identity: identity.toLowerCase(), installed, failure };
}

/** 状態 file の本文を読む(読み込みの唯一の検査)— 読めない JSON・知らない版は silent、欠けた欄の行はその行だけ読み飛ばす
 *  (skipped に理由を控える)。知らない欄は読み飛ばす(書き手が欄を足しても版は上げない約束)。 */
export function parseFollowStatus(body: string): FollowStatusRead {
  let raw: unknown;
  try {
    raw = JSON.parse(body);
  } catch (error) {
    return { tag: 'silent', reason: `状態 file を JSON として読めない(${String(error)})` };
  }
  if (!isRecord(raw)) {
    return { tag: 'silent', reason: '状態 file が JSON の表でない' };
  }
  if (raw.schema !== FOLLOW_STATUS_SCHEMA) {
    return { tag: 'silent', reason: `状態 file の版 ${JSON.stringify(raw.schema)} を知らない(読める版は ${FOLLOW_STATUS_SCHEMA})` };
  }
  if (!Array.isArray(raw.extensions)) {
    return { tag: 'silent', reason: '状態 file に extensions の列が無い' };
  }
  const extensions: ExtensionStatus[] = [];
  const skipped: string[] = [];
  raw.extensions.forEach((value: unknown, index: number) => {
    const entry = readEntry(value);
    if (entry === undefined) {
      skipped.push(`extensions[${index}] の欄が欠けているか形が違う`);
    } else {
      extensions.push(entry);
    }
  });
  return { tag: 'ok', status: { extensions }, skipped };
}

/** 動いている拡張の package.json(VS Code の `Extension.packageJSON`・型の無い値)から版を引く — 比べる版の出どころ。 */
export function manifestVersion(manifest: unknown): string | undefined {
  return isRecord(manifest) ? text(manifest, 'version') : undefined;
}

/** 動いている拡張の組み立てを、入った dir の印の本文(無い・読めなければ undefined)と拡張の package.json の版から決める。
 *  印が読めればその commit、読めなければ版だけ(版も分からなければ absent — 判じない)。 */
export function runningBuild(markBody: string | undefined, packageVersion: string | undefined): RunningBuild {
  if (markBody !== undefined) {
    try {
      const mark: unknown = JSON.parse(markBody);
      if (isRecord(mark)) {
        const commit = text(mark, 'commit');
        const version = text(mark, 'version');
        if (commit && version) {
          return { tag: 'marked', commit, version };
        }
      }
    } catch {
      // 読めない印は印の無い dir と同じに扱う(下で版だけを見る)
    }
  }
  return packageVersion ? { tag: 'unmarked', version: packageVersion } : { tag: 'absent' };
}

/** 入れた版を有効にするのに Reload が要るか — 印があれば commit で、印の無い dir は版で比べる。この窓に居ない拡張と、印が無くて
 *  版が同じ拡張は判じない(false)。 */
function needsReload(installed: InstalledBuild, running: RunningBuild): boolean {
  switch (running.tag) {
    case 'marked':
      return running.commit !== installed.commit;
    case 'unmarked':
      return running.version !== installed.version;
    case 'absent':
      return false;
    default: {
      const unreachable: never = running;
      throw new Error(`網羅されていない組み立て: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 状態と動いている組み立てから、いま当てはまる報せの組を決める(同じ報せを繰り返さない絞りは unseenNotices)。 */
export function followNotices(status: FollowStatus, runningOf: (identity: string) => RunningBuild): FollowNotice[] {
  const notices: FollowNotice[] = [];
  for (const entry of status.extensions) {
    const { name, identity, installed, failure } = entry;
    if (installed !== null && needsReload(installed, runningOf(identity))) {
      notices.push({
        tag: 'reload',
        key: `${identity}@${installed.commit}`,
        name,
        identity,
        version: installed.version,
        commit: installed.commit
      });
    }
    if (failure !== null) {
      notices.push({
        tag: 'failure',
        key: `${identity}@${failure.commit}:${failure.reason}`,
        name,
        identity,
        commit: failure.commit,
        reason: failure.reason
      });
    }
  }
  return notices;
}

/** 当てはまる報せのうち、まだ出していない物と、出した後の控えを返す。Reload の報せは同じ入った版(身元と commit)で 1 回。
 *  失敗の警告は同じ失敗(身元・commit・理由)で 1 回 — 失敗の欄が消えた身元の控えは落とす(後でまた落ちれば、もう 1 度出す)。 */
export function unseenNotices(
  notices: readonly FollowNotice[],
  ledger: NoticeLedger
): { readonly show: FollowNotice[]; readonly ledger: NoticeLedger } {
  const show: FollowNotice[] = [];
  const reloads = [...ledger.reloads];
  const failures: Record<string, string> = {};
  for (const notice of notices) {
    switch (notice.tag) {
      case 'reload':
        if (!reloads.includes(notice.key)) {
          reloads.push(notice.key);
          show.push(notice);
        }
        break;
      case 'failure':
        if (ledger.failures[notice.identity] !== notice.key) {
          show.push(notice);
        }
        failures[notice.identity] = notice.key;
        break;
      default: {
        const unreachable: never = notice;
        throw new Error(`網羅されていない報せ: ${JSON.stringify(unreachable)}`);
      }
    }
  }
  return { show, ledger: { reloads, failures } };
}

/** 保存しておいた失敗の警告の控えを読む — 形が違えば(古い版の拡張が置いた値など)空の控えにする。 */
export function readFailureLedger(value: unknown): Readonly<Record<string, string>> {
  if (!isRecord(value)) {
    return {};
  }
  const read: Record<string, string> = {};
  for (const [identity, key] of Object.entries(value)) {
    if (typeof key === 'string') {
      read[identity] = key;
    }
  }
  return read;
}

/** Reload の報せの文。 */
export function reloadMessage(notice: Extract<FollowNotice, { tag: 'reload' }>): string {
  return `${notice.name} の新しい版 ${notice.version}(${notice.commit.slice(0, 10)})が入りました。Reload Window で有効になります。`;
}

/** 失敗の警告の文。 */
export function failureMessage(notice: Extract<FollowNotice, { tag: 'failure' }>): string {
  return `${notice.name} の組み立てに失敗したため、前の版のままです(${notice.commit.slice(0, 10)}: ${notice.reason})。`;
}
