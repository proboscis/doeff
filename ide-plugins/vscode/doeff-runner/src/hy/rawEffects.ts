// 生の副作用の証拠を読む係 — 目録と証拠の集め方は hy-index(doeff-indexer)が持ち(契約 版 3 の raw)、
// 拡張はその事実を表の参照(DefRef)へ写して読むだけにする。規則の判定(何が違反か)は linter が正本で、ここでも hy-index でもしない。

import { RAW_CATEGORIES, type HyRawEvidence, type RawCategory } from './contract';
import type { DefRef, EffectGraph } from './effects';

/** 生の副作用の証拠 1 件(索引の欄そのもの)。 */
export type RawEvidence = HyRawEvidence;

/** 呼ぶ定義を通した証拠 — 経路は表の参照に写した物。 */
export interface ViaEvidence {
  readonly through: readonly DefRef[];
  readonly evidence: RawEvidence;
}

/** 定義 1 つの証拠。 */
export interface RawMark {
  readonly direct: readonly RawEvidence[];
  readonly via: readonly ViaEvidence[];
}

/** 分類ごとの要約 1 件 — 強い証拠があるか(無ければ「?」を付ける)。 */
export interface CategorySummary {
  readonly category: RawCategory;
  readonly weakOnly: boolean;
}

/** 証拠を分類ごとに要約する(契約の分類の順)。 */
export function summarize(evidence: readonly RawEvidence[]): CategorySummary[] {
  return RAW_CATEGORIES.filter((c) => evidence.some((e) => e.category === c)).map((category) => ({
    category,
    weakOnly: evidence.filter((e) => e.category === category).every((e) => e.strength === 'weak')
  }));
}

/** 要約を「http, time?」の形の文字列にする。 */
export function summaryText(summary: readonly CategorySummary[]): string {
  return summary.map((s) => `${s.category}${s.weakOnly ? '?' : ''}`).join(', ');
}

/** 索引の証拠を表の参照で引く係(表の版ごとに作る)。 */
export class RawEffectIndex {
  constructor(private readonly graph: EffectGraph) {}

  /** 定義の直接の証拠。 */
  direct(ref: DefRef): readonly RawEvidence[] {
    return ref.definition.raw.direct;
  }

  /** 定義の証拠(経由の経路は表の参照に写す。経路の段が表に無い証拠は除く)。 */
  mark(ref: DefRef): RawMark {
    const via: ViaEvidence[] = [];
    for (const v of ref.definition.raw.via) {
      const through = v.through.map((step) => this.graph.definitionsIn(step.path)[step.index]);
      if (through.every((d): d is DefRef => d !== undefined)) {
        via.push({ through, evidence: v.evidence });
      }
    }
    return { direct: ref.definition.raw.direct, via };
  }
}

/** 今の表に合った証拠の係を配る(表が変わった時だけ作り直す)。 */
export class RawEffectSource {
  private cached: { readonly graph: EffectGraph; readonly index: RawEffectIndex } | undefined;

  constructor(private readonly graphs: { current(): EffectGraph }) {}

  /** 今の判定の係。 */
  current(): RawEffectIndex {
    const graph = this.graphs.current();
    if (this.cached === undefined || this.cached.graph !== graph) {
      this.cached = { graph, index: new RawEffectIndex(graph) };
    }
    return this.cached.index;
  }
}
