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

/**
 * 描く部品 1 つ — 隠した文字 1 つに付け、その前(before)に中身を描く。押すと targets へ飛ぶ。区切りの文字も 1 つの部品にする
 * (後ろ(after)に描くと、隣の文字に付けた次の部品の前と同じ位置に重なり、次の部品より後ろへ回る — 実測 2026-09-28)。
 */
export interface Piece {
  readonly at: At;
  readonly before: PieceContent;
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
  /** 文字を隠す範囲(契約の辞書のうち描く行 — 部品の位置で切ってある) */
  readonly hidden: readonly Span[];
  /** 描く物の無い辞書の行(隠さずに淡く見せる) */
  readonly dimmed: readonly Span[];
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

/** 押す部品(型の名・effect の札)を付けてよい文字か — 空白と括弧で、直前も語の文字でない(語の上や直後は Hy の「定義へ移動」が解く)。 */
function clickable(text: string, c: number): boolean {
  return ANCHOR_CHAR.test(text[c] ?? '') && (c === 0 || ANCHOR_CHAR.test(text[c - 1] ?? ''));
}

/** 隠した範囲の中の、押す部品を付けてよい文字の位置(左から)。 */
export function anchorsIn(span: Span, text: string): At[] {
  const found: At[] = [];
  for (let c = span.start; c < span.end; c++) {
    if (clickable(text, c)) {
      found.push({ line: span.line, character: c });
    }
  }
  return found;
}

/** 部品が押すと飛ぶ物か(型の名・effect の札)— 押す部品は押してよい文字に、区切りと見出しの語はどの文字にも付けられる。 */
function isClickable(draft: Draft): boolean {
  // 飛ぶ先の無い部品(組み込みの型・型の書かれていない ?)は押しても何もしないので、どの文字に付けてもよい(短い辞書の行に収めるため)
  return draft.targets.length > 0;
}

/** 型の行の部品 — `(`・引数の型・`, `・`) -> `(Maybe なら `) -> Maybe[`)・答えの型・(Maybe なら `]`)。 */
function typeLineDrafts(signature: LintSignature): Draft[] {
  const punct = (text: string): Draft => ({ before: { kind: 'punct', text }, targets: [] });
  const typePiece = (type: LintTypeRef | null): Draft => ({
    before: { kind: 'type', text: typeText(type), colorKey: typeText(type).split(/[ |[]/)[0] },
    targets: definitionsOf(type)
  });
  const drafts: Draft[] = [punct('(')];
  signature.params.forEach((p, i) => {
    if (i > 0) {
      drafts.push(punct(', '));
    }
    drafts.push(typePiece(p.type));
  });
  drafts.push(punct(signature.absent ? ') -> Maybe[' : ') -> '));
  drafts.push(typePiece(signature.answer));
  if (signature.absent) {
    drafts.push(punct(']'));
  }
  return drafts;
}

/** effect の行の部品 — 見出しの語 `effects` と、effect と Raise の札ごと(effect が無ければ空)。 */
function effectLineDrafts(signature: LintSignature, ownRow: boolean): Draft[] {
  const items = headerEffects(signature);
  const unknown: Draft[] = signature.inferenceComplete ? [] : [{ before: { kind: 'label', text: '+ 追えない呼びの先' }, targets: [] }];
  if (items.length === 0 && unknown.length === 0) {
    // 自分の行があれば、effect を起こさないことも書く(無いことも読める・空の行を残さない)
    return ownRow && signature.kind === 'defk' ? [{ before: { kind: 'label', text: 'effect なし' }, targets: [] }] : [];
  }
  return [
    { before: { kind: 'label', text: 'effects' }, targets: [] },
    ...items.map((item): Draft => ({ before: { kind: item.kind, name: item.name }, targets: item.definition === null ? [] : [item.definition] })),
    ...unknown
  ];
}

/**
 * 部品を隠した範囲の文字へ左から順に付ける。押す部品は押してよい文字に、区切りはどの文字にも付ける(文字は 1 つに 1 部品)。
 * 付けきれなければ、付けられた所までと、最後に `…`(押すと残りの定義の全部へ)を置く。
 */
function place(drafts: readonly Draft[], span: Span, text: string): Piece[] {
  const pieces: Piece[] = [];
  let c = span.start;
  for (let i = 0; i < drafts.length; i++) {
    const draft = drafts[i];
    let at = c;
    while (at < span.end && isClickable(draft) && !clickable(text, at)) {
      at++;
    }
    if (at >= span.end) {
      const rest = drafts.slice(i);
      const last = pieces.pop();
      const where = last?.at ?? { line: span.line, character: span.start };
      return [...pieces, { at: where, before: { kind: 'punct', text: '…' }, targets: [...(last?.targets ?? []), ...rest.flatMap((d) => d.targets)] }];
    }
    pieces.push({ ...draft, at: { line: span.line, character: at } });
    c = at + 1;
  }
  return pieces;
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
    return { headLine, name, hidden: [], dimmed: [], pieces: [], tagsAt, fallback: fallbackText(), lines: whole };
  }
  const spans: Span[] = [];
  for (let line = contract.start.line; line <= contract.end.line; line++) {
    const text = lines.lineText(line);
    const start = line === contract.start.line ? contract.start.character : indentOf(text);
    const end = line === contract.end.line ? contract.end.character : text.length;
    if (end > start) {
      spans.push({ line, start, end });
    }
  }
  const types = typeLineDrafts(signature);
  const secondLine = spans[1];
  const effects = effectLineDrafts(signature, secondLine !== undefined && anchorsIn(secondLine, lines.lineText(secondLine.line)).length > 0);
  const [first, second] = spans;
  if (first === undefined || anchorsIn(first, lines.lineText(first.line)).length === 0) {
    // 描く場所が無い — 辞書は隠さない(隠したのに何も描かない行を作らない)
    return { headLine, name, hidden: [], dimmed: [], pieces: [], tagsAt, fallback: fallbackText(), lines: whole };
  }
  // 型の行は辞書の 1 行目、effect の行は 2 行目(無ければ型の行の後ろへ続ける)。残りの行は、見出しに出した鍵
  // (:tags・:pre・:post・:effects)だけの行なら行ごと隠し、見出しに出していない鍵が在る行だけ淡く見せる
  // (coordinator の決定 2026-09-28 — :tags の行が名の行の tags の札と重複していた)
  const twoRows = effects.length > 0 && second !== undefined && anchorsIn(second, lines.lineText(second.line)).length > 0;
  const firstText = lines.lineText(first.line);
  const pieces = twoRows
    ? [...place(types, first, firstText), ...place(effects, second, lines.lineText(second.line))]
    : place([...types, ...effects], first, firstText);
  const used = twoRows ? 2 : 1;
  const unshown = linesWithUnshownKeys(contract, lines);
  const rest = spans.slice(used);
  return {
    headLine,
    name,
    hidden: [...splitAtPieces(spans.slice(0, used), pieces), ...rest.filter((s) => !unshown.has(s.line))],
    dimmed: rest.filter((s) => unshown.has(s.line)),
    pieces,
    tagsAt,
    fallback: undefined,
    lines: whole
  };
}

/** 見出しに出す契約の辞書の鍵(型の行・effect の行・tags の札)。 */
const SHOWN_KEYS = new Set([':tags', ':pre', ':post', ':effects']);

/**
 * 契約の辞書の中で、見出しに出していない鍵(最上位の鍵のうち SHOWN_KEYS に無い物)が書かれた行 — その行は隠さずに淡く見せるため。
 * 文字列と註の中は読まず、入れ子の括弧の中の keyword(`:context` など)は鍵に数えない。
 */
export function linesWithUnshownKeys(contract: LintRange, lines: LineSource): Set<number> {
  const found = new Set<number>();
  let depth = 0;
  let inString = false;
  for (let line = contract.start.line; line <= contract.end.line; line++) {
    const text = lines.lineText(line);
    const from = line === contract.start.line ? contract.start.character : 0;
    const to = line === contract.end.line ? contract.end.character : text.length;
    for (let c = from; c < to; c++) {
      const ch = text[c];
      if (inString) {
        if (ch === '\\') {
          c++;
        } else if (ch === '"') {
          inString = false;
        }
        continue;
      }
      if (ch === '"') {
        inString = true;
      } else if (ch === ';') {
        break;
      } else if ('([{'.includes(ch)) {
        depth++;
      } else if (')]}'.includes(ch)) {
        depth--;
      } else if (ch === ':' && depth === 1 && (c === 0 || /[\s{]/.test(text[c - 1]))) {
        const key = /^:[^\s()[\]{}"]+/.exec(text.slice(c))?.[0] ?? ':';
        if (!SHOWN_KEYS.has(key)) {
          found.add(line);
        }
        c += key.length - 1;
      }
    }
  }
  return found;
}

/**
 * 隠す範囲を部品の位置で切る — 部品の前(before)に描く文字は、それを囲む隠す範囲の中にあると一緒に隠れる
 * (実測 2026-09-28: 範囲の頭の `(` だけが見え、途中の型の名が全部消えた)。部品の位置を範囲の境目にする。
 */
export function splitAtPieces(spans: readonly Span[], pieces: readonly Piece[]): Span[] {
  const out: Span[] = [];
  for (const span of spans) {
    const cuts = new Set<number>();
    for (const piece of pieces) {
      if (piece.at.line === span.line) {
        cuts.add(piece.at.character);
      }
    }
    const points = [span.start, ...[...cuts].filter((c) => c > span.start && c < span.end).sort((x, y) => x - y), span.end];
    for (let i = 0; i + 1 < points.length; i++) {
      out.push({ line: span.line, start: points[i], end: points[i + 1] });
    }
  }
  return out;
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
