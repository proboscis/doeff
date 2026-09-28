import * as assert from 'assert';
import {
  findReplacements,
  isShownAsIcon,
  parseReplaceKinds,
  REPLACE_KINDS,
  replacementAt,
  replacementHover,
  shownReplacements,
  type Replacement,
  type ReplaceKind
} from '../../pixel/replace';

/** 宣言した effect の名前だけを effect と答える(拡張では hy-index の effect の表が答える)。 */
const effects =
  (...names: string[]) =>
  (name: string): boolean =>
    names.includes(name);

/** 置き換えを短い文字列にする(比べる用)。 */
function show(r: Replacement): string {
  return `${r.line}:${r.start}-${r.end} ${r.kind}/${r.display} ${r.glyph} ${JSON.stringify(r.original)}`;
}

const ALL: ReadonlySet<ReplaceKind> = new Set(REPLACE_KINDS);

const SAMPLE = [
  '(defk poll-window [cursor kinds]',
  '  {:pre [(: cursor int)] :post [(: % tuple)] :tags {:context "durable" :role "foundation"}}',
  '  "純粋: <- と Absent は文字列の中では置き換えない"',
  '  ; (defk commented-out) も置き換えない',
  '  (<- signal dict (WatchOnce cursor))',
  '  (<- env (Ask :key))',
  '  (Absent "no"))'
].join('\n');

suite('文字の置き換え — 置き換える語の場所', () => {
  test('def* の頭・:pre / :post・1 行の :tags の辞書・<-・Ask・宣言した effect・失敗の語彙に当たる(文字列と註の中には当たらない)', () => {
    assert.deepStrictEqual(findReplacements(SAMPLE, effects('WatchOnce')).map(show), [
      '0:1-5 definition/replace defk "defk"',
      '1:3-7 contract/replace contract ":pre"',
      '1:25-30 contract/replace contract ":post"',
      '1:45-90 tags/replace tags ":tags {:context \\"durable\\" :role \\"foundation\\"}"',
      '4:3-5 bind/replace bind "<-"',
      '4:19-28 effect/mark effect-read "WatchOnce"',
      '5:3-5 bind/replace bind "<-"',
      '5:11-14 effect/replace effect-ask "Ask"',
      '6:3-9 failure/replace absent "Absent"'
    ]);
  });

  test('畳んだ :tags の辞書の中の語は別に置き換えない・複数行の辞書は :tags だけを隠し、hover の元の文字は辞書の全文', () => {
    const inside = findReplacements('(defk f [] {:tags {:note Absent}} 1)', effects());
    assert.deepStrictEqual(inside.map((r) => r.kind), ['definition', 'tags']);
    const multi = findReplacements('(defk f []\n  {:tags {:context "kanban"\n          :role "judgment"}}\n  1)', effects());
    const tags = multi.find((r) => r.kind === 'tags');
    assert.ok(tags !== undefined);
    assert.deepStrictEqual([tags.line, tags.start, tags.end], [1, 3, 8], '隠すのは :tags の 5 文字だけ(行をまたいで隠さない)');
    assert.strictEqual(tags.original, ':tags {:context "kanban"\n          :role "judgment"}');
  });

  test('resume / finish は defhandler・handle の中の頭だけ — 外の同じ名前(利用者の関数)は置き換えない', () => {
    const text = [
      '(defhandler h',
      '  (Ask [key] (resume 1))',
      '  (Stop [x] (finish x)))',
      '(defk f [] (<- (finish session turn)))'
    ].join('\n');
    const found = findReplacements(text, effects('Stop'));
    assert.deepStrictEqual(
      found.filter((r) => r.kind === 'handler').map((r) => `${r.line} ${r.glyph}`),
      ['1 resume', '2 finish']
    );
    // handler の節の頭の effect の名前にも印(組み込みの Ask は吹き出しに置き換え)
    assert.deepStrictEqual(
      found.filter((r) => r.kind === 'effect').map((r) => `${r.original}/${r.display}`),
      ['Ask/replace', 'Stop/mark']
    );
  });

  test('import の並び・頭でない def* の名前・Object の名前(toString)・f 文字列と #[[…]] の中には当てない', () => {
    const text = [
      '(import doeff_core_effects.effects [Absent Raise])',
      '(setv defk 1 toString 2)',
      '(toString x)',
      '(print f"(defk {x})" #[[(<- y)]])'
    ].join('\n');
    assert.deepStrictEqual(findReplacements(text, effects()), []);
  });

  test('位置は UTF-16 の列(行の前に日本語があっても VS Code の位置と合う)・CRLF の行も数える', () => {
    const text = '(setv 名前 "日本語")\r\n  (<- x (Raise "失敗"))';
    const found = findReplacements(text, effects());
    assert.deepStrictEqual(found.map(show), ['1:3-5 bind/replace bind "<-"', '1:9-14 failure/replace raise "Raise"']);
  });
});

suite('文字の置き換え — 見せ方と hover', () => {
  const found = findReplacements(SAMPLE, effects('WatchOnce'));

  test('カーソルの行と選んだ範囲の行は元の文字・切った種類は元の文字・見えている範囲の外には付けない', () => {
    const shown = (plain: Array<{ start: number; end: number }>, enabled: ReadonlySet<ReplaceKind>, visible = [{ start: 0, end: 100 }]): string[] =>
      shownReplacements(found, enabled, plain, visible).map((r) => `${r.line}:${r.original.slice(0, 5)}`);
    assert.deepStrictEqual(shown([{ start: 4, end: 4 }], ALL), ['0:defk', '1::pre', '1::post', '1::tags', '5:<-', '5:Ask', '6:Absen']);
    assert.deepStrictEqual(shown([{ start: 0, end: 5 }], ALL), ['6:Absen'], '選んだ範囲の行は全部元の文字');
    assert.deepStrictEqual(shown([], new Set<ReplaceKind>(['bind'])), ['4:<-', '5:<-']);
    assert.deepStrictEqual(shown([], ALL, [{ start: 5, end: 6 }]), ['5:<-', '5:Ask', '6:Absen']);
    assert.strictEqual(isShownAsIcon(found[0], ALL, [{ start: 0, end: 0 }]), false);
    assert.strictEqual(isShownAsIcon(found[0], ALL, [{ start: 3, end: 3 }]), true);
  });

  test('hover の位置 — 隠した語の頭(icon の上)と語の中で当たり、語の後ろでは当たらない', () => {
    assert.strictEqual(replacementAt(found, 0, 1)?.glyph, 'defk');
    assert.strictEqual(replacementAt(found, 0, 4)?.glyph, 'defk');
    assert.strictEqual(replacementAt(found, 0, 5), undefined);
    assert.strictEqual(replacementAt(found, 1, 60)?.kind, 'tags');
  });

  test('hover の中身 — icon で見せている時は元の文字をそのまま code block で、その下に icon と一言。文字のままなら icon と一言だけ', () => {
    const tags = found.find((r) => r.kind === 'tags');
    assert.ok(tags !== undefined);
    const image = '<img src="data:x">';
    assert.strictEqual(
      replacementHover(tags, true, image, ':tags — 荷札 *タグ*'),
      ['```hy', ':tags {:context "durable" :role "foundation"}', '```', '', '<img src="data:x">&nbsp; :tags — 荷札 \\*タグ\\*'].join('\n')
    );
    assert.strictEqual(replacementHover(tags, false, image, 'x'), '<img src="data:x">&nbsp; x');
    const mark = found.find((r) => r.display === 'mark');
    assert.ok(mark !== undefined);
    assert.strictEqual(replacementHover(mark, true, '', 'effect'), 'effect', '名前を残す印は元の文字を出さない');
    // 元の文字に ``` があっても code block が閉じない(囲みを 1 つ長くする)
    const fenced: Replacement = { ...tags, original: ':tags {:note "```"}' };
    assert.ok(replacementHover(fenced, true, '', 'x').startsWith('````hy\n:tags {:note "```"}\n````'));
    // 一言の中の <- は HTML でなく文字として出す
    assert.ok(replacementHover(tags, false, '', '<- — 束ねる').startsWith('\\<- — 束ねる'));
  });

  test('設定の読み方 — false の種類は切る、知らない種類と真偽値でない値は理由に出して黙って既定にしない', () => {
    const off = parseReplaceKinds({ tags: false, bind: true });
    assert.strictEqual(off.enabled.has('tags'), false);
    assert.strictEqual(off.enabled.has('bind'), true);
    assert.deepStrictEqual(off.problems, []);
    const bad = parseReplaceKinds({ tag: false, effect: 'no' });
    assert.strictEqual(bad.enabled.size, REPLACE_KINDS.length);
    assert.strictEqual(bad.problems.length, 2);
    assert.match(bad.problems[0], /知らない種類 "tag"/);
    assert.strictEqual(parseReplaceKinds('all').problems.length, 1);
    assert.strictEqual(parseReplaceKinds(undefined).enabled.size, REPLACE_KINDS.length);
  });
});
