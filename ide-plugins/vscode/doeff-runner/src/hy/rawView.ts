// 生の副作用の印の中身 — パネルの印・コード上の注記の見出し・hover の行を、hy-index が出す証拠(事実)から作る純粋な関数。
// 事実の表示(ナビゲーションの情報)であって違反の表示ではない。違反は linter が正本で、その表示は lint の側が持つ。
// handler(defhandler と effect の節)は直接も経由も、defk / deff / defp は直接の証拠だけを出す。

import type { HyDefinitionKind, HyRange } from './contract';
import { PROGRAM_KINDS } from './effects';
import { summarize, summaryText, type RawEvidence, type RawMark } from './rawEffects';

/** 印を付ける役割 — handler(直接も経由も)か、プログラム(直接だけ)。 */
export type RawRole = 'handler' | 'program';

/** 定義の kind から印の役割を決める(どちらでもなければ印を付けない)。 */
export function rawRoleOf(kind: HyDefinitionKind): RawRole | undefined {
  if (kind === 'defhandler' || kind === 'effect-clause') {
    return 'handler';
  }
  return PROGRAM_KINDS.includes(kind) ? 'program' : undefined;
}

/** パネルの項目の印。 */
export type RawBadge =
  | { readonly tag: 'direct'; readonly text: string }
  | { readonly tag: 'via'; readonly text: string };

/** 判定の結果をパネルの印にする(生に触らなければ undefined)。 */
export function rawBadge(mark: RawMark, role: RawRole): RawBadge | undefined {
  if (mark.direct.length > 0) {
    return { tag: 'direct', text: `生: ${summaryText(summarize(mark.direct))}` };
  }
  if (role === 'handler' && mark.via.length > 0) {
    return { tag: 'via', text: `経由: ${summaryText(summarize(mark.via.map((v) => v.evidence)))}` };
  }
  return undefined;
}

/** 分類ごとに最初の証拠を「http(httpx.post・42 行)」の形にする。 */
function firstPerCategory(evidence: readonly RawEvidence[]): string {
  return summarize(evidence)
    .map((s) => {
      const first = evidence.find((e) => e.category === s.category);
      const where = first === undefined ? '' : `(${first.name}・${first.range.start.line + 1} 行)`;
      return `${s.category}${s.weakOnly ? '?' : ''}${where}`;
    })
    .join(', ');
}

/** コード上の注記の見出し(生に触らなければ undefined)。 */
export function rawLensTitle(mark: RawMark, role: RawRole): string | undefined {
  if (mark.direct.length > 0) {
    return role === 'handler'
      ? `⚡ 生の副作用: ${firstPerCategory(mark.direct)}`
      : `⚡ 生の副作用に直接触る: ${firstPerCategory(mark.direct)}`;
  }
  if (role === 'handler' && mark.via.length > 0) {
    const first = mark.via[0];
    const through = first.through.map((d) => d.definition.name).join(' → ');
    return `↳ 経由で生の副作用: ${summaryText(summarize(mark.via.map((v) => v.evidence)))}(${through} 経由)`;
  }
  return undefined;
}

/** 注記を押した時に一覧で出す証拠の位置(直接が先、経由が後)。 */
export function rawEvidenceLocations(mark: RawMark, role: RawRole): Array<{ readonly path: string; readonly range: HyRange }> {
  const via = role === 'handler' ? mark.via.map((v) => v.evidence) : [];
  return [...mark.direct, ...via].map((e) => ({ path: e.path, range: e.range }));
}

/** 証拠 1 件を hover の 1 行にする。 */
function evidenceLine(e: RawEvidence, prefix: string): string {
  const weak = e.strength === 'weak' ? '(method 名だけ・弱い)' : '';
  return `- ${prefix} ${e.category} \`${e.name}\`${weak} — ${e.range.start.line + 1} 行`;
}

/** hover に足す証拠の一覧(分類・名前・行・直接か経由か、経由なら経路)。 */
export function rawHoverLines(mark: RawMark, role: RawRole, limit: number): string[] {
  const lines: string[] = [];
  for (const e of mark.direct) {
    lines.push(evidenceLine(e, '直接'));
  }
  if (role === 'handler') {
    for (const v of mark.via) {
      lines.push(`${evidenceLine(v.evidence, '経由')}(${v.through.map((d) => d.definition.name).join(' → ')})`);
    }
  }
  if (lines.length === 0) {
    return [];
  }
  const shown = lines.slice(0, limit);
  const rest = lines.length - shown.length;
  return ['**生の副作用**', ...shown, ...(rest > 0 ? [`- 他 ${rest} 件`] : [])];
}
