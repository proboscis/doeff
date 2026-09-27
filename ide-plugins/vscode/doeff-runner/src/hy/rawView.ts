// 生の副作用の印の中身 — パネルの印・コード上の注記の見出し・hover の行・問題の一覧の警告を、判定の結果から作る純粋な関数。
// 主役は handler(defhandler と effect の節)。defk / deff / defp は直接触る時だけ印を付ける(決まりの違反の候補)。

import type { HyDefinitionKind, HyRange } from './contract';
import type { DefRef } from './effects';
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
      : `⚠ 生の副作用に直接触っています: ${firstPerCategory(mark.direct)}(業務の Program は effect で出す決まり)`;
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

/** 問題の一覧に出す警告 1 件。 */
export interface RawDiagnostic {
  readonly path: string;
  readonly range: HyRange;
  readonly message: string;
}

/** 直接 生に触る defk / deff / defp の証拠ごとに警告を作る(handler は対象外 — 実 I/O の置き場なので)。 */
export function rawProgramDiagnostics(programs: readonly DefRef[], directOf: (ref: DefRef) => readonly RawEvidence[]): RawDiagnostic[] {
  const found: RawDiagnostic[] = [];
  for (const ref of programs) {
    if (rawRoleOf(ref.definition.kind) !== 'program') {
      continue;
    }
    for (const e of directOf(ref)) {
      found.push({
        path: e.path,
        range: e.range,
        message:
          `${ref.definition.kind} ${ref.definition.name} が生の副作用(${e.category}: ${e.name}` +
          `${e.strength === 'weak' ? '・method 名だけの弱い根拠' : ''})に直接触っています。` +
          `業務の Program は effect で出し、実 I/O は handler の中に置く決まりです。`
      });
    }
  }
  return found;
}
