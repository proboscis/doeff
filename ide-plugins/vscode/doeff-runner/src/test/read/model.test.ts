import * as assert from 'assert';
import * as fs from 'fs';
import * as path from 'path';
import { parseHyIndexJson, type HyDefinition, type HyFileIndex } from '../../hy/contract';
import { parseLintJson, type LintReport, type LintViolation } from '../../lint/contract';
import {
  axesOf,
  axisKey,
  buildCards,
  facets,
  NO_VALUE,
  parseAxisKey,
  toggle,
  valueOf,
  visibleCards,
  type Card,
  type PlaneAxis,
  type Selection
} from '../../read/model';
import { escapeHtml, renderPage } from '../../read/render';

// 材料は test-fixtures/read/plane.hy に hy-index と doeff-linter(editor-json・--stdin --path)を当てた実出力
// (path だけ /repo に置き換えた)。定義を読む面の受け入れの検査 V1・V2・V3・V10(agora-redesign #910)。

const FIXTURES = path.join(__dirname, '..', '..', '..', 'test-fixtures', 'read');
const FILE = '/repo/pkg/plane.hy';

/** 索引の実出力の 1 file 分を契約の入口で読む。 */
function planeIndex(): HyFileIndex {
  const parsed = parseHyIndexJson(fs.readFileSync(path.join(FIXTURES, 'plane-index.json'), 'utf8'));
  if (parsed.tag !== 'ok') {
    assert.fail(`索引の fixture を読めない: ${parsed.reason}`);
  }
  const file = parsed.document.files.find((f) => f.path === FILE);
  assert.ok(file !== undefined, '索引に plane.hy が無い');
  return file;
}

/** linter の実出力を契約の入口で読む。 */
function planeLint(): LintReport {
  const parsed = parseLintJson(fs.readFileSync(path.join(FIXTURES, 'plane-lint.json'), 'utf8'));
  if (parsed.tag !== 'ok') {
    assert.fail(`linter の fixture を読めない: ${parsed.reason}`);
  }
  return parsed.report;
}

/** 見本の source の行。 */
function planeLines(): string[] {
  return fs.readFileSync(path.join(FIXTURES, 'plane.hy'), 'utf8').split(/\r?\n/);
}

/** 見本のカード(違反は渡した物)。 */
function planeCards(violations: readonly LintViolation[] = []): Card[] {
  return buildCards({ definitions: planeIndex().definitions, signatures: planeLint().signatures, violations, lines: planeLines() });
}

/** カードの名の一覧。 */
function names(cards: readonly Card[]): string[] {
  return cards.map((c) => c.definition.name);
}

/** 軸の値を選んだ選択。 */
function select(pairs: ReadonlyArray<readonly [PlaneAxis, string]>): Selection {
  return pairs.reduce<Selection>((s, [axis, value]) => toggle(s, axis, value), new Map());
}

const KIND: PlaneAxis = { tag: 'kind' };
const CONTEXT: PlaneAxis = { tag: 'tag', key: 'context' };
const ROLE: PlaneAxis = { tag: 'tag', key: 'role' };
const OWNER: PlaneAxis = { tag: 'tag', key: 'owner' };

suite('定義を読む面 — カード(V1: 単位は定義)', () => {
  test('索引のその file の入れ子でない定義 1 つにつき 1 枚(defk 以外の kind も・source の順)', () => {
    const cards = planeCards();
    assert.deepStrictEqual(names(cards), ['LIMIT', 'Row', 'fetch-row', 'row-text', 'shout', 'test-row-text-is-the-text']);
    assert.deepStrictEqual(
      cards.map((c) => c.definition.kind),
      ['variable', 'defrecord', 'defk', 'deff', 'defk', 'deftest']
    );
    // defrecord の欄(field)は入れ子なのでカードにしない
    assert.ok(!names(cards).includes('key'));
  });

  test('defk / deff には linter の見出しを添え、他の kind には添えない', () => {
    const cards = planeCards();
    const byName = new Map(cards.map((c) => [c.definition.name, c]));
    assert.deepStrictEqual(byName.get('fetch-row')?.signature?.declared?.map((e) => e.name), ['ReadInput']);
    assert.strictEqual(byName.get('row-text')?.signature?.kind, 'deff');
    assert.strictEqual(byName.get('Row')?.signature, undefined);
    assert.strictEqual(byName.get('test-row-text-is-the-text')?.signature, undefined);
  });

  test('カードは source の lisp と頭の行(1 始まり)を持つ', () => {
    const shout = planeCards().find((c) => c.definition.name === 'shout');
    assert.ok(shout !== undefined);
    assert.ok(shout.source.startsWith('(defk shout [key]'));
    assert.ok(shout.source.endsWith('(.upper (row-text row)))'));
    assert.strictEqual(planeLines()[shout.firstLine - 1], '(defk shout [key]');
  });

  test('違反は範囲に入る定義のカードにだけ付く', () => {
    const shout = planeCards().find((c) => c.definition.name === 'shout');
    assert.ok(shout !== undefined);
    const inside = violationAt(shout.definition, 'DOEFF999');
    const cards = planeCards([inside]);
    assert.deepStrictEqual(
      cards.filter((c) => c.violations.length > 0).map((c) => c.definition.name),
      ['shout']
    );
  });
});

/** 定義の頭の行に置いた違反(テスト用)。 */
function violationAt(definition: HyDefinition, rule: string): LintViolation {
  const start = definition.fullRange.start;
  return {
    rule,
    law: null,
    adr: null,
    severity: 'warning',
    path: FILE,
    range: { start, end: { line: start.line, character: start.character + 1 } },
    message: 'テストの違反',
    hint: null,
    key: null,
    registered: false,
    baseSeverity: 'warning',
    standing: 'new',
    level: 'major',
    explanation: null,
    source: 'linter',
    probability: null
  };
}

suite('定義を読む面 — 軸(V2: tag の key を列挙しない)', () => {
  test('軸は kind と、索引の :tags に現れた key の全部(context・role が先、残りは名前の順)', () => {
    assert.deepStrictEqual(axesOf(planeCards()).map(axisKey), ['kind', 'tag:context', 'tag:role', 'tag:owner']);
  });

  test('索引に新しい key が現れると軸が 1 つ増える(key を固定しない)', () => {
    const cards = planeCards();
    const widened = cards.map((c) =>
      c.definition.name === 'LIMIT' ? { ...c, definition: { ...c.definition, tags: { lifetime: 'session' } } } : c
    );
    assert.deepStrictEqual(axesOf(widened).map(axisKey), ['kind', 'tag:context', 'tag:role', 'tag:lifetime', 'tag:owner']);
    const lifetime: PlaneAxis = { tag: 'tag', key: 'lifetime' };
    assert.deepStrictEqual(names(visibleCards(widened, select([[lifetime, 'session']]))), ['LIMIT']);
  });

  test('その key を持たない定義の値は「(なし)」', () => {
    const limit = planeCards().find((c) => c.definition.name === 'LIMIT');
    assert.ok(limit !== undefined);
    assert.strictEqual(valueOf(limit, OWNER), NO_VALUE);
    assert.strictEqual(valueOf(limit, KIND), 'variable');
  });

  test('軸の文字は往復する(知らない形は読まない)', () => {
    for (const axis of [KIND, CONTEXT, OWNER]) {
      assert.deepStrictEqual(parseAxisKey(axisKey(axis)), axis);
    }
    assert.strictEqual(parseAxisKey('tag:'), undefined);
    assert.strictEqual(parseAxisKey('service'), undefined);
  });
});

suite('定義を読む面 — 絞り込み(V3: 軸の交差 = 定義の集合の積)', () => {
  test('選ばなければ全部', () => {
    assert.strictEqual(visibleCards(planeCards(), new Map()).length, 6);
  });

  test('軸どうしは積: context = messaging かつ role = judgment → row-text だけ', () => {
    const cards = planeCards();
    const messaging = new Set(names(visibleCards(cards, select([[CONTEXT, 'messaging']]))));
    const judgment = new Set(names(visibleCards(cards, select([[ROLE, 'judgment']]))));
    const both = names(visibleCards(cards, select([[CONTEXT, 'messaging'], [ROLE, 'judgment']])));
    assert.deepStrictEqual(both, ['row-text']);
    assert.deepStrictEqual(both, [...messaging].filter((n) => judgment.has(n)));
  });

  test('同じ軸の中は「どれか」: kind = defk か deff', () => {
    assert.deepStrictEqual(names(visibleCards(planeCards(), select([[KIND, 'defk'], [KIND, 'deff']]))), ['fetch-row', 'row-text', 'shout']);
  });

  test('もう 1 度押すと外れる', () => {
    const once = select([[OWNER, 'input']]);
    assert.deepStrictEqual(names(visibleCards(planeCards(), once)), ['fetch-row']);
    assert.strictEqual(toggle(once, OWNER, 'input').size, 0);
  });

  test('札の数 = 他の軸の選択はそのままで、その値にしたら残る数(同じ軸の選択は数えない)', () => {
    const selection = select([[CONTEXT, 'messaging']]);
    const all = facets(planeCards(), selection);
    const role = all.find((f) => axisKey(f.axis) === 'tag:role');
    assert.deepStrictEqual(
      role?.values.map((v) => [v.value, v.count]),
      [
        ['judgment', 1],
        ['program', 1],
        [NO_VALUE, 0]
      ]
    );
    const context = all.find((f) => axisKey(f.axis) === 'tag:context');
    assert.deepStrictEqual(
      context?.values.map((v) => [v.value, v.count, v.selected]),
      [
        ['messaging', 2, true],
        ['screen', 1, false],
        [NO_VALUE, 3, false]
      ]
    );
  });
});

/** 見本の頁(effect の絵は名を返すだけの偽物)。 */
function planePage(selection: Selection): string {
  return renderPage({
    place: 'pkg/plane.hy',
    state: { tag: 'cards', cards: planeCards(), selection },
    glyphs: { effect: (name) => `data:image/svg+xml;fake,${name}` },
    cspSource: 'vscode-resource:',
    nonce: 'n'
  });
}

/** 頁の中の 1 枚のカードの HTML。 */
function cardHtml(html: string, name: string): string {
  const found = html.split('<section ').find((part) => part.includes(`<span class="name">${name}</span>`));
  assert.ok(found !== undefined, `カード ${name} が無い`);
  return found;
}

suite('定義を読む面 — 頁(V10・V12・V13)', () => {
  test('切った時は理由の文だけでカードを出さない(V10)', () => {
    const html = renderPage({
      place: 'pkg/plane.hy',
      state: { tag: 'message', text: '定義を読む面は設定で切ってあります' },
      glyphs: { effect: () => undefined },
      cspSource: 'vscode-resource:',
      nonce: 'n'
    });
    assert.ok(html.includes('定義を読む面は設定で切ってあります'));
    assert.ok(!html.includes('<section class="card"'));
  });

  test('カードは選択に合わない物を隠して全部描き、tags のチップは軸の入口(押すとその値で絞る)', () => {
    const html = planePage(select([[CONTEXT, 'screen']]));
    assert.strictEqual((html.match(/<section class="card"/g) ?? []).length, 6);
    assert.strictEqual((html.match(/<section class="card" id="d\d+" hidden>/g) ?? []).length, 5);
    assert.ok(html.includes('data-axis="tag:owner" data-value="input"'));
    assert.ok(html.includes('context = screen → 定義 1 / 6'));
  });

  test('実体ごとに source のボタン(元の Hy を行番号つきで開閉)と editor で開くボタン(定義の頭の位置)(V12)', () => {
    const shout = planeCards().find((c) => c.definition.name === 'shout');
    assert.ok(shout !== undefined);
    const card = cardHtml(planePage(new Map()), 'shout');
    assert.ok(card.includes(`data-src="${shout.id}">source</button>`));
    assert.ok(card.includes(`data-line="${shout.definition.fullRange.start.line}" data-character="0">editor で開く</button>`));
    assert.ok(card.includes(`<div class="srcbox" id="src-${shout.id}" hidden>`));
    assert.ok(card.includes(`<span class="ln">${shout.firstLine}</span>(defk shout [key]`));
    assert.ok(card.includes(`pkg/plane.hy:${shout.firstLine}`));
  });

  test('defk は引数と答えのチップ・使う effect(絵つき)・説明。構文(def・using)はなぞらない', () => {
    const card = cardHtml(planePage(new Map()), 'fetch-row');
    assert.ok(card.includes('<span class="p"><span class="n">key</span><span class="t">str</span></span>'));
    assert.ok(card.includes('<span class="ret">Row</span>'));
    assert.ok(card.includes('<img src="data:image/svg+xml;fake,ReadInput" alt="">ReadInput'));
    assert.ok(card.includes('鍵の行を読むため。'));
    assert.ok(!/\busing\b|\bdef /.test(card));
  });

  test('入れ子の定義はカードの部品: defrecord の欄はチップ(V13 の一部)', () => {
    const card = cardHtml(planePage(new Map()), 'Row');
    assert.ok(card.includes('<span class="k">欄</span><div><span class="p"><span class="n">key</span></span><span class="p"><span class="n">text</span></span></div>'));
  });

  test('source の文字は HTML として逃がす', () => {
    assert.strictEqual(escapeHtml('(< a "b")'), '(&lt; a &quot;b&quot;)');
  });
});
