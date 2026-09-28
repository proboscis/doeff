// 読む面の source の色付け(agora-redesign #910 U16・V18)— editor と同じ文法・theme・記号ごとの色で塗り、
// file 全体で塗ってから定義の範囲を切り出すことを確かめる。文法の fixture は python-semantic-highlighter 1.7.0 / 1.8.0 の
// syntaxes/hy.tmLanguage.json の写し(editor が `.hy` に使う file と同じ中身)。

import * as assert from 'assert';
import * as fs from 'fs';
import * as path from 'path';
import { parseHyIndexJson } from '../../hy/contract';
import { locateGrammar, locateTheme, type InstalledExtension } from '../../hy/highlight/locate';
import { asSemanticApi, parseSemanticSpans } from '../../hy/highlight/semantic';
import { overlaySemantic, pieceStyle, PLAIN, sliceHighlight, type HighlightedLines, type SemanticSpan } from '../../hy/highlight/spans';
import { createTokenizer, type LineTokenizer } from '../../hy/highlight/textmate';
import { resolveThemeRules, type ReadThemeFile } from '../../hy/highlight/theme';
import { INITIAL_FOLD, cardKey, unfoldAll } from '../../read/fold';
import { buildCards } from '../../read/model';
import { renderPage } from '../../read/render';
import { buildCallGraph } from '../../read/tree';

const FIXTURES = path.join(__dirname, '..', '..', '..', 'test-fixtures', 'highlight');

/** fixture の file を読む口(無ければ理由)。 */
const readFixture: ReadThemeFile = async (file) =>
  fs.existsSync(file) ? { tag: 'ok', text: fs.readFileSync(file, 'utf8') } : { tag: 'error', reason: '無い' };

/** theme の include の相対 path を解く。 */
const join = (from: string, relative: string): string => path.join(path.dirname(from), relative);

/** fixture の文法と theme で tokenizer を作る。 */
async function fixtureTokenizer(): Promise<LineTokenizer> {
  const theme = await resolveThemeRules(path.join(FIXTURES, 'theme.json'), 'Fixture Dark', undefined, readFixture, join);
  const grammarPath = path.join(FIXTURES, 'hy.tmLanguage.json');
  const tokenizer = await createTokenizer({ scopeName: 'source.hy', path: grammarPath, text: fs.readFileSync(grammarPath, 'utf8') }, theme.rules);
  assert.ok(tokenizer !== null);
  return tokenizer;
}

const SAMPLE_LINES = fs.readFileSync(path.join(FIXTURES, 'sample.hy'), 'utf8').split('\n');

/** 行の色の付いた区切りを (文字, 色) の列にする。 */
function colored(lines: readonly string[], spans: HighlightedLines, line: number): [string, string | null][] {
  return spans[line].filter((s) => s.color !== null).map((s) => [lines[line].slice(s.start, s.end), s.color]);
}

suite('source の色付け — theme の規則', () => {
  test('include を先に、自分の tokenColors、customizations(全体 → [theme の名])の順に組む', async () => {
    const custom = { comments: '#111111', '[Fixture Dark]': { textMateRules: [{ scope: 'keyword.control', settings: { foreground: '#222222' } }] } };
    const theme = await resolveThemeRules(path.join(FIXTURES, 'theme.json'), 'Fixture Dark', custom, readFixture, join);
    assert.deepStrictEqual(theme.problems, []);
    const scopes = theme.rules.map((r) => (r.scope === undefined ? '(既定)' : typeof r.scope === 'string' ? r.scope : r.scope.join(',')));
    assert.deepStrictEqual(scopes, [
      '(既定)',
      'comment',
      'string',
      'keyword.control',
      'constant.numeric',
      'entity.name.function',
      'comment,punctuation.definition.comment',
      'keyword.control'
    ]);
    assert.strictEqual(theme.rules[theme.rules.length - 1].settings.foreground, '#222222');
  });

  test('.tmTheme を指す theme と読めない include は理由を積んで飛ばす', async () => {
    const tm = await resolveThemeRules(path.join(FIXTURES, 'theme-tm.json'), 'Old', undefined, readFixture, join);
    assert.deepStrictEqual(tm.rules, []);
    assert.match(tm.problems[0], /\.tmTheme は読まない/);
    const missing = await resolveThemeRules(path.join(FIXTURES, 'no-such-theme.json'), 'x', { strings: '#333333' }, readFixture, join);
    assert.match(missing.problems[0], /無い/);
    assert.strictEqual(missing.rules.length, 1, 'customizations の規則だけは残る');
  });
});

suite('source の色付け — 下の層(TextMate)', () => {
  test('editor と同じ文法で分けた token に theme の色と字の形を当てる(規則の無い token は既定の色 = null)', async () => {
    const tokenizer = await fixtureTokenizer();
    const spans = tokenizer.tokenize(SAMPLE_LINES);
    assert.deepStrictEqual(colored(SAMPLE_LINES, spans, 0), [
      ['defk', '#C586C0'],
      ['run-it', '#DCDCAA']
    ]);
    assert.deepStrictEqual(colored(SAMPLE_LINES, spans, 1), [
      ['"要求 1 つを実体化するため。"', '#CE9178'],
      [';; 註', '#6A9955']
    ]);
    const comment = spans[1].find((s) => SAMPLE_LINES[1].slice(s.start, s.end) === ';; 註');
    assert.strictEqual(comment?.fontStyle.italic, true);
    const number = spans[2].find((s) => SAMPLE_LINES[2].slice(s.start, s.end) === '42');
    assert.deepStrictEqual(number?.fontStyle, { italic: false, bold: true, underline: true, strikethrough: false });
    // 区切りは行の頭から終わりまで隙間なく並ぶ
    for (const [i, line] of spans.entries()) {
      assert.strictEqual(line.length === 0 ? 0 : line[line.length - 1].end, SAMPLE_LINES[i].length, `行 ${i}`);
      line.forEach((s, j) => assert.strictEqual(s.start, j === 0 ? 0 : line[j - 1].end));
    }
  });
});

suite('source の色付け — 上の層(記号ごとの色)と切り出し', () => {
  const base: HighlightedLines = [[{ start: 0, end: 12, color: '#aaaaaa', fontStyle: { ...PLAIN, italic: true } }]];

  test('上の層は色だけを上書きし、字の形は下の層のまま・区切りを境目で割る', () => {
    const out = overlaySemantic(base, [{ line: 0, column: 4, length: 3, color: '#123456' }]);
    assert.deepStrictEqual(
      out[0].map((s) => [s.start, s.end, s.color, s.fontStyle.italic]),
      [
        [0, 4, '#aaaaaa', true],
        [4, 7, '#123456', true],
        [7, 12, '#aaaaaa', true]
      ]
    );
    assert.deepStrictEqual(overlaySemantic(base, [{ line: 5, column: 0, length: 3, color: '#123456' }]), base, '行の外は当てない');
  });

  test('file 全体で塗ってから定義の範囲を切り出す(記号ごとの色は file 全体の記号の順で決まるため)', async () => {
    const tokenizer = await fixtureTokenizer();
    // 上の層の偽物: outcome に色(file 全体の答え — 2 行目と 3 行目の出現)
    const semantic: SemanticSpan[] = [
      { line: 2, column: 6, length: 7, color: '#ff0000' },
      { line: 3, column: 12, length: 7, color: '#ff0000' }
    ];
    const whole = overlaySemantic(tokenizer.tokenize(SAMPLE_LINES), semantic);
    const pieces = sliceHighlight(SAMPLE_LINES, whole, { start: { line: 3, character: 2 }, end: { line: 4, character: 19 } });
    assert.strictEqual(pieces.length, 2);
    assert.strictEqual(pieces[0].map((p) => p.text).join(''), SAMPLE_LINES[3].slice(2), '行の途中から切り出す');
    assert.deepStrictEqual(
      pieces[0].filter((p) => p.color !== null).map((p) => [p.text, p.color]),
      [
        ['when', '#C586C0'],
        ['is', '#C586C0'],
        ['outcome', '#ff0000']
      ]
    );
  });

  test('1 片の style は # の 16 進の色だけを置く(theme や拡張の値を CSS へ流さない)', () => {
    assert.strictEqual(pieceStyle({ text: 'x', color: '#C586C0', fontStyle: { ...PLAIN, bold: true, underline: true } }), 'color:#C586C0;font-weight:bold;text-decoration:underline');
    assert.strictEqual(pieceStyle({ text: 'x', color: 'red;background:url(x)', fontStyle: PLAIN }), '');
    assert.strictEqual(pieceStyle({ text: 'x', color: null, fontStyle: PLAIN }), '');
  });
});

suite('source の色付け — python-semantic-highlighter の API', () => {
  test('版 1 の API だけを受け付け、答えの形を確かめる', async () => {
    assert.strictEqual(asSemanticApi(undefined), undefined, '1.7.0 は API を返さない');
    assert.strictEqual(asSemanticApi({ apiVersion: 2, colorize: () => null }), undefined);
    const api = asSemanticApi({ apiVersion: 1, colorize: (source: string) => [{ line: 0, column: 0, length: source.length, color: '#abcdef' }] });
    assert.ok(api !== undefined);
    assert.deepStrictEqual(parseSemanticSpans(await api.colorize('defk', 'hy')), { tag: 'ok', spans: [{ line: 0, column: 0, length: 4, color: '#abcdef' }] });
    assert.deepStrictEqual(parseSemanticSpans(null), { tag: 'none' }, '切ってある・解析できない');
    const bad = parseSemanticSpans([{ line: 0, column: 0, length: 1, color: 'url(x)' }]);
    assert.strictEqual(bad.tag, 'rejected');
  });
});

suite('source の色付け — 文法と theme の場所', () => {
  const ext = (id: string, contributes: unknown): InstalledExtension => ({ id, extensionPath: `/ext/${id}`, packageJSON: { contributes } });

  test('言語 hy の文法は一覧の後の方(後から登録した物)を使う', () => {
    const found = locateGrammar(
      [
        ext('a', { grammars: [{ language: 'hy', scopeName: 'source.hy.old', path: './old.json' }] }),
        ext('b', { grammars: [{ language: 'python', scopeName: 'source.python', path: './py.json' }] }),
        ext('c', { grammars: [{ language: 'hy', scopeName: 'source.hy', path: './syntaxes/hy.tmLanguage.json' }] })
      ],
      'hy'
    );
    assert.deepStrictEqual(found, { extensionId: 'c', scopeName: 'source.hy', path: path.join('/ext/c', 'syntaxes/hy.tmLanguage.json') });
    assert.strictEqual(locateGrammar([ext('b', {})], 'hy'), null);
  });

  test('theme は id(無ければ label)で照らす', () => {
    const extensions = [ext('t', { themes: [{ id: 'Dark X', label: 'Dark Label', path: './dark.json' }, { label: 'Light Y', path: './light.json' }] })];
    assert.strictEqual(locateTheme(extensions, 'Dark X'), path.join('/ext/t', 'dark.json'));
    assert.strictEqual(locateTheme(extensions, 'Dark Label'), null, 'id が在れば label では照らさない');
    assert.strictEqual(locateTheme(extensions, 'Light Y'), path.join('/ext/t', 'light.json'));
  });
});

suite('source の色付け — 読む面の source の箱', () => {
  test('file 全体の色があれば source の箱を色つきで描き、無ければ色なしの文字で描く', async () => {
    const parsed = parseHyIndexJson(fs.readFileSync(path.join(FIXTURES, 'sample-index.json'), 'utf8'));
    assert.strictEqual(parsed.tag, 'ok');
    const file = parsed.tag === 'ok' ? parsed.document.files[0] : undefined;
    assert.ok(file !== undefined);
    const cards = buildCards({ definitions: file.definitions, signatures: [], violations: [], bodies: [], lines: SAMPLE_LINES });
    const tokenizer = await fixtureTokenizer();
    const page = (coloring: Parameters<typeof renderPage>[0]['coloring']): string =>
      renderPage({
        place: 'sample.hy',
        state: { tag: 'cards', cards, selection: new Map() },
        glyphs: { effect: () => undefined },
        fold: unfoldAll(INITIAL_FOLD, cards.map((c) => cardKey(c.definition))),
        graph: buildCallGraph([file]),
        tree: undefined,
        coloring,
        cspSource: 'vscode-resource:',
        nonce: 'n'
      });
    const withColor = page({ lines: SAMPLE_LINES, spans: tokenizer.tokenize(SAMPLE_LINES) });
    assert.ok(withColor.includes('<span style="color:#C586C0">defk</span>'), 'keyword の色');
    assert.ok(withColor.includes('<span style="color:#6A9955;font-style:italic">;; 註</span>'), '註の色と斜体');
    const plain = page(undefined);
    assert.ok(!plain.includes('<span style="color:#C586C0">'), '色がまだ無ければ色なし');
    assert.ok(plain.includes('(defk run-it [request budget]'));
  });
});
