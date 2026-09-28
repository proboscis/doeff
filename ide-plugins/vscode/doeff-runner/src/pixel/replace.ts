// エディタの文字の表示の置き換え — Hy の本文から、決まった語彙(def* の頭・`<-`・`:tags` の辞書・`:pre` / `:post`・
// effect の頭・handler の resume / finish・失敗の語彙)の場所を探す純粋な関数。file は変えない。VS Code の飾りが
// 表示の上でだけ icon に置き換え、hover と見本の HTML も同じ結果を使う(何を置き換えるかの持ち主はこの 1 か所)。
// 文字列・註の中は見ない。VS Code には触らない。

import { EFFECT_GLYPHS, effectGlyph } from './vocabulary';

/** 置き換えの種類(設定で種類ごとに入り切りする閉じた集合)。 */
export const REPLACE_KINDS = ['definition', 'bind', 'tags', 'contract', 'effect', 'handler', 'failure'] as const;
export type ReplaceKind = (typeof REPLACE_KINDS)[number];

/** 種類の説明(設定の画面と選ぶ命令に出す)。 */
export const REPLACE_KIND_LABELS: Readonly<Record<ReplaceKind, string>> = {
  definition: 'def* の頭(defk・defhandler・defeffect・defrecord・defwire・defsystem・deftest・defp)',
  bind: '<-(Program の結果を受け取る)',
  tags: ':tags の辞書(1 行に収まる辞書は丸ごと 1 つの荷札に畳む)',
  contract: ':pre / :post(定義の契約)',
  effect: 'effect の頭(Ask は受話器に置き換え、宣言した effect は名前の前に種類の装置の印 — 読み取り機・印刷機・判子・時計・無線の塔ほか)',
  handler: 'handler の中の resume / finish',
  failure: '失敗の語彙(Absent・Raise・Unreachable・Refused・Conflict・Malformed)'
};

/**
 * 置き換え 1 つ。隠す範囲はいつも 1 行の中(`line` の `start`〜`end`)。
 * - replace: 文字を隠して icon を出す
 * - mark: 文字は残し、前に icon を付ける(利用者が付けた名前の effect — 名前を消すと読めなくなるため)
 */
export interface Replacement {
  readonly kind: ReplaceKind;
  readonly display: 'replace' | 'mark';
  /** icon の名前(元の定義 glyphs.json の名前) */
  readonly glyph: string;
  readonly line: number;
  readonly start: number;
  readonly end: number;
  /** 置き換える前の文字を一字一句そのまま(複数行の :tags の辞書は、隠すのが `:tags` だけでも辞書の全文) */
  readonly original: string;
}

/** def* の頭 → icon。 */
const DEFINITION_HEADS: Readonly<Record<string, string>> = {
  defk: 'defk',
  defhandler: 'defhandler',
  defeffect: 'defeffect',
  defrecord: 'defrecord',
  defwire: 'defwire',
  defsystem: 'defsystem',
  deftest: 'deftest',
  defp: 'program'
};

/** doeff の組み込みの effect で、icon に置き換える頭(絵は effect の名 → 装置の絵の表から引く)。 */
const BUILTIN_EFFECT_HEADS: Readonly<Record<string, string>> = { Ask: effectGlyph('Ask') };

/** 失敗の語彙 → icon(頭でも値でも置き換える)。 */
const FAILURE_WORDS: Readonly<Record<string, string>> = {
  Absent: 'absent',
  Raise: 'raise',
  Unreachable: 'unreachable',
  Refused: 'refused',
  Conflict: 'conflict',
  Malformed: 'malformed'
};

/** handler の終わり方 → icon(defhandler か handle の中の頭だけ — 外の同じ名前は利用者の関数)。 */
const HANDLER_HEADS: Readonly<Record<string, string>> = { resume: 'resume', finish: 'finish' };

/** resume / finish を handler の終わり方と読む入れ物の頭。 */
const HANDLER_FORMS = new Set(['defhandler', 'handle']);

/** 名前を字のまま読ませる入れ物(import の並びは置き換えない)。 */
const LITERAL_FORMS = new Set(['import', 'require']);

/** 辞書の鍵 → icon(`:tags` は値の辞書ごと畳む)。 */
const CONTRACT_KEYS: Readonly<Record<string, string>> = { ':pre': 'contract', ':post': 'contract' };

/** 置き換えが使う icon の名前の全部(元の定義に全部あるかを検で確かめるため)。 */
export function replacementGlyphs(): string[] {
  const tables = [DEFINITION_HEADS, BUILTIN_EFFECT_HEADS, FAILURE_WORDS, HANDLER_HEADS, CONTRACT_KEYS];
  return [...new Set([...tables.flatMap((t) => Object.values(t)), 'bind', 'tags', ...EFFECT_GLYPHS])].sort();
}

/** 語彙の表で名前を引く(Object の prototype の名前 — toString など — に当てない)。 */
function lookup(table: Readonly<Record<string, string>>, word: string): string | undefined {
  return Object.prototype.hasOwnProperty.call(table, word) ? table[word] : undefined;
}

/** 字句 1 つ(位置は本文の先頭からの UTF-16 の位置)。 */
type Token =
  | { readonly tag: 'open'; readonly bracket: '(' | '[' | '{'; readonly start: number; readonly end: number }
  | { readonly tag: 'close'; readonly start: number; readonly end: number }
  | { readonly tag: 'atom'; readonly text: string; readonly start: number; readonly end: number }
  | { readonly tag: 'string'; readonly start: number; readonly end: number };

/** 語を区切る文字(空白と括弧・引用符・註の頭)。 */
const DELIMITERS = new Set([' ', '\t', '\n', '\r', '\f', '\v', ' ', '　', '(', ')', '[', ']', '{', '}', '"', ';']);

/** 文字列の前置き(f"…"・b"…"・r"…" と組み合わせ)。 */
const STRING_PREFIX = /^[bfrBFR]{1,2}$/;

/** `"` から閉じの `"` の次までの位置(閉じが無ければ本文の終わり)。 */
function stringEnd(text: string, quote: number): number {
  let i = quote + 1;
  while (i < text.length) {
    if (text[i] === '\\') {
      i += 2;
      continue;
    }
    if (text[i] === '"') {
      return i + 1;
    }
    i += 1;
  }
  return text.length;
}

/** `#[区切り[ … ]区切り]` の終わりの次の位置(形でなければ undefined)。 */
function bracketStringEnd(text: string, at: number): number | undefined {
  const open = text.indexOf('[', at + 2);
  if (open < 0) {
    return undefined;
  }
  const delimiter = text.slice(at + 2, open);
  if (/[\s\]]/.test(delimiter)) {
    return undefined;
  }
  const close = text.indexOf(`]${delimiter}]`, open + 1);
  return close < 0 ? text.length : close + delimiter.length + 2;
}

/** Hy の本文を字句に分ける — 註(`;` から行末)は捨て、引用の前置き(' ` ~ ~@)は語に含めない。 */
function tokenize(text: string): Token[] {
  const tokens: Token[] = [];
  let i = 0;
  while (i < text.length) {
    const ch = text[i];
    if (ch === ';') {
      const newline = text.indexOf('\n', i);
      i = newline < 0 ? text.length : newline;
      continue;
    }
    if (/\s/.test(ch)) {
      i += 1;
      continue;
    }
    if (ch === '(' || ch === '[' || ch === '{') {
      tokens.push({ tag: 'open', bracket: ch, start: i, end: i + 1 });
      i += 1;
      continue;
    }
    if (ch === ')' || ch === ']' || ch === '}') {
      tokens.push({ tag: 'close', start: i, end: i + 1 });
      i += 1;
      continue;
    }
    if (ch === '"') {
      const end = stringEnd(text, i);
      tokens.push({ tag: 'string', start: i, end });
      i = end;
      continue;
    }
    if (ch === "'" || ch === '`' || ch === '~') {
      i += text[i + 1] === '@' && ch === '~' ? 2 : 1;
      continue;
    }
    if (ch === '#') {
      const next = text[i + 1];
      if (next === '(' || next === '{') {
        // #( … ) の tuple と #{ … } の set — 頭を持たない入れ物
        tokens.push({ tag: 'open', bracket: '[', start: i, end: i + 2 });
        i += 2;
        continue;
      }
      if (next === '[') {
        const end = bracketStringEnd(text, i);
        if (end !== undefined) {
          tokens.push({ tag: 'string', start: i, end });
          i = end;
          continue;
        }
      }
      if (next === '_' || next === '^') {
        // #_ は次の形を捨てる印、#^ は型の註 — どちらも語ではない(次の形はそのまま読む)
        i += 2;
        continue;
      }
    }
    let end = i;
    while (end < text.length && !DELIMITERS.has(text[end])) {
      end += 1;
    }
    const word = text.slice(i, end);
    if (text[end] === '"' && STRING_PREFIX.test(word)) {
      const close = stringEnd(text, end);
      tokens.push({ tag: 'string', start: i, end: close });
      i = close;
      continue;
    }
    tokens.push({ tag: 'atom', text: word, start: i, end });
    i = end;
  }
  return tokens;
}

/** 字句の添字 at から始まる形 1 つの終わり(閉じの字句の添字)。形が無ければ undefined。 */
function formEnd(tokens: readonly Token[], at: number): number | undefined {
  const first = tokens[at];
  if (first === undefined || first.tag === 'close') {
    return undefined;
  }
  if (first.tag !== 'open') {
    return at;
  }
  let depth = 0;
  for (let j = at; j < tokens.length; j++) {
    const t = tokens[j];
    if (t.tag === 'open') {
      depth += 1;
    } else if (t.tag === 'close') {
      depth -= 1;
      if (depth === 0) {
        return j;
      }
    }
  }
  return tokens.length - 1;
}

/** 本文の位置 → 行と列(行の頭の位置の表を二分探索)。 */
function positionOf(lineStarts: readonly number[], offset: number): { readonly line: number; readonly character: number } {
  let lo = 0;
  let hi = lineStarts.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (lineStarts[mid] <= offset) {
      lo = mid;
    } else {
      hi = mid - 1;
    }
  }
  return { line: lo, character: offset - lineStarts[lo] };
}

/** 入れ物 1 つ(開き括弧・頭の語・中の形の数)。 */
interface Frame {
  readonly bracket: '(' | '[' | '{';
  head: string | null;
  count: number;
}

/**
 * Hy の本文から置き換えの場所を全部探す(本文の順)。`isEffect` は頭の語が宣言された effect か — 答えるのは
 * 拡張の effect の表(hy-index から作る)で、ここは判じない。
 */
export function findReplacements(text: string, isEffect: (name: string) => boolean): Replacement[] {
  const tokens = tokenize(text);
  const lineStarts = [0];
  for (let i = 0; i < text.length; i++) {
    if (text[i] === '\n') {
      lineStarts.push(i + 1);
    }
  }
  const found: Replacement[] = [];
  const add = (kind: ReplaceKind, display: 'replace' | 'mark', glyph: string, start: number, end: number, original: string): void => {
    const at = positionOf(lineStarts, start);
    found.push({ kind, display, glyph, line: at.line, start: at.character, end: at.character + (end - start), original });
  };
  const stack: Frame[] = [];
  // 畳んだ :tags の辞書の終わり(この位置より前の語は置き換えない — 辞書ごと 1 つの icon に畳んだため)
  let foldedUntil = -1;
  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i];
    const top = stack.length > 0 ? stack[stack.length - 1] : undefined;
    switch (token.tag) {
      case 'open':
        if (top !== undefined) {
          top.count += 1;
        }
        stack.push({ bracket: token.bracket, head: null, count: 0 });
        continue;
      case 'close':
        stack.pop();
        continue;
      case 'string':
        if (top !== undefined) {
          top.count += 1;
        }
        continue;
      case 'atom':
        break;
      default: {
        const unreachable: never = token;
        throw new Error(`網羅されていない字句: ${JSON.stringify(unreachable)}`);
      }
    }
    const word = token.text;
    const isHead = top !== undefined && top.bracket === '(' && top.count === 0;
    if (isHead) {
      top.head = word;
    }
    if (top !== undefined) {
      top.count += 1;
    }
    if (token.start < foldedUntil) {
      continue;
    }
    const enclosing = stack.slice(0, isHead ? -1 : undefined).map((f) => f.head);
    if (enclosing.some((h) => h !== null && LITERAL_FORMS.has(h))) {
      continue;
    }
    const failure = lookup(FAILURE_WORDS, word);
    if (failure !== undefined) {
      add('failure', 'replace', failure, token.start, token.end, word);
      continue;
    }
    if (top?.bracket === '{') {
      if (word === ':tags') {
        const last = formEnd(tokens, i + 1);
        const end = last === undefined ? token.end : tokens[last].end;
        const original = text.slice(token.start, end);
        const oneLine = !original.includes('\n');
        add('tags', 'replace', 'tags', token.start, oneLine ? end : token.end, original);
        if (oneLine) {
          foldedUntil = end;
        }
        continue;
      }
      const contract = lookup(CONTRACT_KEYS, word);
      if (contract !== undefined) {
        add('contract', 'replace', contract, token.start, token.end, word);
      }
      continue;
    }
    if (!isHead) {
      continue;
    }
    const definition = lookup(DEFINITION_HEADS, word);
    if (definition !== undefined) {
      add('definition', 'replace', definition, token.start, token.end, word);
      continue;
    }
    if (word === '<-') {
      add('bind', 'replace', 'bind', token.start, token.end, word);
      continue;
    }
    const ending = lookup(HANDLER_HEADS, word);
    if (ending !== undefined) {
      if (enclosing.some((h) => h !== null && HANDLER_FORMS.has(h))) {
        add('handler', 'replace', ending, token.start, token.end, word);
      }
      continue;
    }
    const builtin = lookup(BUILTIN_EFFECT_HEADS, word);
    if (builtin !== undefined) {
      add('effect', 'replace', builtin, token.start, token.end, word);
      continue;
    }
    if (isEffect(word)) {
      add('effect', 'mark', effectGlyph(word), token.start, token.end, word);
    }
  }
  return found;
}

/** 行の範囲(両端を含む)。 */
export interface LineSpan {
  readonly start: number;
  readonly end: number;
}

/** 行が範囲のどれかに入るか。 */
function inSpans(line: number, spans: readonly LineSpan[]): boolean {
  return spans.some((s) => s.start <= line && line <= s.end);
}

/**
 * 今 icon に置き換えて見せるか — 入れた種類で、元の文字で見せる行(カーソルの行と選んだ範囲の行)の外。
 * 置き換えた icon の hover が元の文字を出すかもこれで決める。
 */
export function isShownAsIcon(replacement: Replacement, enabled: ReadonlySet<ReplaceKind>, plain: readonly LineSpan[]): boolean {
  return enabled.has(replacement.kind) && !inSpans(replacement.line, plain);
}

/** 見えている範囲(visible)の中で、今 icon にする置き換え。 */
export function shownReplacements(
  all: readonly Replacement[],
  enabled: ReadonlySet<ReplaceKind>,
  plain: readonly LineSpan[],
  visible: readonly LineSpan[]
): Replacement[] {
  return all.filter((r) => inSpans(r.line, visible) && isShownAsIcon(r, enabled, plain));
}

/** 位置にある置き換え(位置が隠す範囲の中か、範囲の頭 — 隠した文字の上の icon は範囲の頭の位置で hover になる)。 */
export function replacementAt(all: readonly Replacement[], line: number, character: number): Replacement | undefined {
  return all.find((r) => r.line === line && r.start <= character && character < Math.max(r.end, r.start + 1));
}

/** 設定の値から入れた種類を読む — 知らない種類の名前と真偽値でない値は理由に出す(黙って既定にしない)。 */
export function parseReplaceKinds(value: unknown): { readonly enabled: Set<ReplaceKind>; readonly problems: string[] } {
  const enabled = new Set<ReplaceKind>(REPLACE_KINDS);
  const problems: string[] = [];
  if (value === undefined || value === null) {
    return { enabled, problems };
  }
  if (typeof value !== 'object' || Array.isArray(value)) {
    problems.push(`種類ごとの入り切りは { 種類: true / false } の形 — ${JSON.stringify(value)} は読めないので全部の種類を入れる`);
    return { enabled, problems };
  }
  for (const [key, on] of Object.entries(value)) {
    const kind = REPLACE_KINDS.find((k) => k === key);
    if (kind === undefined) {
      problems.push(`知らない種類 ${JSON.stringify(key)}(種類は ${REPLACE_KINDS.join('・')})`);
      continue;
    }
    if (typeof on !== 'boolean') {
      problems.push(`種類 ${kind} の値 ${JSON.stringify(on)} は true / false ではない — 入れたままにする`);
      continue;
    }
    if (!on) {
      enabled.delete(kind);
    }
  }
  return { enabled, problems };
}

/** Markdown の code block の囲み — 元の文字に含まれる最も長い ` の連なりより 1 つ長くする(最低 3)。 */
function fenceFor(original: string): string {
  const longest = Math.max(0, ...(original.match(/`+/g) ?? []).map((run) => run.length));
  return '`'.repeat(Math.max(3, longest + 1));
}

/** hover の一言を Markdown の文字として読ませる(`*`・`_`・`<` などを文字のまま出す)。 */
function escapeMarkdown(text: string): string {
  return text.replace(/[\\`*_{}[\]<>()#+!|]/g, (ch) => `\\${ch}`);
}

/**
 * 置き換えの hover の Markdown — icon に置き換えて見せている時は、元の文字を一字一句そのまま(コピーできる code block)
 * 出し、その下に語の icon と一言。文字のまま見せている時(カーソルの行・種類を切った時・mark)は icon と一言だけ。
 * `image` は icon の `<img>`(HTML)、`summary` は元の定義の一言。
 */
export function replacementHover(replacement: Replacement, shownAsIcon: boolean, image: string, summary: string): string {
  const line = `${image}${image === '' ? '' : '&nbsp; '}${escapeMarkdown(summary)}`;
  if (!shownAsIcon || replacement.display === 'mark') {
    return line;
  }
  const fence = fenceFor(replacement.original);
  return [`${fence}hy`, replacement.original, fence, '', line].join('\n');
}
