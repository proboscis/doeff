// 層を見分ける VS Code の表示 — エクスプローラーの印(FileDecorationProvider)・ステータスバー・タグと違反の hover・
// 層の説明の表。中身は layers.ts の純粋な関数が linter の出力から作り、ここは VS Code の型へ写すだけ。

import * as vscode from 'vscode';
import {
  layerBadge,
  layerStatusText,
  layerTableMarkdown,
  tagAt,
  tagHoverMarkdown,
  violationExplanationLines
} from './layers';
import type { LintStore } from './store';
import type { IconSource } from '../pixel/icons';
import { violationMark } from '../pixel/vocabulary';

/** エクスプローラーの file に層の頭文字と色を付ける(文字 = 層、色 = 違反の有無を優先)。 */
export class LayerFileDecorations implements vscode.FileDecorationProvider, vscode.Disposable {
  private readonly changed = new vscode.EventEmitter<undefined>();
  readonly onDidChangeFileDecorations = this.changed.event;
  private readonly unsubscribe: () => void;

  constructor(private readonly store: LintStore) {
    this.unsubscribe = store.onDidChange(() => this.changed.fire(undefined));
  }

  /** file の印(linter の結果に層が無ければ付けない)。 */
  provideFileDecoration(uri: vscode.Uri): vscode.FileDecoration | undefined {
    if (uri.scheme !== 'file') {
      return undefined;
    }
    const module = this.store.moduleFor(uri.fsPath);
    const badge = module === undefined ? undefined : layerBadge(module, this.store.layers());
    return badge === undefined ? undefined : new vscode.FileDecoration(badge.badge, badge.tooltip, new vscode.ThemeColor(badge.color));
  }

  /** 購読を止める。 */
  dispose(): void {
    this.unsubscribe();
    this.changed.dispose();
  }
}

/** ステータスバーに今開いている file の層を出す係(押すと層の説明の表)。 */
export class LayerStatusBar implements vscode.Disposable {
  private readonly item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 40);
  private readonly disposables: vscode.Disposable[] = [];

  constructor(private readonly store: LintStore) {
    this.item.command = 'doeff-runner.lint.showLayers';
    const unsubscribe = store.onDidChange(() => this.refresh());
    this.disposables.push({ dispose: unsubscribe }, vscode.window.onDidChangeActiveTextEditor(() => this.refresh()));
    this.refresh();
  }

  /** 今開いている file の層を出し直す(linter の結果に無い file では隠す)。 */
  refresh(): void {
    const document = vscode.window.activeTextEditor?.document;
    const module = document === undefined ? undefined : this.store.moduleFor(document.uri.fsPath);
    if (module === undefined) {
      this.item.hide();
      return;
    }
    this.item.text = `$(layers) ${layerStatusText(module, this.store.layers())}`;
    this.item.tooltip = module.layerReason ?? '層は linter が決めます(押すと層の説明の表)';
    this.item.show();
  }

  /** 表示と購読を片づける。 */
  dispose(): void {
    for (const d of this.disposables) {
      d.dispose();
    }
    this.item.dispose();
  }
}

/** タグ(MODULE-TAGS・:tags・:role)と違反の行の hover — 文はすべて linter の出力から。 */
export class LayerHover implements vscode.HoverProvider {
  constructor(
    private readonly store: LintStore,
    /** 違反の印(火・旗・足場・ふくろう)の画を引く口 */
    private readonly icons: IconSource
  ) {}

  /** タグの上なら role と層の説明、違反のある行ならその違反の説明を出す。 */
  provideHover(document: vscode.TextDocument, position: vscode.Position): vscode.Hover | undefined {
    const parts: string[] = [];
    const tag = tagAt(document.lineAt(position.line).text, position.character);
    if (tag !== undefined) {
      parts.push(tagHoverMarkdown(tag, this.store.moduleFor(document.uri.fsPath), this.store.layers()));
    }
    for (const v of this.store.violationsIn(document.uri.fsPath)) {
      if (v.range.start.line <= position.line && position.line <= v.range.end.line) {
        const mark = this.icons.hoverImage(violationMark(v), 16);
        const head = `${mark}${mark === '' ? '' : ' '}**${v.severity} · ${v.rule}${v.law === null ? '' : ` · law ${v.law}`}** — ${v.message}`;
        parts.push([head, ...violationExplanationLines(v).map((l) => `- ${l}`)].join('\n'));
      }
    }
    if (parts.length === 0) {
      return undefined;
    }
    const markdown = new vscode.MarkdownString(parts.join('\n\n---\n\n'));
    // 印の画(data URI の <img>)を出すため。文の中身は linter の出力で、利用者の入力ではない
    markdown.supportHtml = true;
    return new vscode.Hover(markdown);
  }
}

/** 層の説明の表を Markdown の preview で見せる(linter の layers から)。 */
export async function showLayerTable(store: LintStore): Promise<void> {
  const document = await vscode.workspace.openTextDocument({ language: 'markdown', content: layerTableMarkdown(store.layers()) });
  await vscode.commands.executeCommand('markdown.showPreview', document.uri);
}
