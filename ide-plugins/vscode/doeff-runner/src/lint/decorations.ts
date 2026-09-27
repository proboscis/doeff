// linter の違反を editor の上で見つけやすくする飾り — 行末の短い文(重さごとの色)・行の左端の印・右端の
// スクロールバー(overview ruler)の印。中身は view.ts の inlineAnnotations が作り、ここは VS Code の飾りへ写すだけ。
// 判定はしない(何を出すかは linter の出力のまま)。

import * as path from 'path';
import * as vscode from 'vscode';
import type { LintSeverity } from './contract';
import type { LintStore } from './store';
import { inlineAnnotations } from './view';

/** 行末の文を出すかの設定。 */
export const INLINE_SETTING = 'doeff-runner.hy.lintInlineMessages';

/** 重さの色(テーマの色の名前)。 */
function severityColor(severity: LintSeverity): string {
  switch (severity) {
    case 'error':
      return 'editorError.foreground';
    case 'warning':
      return 'editorWarning.foreground';
    case 'info':
      return 'editorInfo.foreground';
    default: {
      const unreachable: never = severity;
      throw new Error(`網羅されていない重さ: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 行の左端の印の画(重さごとの色の丸。テーマの色は画に使えないので固定の色)。 */
function gutterIcon(severity: LintSeverity): vscode.Uri {
  const fill = severity === 'error' ? '#e51400' : severity === 'warning' ? '#bf8803' : '#1a85ff';
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><circle cx="8" cy="8" r="4.5" fill="${fill}"/></svg>`;
  return vscode.Uri.parse(`data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}`);
}

/** 重さ 1 つの飾りの型(行末の文は付けるか付けないかで 2 種)。 */
function decorationType(severity: LintSeverity): vscode.TextEditorDecorationType {
  return vscode.window.createTextEditorDecorationType({
    isWholeLine: true,
    gutterIconPath: gutterIcon(severity),
    gutterIconSize: 'contain',
    overviewRulerColor: new vscode.ThemeColor(severityColor(severity)),
    overviewRulerLane: vscode.OverviewRulerLane.Right
  });
}

/** 違反の飾りを見えている editor に出す係。 */
export class LintDecorations implements vscode.Disposable {
  private readonly types: Readonly<Record<LintSeverity, vscode.TextEditorDecorationType>> = {
    error: decorationType('error'),
    warning: decorationType('warning'),
    info: decorationType('info')
  };
  private readonly disposables: vscode.Disposable[] = [];

  constructor(private readonly store: LintStore) {}

  /** 置き場・見えている editor・設定の変化で出し直し始める。 */
  start(): void {
    const unsubscribe = this.store.onDidChange(() => this.refresh());
    this.disposables.push(
      { dispose: unsubscribe },
      vscode.window.onDidChangeVisibleTextEditors(() => this.refresh()),
      vscode.workspace.onDidChangeConfiguration((event) => {
        if (event.affectsConfiguration(INLINE_SETTING)) {
          this.refresh();
        }
      })
    );
    this.refresh();
  }

  /** 見えている editor 全部の飾りを出し直す。 */
  refresh(): void {
    const inline = vscode.workspace.getConfiguration().get<boolean>(INLINE_SETTING) !== false;
    const violations = this.store.violations();
    for (const editor of vscode.window.visibleTextEditors) {
      const here = path.normalize(editor.document.uri.fsPath);
      const own = violations.filter((v) => path.normalize(v.path) === here);
      const bySeverity: Record<LintSeverity, vscode.DecorationOptions[]> = { error: [], warning: [], info: [] };
      for (const annotation of inlineAnnotations(own)) {
        if (annotation.line >= editor.document.lineCount) {
          continue;
        }
        const end = editor.document.lineAt(annotation.line).range.end;
        const option: vscode.DecorationOptions = {
          range: new vscode.Range(end, end),
          renderOptions: inline
            ? {
                after: {
                  contentText: `  ${annotation.text}`,
                  color: new vscode.ThemeColor(severityColor(annotation.severity)),
                  fontStyle: 'italic',
                  margin: '0 0 0 1.5em'
                }
              }
            : undefined
        };
        bySeverity[annotation.severity].push(option);
      }
      for (const severity of ['error', 'warning', 'info'] as const) {
        editor.setDecorations(this.types[severity], bySeverity[severity]);
      }
    }
  }

  /** 購読と飾りの型を片づける。 */
  dispose(): void {
    for (const d of this.disposables) {
      d.dispose();
    }
    for (const type of Object.values(this.types)) {
      type.dispose();
    }
  }
}
