// 定義を読む面の HTML の小さな共通の部品 — カード(render.ts)と呼び出しの木(treeRender.ts)が同じ逃がし方・同じ tags の色・
// 同じ effect の絵の口を使うため(2 つの描き手が互いを import しないよう、ここに置く)。

/** HTML の特別な文字を逃がす。 */
export function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/** tags の key ごとの色(context と role は見本と同じ決まった色、他の key は名前から選ぶ — 同じ key は同じ色に見せるため)。 */
const TAG_PALETTE: readonly string[] = ['tag-c0', 'tag-c1', 'tag-c2', 'tag-c3', 'tag-c4', 'tag-c5'];

/** tags の key の色の class。 */
export function tagClass(key: string): string {
  if (key === 'context') {
    return 'tag-context';
  }
  if (key === 'role') {
    return 'tag-role';
  }
  let hash = 0;
  for (const ch of key) {
    hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  }
  return TAG_PALETTE[hash % TAG_PALETTE.length];
}

/** 説明の 1 行目(先頭の 1 文を省略記号で切る — 畳んだ 1 行と hover に短く出すため)。 */
export function docFirstLine(docstring: string | null, limit = 80): string {
  if (docstring === null) {
    return '';
  }
  const first = docstring.split('\n')[0].trim();
  const sentence = /^[^。.!?！？]*[。.!?！？]?/.exec(first)?.[0] ?? first;
  return sentence.length > limit ? `${sentence.slice(0, limit - 1)}…` : sentence;
}

/** 絵の口 — effect の名から pixel art の data URI(#849 の装飾 A と同じ絵。無ければ undefined)。 */
export interface Glyphs {
  readonly effect: (name: string) => string | undefined;
}
