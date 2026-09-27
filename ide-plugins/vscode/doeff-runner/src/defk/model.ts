// defk の見出しと束縛の型を、editor のどこに何を描くかに直す純粋な関数(VS Code に触らない)。
// 型の読み方は linter の editor-json(契約 版 2 の signatures と bindings)が唯一の正本で、ここは位置と描く物を決めるだけ。
// 読むだけの表示 — file の文字は変えない(agora-redesign #849・operator 2026-09-28 "I will never edit the source manually")。
//
// 見出し(A1 の形): 頭の行 `(defk 名 [引数]` を見出しにし(名を太字・行に薄い帯と下の線)、契約の辞書 `{:pre … :post …}` の
// 1 行目に型の流れ `(X, Y) → Program[effect | B]`、2 行目に tags と状態を描く。辞書の残りの行の文字は隠す。辞書が 1 行なら、
// tags と状態は頭の行の後ろに置く。辞書が頭と同じ行にあれば、辞書の範囲だけを隠してそこへ型の流れと tags を描く。
// 束縛: `(<- x T e)` → `T x <- e`・`(val x e)` / `(setv x e)` → `T x = e`・`(var x e)` → `var T x = e`・`(:= x v)` → `x := v`。

import type { LintBinding, LintEffectRef, LintRange, LintSignature, LintTypeRef } from '../lint/contract';

/** 行の中の文字の範囲(0 始まりの行・UTF-16 の列)。 */
export interface Span {
  readonly line: number;
  readonly start: number;
  readonly end: number;
}

/** 見出しの effect 1 つの状態 — 宣言と推論の突き合わせ。 */
export type EffectState =
  /** 宣言にも推論にもある */
  | 'both'
  /** 推論で起こしているのに `:effects` に無い */
  | 'undeclared'
  /** `:effects` に在るのに推論では起こしていない */
  | 'unused'
  /** `:effects` の宣言が無い(推論だけ) */
  | 'inferred';

/** 見出しに描く effect 1 つ。 */
export interface ShownEffect {
  readonly effect: LintEffectRef;
  readonly state: EffectState;
}

/** 宣言と推論の突き合わせの状態。 */
export type EffectAgreement =
  | { readonly tag: 'match' }
  | { readonly tag: 'mismatch'; readonly count: number }
  | { readonly tag: 'undeclared' };

/** 型の式を 1 行の文字にする(`A | B`・`H[A, B]`・読めない式はそのまま)。 */
export function typeText(type: LintTypeRef | null): string {
  if (type === null) {
    return '?';
  }
  switch (type.kind) {
    case 'name':
      return type.name;
    case 'union':
      return type.members.map(typeText).join(' | ');
    case 'apply':
      return `${typeText(type.head)}[${type.args.map(typeText).join(', ')}]`;
    case 'unknown':
      return type.text;
    default: {
      const unreachable: never = type;
      throw new Error(`網羅されていない型の式: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 型の式に出てくる名の型の全部(hover の定義への移動の一覧のため・同じ名は 1 つ)。 */
export function namedTypes(types: readonly (LintTypeRef | null)[]): Array<Extract<LintTypeRef, { kind: 'name' }>> {
  const seen = new Map<string, Extract<LintTypeRef, { kind: 'name' }>>();
  const walk = (type: LintTypeRef | null): void => {
    if (type === null) {
      return;
    }
    switch (type.kind) {
      case 'name':
        if (!seen.has(type.name)) {
          seen.set(type.name, type);
        }
        return;
      case 'union':
        type.members.forEach(walk);
        return;
      case 'apply':
        walk(type.head);
        type.args.forEach(walk);
        return;
      case 'unknown':
        return;
      default: {
        const unreachable: never = type;
        throw new Error(`網羅されていない型の式: ${JSON.stringify(unreachable)}`);
      }
    }
  };
  types.forEach(walk);
  return [...seen.values()];
}

/** 見出しの effect(宣言の順、宣言に無い推論は後ろ)と、それぞれの突き合わせの状態。 */
export function shownEffects(signature: LintSignature): ShownEffect[] {
  const inferred = new Set(signature.inferred.map((e) => e.name));
  if (signature.declared === null) {
    return signature.inferred.map((effect) => ({ effect, state: 'inferred' }));
  }
  const declared = new Set(signature.declared.map((e) => e.name));
  return [
    ...signature.declared.map((effect): ShownEffect => ({ effect, state: inferred.has(effect.name) ? 'both' : 'unused' })),
    ...signature.inferred.filter((e) => !declared.has(e.name)).map((effect): ShownEffect => ({ effect, state: 'undeclared' }))
  ];
}

/** 宣言と推論の突き合わせ。 */
export function effectAgreement(signature: LintSignature): EffectAgreement {
  if (signature.declared === null) {
    return { tag: 'undeclared' };
  }
  const off = shownEffects(signature).filter((e) => e.state !== 'both').length;
  return off === 0 ? { tag: 'match' } : { tag: 'mismatch', count: off };
}

/** 見出しの型の流れを 1 行の文字にする(hover と画面読み上げの代わりの文)。 */
export function signatureText(signature: LintSignature): string {
  const params = signature.params.map((p) => typeText(p.type)).join(', ');
  const effects = shownEffects(signature)
    .map((e) => e.effect.name)
    .concat(signature.raises.map((r) => `Raise ${typeText(r)}`));
  const answer = signature.absent ? `Maybe[${typeText(signature.answer)}]` : typeText(signature.answer);
  return signature.kind === 'deff' ? `(${params}) -> ${answer}` : `(${params}) -> Program[{${effects.join(', ')}}, ${answer}]`;
}

/** 行の文字を引く口(document の行)。 */
export interface LineSource {
  readonly lineCount: number;
  lineText(line: number): string;
}

/** 見出しを描く場所。 */
export interface HeaderPlan {
  /** 頭の行(薄い帯と下の線) */
  readonly headLine: number;
  /** 太字にする名 */
  readonly name: Span;
  /** 文字を隠す範囲(契約の辞書) */
  readonly hidden: readonly Span[];
  /** 型の流れを置く所(隠した範囲の頭か、頭の行の末尾) */
  readonly flowAt: { readonly line: number; readonly character: number; readonly placement: 'before' | 'after' };
  /** tags と状態を置く所 */
  readonly tagsAt: { readonly line: number; readonly character: number; readonly placement: 'before' | 'after' };
  /** 定義の全体の行(カーソルが入ったら元の文字を見せる範囲) */
  readonly lines: { readonly start: number; readonly end: number };
}

/** 行の頭の空白の長さ。 */
function indentOf(text: string): number {
  return text.length - text.trimStart().length;
}

/** 範囲が document の中で正しいか(版の食い違いで外れた位置を描かない)。 */
function inside(range: LintRange, lines: LineSource): boolean {
  return range.start.line <= range.end.line && range.end.line < lines.lineCount && range.end.character <= lines.lineText(range.end.line).length;
}

/** 見出し 1 つの描く場所(位置が document に合わなければ undefined)。 */
export function headerPlan(signature: LintSignature, lines: LineSource): HeaderPlan | undefined {
  if (!inside(signature.fullRange, lines) || !inside(signature.range, lines)) {
    return undefined;
  }
  const headLine = signature.range.start.line;
  const name: Span = { line: headLine, start: signature.range.start.character, end: signature.range.end.character };
  const headEnd = { line: headLine, character: lines.lineText(headLine).length, placement: 'after' as const };
  const whole = { start: signature.fullRange.start.line, end: signature.fullRange.end.line };
  const contract = signature.contractRange;
  if (contract === null || !inside(contract, lines)) {
    return { headLine, name, hidden: [], flowAt: headEnd, tagsAt: headEnd, lines: whole };
  }
  if (contract.start.line === headLine || contract.start.line === contract.end.line) {
    const at = { line: contract.start.line, character: contract.start.character, placement: 'before' as const };
    const hidden: Span = { line: contract.start.line, start: contract.start.character, end: contract.end.character };
    // 辞書が 1 行: 型の流れを辞書の所へ、tags と状態を頭の行の後ろへ(頭と同じ行なら同じ所の後ろ)
    const tagsAt = contract.start.line === headLine ? { ...at, placement: 'before' as const } : headEnd;
    return { headLine, name, hidden: [hidden], flowAt: at, tagsAt, lines: whole };
  }
  const hidden: Span[] = [];
  for (let line = contract.start.line; line <= contract.end.line; line++) {
    const text = lines.lineText(line);
    const start = line === contract.start.line ? contract.start.character : indentOf(text);
    const end = line === contract.end.line ? contract.end.character : text.length;
    if (end > start) {
      hidden.push({ line, start, end });
    }
  }
  const first = hidden[0];
  const second = hidden[1];
  if (first === undefined) {
    return { headLine, name, hidden: [], flowAt: headEnd, tagsAt: headEnd, lines: whole };
  }
  return {
    headLine,
    name,
    hidden,
    flowAt: { line: first.line, character: first.start, placement: 'before' },
    tagsAt: second === undefined ? headEnd : { line: second.line, character: second.start, placement: 'before' },
    lines: whole
  };
}

/** 束縛の型の札の中身。 */
export type BindingChip =
  | { readonly tag: 'type'; readonly type: LintTypeRef; readonly absent: boolean; readonly raises: readonly LintTypeRef[] }
  /** 型が分からない(linter が null を返した — 別の型で埋めない) */
  | { readonly tag: 'unknown' };

/** 束縛 1 つの描き方。 */
export interface BindingPlan {
  /** 文字を隠す範囲 — 開き括弧と頭(`(<-`)・注記(` T`)・閉じ括弧 */
  readonly hidden: readonly Span[];
  /** 隠した頭の所に置く札(`:=` は札を置かない) */
  readonly chip: BindingChip | undefined;
  /** 札の前に添える語(var だけ `var`) */
  readonly prefix: string | undefined;
  /** 名の後ろに添える記号(`<-`・`=`・`:=`) */
  readonly operator: string;
  /** 名 */
  readonly name: Span;
}

/** 同じ form を持つ束縛が複数ある(`(setv a 1 b 2)`)か — 1 つの形に直せないので描かない。 */
export function multiBindingForms(bindings: readonly LintBinding[]): Set<string> {
  const counts = new Map<string, number>();
  for (const b of bindings) {
    const k = rangeKey(b.formRange);
    counts.set(k, (counts.get(k) ?? 0) + 1);
  }
  return new Set([...counts].filter(([, n]) => n > 1).map(([k]) => k));
}

/** 範囲の比べる鍵。 */
export function rangeKey(range: LintRange): string {
  return `${range.start.line}:${range.start.character}-${range.end.line}:${range.end.character}`;
}

/** 束縛 1 つの描き方(位置が document に合わない・頭と名が別の行・閉じ括弧が見つからなければ undefined)。 */
export function bindingPlan(binding: LintBinding, lines: LineSource): BindingPlan | undefined {
  const { formRange, headRange, range } = binding;
  if (!inside(formRange, lines) || !inside(headRange, lines) || !inside(range, lines)) {
    return undefined;
  }
  if (headRange.start.line !== formRange.start.line || range.start.line !== headRange.end.line) {
    return undefined;
  }
  const closeLine = lines.lineText(formRange.end.line);
  if (formRange.end.character < 1 || closeLine[formRange.end.character - 1] !== ')') {
    return undefined;
  }
  const hidden: Span[] = [{ line: formRange.start.line, start: formRange.start.character, end: headRange.end.character }];
  const annotation = binding.annotationRange;
  if (annotation !== null) {
    if (annotation.start.line !== range.end.line || annotation.end.line !== annotation.start.line) {
      return undefined;
    }
    hidden.push({ line: annotation.start.line, start: range.end.character, end: annotation.end.character });
  }
  hidden.push({ line: formRange.end.line, start: formRange.end.character - 1, end: formRange.end.character });
  const chip: BindingChip | undefined =
    binding.form === ':='
      ? undefined
      : binding.type === null
        ? { tag: 'unknown' }
        : { tag: 'type', type: binding.type, absent: binding.absent, raises: binding.raises };
  const operator = binding.form === '<-' ? '<-' : binding.form === ':=' ? ':=' : '=';
  return {
    hidden,
    chip,
    prefix: binding.form === 'var' ? 'var' : undefined,
    operator,
    name: { line: range.start.line, start: range.start.character, end: range.end.character }
  };
}

/** カーソル(と選んだ範囲)の行の列 `[start, end]`。 */
export interface LineSpan {
  readonly start: number;
  readonly end: number;
}

/** カーソルが入ったら元の文字を見せる範囲の選び方(設定)。 */
export const REVEAL_MODES = ['definition', 'line', 'never'] as const;
export type RevealMode = (typeof REVEAL_MODES)[number];

/** 見出しを元の文字で見せるか — definition = カーソルが定義の中・line = カーソルが見出しの行(頭と辞書)の上・never = 見せない。 */
export function headerRevealed(plan: HeaderPlan, cursors: readonly LineSpan[], mode: RevealMode): boolean {
  switch (mode) {
    case 'never':
      return false;
    case 'definition':
      return cursors.some((c) => c.start <= plan.lines.end && c.end >= plan.lines.start);
    case 'line': {
      const touched = [plan.headLine, ...plan.hidden.map((h) => h.line)];
      return cursors.some((c) => touched.some((l) => c.start <= l && l <= c.end));
    }
    default: {
      const unreachable: never = mode;
      throw new Error(`網羅されていない設定: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 束縛を元の文字で見せるか — カーソルの行(と選んだ範囲の行)にかかる束縛。 */
export function bindingRevealed(plan: BindingPlan, cursors: readonly LineSpan[]): boolean {
  return plan.hidden.some((h) => cursors.some((c) => c.start <= h.line && h.line <= c.end));
}

/** 設定の値を読む(知らない値は既定の definition と理由)。 */
export function parseRevealMode(value: unknown): { readonly mode: RevealMode; readonly problem: string | undefined } {
  if (value === undefined) {
    return { mode: 'definition', problem: undefined };
  }
  const found = REVEAL_MODES.find((m) => m === value);
  return found === undefined
    ? { mode: 'definition', problem: `知らない値 ${JSON.stringify(value)}(${REVEAL_MODES.join(' / ')} のどれか)— definition として読んだ` }
    : { mode: found, problem: undefined };
}
