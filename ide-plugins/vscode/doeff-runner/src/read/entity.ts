// 実体の種類ごとの欄と下の帯(v2 2.1 節の表・agora-redesign #910 U4)の HTML を組む純粋な関数(VS Code に触らない)。
// 材料は hy-index 版 5(欄の型・答えの型・宣言した effect・effect 節の handles・型でない契約)と、呼び出しの表の逆引き。
// defk / deff の見出しは linter の signature が正本なので、ここは見出しの無い実体(defeffect・defrecord・defenum・defhandler・
// 最上位の変数 ほか)と、全部の実体に共通の「契約」の欄と下の帯を受け持つ。

import type { HyDefinition, HyTypeNote } from '../hy/contract';
import { escapeHtml, type Glyphs } from './html';
import { LABELS } from './labels';
import type { Card } from './model';
import { indexTypeText, relationOf, type CallGraph } from './tree';

/** 欄 1 つの行。 */
function row(label: string, body: string): string {
  return `<div class="row"><span class="k">${escapeHtml(label)}</span><div>${body}</div></div>`;
}

/** 名と型のチップ(型が無ければ名だけ)。 */
function nameTypeChip(name: string, type: HyTypeNote | undefined): string {
  const t = type === undefined ? '' : `<span class="t">${escapeHtml(indexTypeText(type))}</span>`;
  return `<span class="p"><span class="n">${escapeHtml(name)}</span>${t}</span>`;
}

/** effect のチップ(絵つき)。 */
function effectChip(name: string, glyphs: Glyphs): string {
  const src = glyphs.effect(name);
  return `<span class="eff">${src === undefined ? '' : `<img src="${escapeHtml(src)}" alt="">`}${escapeHtml(name)}</span>`;
}

/** 入れ子の定義のうち指定の種類(書いた順)。 */
function membersOf(card: Card, kind: HyDefinition['kind']): HyDefinition[] {
  return card.members.filter((m) => m.kind === kind);
}

/** 欄の型の並び(欄の型が無い欄は入れ子の定義の名だけ)。 */
function fieldChips(card: Card): string {
  const d = card.definition;
  if (d.paramTypes.length > 0) {
    return d.paramTypes.map((p) => nameTypeChip(p.name, p.type)).join('');
  }
  return membersOf(card, 'field')
    .map((m) => nameTypeChip(m.name, undefined))
    .join('');
}

/** 型でない契約の欄(全部の実体に共通 — 型の注記は引数と答えの型へ溶けるので、残った述語だけ。無ければ出さない)。 */
export function contractRow(definition: HyDefinition): string {
  const clauses = [
    ...definition.contracts.map((c) => `${c.side}: ${c.text}`),
    ...(definition.checks ?? []).map((c) => `check: ${c}`)
  ];
  return clauses.length === 0 ? '' : row(LABELS.contract, clauses.map((c) => `<code>${escapeHtml(c)}</code>`).join(''));
}

/** 見出しの無い実体の欄(v2 2.1 節の表)。 */
export function entityRows(card: Card, glyphs: Glyphs): string {
  const d = card.definition;
  switch (d.kind) {
    case 'defeffect': {
      const answer = d.answerType === null ? '' : `<span class="arrow">→</span><span class="ret">${escapeHtml(indexTypeText(d.answerType))}</span>`;
      const fields = fieldChips(card);
      return `<div class="sig">${fields === '' ? `<span class="none">${escapeHtml(LABELS.noArgs)}</span>` : fields}${answer}</div>`;
    }
    case 'defrecord':
    case 'deftype': {
      const fields = fieldChips(card);
      return fields === '' ? '' : row(LABELS.fields, fields);
    }
    case 'defenum': {
      const values = membersOf(card, 'enum-member');
      return values.length === 0 ? '' : row(LABELS.values, values.map((m) => `<span class="p"><span class="n">${escapeHtml(m.name)}</span></span>`).join(''));
    }
    case 'defhandler': {
      const handles = membersOf(card, 'effect-clause').map((m) => effectChip(m.handles?.name ?? m.name, glyphs));
      const uses = (d.effects ?? []).map((e) => effectChip(e.name, glyphs));
      return [handles.length === 0 ? '' : row(LABELS.handles, handles.join('')), uses.length === 0 ? '' : row(LABELS.effects, uses.join(''))].join('');
    }
    case 'variable': {
      const type = d.answerType === null ? '' : row(LABELS.type, `<span class="p"><span class="t">${escapeHtml(indexTypeText(d.answerType))}</span></span>`);
      const first = card.source.split('\n')[0];
      return `${type}${row(LABELS.value, `<span class="lisp" title="${escapeHtml(LABELS.lispAsIs)}">${escapeHtml(first)}</span>`)}`;
    }
    default: {
      // 他の種類(deftest・defclass・defn など)は引数の名・基底・入れ子の定義
      const rows: string[] = [];
      if (d.params.length > 0) {
        rows.push(row(LABELS.args, d.params.map((p) => nameTypeChip(p, d.paramTypes.find((t) => t.name === p)?.type)).join('')));
      }
      if (d.bases.length > 0) {
        rows.push(row(LABELS.bases, d.bases.map((b) => `<span class="p"><span class="t">${escapeHtml(b)}</span></span>`).join('')));
      }
      const methods = membersOf(card, 'method');
      if (methods.length > 0) {
        rows.push(row(LABELS.methods, methods.map((m) => `<span class="p"><span class="n">${escapeHtml(m.name)}</span></span>`).join('')));
      }
      return rows.join('');
    }
  }
}

/** 1 行の形の args / return type(見出しの無い実体 — 見本 v5 の `(id: str) → InputRow | …` の形)。 */
export function entityLineArgs(card: Card): string {
  const d = card.definition;
  const typed = (items: ReadonlyArray<{ readonly name: string; readonly type: HyTypeNote }>): string =>
    items.map((p) => `${escapeHtml(p.name)}: <span class="t">${escapeHtml(indexTypeText(p.type))}</span>`).join(', ');
  switch (d.kind) {
    case 'defeffect': {
      const answer = d.answerType === null ? '' : ` → <span class="r">${escapeHtml(indexTypeText(d.answerType))}</span>`;
      return `<span class="f f-args">(${typed(d.paramTypes)})${answer}</span>`;
    }
    case 'defrecord':
    case 'deftype':
      if (d.paramTypes.length > 0) {
        return `<span class="f f-args">(${typed(d.paramTypes)})</span>`;
      }
      break;
    case 'defenum': {
      const values = membersOf(card, 'enum-member').map((m) => escapeHtml(m.name));
      return values.length === 0 ? '' : `<span class="f f-args">${values.join(' | ')}</span>`;
    }
    case 'defhandler': {
      const handles = membersOf(card, 'effect-clause').map((m) => escapeHtml(m.handles?.name ?? m.name));
      return handles.length === 0 ? '' : `<span class="f f-args">${escapeHtml(LABELS.handles)}: ${handles.join(', ')}</span>`;
    }
    default:
      break;
  }
  const fields = membersOf(card, 'field').map((m) => escapeHtml(m.name));
  if (fields.length > 0) {
    return `<span class="f f-args">(${fields.join(', ')})</span>`;
  }
  return d.params.length === 0 ? '' : `<span class="f f-args">(${d.params.map(escapeHtml).join(', ')})</span>`;
}

/** 帯の数 1 つ(押すと木 — 呼び出しの向きがある物だけ)。 */
function relationButton(qn: string, direction: 'callers' | 'callees', label: string, count: number): string {
  return `<button class="rel" data-tree-root="${escapeHtml(qn)}" data-tree-dir="${direction}">${escapeHtml(label)} <b>${count}</b></button>`;
}

/** 帯の数 1 つ(押せない物)。 */
function relationCount(label: string, count: number): string {
  return `<span>${escapeHtml(label)} <b>${count}</b></span>`;
}

/** 下の帯の関係(v2 2.1 節の表・ラベルは v5 の表)— 実体の種類ごとに出す関係が変わる。 */
export function relationBand(definition: HyDefinition, graph: CallGraph): string {
  const qn = definition.qualifiedName;
  const r = relationOf(graph, qn);
  switch (definition.kind) {
    case 'defeffect':
      return relationButton(qn, 'callers', LABELS.usedBy, r.callers) + relationCount(LABELS.handlers, graph.handlers.get(qn) ?? 0);
    case 'defrecord':
    case 'deftype':
      return relationCount(LABELS.returnedBy, (graph.returnedBy.get(qn) ?? []).length) + relationCount(LABELS.acceptedBy, (graph.acceptedBy.get(qn) ?? []).length);
    case 'defenum':
      return relationButton(qn, 'callers', LABELS.usedBy, r.callers);
    case 'defhandler':
      return relationButton(qn, 'callers', LABELS.installedAt, r.callers);
    case 'deftest':
      return relationButton(qn, 'callees', LABELS.callees, r.callees);
    case 'variable':
      return '';
    default:
      return relationButton(qn, 'callers', LABELS.callers, r.callers) + relationButton(qn, 'callees', LABELS.callees, r.callees) + relationCount(LABELS.tests, r.tests);
  }
}
