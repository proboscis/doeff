// Rust が解決した明示参照を、説明付きの移動ボタンとして描く。
import type { DocIndex } from '../lint/docWorkspaceContract';
import { escapeHtml } from './html';
export function termLinks(index: DocIndex | undefined, file: string | undefined, start: number, end: number): string {
  if (index === undefined || file === undefined) {
    return '';
  }
  const refs = index.references.filter((r) => r.location.path === file && r.location.start.line >= start && r.location.end.line <= end);
  const ids = [...new Set(refs.map((r) => r.id))];
  const buttons = ids.map((id) => {
    const defs = index.definitions.filter((d) => d.id === id);
    const title = defs.length === 1 ? `${defs[0].title}: ${defs[0].explanation}` : '用語の定義が一意に見つかりません';
    const name = defs.length === 1 ? defs[0].title : id;
    return `<span><button class="btn" data-term-id="${escapeHtml(id)}" data-term-file="${escapeHtml(file)}" title="${escapeHtml(title)}">${escapeHtml(name)}</button> <button class="btn" data-term-id="${escapeHtml(id)}" data-term-file="${escapeHtml(file)}" data-term-refs="true">使用箇所</button></span>`;
  });
  return buttons.length === 0 ? '' : `<div class="doc"><b>用語の説明:</b> ${buttons.join(' · ')}</div>`;
}
