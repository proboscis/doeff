// カーソルの下の Hy の記号を、行の文字列から取り出す純粋な処理。
// 索引は debounce の間は古いことがあるので、カーソルの記号は常に今の文字列から読む。

/** カーソルの記号 — 書かれたとおりの名前と、dotted の前の区切り(無ければ null)。 */
export interface CursorSymbol {
  readonly name: string;
  readonly qualifier: string | null;
  /** 名前(dotted の区切り 1 つ)の行内の範囲 [start, end) */
  readonly start: number;
  readonly end: number;
}

/** 記号の一部にならない文字(空白・括弧・引用・読み取りの記号)。 */
const DELIMITER = /[\s()[\]{}"';`~,]/;

/** 1 文字が記号の区切りかを見る。 */
function isDelimiter(ch: string): boolean {
  return DELIMITER.test(ch);
}

/**
 * 行 `lineText` の列 `character` にある記号を取り出す。keyword(`:foo`)・数・文字列の記号でない物は undefined。
 */
export function symbolAt(lineText: string, character: number): CursorSymbol | undefined {
  let start = Math.min(character, lineText.length);
  let end = start;
  while (start > 0 && !isDelimiter(lineText[start - 1])) {
    start -= 1;
  }
  while (end < lineText.length && !isDelimiter(lineText[end])) {
    end += 1;
  }
  // 読み取りの前置き(#^ #* #** @)を外す
  while (start < end && /[#^*@]/.test(lineText[start])) {
    start += 1;
  }
  if (start >= end) {
    return undefined;
  }
  const token = lineText.slice(start, end);
  if (token.startsWith(':') || /^[-+]?\d/.test(token)) {
    return undefined;
  }
  // dotted の区切りのうち、カーソルが入っている区切りを名前にする
  const parts = token.split('.');
  let offset = start;
  for (let i = 0; i < parts.length; i += 1) {
    const partStart = offset;
    const partEnd = offset + parts[i].length;
    const isLast = i === parts.length - 1;
    if (character <= partEnd || isLast) {
      if (parts[i] === '') {
        return undefined;
      }
      const before = parts.slice(0, i).filter((p) => p !== '');
      return {
        name: parts[i],
        qualifier: before.length > 0 ? before.join('.') : null,
        start: partStart,
        end: partEnd
      };
    }
    offset = partEnd + 1;
  }
  return undefined;
}
