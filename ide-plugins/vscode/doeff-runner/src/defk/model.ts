// defk の見出しと束縛の型を、editor のどこに何を描くかに直す純粋な関数(VS Code に触らない)。
// 型の読み方は linter の editor-json(契約 版 2 の signatures と bindings)が唯一の正本で、ここは位置と描く物を決めるだけ。
// 読むだけの表示 — file の文字は変えない(agora-redesign #849・operator 2026-09-28 "I will never edit the source manually")。
//
// 見出し(B 案 — operator 2026-09-28 "i want it to show more like (dict,str)->JsonAnswer" ほか):
// - 頭の行 `(defk 名 [引数]` を見出しにし(名を太字・行に薄い帯と下の線)、tags は頭の行の末尾に小さな札で置く。
// - 契約の辞書 `{:pre … :post … :effects … :tags …}` の文字は隠し、1 行目に型の行 `(dict, str) -> JsonAnswer`
//   (Absent を起こしうる答えは `Maybe[B]`)、2 行目に effect の行(装置の絵と名の札・Raise を含む)を描く。辞書が 1 行なら、
//   effect の札は型の行の後ろに並べる。linter の知らせ(宣言と推論の食い違いなど)は見出しに出さない — 違反の場所に linter が出す。
// - 定義へ飛ぶ: 型の名と effect の札は 1 つずつ別の部品として、隠した辞書の中の別々の文字(空白と括弧 — 語の文字の上に置くと
//   Hy の「定義へ移動」がその語を解いて混ざる)に付ける。押された位置 → 部品 → 定義の位置を引く(Cmd+クリック)。
// 束縛: `(<- x T e)` → `T x <- e`・`(val x e)` / `(setv x e)` → `T x = e`・`(var x e)` → `var T x = e`・`(:= x v)` → `x := v`。
// 型の札は頭と名の間の空白に付ける(押せば型の定義へ)。

import type { LintBinding, LintLocation, LintRange, LintSignature, LintTypeRef } from '../lint/contract';

/** 行の中の文字の範囲(0 始まりの行・UTF-16 の列)。 */
export interface Span {
  readonly line: number;
  readonly start: number;
  readonly end: number;
}

/** 行の中の位置。 */
export interface At {
  readonly line: number;
  readonly character: number;
}

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

/** 型の式に出てくる名の型の全部(定義への移動の先のため・同じ名は 1 つ)。 */
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

/** 型の式が指す定義の位置(組み込みの型と解けない名は無い — 飛ばない)。 */
export function definitionsOf(type: LintTypeRef | null): LintLocation[] {
  return namedTypes([type]).flatMap((t) => (t.definition === null ? [] : [t.definition]));
}

/** 見出しの effect の札 1 つ(effect は宣言と推論を合わせた一覧 — 食い違いの印は付けない)。 */
export type EffectItem =
  | { readonly kind: 'effect'; readonly name: string; readonly definition: LintLocation | null }
  | { readonly kind: 'raise'; readonly name: string; readonly definition: LintLocation | null };

/** 見出しに並べる effect(宣言の順、宣言に無い推論は後ろ)と Raise。 */
export function headerEffects(signature: LintSignature): EffectItem[] {
  const seen = new Set<string>();
  const effects: EffectItem[] = [];
  for (const effect of [...(signature.declared ?? []), ...signature.inferred]) {
    if (!seen.has(effect.name)) {
      seen.add(effect.name);
      effects.push({ kind: 'effect', name: effect.name, definition: effect.definition });
    }
  }
  const raises = namedTypes(signature.raises).map((r): EffectItem => ({ kind: 'raise', name: r.name, definition: r.definition }));
  return [...effects, ...raises];
}

/** 型の行の答えの文字(Absent を起こしうるなら `Maybe[B]`)。 */
export function answerText(signature: LintSignature): string {
  return signature.absent ? `Maybe[${typeText(signature.answer)}]` : typeText(signature.answer);
}

/** 型の行を 1 行の文字にする(hover の文のため)。 */
export function signatureText(signature: LintSignature): string {
  return `(${signature.params.map((p) => typeText(p.type)).join(', ')}) -> ${answerText(signature)}`;
}

/** 部品の前に描く物。 */
export type PieceContent =
  /** 型の名(型ごとの色と薄い枠) */
  | { readonly kind: 'type'; readonly text: string; readonly colorKey: string }
  /** 区切りの文字(普通の文字) */
  | { readonly kind: 'punct'; readonly text: string }
  /** 行の見出しの語(小さく淡い) */
  | { readonly kind: 'label'; readonly text: string }
  /** effect の札(装置の絵と名) */
  | { readonly kind: 'effect'; readonly name: string }
  /** Raise の札(警報灯と型の名) */
  | { readonly kind: 'raise'; readonly name: string };

/** 描く部品 1 つ — 隠した文字 1 つに付け、前(before)に中身、後ろ(after)に区切りの文字を描く。押すと targets へ飛ぶ。 */
export interface Piece {
  readonly at: At;
  readonly before: PieceContent;
  readonly after: string | undefined;
  readonly targets: readonly LintLocation[];
}

/** 部品の並び(置く前)。 */
type Draft = Omit<Piece, 'at'>;

/** 行の文字を引く口(document の行)。 */
export interface LineSource {
  readonly lineCount: number;
  lineText(line: number): string;
}

/** 見出しを描く場所と部品。 */
export interface HeaderPlan {
  /** 頭の行(薄い帯と下の線) */
  readonly headLine: number;
  /** 太字にする名 */
  readonly name: Span;
  /** 文字を隠す範囲(契約の辞書) */
  readonly hidden: readonly Span[];
  /** 隠した文字に付ける部品(型の行と effect の行) */
  readonly pieces: readonly Piece[];
  /** tags の札を置く所(頭の行の末尾) */
  readonly tagsAt: At;
  /** 辞書が無い・隠す場所が足りない時の、頭の行の末尾に置く型の行の文字(押しても飛ばない) */
  readonly fallback: string | undefined;
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

/** 部品を付けてよい文字(空白・括弧・引用符 — 語の文字の上では Hy の「定義へ移動」がその語を解いてしまう)。 */
const ANCHOR_CHAR = /[\s()[\]{}"'#]/;

/** 隠した範囲の中の、部品を付けてよい文字の位置(左から)。 */
export function anchorsIn(span: Span, text: string): At[] {
  const found: At[] = [];
  for (let c = span.start; c < span.end; c++) {
    // 直前の文字も語の文字でないこと — 語の直後の位置は、その語の範囲の端として Hy の「定義へ移動」に解かれる(実測: `ReadInput WriteInput` の間の空白)
    if (ANCHOR_CHAR.test(text[c] ?? '') && (c === 0 || ANCHOR_CHAR.test(text[c - 1] ?? ''))) {
      found.push({ line: span.line, character: c });
    }
  }
  return found;
}

/** 型の行の部品 — `(`・引数の型ごと(後ろに `,` か `) -> `)・答えの型(Maybe の包みは前後の区切りに)。 */
function typeLineDrafts(signature: LintSignature): Draft[] {
  const maybe = signature.absent;
  const arrow = `) -> ${maybe ? 'Maybe[' : ''}`;
  const typePiece = (type: LintTypeRef | null, after: string | undefined): Draft => ({
    before: { kind: 'type', text: typeText(type), colorKey: typeText(type).split(/[ |[]/)[0] },
    after,
    targets: definitionsOf(type)
  });
  const params = signature.params;
  const drafts: Draft[] = [{ before: { kind: 'punct', text: '(' }, after: params.length === 0 ? arrow : undefined, targets: [] }];
  params.forEach((p, i) => drafts.push(typePiece(p.type, i === params.length - 1 ? arrow : ',')));
  drafts.push(typePiece(signature.answer, maybe ? ']' : undefined));
  return drafts;
}

/** effect の行の部品 — 見出しの語 `effects` と、effect と Raise の札ごと(effect が無ければ空)。 */
function effectLineDrafts(signature: LintSignature): Draft[] {
  const items = headerEffects(signature);
  if (items.length === 0) {
    return [];
  }
  return [
    { before: { kind: 'label', text: 'effects' }, after: undefined, targets: [] },
    ...items.map((item): Draft => ({ before: { kind: item.kind, name: item.name }, after: undefined, targets: item.definition === null ? [] : [item.definition] }))
  ];
}

/** 部品を付けてよい位置へ並べる — 足りなければ最後の位置に残りを `…` として畳む(押すと残りの定義の全部へ)。 */
function place(drafts: readonly Draft[], anchors: readonly At[]): Piece[] {
  if (drafts.length === 0 || anchors.length === 0) {
    return [];
  }
  if (drafts.length <= anchors.length) {
    return drafts.map((d, i) => ({ ...d, at: anchors[i] }));
  }
  const kept = drafts.slice(0, anchors.length - 1).map((d, i) => ({ ...d, at: anchors[i] }));
  const rest = drafts.slice(anchors.length - 1);
  return [...kept, { at: anchors[anchors.length - 1], before: { kind: 'punct', text: '…' }, after: undefined, targets: rest.flatMap((d) => d.targets) }];
}

/** 見出し 1 つの描く場所と部品(位置が document に合わなければ undefined)。 */
export function headerPlan(signature: LintSignature, lines: LineSource): HeaderPlan | undefined {
  if (!inside(signature.fullRange, lines) || !inside(signature.range, lines)) {
    return undefined;
  }
  const headLine = signature.range.start.line;
  const name: Span = { line: headLine, start: signature.range.start.character, end: signature.range.end.character };
  const tagsAt = { line: headLine, character: lines.lineText(headLine).length };
  const whole = { start: signature.fullRange.start.line, end: signature.fullRange.end.line };
  const contract = signature.contractRange;
  const fallbackText = (): string => {
    const effects = headerEffects(signature).map((e) => (e.kind === 'raise' ? `Raise ${e.name}` : e.name));
    return `${signatureText(signature)}${effects.length > 0 ? `   effects: ${effects.join(' ')}` : ''}`;
  };
  if (contract === null || !inside(contract, lines)) {
    return { headLine, name, hidden: [], pieces: [], tagsAt, fallback: fallbackText(), lines: whole };
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
  const types = typeLineDrafts(signature);
  const effects = effectLineDrafts(signature);
  const anchors = hidden.map((span) => anchorsIn(span, lines.lineText(span.line)));
  const [first, second] = anchors;
  if (first === undefined || first.length === 0) {
    return { headLine, name, hidden, pieces: [], tagsAt, fallback: fallbackText(), lines: whole };
  }
  // 2 行目があれば effect の行はそこへ、無ければ型の行の後ろへ続けて並べる(辞書が頭と同じ行でも同じ)
  const pieces =
    second !== undefined && second.length > 0
      ? [...place(types, first), ...place(effects, second)]
      : place([...types, ...effects], first);
  return { headLine, name, hidden, pieces, tagsAt, fallback: undefined, lines: whole };
}

/** 束縛の型の札の中身。 */
export type BindingChip =
  | { readonly tag: 'type'; readonly text: string; readonly colorKey: string; readonly absent: boolean }
  /** 型が分からない(linter が null を返した — 別の型で埋めない) */
  | { readonly tag: 'unknown' };

/** 束縛 1 つの描き方。 */
export interface BindingPlan {
  /** 文字を隠す範囲 — 開き括弧と頭(var は頭を残す)・頭と名の間の空白・注記・閉じ括弧 */
  readonly hidden: readonly Span[];
  /** 型の札を付ける位置(頭と名の間の空白の頭 — `:=` は札を置かない) */
  readonly chipAt: At | undefined;
  readonly chip: BindingChip | undefined;
  /** 札を押した時に飛ぶ先(型の定義) */
  readonly targets: readonly LintLocation[];
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
  if (headRange.start.line !== formRange.start.line || range.start.line !== headRange.end.line || headRange.end.character >= range.start.character) {
    return undefined;
  }
  const closeLine = lines.lineText(formRange.end.line);
  if (formRange.end.character < 1 || closeLine[formRange.end.character - 1] !== ')') {
    return undefined;
  }
  const line = formRange.start.line;
  // var は頭の語を残し(`var T x = e`)、それ以外は開き括弧と頭を隠す
  const opening: Span =
    binding.form === 'var'
      ? { line, start: formRange.start.character, end: formRange.start.character + 1 }
      : { line, start: formRange.start.character, end: headRange.end.character };
  const gap: Span = { line, start: headRange.end.character, end: range.start.character };
  const hidden: Span[] = [opening, gap];
  const annotation = binding.annotationRange;
  if (annotation !== null) {
    if (annotation.start.line !== range.end.line || annotation.end.line !== annotation.start.line) {
      return undefined;
    }
    hidden.push({ line: annotation.start.line, start: range.end.character, end: annotation.end.character });
  }
  hidden.push({ line: formRange.end.line, start: formRange.end.character - 1, end: formRange.end.character });
  const assign = binding.form === ':=';
  const chip: BindingChip | undefined = assign
    ? undefined
    : binding.type === null
      ? { tag: 'unknown' }
      : { tag: 'type', text: typeText(binding.type), colorKey: typeText(binding.type).split(/[ |[]/)[0], absent: binding.absent };
  return {
    hidden,
    chipAt: assign ? undefined : { line, character: gap.start },
    chip,
    targets: definitionsOf(binding.type),
    operator: binding.form === '<-' ? '<-' : assign ? ':=' : '=',
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

/** 押された位置の部品の飛ぶ先(部品の無い位置は undefined — 他の「定義へ移動」に任せる)。 */
export function targetsAt(
  headers: readonly HeaderPlan[],
  bindings: readonly BindingPlan[],
  at: At
): readonly LintLocation[] | undefined {
  for (const plan of headers) {
    const piece = plan.pieces.find((p) => p.at.line === at.line && p.at.character === at.character);
    if (piece !== undefined) {
      return piece.targets;
    }
  }
  for (const plan of bindings) {
    if (plan.chipAt !== undefined && plan.chipAt.line === at.line && plan.chipAt.character === at.character) {
      return plan.targets;
    }
  }
  return undefined;
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
