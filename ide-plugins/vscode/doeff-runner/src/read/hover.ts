// 定義を読む面の hover の文(v1 2.5 節「束縛の型・effect の絵の hover」・agora-redesign #910)を組む純粋な関数(VS Code に触らない)。
// 本体の名には、その名の束縛の型(linter の bindings)か引数の型(linter の見出し)、effect の絵とチップには、索引の defeffect の
// 引数と答えと説明の 1 行目を出す。違反の印には、違反の欄を並べた吹き出しの HTML を出す(v13・agora-redesign #1685)。
// 読み方の正本は linter と索引で、ここは並べるだけ。

import { typeText } from '../defk/model';
import type { LintBinding, LintRule, LintSignature, LintStanding, LintViolation } from '../lint/contract';
import { ruleSummaries } from '../lint/view';
import { docFirstLine, escapeHtml } from './html';
import { LABELS } from './labels';
import { violationRef } from './locate';
import { indexTypeText, type CallGraph } from './tree';

/** 定義 1 つの名の表 — 本体の名の hover に型を引くため。 */
export interface NameScope {
  /** 名 → その名の束縛(source の順) */
  readonly bindings: ReadonlyMap<string, readonly LintBinding[]>;
  /** 引数の名 → 型の綴り */
  readonly params: ReadonlyMap<string, string>;
}

/** 定義の束縛と見出しの引数から名の表を作る(カード 1 枚ごと)。 */
export function nameScope(bindings: readonly LintBinding[], signature: LintSignature | undefined): NameScope {
  const byName = new Map<string, LintBinding[]>();
  const ordered = [...bindings].sort((a, b) => a.range.start.line - b.range.start.line || a.range.start.character - b.range.start.character);
  for (const binding of ordered) {
    byName.set(binding.name, [...(byName.get(binding.name) ?? []), binding]);
  }
  const params = new Map((signature?.params ?? []).map((p) => [p.name, typeText(p.type)]));
  return { bindings: byName, params };
}

/**
 * 本体の line 行(0 始まり)に出る名の hover の文 — その行まで(同じ行を含む)の最後の束縛の型と束縛した行、束縛でなければ
 * 引数の型。どちらでもなければ undefined(hover を付けない)。
 */
export function nameHover(scope: NameScope, name: string, line: number): string | undefined {
  const before = (scope.bindings.get(name) ?? []).filter((b) => b.range.start.line <= line);
  const bound = before.length === 0 ? undefined : before[before.length - 1];
  if (bound !== undefined) {
    return `${name}: ${typeText(bound.type)}\n${LABELS.boundAtLine} ${bound.range.start.line + 1}`;
  }
  const param = scope.params.get(name);
  return param === undefined ? undefined : `${name}: ${param}\n${LABELS.argument}`;
}

/**
 * effect の絵とチップの hover の文 — 索引の defeffect が名で 1 つに決まれば `Name(引数: 型) → 答え` と説明の 1 行目、
 * 決まらなければ(無い・同名が複数)名だけ。
 */
export function effectHover(name: string, graph: CallGraph): string {
  const found = graph.effectsByName.get(name) ?? [];
  const definition = found.length === 1 ? graph.definitions.get(found[0])?.definition : undefined;
  if (definition === undefined) {
    return name;
  }
  const params = definition.paramTypes.map((p) => `${p.name}: ${indexTypeText(p.type)}`).join(', ');
  const answer = definition.answerType === null ? '' : ` → ${indexTypeText(definition.answerType)}`;
  const doc = docFirstLine(definition.docstring);
  return `${name}(${params})${answer}${doc === '' ? '' : `\n${doc}`}`;
}

/** 規則の ID → 短い名(linter の rules[].title — 名の無い規則は載らない)。違反の吹き出しの見出しに引く索引。 */
export type RuleTitles = ReadonlyMap<string, string>;

/** linter の規則の一覧から規則の短い名の索引を作る(同じ ID が law ごとに並ぶ時は名のある物 — 違反の表の束の見出しと同じ引き方)。 */
export function ruleTitles(rules: readonly LintRule[]): RuleTitles {
  const titles = new Map<string, string>();
  for (const [rule, summary] of ruleSummaries(rules)) {
    if (summary.title !== null) {
      titles.set(rule, summary.title);
    }
  }
  return titles;
}

/** 違反の立場の語(labels の表から)。 */
function standingLabel(standing: LintStanding): string {
  switch (standing) {
    case 'new':
      return LABELS.standingNew;
    case 'registered':
      return LABELS.standingRegistered;
    case 'reconciling':
      return LABELS.standingReconciling;
    default: {
      const unreachable: never = standing;
      throw new Error(`網羅されていない立場: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 吹き出しの 1 行(見出しの語と linter の文)。linter が文を出していなければ(null)行ごと出さない。 */
function tipRow(label: string, text: string | null): string {
  return text === null ? '' : `<div class="vt-r"><span class="k">${escapeHtml(label)}</span><span>${escapeHtml(text)}</span></div>`;
}

/**
 * 違反の吹き出しの HTML(v13・agora-redesign #1685 の 2)— 規則の ID・短い名(無ければ ID だけ)・重大さ・立場(新しい /
 * 既知 / 照合中)・Jev の p(Jev の違反だけ)・違反の文・why(explanation.reason)・how to fix(hint)・law(law_statement)・
 * ボタン 2 つ(違反の表の項目へ・editor で範囲を選んで開く)。違反の欄と規則の短い名を並べるだけで、面は文を作らない。
 * webview の script は、この HTML を印の近くに写すだけ(頭・本体・source・帯・足のどの印からも同じ吹き出し)。
 */
export function violationTipHtml(violation: LintViolation, title: string | null): string {
  const named = title === null ? '' : `<span class="vt-t">${escapeHtml(title)}</span>`;
  const head = `<div class="vt-h"><b>${escapeHtml(violation.rule)}</b>${named}<span class="viol viol-${violation.level}">${violation.level}</span></div>`;
  const jev = violation.probability === null ? [] : [`Jev p=${violation.probability.toFixed(2)}`];
  if (violation.source === 'doc-linter') {jev.unshift('doc-linter（文章の検査・説明は規則の固定文）');}
  const meta = [standingLabel(violation.standing), ...jev].join(' · ');
  const explanation = violation.explanation;
  const rows = [
    tipRow(LABELS.why, explanation === null ? null : explanation.reason),
    tipRow(LABELS.howToFix, violation.hint),
    tipRow(LABELS.law, explanation === null ? null : explanation.lawStatement)
  ].join('');
  const buttons = `<div class="vt-b"><button class="btn" data-vlist>${escapeHtml(LABELS.showInViolations)}</button><button class="btn" data-vopen>${escapeHtml(LABELS.openInEditor)}</button></div>`;
  // 目印は JSON にして持たせ、ボタンを押した時に webview がそのまま送り返す(拡張の側は readViolationRef で形を確かめて読む)
  const ref = escapeHtml(JSON.stringify(violationRef(violation)));
  return `<div class="vt" data-vref="${ref}">${head}<div class="vt-m">${escapeHtml(meta)}</div><div class="vt-msg">${escapeHtml(violation.message)}</div>${rows}${buttons}</div>`;
}
