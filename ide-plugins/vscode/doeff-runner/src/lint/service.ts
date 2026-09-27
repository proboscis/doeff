// linter の結果を保つ係 — 起動時と workspace の変化で全体を、保存と編集(debounce)でその file を linter に聞き、
// 結果を置き場へ入れて波線(Diagnostics)を出し直す。判定はしない(linter の出力を写すだけ)。Jev の判定(保存した時と、編集中に
// 打つのが止まった時)は SemanticJudge に渡す。

import * as vscode from 'vscode';
import type { LintSeverity } from './contract';
import type { Linter, LintOutcome, LintRequest } from './runner';
import { SemanticJudge, SYSTEM_CLOCK, type OpenDocument, type SemanticState, type SemanticTriggers } from './semantic';
import type { LintStore } from './store';
import { diagnosticsByPath, displayRange } from './view';

const EDIT_DEBOUNCE_MS = 800;

/** Output channel のうち、ここが使う面だけ。 */
export interface LintLog {
  appendLine(line: string): void;
}

/** 契約の重さを VS Code の重さへ写す。 */
function toSeverity(severity: LintSeverity): vscode.DiagnosticSeverity {
  switch (severity) {
    case 'error':
      return vscode.DiagnosticSeverity.Error;
    case 'warning':
      return vscode.DiagnosticSeverity.Warning;
    case 'info':
      return vscode.DiagnosticSeverity.Information;
    default: {
      const unreachable: never = severity;
      throw new Error(`網羅されていない重さ: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** linter に聞く file か(Hy と Python)。 */
function isLintedDocument(document: vscode.TextDocument): boolean {
  return (
    document.uri.scheme === 'file' &&
    (document.languageId === 'hy' || document.languageId === 'python' || /\.(hy|hyk|hyp|py)$/.test(document.uri.fsPath))
  );
}

/** Jev の判定に要る物 — 別の子 process の口(時間切れ 30 秒)・入り切りの設定・状態の知らせ・通知。 */
export interface SemanticWiring {
  readonly linter: Linter;
  readonly triggers: () => SemanticTriggers;
  readonly onState: (state: SemanticState) => void;
  readonly notify: (message: string) => void;
}

/** 開いている document の今の中身と版(閉じた・workspace の外なら undefined)。 */
function openDocument(filePath: string): OpenDocument | undefined {
  const document = vscode.workspace.textDocuments.find((d) => !d.isClosed && d.uri.scheme === 'file' && d.uri.fsPath === filePath);
  const root = document === undefined ? undefined : vscode.workspace.getWorkspaceFolder(document.uri)?.uri.fsPath;
  return document === undefined || root === undefined ? undefined : { root, text: document.getText(), version: document.version };
}

/**
 * linter の結果を保ち、波線を出す係。保存した時は決定的な実行の後に、編集中は打つのが止まった時に、Jev の判定を背景で 1 本走らせる
 * (SemanticJudge)。
 */
export class LintService implements vscode.Disposable {
  private readonly disposables: vscode.Disposable[] = [];
  private readonly debounces = new Map<string, NodeJS.Timeout>();
  private readonly lastFailure = new Map<string, string>();
  private readonly judge: SemanticJudge;

  constructor(
    private readonly store: LintStore,
    private readonly linter: Linter,
    private readonly log: LintLog,
    private readonly diagnostics: vscode.DiagnosticCollection,
    semantic: SemanticWiring
  ) {
    this.judge = new SemanticJudge({
      linter: semantic.linter,
      clock: SYSTEM_CLOCK,
      triggers: semantic.triggers,
      document: openDocument,
      deliver: (request, report) => this.apply(request, { tag: 'ok', report }),
      log: (line) => this.log.appendLine(line),
      onState: semantic.onState,
      notify: semantic.notify
    });
  }

  /** event の購読を始め、各 folder の全体を linter に聞く。 */
  start(): void {
    const unsubscribe = this.store.onDidChange(() => this.publishDiagnostics());
    this.disposables.push(
      { dispose: unsubscribe },
      vscode.workspace.onDidSaveTextDocument((document) => {
        if (isLintedDocument(document)) {
          this.scheduleDocument(document, 0, true);
        }
      }),
      vscode.workspace.onDidChangeTextDocument((event) => {
        if (event.contentChanges.length > 0 && isLintedDocument(event.document)) {
          this.scheduleDocument(event.document, EDIT_DEBOUNCE_MS);
          this.judge.edited(event.document.uri.fsPath);
        }
      }),
      vscode.workspace.onDidChangeWorkspaceFolders((event) => {
        for (const removed of event.removed) {
          this.store.removeRoot(removed.uri.fsPath);
        }
        for (const added of event.added) {
          void this.lintRoot(added.uri.fsPath);
        }
      })
    );
    this.lintAll();
  }

  /** 全 folder の全体を linter に聞き直す(起動時・再実行のボタン・設定の変更)。 */
  lintAll(): void {
    for (const folder of vscode.workspace.workspaceFolders ?? []) {
      void this.lintRoot(folder.uri.fsPath);
    }
  }

  /** 購読と保留中の debounce を止め、波線を消す。 */
  dispose(): void {
    for (const timer of this.debounces.values()) {
      clearTimeout(timer);
    }
    this.debounces.clear();
    this.judge.dispose();
    for (const d of this.disposables) {
      d.dispose();
    }
    this.diagnostics.clear();
  }

  /** root の全体を linter に聞く。 */
  private async lintRoot(root: string): Promise<void> {
    this.apply({ tag: 'root', root }, await this.linter.lint({ tag: 'root', root }));
  }

  /** document の今の内容を stdin で聞く依頼を、path ごとに debounce して積む(保存なら、その後に Jev の判定を積む)。 */
  private scheduleDocument(document: vscode.TextDocument, delayMs: number, saved = false): void {
    const key = document.uri.fsPath;
    const pending = this.debounces.get(key);
    if (pending !== undefined) {
      clearTimeout(pending);
    }
    this.debounces.set(
      key,
      setTimeout(() => {
        this.debounces.delete(key);
        const root = vscode.workspace.getWorkspaceFolder(document.uri)?.uri.fsPath;
        if (root === undefined || document.isClosed) {
          return;
        }
        const request: LintRequest = { tag: 'stdin', root, path: document.uri.fsPath, text: document.getText() };
        const version = document.version;
        void this.linter.lint(request).then((outcome) => {
          this.apply(request, outcome);
          if (saved) {
            this.judge.saved(root, document.uri.fsPath, version);
          }
        });
      }, delayMs)
    );
  }

  /** linter の結果を置き場へ入れる。失敗は理由を Output に出す(同じ理由は続けて出さない)。 */
  private apply(request: LintRequest, outcome: LintOutcome): void {
    switch (outcome.tag) {
      case 'disabled':
        this.store.removeRoot(request.root);
        return;
      case 'failed': {
        if (this.lastFailure.get(request.root) !== outcome.reason) {
          this.lastFailure.set(request.root, outcome.reason);
          this.log.appendLine(`[lint] 失敗 (${request.tag === 'root' ? request.root : request.path}): ${outcome.reason}`);
        }
        return;
      }
      case 'ok':
        break;
      default: {
        const unreachable: never = outcome;
        throw new Error(`網羅されていない結果: ${JSON.stringify(unreachable)}`);
      }
    }
    this.lastFailure.delete(request.root);
    for (const error of outcome.report.errors) {
      this.log.appendLine(`[lint] linter が読めなかった: ${error}`);
    }
    switch (request.tag) {
      case 'root':
        this.store.replaceRoot(request.root, outcome.report);
        this.log.appendLine(`[lint] ${request.root}: 違反 ${outcome.report.violations.length} 件`);
        return;
      case 'stdin':
      case 'semantic':
      case 'semantic-change':
        this.store.replaceFile(request.root, request.path, outcome.report);
        return;
      default: {
        const unreachable: never = request;
        throw new Error(`網羅されていない依頼: ${JSON.stringify(unreachable)}`);
      }
    }
  }

  /** 置き場の違反を波線にして出し直す(重さ・文・規則の ID は linter の出力のまま)。 */
  private publishDiagnostics(): void {
    const started = Date.now();
    // 空(0 幅)の範囲は見えないので、開いている document ならその行の長さまで、それ以外は行全体に広げる
    const open = new Map(vscode.workspace.textDocuments.map((doc) => [doc.uri.fsPath, doc]));
    const entries: Array<[vscode.Uri, vscode.Diagnostic[]]> = [];
    for (const [filePath, specs] of diagnosticsByPath(this.store.violations())) {
      const doc = open.get(filePath);
      const list = specs.map((spec) => {
        const line = spec.violation.range.start.line;
        const lineLength = doc !== undefined && line < doc.lineCount ? doc.lineAt(line).text.length : undefined;
        const r = displayRange(spec.violation.range, lineLength);
        const diagnostic = new vscode.Diagnostic(
          new vscode.Range(r.start.line, r.start.character, r.end.line, r.end.character),
          spec.message,
          toSeverity(spec.severity)
        );
        diagnostic.source = 'doeff-linter';
        diagnostic.code = spec.code;
        return diagnostic;
      });
      entries.push([vscode.Uri.file(filePath), list]);
    }
    // file ごとに 1 度ずつではなく、まとめて 1 回で渡す
    this.diagnostics.clear();
    this.diagnostics.set(entries);
    const elapsed = Date.now() - started;
    if (elapsed > 1000) {
      this.log.appendLine(`[lint] 波線の設定に ${elapsed}ms かかった(違反 ${entries.reduce((n, [, l]) => n + l.length, 0)} 件)`);
    }
  }
}
