// VS Code の拡張の追随の報せ(agora-redesign #1043)— 追随(dotfiles の agentcli/src/agentcli/vsix_follow.py)が書く状態 file を、
// 起動した時と file が変わった時に読み、新しい版が入ったのに動いているのが前の版なら Reload のボタンつきの通知を、組み立てが
// 落ちていれば警告を出す。判定は ./status の純関数、この file は VS Code と file system に触る口だけ。
// doeff-runner 1 つが状態 file の全部の拡張(python-semantic-highlighter を含む)について出す — 他の拡張の動いている版は
// `vscode.extensions.getExtension(<身元>).extensionPath` の入った dir の印で読む。

import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import * as vscode from 'vscode';
import {
  BUILD_MARK_RELPATH,
  followNotices,
  followStatusPath,
  failureMessage,
  manifestVersion,
  parseFollowStatus,
  readFailureLedger,
  reloadMessage,
  runningBuild,
  unseenNotices,
  type FollowNotice,
  type RunningBuild
} from './status';

/** 出した失敗の警告の控えの鍵(globalState — 窓をまたいで同じ失敗を繰り返さない)。 */
const FAILURE_LEDGER_KEY = 'doeff-runner.follow.failureNotices';
/** 通知のボタンの文字。 */
const RELOAD_ACTION = 'Reload Window';
/** 見張りを張り直し、状態 file を読み直す周期(ms)— dir がまだ無い機体と、見張りが取りこぼした変化のため。追随の拍と同じ 60 秒。 */
const RECHECK_MS = 60_000;
/** 変化の知らせをまとめる間(ms)— 原子的な書き直し(一時 file → rename)は知らせが続けて来る。 */
const SETTLE_MS = 500;

/** file を読んだ結果 — missing = file が無い(追随の居ない機体・印の無い古い dir)・unreadable = 在るが読めない。 */
type FileRead =
  | { readonly tag: 'ok'; readonly body: string }
  | { readonly tag: 'missing' }
  | { readonly tag: 'unreadable'; readonly reason: string };

/** 報せの材料の file(状態 file・入った dir の印)を読む — 無いのと読めないのを分けて返す。 */
function readText(file: string): FileRead {
  try {
    return { tag: 'ok', body: fs.readFileSync(file, 'utf8') };
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    return code === 'ENOENT' ? { tag: 'missing' } : { tag: 'unreadable', reason: String(error) };
  }
}

/** 動いている拡張の組み立ての控え — 入った dir ごとに最初に読んだ印を覚える。同じ版の組み直しは同じ dir を上書きするので、
 *  後から読むと動いている物ではなく入った物の印になるため(自分の分は起動した時に読む)。 */
class RunningBuilds {
  private readonly byPath = new Map<string, RunningBuild>();

  /** 入った dir の印を 1 度だけ読んで覚える。 */
  remember(extensionPath: string, packageVersion: string | undefined): RunningBuild {
    const known = this.byPath.get(extensionPath);
    if (known !== undefined) {
      return known;
    }
    // 印が無い・読めない dir は版だけで比べる(runningBuild の unmarked)
    const mark = readText(path.join(extensionPath, BUILD_MARK_RELPATH));
    const read = runningBuild(mark.tag === 'ok' ? mark.body : undefined, packageVersion);
    this.byPath.set(extensionPath, read);
    return read;
  }

  /** 身元の拡張がこの窓で動いている組み立て(一覧に居なければ absent)。 */
  of(identity: string): RunningBuild {
    const extension = vscode.extensions.getExtension(identity);
    if (extension === undefined) {
      return { tag: 'absent' };
    }
    return this.remember(extension.extensionPath, manifestVersion(extension.packageJSON));
  }
}

/** 報せを 1 つ出す(output にも控える)— Reload のボタンを押したら窓を再読み込みする。 */
function present(notice: FollowNotice, output: vscode.OutputChannel): void {
  switch (notice.tag) {
    case 'reload': {
      const message = reloadMessage(notice);
      output.appendLine(`[vsix の追随] ${message}`);
      void vscode.window.showInformationMessage(message, RELOAD_ACTION).then((picked) => {
        if (picked === RELOAD_ACTION) {
          void vscode.commands.executeCommand('workbench.action.reloadWindow');
        }
      });
      return;
    }
    case 'failure': {
      const message = failureMessage(notice);
      output.appendLine(`[vsix の追随] ${message}`);
      void vscode.window.showWarningMessage(message);
      return;
    }
    default: {
      const unreachable: never = notice;
      throw new Error(`網羅されていない報せ: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 追随の報せを起動する(activate から 1 回)— 開発用の窓(F5)では動いているのが入った版ではないので起動しない。 */
export function registerFollowNotice(context: vscode.ExtensionContext, output: vscode.OutputChannel): void {
  if (context.extensionMode !== vscode.ExtensionMode.Production) {
    return;
  }
  const statusPath = followStatusPath(process.env, os.homedir());
  const statusName = path.basename(statusPath);
  const running = new RunningBuilds();
  running.remember(context.extensionPath, manifestVersion(context.extension.packageJSON));
  let reloads: readonly string[] = [];
  let said = '';

  /** 同じ理由で黙る間は output に 1 回だけ書く。 */
  const note = (line: string): void => {
    if (line !== said) {
      output.appendLine(`[vsix の追随] ${line}`);
      said = line;
    }
  };

  /** 状態 file を読み、まだ出していない報せを出す。 */
  const check = (): void => {
    const file = readText(statusPath);
    switch (file.tag) {
      case 'missing':
        return; // 追随の居ない機体か、まだ 1 度も書いていない — 黙る
      case 'unreadable':
        note(`${statusPath} を読めない: ${file.reason}`);
        return;
      case 'ok':
        break;
      default: {
        const unreachable: never = file;
        throw new Error(`網羅されていない読み: ${JSON.stringify(unreachable)}`);
      }
    }
    const read = parseFollowStatus(file.body);
    if (read.tag === 'silent') {
      note(`${statusPath} を読まない: ${read.reason}`);
      return;
    }
    if (read.skipped.length > 0) {
      note(`${statusPath} の読み飛ばした行: ${read.skipped.join('・')}`);
    }
    const stored: unknown = context.globalState.get(FAILURE_LEDGER_KEY);
    const failures = readFailureLedger(stored);
    const next = unseenNotices(followNotices(read.status, (identity) => running.of(identity)), { reloads, failures });
    reloads = next.ledger.reloads;
    if (JSON.stringify(next.ledger.failures) !== JSON.stringify(failures)) {
      void context.globalState.update(FAILURE_LEDGER_KEY, next.ledger.failures);
    }
    for (const notice of next.show) {
      present(notice, output);
    }
  };

  let settle: NodeJS.Timeout | undefined;
  const schedule = (): void => {
    if (settle !== undefined) {
      clearTimeout(settle);
    }
    settle = setTimeout(() => {
      settle = undefined;
      check();
    }, SETTLE_MS);
  };

  let watcher: fs.FSWatcher | undefined;
  /** 状態 file の dir を見張る(file は rename で置き換わるので dir を見る)— dir がまだ無ければ次の周期で張り直す。 */
  const watch = (): void => {
    if (watcher !== undefined) {
      return;
    }
    try {
      const opened = fs.watch(path.dirname(statusPath), { persistent: false }, (_event, filename) => {
        if (filename === null || filename === statusName) {
          schedule();
        }
      });
      opened.on('error', () => {
        opened.close();
        if (watcher === opened) {
          watcher = undefined;
        }
      });
      watcher = opened;
    } catch (error) {
      // dir が無い(追随がまだ 1 度も書いていない)のは黙って次の周期で張り直す。それ以外は理由を 1 度だけ控える
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') {
        note(`${path.dirname(statusPath)} を見張れない: ${String(error)} — ${RECHECK_MS / 1000} 秒ごとに読み直す`);
      }
    }
  };

  watch();
  const recheck = setInterval(() => {
    watch();
    schedule();
  }, RECHECK_MS);
  context.subscriptions.push(vscode.extensions.onDidChange(() => schedule()), {
    dispose: () => {
      clearInterval(recheck);
      if (settle !== undefined) {
        clearTimeout(settle);
      }
      watcher?.close();
      watcher = undefined;
    }
  });
  check();
}
