// 層(core・intent・protocol・foundation・entry …)を見分ける表示の中身 — エクスプローラーの印・ステータスバーの文・
// タグの hover・説明の表・違反の説明の Markdown を作る純粋な関数。
// 層も説明の文も違反の理由も、すべて linter の editor-json(layers・modules[].layer / layer_reason・violations[].explanation)
// から読む。拡張は文を持たず、linter が説明を出さない時は層の名前だけを出す。

import type { LintLayer, LintModule, LintViolation } from './contract';

/** 印の色の並び(linter の layers の順に当てる。層の名前に色を結びつけない)。 */
const BADGE_PALETTE = ['charts.purple', 'charts.blue', 'charts.green', 'charts.orange', 'charts.yellow', 'charts.red'];

/** エクスプローラーの印 1 つ。 */
export interface LayerBadge {
  readonly badge: string;
  /** 色(テーマの色の名前)— 違反がある時は違反の色を優先する */
  readonly color: string;
  readonly tooltip: string;
}

/** 層の名前から linter の説明を引く(無ければ undefined)。 */
export function layerOf(name: string, layers: readonly LintLayer[]): LintLayer | undefined {
  return layers.find((l) => l.name === name);
}

/**
 * file の印 — 文字は層の名前の頭文字、色は違反の有無を優先する(違反あり = 問題の色、無し = linter の層の順の色)。
 * 層が無い(層の外)なら印は付けない。tooltip は linter の説明と、その file の層を決めた理由。
 */
export function layerBadge(module: LintModule, layers: readonly LintLayer[]): LayerBadge | undefined {
  if (module.layer === null) {
    return undefined;
  }
  const index = layers.findIndex((l) => l.name === module.layer);
  const summary = layerOf(module.layer, layers)?.summary ?? null;
  const lines = [`層 ${module.layer}${summary === null ? '' : ` — ${summary}`}`];
  if (module.layerReason !== null) {
    lines.push(module.layerReason);
  }
  if (module.violations > 0) {
    lines.push(`linter の違反 ${module.violations} 件`);
  }
  return {
    badge: module.layer.slice(0, 1).toUpperCase(),
    color:
      module.violations > 0
        ? 'problemsErrorIcon.foreground'
        : index >= 0
          ? BADGE_PALETTE[index % BADGE_PALETTE.length]
          : 'foreground',
    tooltip: lines.join('\n')
  };
}

/** ステータスバーの文 — 「層: protocol — 相手の話し方へ訳す handler(context: land-notice)」(説明が無ければ名前だけ)。 */
export function layerStatusText(module: LintModule, layers: readonly LintLayer[]): string {
  if (module.layer === null) {
    return '層: (層の外)';
  }
  const summary = layerOf(module.layer, layers)?.summary ?? null;
  const context = module.context === null ? '' : `(context: ${module.context})`;
  return `層: ${module.layer}${summary === null ? '' : ` — ${summary}`}${context}`;
}

/** 地図の層の項目の一行の説明(linter が出さなければ空)。 */
export function layerSummary(name: string, layers: readonly LintLayer[]): string {
  return layerOf(name, layers)?.summary ?? '';
}

/** 表の中の `|` を逃がし、無い欄は空にする。 */
function cell(text: string | null): string {
  return text === null ? '' : text.replace(/\|/g, '\\|');
}

/** 層の説明の表の Markdown(ステータスバーを押した時に見せる物)。 */
export function layerTableMarkdown(layers: readonly LintLayer[]): string {
  const body =
    layers.length === 0
      ? ['linter が層の説明(layers)を出していません。doeff-linter の設定 `[tool.doeff-linter.layers.describe.<層>]` に書くと出ます。']
      : [
          '| 層 | 一行の説明 | 知っていること | 知らないこと | 迷った時の問い |',
          '|---|---|---|---|---|',
          ...layers.map(
            (l) =>
              `| **${cell(l.name)}** | ${cell(l.summary)} | ${cell(l.knows)} | ${cell(l.doesNotKnow)} | ${cell(l.question)} |`
          )
        ];
  return ['# 層の説明', '', '層とその説明は linter(doeff-linter)の設定で決まり、エディタはそれを表示するだけです。', '', ...body, ''].join(
    '\n'
  );
}

/** 行の上のタグ — `MODULE-TAGS` の辞書か、定義の契約の辞書の `:tags` / `:role`。 */
export interface TagMention {
  readonly role: string | null;
  readonly context: string | null;
}

/** カーソルの位置がタグの上かを見て、行に書かれた role と context を取り出す(タグの上でなければ undefined)。 */
export function tagAt(lineText: string, character: number): TagMention | undefined {
  const markers = [/MODULE-TAGS/g, /:tags\b/g, /:role\b/g];
  const onMarker = markers.some((re) =>
    [...lineText.matchAll(re)].some((m) => m.index !== undefined && character >= m.index && character <= m.index + m[0].length)
  );
  const roleMatch = /:role\s+"([^"]*)"/.exec(lineText);
  const onRoleValue = roleMatch !== null && character >= roleMatch.index && character <= roleMatch.index + roleMatch[0].length;
  if (!onMarker && !onRoleValue) {
    return undefined;
  }
  const contextMatch = /:context\s+"([^"]*)"/.exec(lineText);
  return { role: roleMatch?.[1] ?? null, context: contextMatch?.[1] ?? null };
}

/** 層の説明の行(linter が出した欄だけ)。 */
function layerLines(layer: LintLayer | undefined): string[] {
  if (layer === undefined) {
    return [];
  }
  const lines: string[] = [];
  if (layer.knows !== null) lines.push(`- 知っていること: ${layer.knows}`);
  if (layer.doesNotKnow !== null) lines.push(`- 知らないこと: ${layer.doesNotKnow}`);
  if (layer.question !== null) lines.push(`- 迷った時の問い: ${layer.question}`);
  return lines;
}

/** タグの hover の Markdown — 書かれた role と、linter が決めた file の層・その理由・層の説明。 */
export function tagHoverMarkdown(tag: TagMention, module: LintModule | undefined, layers: readonly LintLayer[]): string {
  const lines = [`**role** \`${tag.role ?? '(書かれていない)'}\`${tag.context === null ? '' : ` · **context** \`${tag.context}\``}`];
  if (module === undefined) {
    lines.push('', 'この file の層は linter の結果にまだありません(linter が見ていないか、まだ走っていない)。');
    return lines.join('\n');
  }
  if (module.layer === null) {
    lines.push('', 'linter によると、この file は層の外です。');
    if (module.layerReason !== null) lines.push('', module.layerReason);
    return lines.join('\n');
  }
  const layer = layerOf(module.layer, layers);
  lines.push('', `**層 ${module.layer}**(linter)${layer?.summary ? ` — ${layer.summary}` : ''}`);
  if (module.layerReason !== null) {
    lines.push('', `層の決め方: ${module.layerReason}`);
  }
  const described = layerLines(layer);
  if (described.length > 0) {
    lines.push('', ...described);
  }
  return lines.join('\n');
}

/** 違反の説明の文(問題の一覧と hover 用)— Jev の判定の印と確率、subject・reason・law の :statement・直し方を、linter が出した分だけ。 */
export function violationExplanationLines(violation: LintViolation): string[] {
  const lines: string[] = [];
  if (violation.source === 'jev') {
    const p = violation.probability === null ? '' : ` p=${violation.probability.toFixed(2)}`;
    lines.push(`Jev の判定(意味の規則・止めはしない)${p}`);
  }
  if (violation.explanation !== null) {
    lines.push(`これは何か: ${violation.explanation.subject}`);
    lines.push(`なぜ違反か: ${violation.explanation.reason}`);
    if (violation.explanation.lawStatement !== null) {
      lines.push(`law: ${violation.explanation.lawStatement}`);
    }
  }
  if (violation.hint !== null) {
    lines.push(`直し方: ${violation.hint}`);
  }
  return lines;
}
