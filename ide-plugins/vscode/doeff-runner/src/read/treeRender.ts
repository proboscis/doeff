// 呼び出しの依存の木の HTML を組む純粋な関数(v7 3 節・見本 docs/design/hy-reading-plane/artifacts/v7/call-tree.html)。
// 節は実体の畳んだ 1 行と同じ形(v4 の show in line の切り替えに従う — body の class で出し入れする)。名を押すとカードへ。

import type { LintSignature } from '../lint/contract';
import { LABELS } from './labels';
import { escapeHtml, tagClass, type Glyphs } from './html';
import { effectRef, entityLink, indexTypeHtml, nameRefOf, resolveEntity, typeHtml } from './resolve';
import { indexTypeText, type CallGraph, type CallTree, type TreeNode } from './tree';

/** 木を描く材料(木の他に)。 */
export interface TreeRenderContext {
  readonly glyphs: Glyphs;
  /** 節の定義の linter の見出し(開いている file の定義だけ。無ければ索引の型の綴りで描く) */
  readonly signatureOf: (qualifiedName: string) => LintSignature | undefined;
  /** 木の上の切り替えの状態 */
  readonly showTests: boolean;
  /** その effect を解く effect 節の数(defeffect の節の handled by N) */
  readonly handlersOf: (qualifiedName: string) => number;
  /** 名 → 定義の解決の表(節の型と effect の名を押せるようにする — v12) */
  readonly graph: CallGraph;
}

/** 節の 1 行の args / return type — linter の見出しが有ればそれ、無ければ索引の型の綴り。 */
function nodeArgs(node: TreeNode, signature: LintSignature | undefined, graph: CallGraph): string {
  if (signature !== undefined) {
    const params = signature.params.map((p) => `${escapeHtml(p.name)}: <span class="t">${typeHtml(p.type, graph)}</span>`).join(', ');
    const answer = signature.absent ? `Maybe[${typeHtml(signature.answer, graph)}]` : typeHtml(signature.answer, graph);
    return `<span class="f f-args">(${params}) → <span class="r">${answer}</span></span>`;
  }
  const d = node.definition;
  const typed = new Map(d.paramTypes.map((p) => [p.name, p.type]));
  const names = d.params.length > 0 ? d.params : d.paramTypes.map((p) => p.name);
  const params = names
    .map((name) => {
      const type = typed.get(name);
      return type === undefined ? escapeHtml(name) : `${escapeHtml(name)}: <span class="t">${indexTypeHtml(indexTypeText(type), type.names, graph)}</span>`;
    })
    .join(', ');
  const answer = d.answerType === null ? '' : ` → <span class="r">${indexTypeHtml(indexTypeText(d.answerType), d.answerType.names, graph)}</span>`;
  return names.length === 0 && answer === '' ? '' : `<span class="f f-args">(${params})${answer}</span>`;
}

/** 節の effect のチップ(宣言した effect・絵つき)。 */
function nodeEffects(node: TreeNode, glyphs: Glyphs, graph: CallGraph): string {
  const chips = (node.definition.effects ?? [])
    .map((e) => {
      const src = glyphs.effect(e.name);
      const img = src === undefined ? '' : `<img src="${escapeHtml(src)}" alt="">`;
      return entityLink(`${img}${escapeHtml(e.name)}`, resolveEntity(effectRef(e.name, nameRefOf(e)), graph), 'eff');
    })
    .join('');
  return chips === '' ? '' : `<span class="f f-effects">${chips}</span>`;
}

/** 節の右の数 — defeffect は handled by N、他は向きの隣の数。 */
function nodeCount(node: TreeNode, tree: CallTree, ctx: TreeRenderContext): string {
  if (node.definition.kind === 'defeffect') {
    return `<span class="tcount">${escapeHtml(LABELS.handledBy)} ${ctx.handlersOf(node.qualifiedName)}</span>`;
  }
  return `<span class="tcount">${escapeHtml(tree.direction === 'callees' ? LABELS.callees : LABELS.callers)} ${node.count}</span>`;
}

/** 節 1 つ(子を含む)。 */
function renderNode(node: TreeNode, tree: CallTree, ctx: TreeRenderContext): string {
  const d = node.definition;
  const again = node.seen !== 'first';
  const toggle = again || (node.children.length === 0 && !node.truncated) ? '<span class="tt">·</span>' : node.children.length > 0 ? '<button class="tt" data-node-toggle>▾</button>' : '<span class="tt">▸</span>';
  const name = `<button class="tname" data-reveal="${escapeHtml(node.qualifiedName)}">${escapeHtml(d.name)}</button>`;
  const mark = again ? `<span class="again">↺ ${escapeHtml(node.seen === 'cycle' ? LABELS.cycle : LABELS.seenAbove)}</span>` : '';
  const tags = d.tags === null ? '' : `<span class="f f-tags">${Object.entries(d.tags).map(([k, v]) => `<span class="mini ${tagClass(k)}">${escapeHtml(k)}: ${escapeHtml(v)}</span>`).join('')}</span>`;
  const body = again ? '' : `${nodeArgs(node, ctx.signatureOf(node.qualifiedName), ctx.graph)}${nodeEffects(node, ctx.glyphs, ctx.graph)}${tags}`;
  const more = node.truncated ? `<span class="more">… ${node.count}</span>` : '';
  const children = node.children.length === 0 ? '' : `<ul>${node.children.map((c) => renderNode(c, tree, ctx)).join('')}</ul>`;
  return `<li class="tn${again ? ' again-node' : ''}"><div class="trow line">${toggle}<span class="kind k-${escapeHtml(d.kind)}">${escapeHtml(d.kind)}</span>${name}${mark}${body}${more}${nodeCount(node, tree, ctx)}</div>${children}</li>`;
}

/** 木の面(上の切り替え・根の下の数え・節の列)。 */
export function renderTree(tree: CallTree, ctx: TreeRenderContext): string {
  const dir = (value: 'callees' | 'callers', label: string): string =>
    `<button class="btn${tree.direction === value ? ' on' : ''}" data-tree-dir="${value}">${escapeHtml(label)}</button>`;
  const bar =
    `<div class="treebar"><b>${escapeHtml(LABELS.callTree)}</b><span class="k">${escapeHtml(LABELS.root)}</span><b class="mono">${escapeHtml(tree.root.definition.name)}</b>` +
    `<span class="sep"></span>${dir('callees', `${LABELS.callees} ↓`)}${dir('callers', `${LABELS.callers} ↑`)}` +
    `<span class="sep"></span><span class="k">${escapeHtml(LABELS.depth)}</span><span class="depth">${tree.depth}</span><button class="btn" id="tree-more">+</button>` +
    `<span class="sep"></span><label><input type="checkbox" id="tree-tests"${ctx.showTests ? ' checked' : ''}>${escapeHtml(LABELS.tests)}</label>` +
    `<button class="btn" id="tree-close">${escapeHtml(LABELS.close)}</button></div>`;
  const effects = tree.effects
    .map((name) => {
      const src = ctx.glyphs.effect(name);
      const img = src === undefined ? '' : `<img src="${escapeHtml(src)}" alt="">`;
      return entityLink(`${img}${escapeHtml(name)}`, resolveEntity({ tag: 'effect-name', name }, ctx.graph), 'eff');
    })
    .join('');
  const summary = `<div class="treesum"><span class="k">${escapeHtml(LABELS.treeEffects)}</span>${effects === '' ? `<span class="none">${escapeHtml(LABELS.none)}</span>` : effects}<span class="sep"></span>${escapeHtml(LABELS.nodes)} <b>${tree.nodes}</b>(${escapeHtml(LABELS.repeats)} ${tree.repeats} · ${escapeHtml(LABELS.cycles)} ${tree.cycles})</div>`;
  return `<section class="tree">${bar}${summary}<ul class="tnodes">${renderNode(tree.root, tree, ctx)}</ul></section>`;
}
