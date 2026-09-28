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
  locationOf,
  NO_VALUE,
  parseAxisKey,
  setSearch,
  toggle,
  valuesOf,
  visibleCards,
  type Card,
  type PlaneAxis,
  type Selection
} from '../../read/model';
import { escapeHtml } from '../../read/html';
import { isTallSignature, renderCard, renderPage, renderWorkspaceCards, type CardContext, type WorkspaceState } from '../../read/render';
import { buildCallGraph, buildCallTree, DEFAULT_TREE_DEPTH, indexTypeText, relationOf } from '../../read/tree';
import {
  cardKey,
  DEFAULT_LINE_FIELDS,
  foldAll,
  INITIAL_FOLD,
  loadFold,
  saveFold,
  toggleLineField,
  toggleOpen,
  unfoldAll,
  type FoldState
} from '../../read/fold';
import { LABELS } from '../../read/labels';

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
  const graph = buildCallGraph([planeIndex()]);
  return buildCards({
    definitions: planeIndex().definitions,
    signatures: planeLint().signatures,
    bodies: planeLint().bodies,
    violations,
    lines: planeLines(),
    testsOf: (qn) => relationOf(graph, qn).tests,
    place: 'pkg/plane.hy'
  });
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
    assert.deepStrictEqual(names(cards), ['LIMIT', 'Row', 'fetch-row', 'row-text', 'shout', 'test-row-text-is-the-text', 'describe-row']);
    assert.deepStrictEqual(
      cards.map((c) => c.definition.kind),
      ['variable', 'defrecord', 'defk', 'deff', 'defk', 'deftest', 'defk']
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
    assert.deepStrictEqual(axesOf(planeCards()).map(axisKey), ['kind', 'tag:context', 'tag:role', 'tag:owner', 'effect', 'type', 'tests', 'location']);
  });

  test('索引に新しい key が現れると軸が 1 つ増える(key を固定しない)', () => {
    const cards = planeCards();
    const widened = cards.map((c) =>
      c.definition.name === 'LIMIT' ? { ...c, definition: { ...c.definition, tags: { lifetime: 'session' } } } : c
    );
    assert.deepStrictEqual(axesOf(widened).map(axisKey), ['kind', 'tag:context', 'tag:role', 'tag:lifetime', 'tag:owner', 'effect', 'type', 'tests', 'location']);
    const lifetime: PlaneAxis = { tag: 'tag', key: 'lifetime' };
    assert.deepStrictEqual(names(visibleCards(widened, select([[lifetime, 'session']]))), ['LIMIT']);
  });

  test('その key を持たない定義の値は「(なし)」', () => {
    const limit = planeCards().find((c) => c.definition.name === 'LIMIT');
    assert.ok(limit !== undefined);
    assert.deepStrictEqual(valuesOf(limit, OWNER), [NO_VALUE]);
    assert.deepStrictEqual(valuesOf(limit, KIND), ['variable']);
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
    assert.strictEqual(visibleCards(planeCards(), new Map()).length, 7);
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
    assert.deepStrictEqual(names(visibleCards(planeCards(), select([[KIND, 'defk'], [KIND, 'deff']]))), ['fetch-row', 'row-text', 'shout', 'describe-row']);
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
        ['screen', 2, false],
        [NO_VALUE, 3, false]
      ]
    );
  });
});

/** 見本の頁(effect の絵は名を返すだけの偽物)。既定は全部開いた形(カードの中身を確かめるため)。 */
function planePage(selection: Selection, fold?: FoldState): string {
  const cards = planeCards();
  return renderPage({
    place: 'pkg/plane.hy',
    state: { tag: 'cards', cards, selection },
    glyphs: { effect: (name) => `data:image/svg+xml;fake,${name}` },
    fold: fold ?? unfoldAll(INITIAL_FOLD, cards.map((c) => cardKey(c.definition))),
    graph: buildCallGraph([planeIndex()]),
    tree: undefined,
    coloring: undefined,
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
      fold: INITIAL_FOLD,
      graph: buildCallGraph([]),
      tree: undefined,
      coloring: undefined,
      cspSource: 'vscode-resource:',
      nonce: 'n'
    });
    assert.ok(html.includes('定義を読む面は設定で切ってあります'));
    assert.ok(!html.includes('<section class="card'));
  });

  test('カードは選択に合わない物を隠して全部描き、tags のチップは軸の入口(押すとその値で絞る)', () => {
    const html = planePage(select([[CONTEXT, 'screen']]));
    assert.strictEqual((html.match(/<section class="card[^"]*"/g) ?? []).length, 7);
    assert.strictEqual((html.match(/<section class="card[^"]*" id="d\d+" data-key="[^"]*" data-qn="[^"]*" hidden>/g) ?? []).length, 5);
    assert.ok(html.includes('data-axis="tag:owner" data-value="input"'));
    assert.ok(html.includes(`context = screen → ${LABELS.definitions} 2 / 7`));
  });

  test('実体ごとに source のボタン(元の Hy を行番号つきで開閉)と editor で開くボタン(定義の頭の位置)(V12)', () => {
    const shout = planeCards().find((c) => c.definition.name === 'shout');
    assert.ok(shout !== undefined);
    const card = cardHtml(planePage(new Map()), 'shout');
    assert.ok(card.includes(`data-src="${shout.id}">source</button>`));
    assert.ok(card.includes(`data-line="${shout.definition.fullRange.start.line}" data-character="0">open in editor</button>`));
    assert.ok(card.includes(`<div class="srcbox" id="src-${shout.id}" hidden>`));
    assert.ok(card.includes(`<span class="ln">${shout.firstLine}</span>(defk shout [key]`));
    assert.ok(card.includes(`pkg/plane.hy:${shout.firstLine}`));
  });

  test('defk は引数と答えのチップ・使う effect(絵つき)・説明。構文(def・using)はなぞらない', () => {
    const card = cardHtml(planePage(new Map()), 'fetch-row');
    assert.ok(card.includes('<span class="p"><span class="n">key</span><span class="t">str</span></span>'));
    assert.ok(card.includes('<span class="ret" title="return type">Row</span>'));
    assert.ok(card.includes('<img src="data:image/svg+xml;fake,ReadInput" alt="">ReadInput'));
    assert.ok(card.includes('鍵の行を読むため。'));
    assert.ok(!/\busing\b|\bdef /.test(card));
  });

  test('入れ子の定義はカードの部品: defrecord の欄はチップ(V13 の一部)', () => {
    const card = cardHtml(planePage(new Map()), 'Row');
    assert.ok(card.includes('<span class="k">fields</span><div><span class="p"><span class="n">key</span><span class="t">str</span></span><span class="p"><span class="n">text</span><span class="t">str</span></span></div>'));
  });

  test('source の文字は HTML として逃がす', () => {
    assert.strictEqual(escapeHtml('(< a "b")'), '(&lt; a &quot;b&quot;)');
  });
});

suite('定義を読む面 — 畳む形と切り替え(V14・v4 2 節)', () => {
  const keyOf = (name: string): string => {
    const card = planeCards().find((c) => c.definition.name === name);
    assert.ok(card !== undefined);
    return cardKey(card.definition);
  };

  test('既定: カードは全部畳み、1 行に出すのは args / return type・effects・tags', () => {
    assert.strictEqual(INITIAL_FOLD.open.size, 0);
    assert.deepStrictEqual([...INITIAL_FOLD.line], ['args', 'effects', 'tags']);
    assert.deepStrictEqual(DEFAULT_LINE_FIELDS, ['args', 'effects', 'tags']);
    const html = planePage(new Map(), INITIAL_FOLD);
    assert.strictEqual((html.match(/<section class="card open"/g) ?? []).length, 0);
    assert.ok(html.includes('<body class="show-args show-effects show-tags">'));
  });

  test('1 枚ずつの開閉・全部開く・全部畳む(開閉のボタンは ▸ / ▾)', () => {
    const one = toggleOpen(INITIAL_FOLD, keyOf('shout'));
    assert.ok(cardHtml(planePage(new Map(), one), 'shout').startsWith('class="card open"'));
    assert.ok(cardHtml(planePage(new Map(), one), 'shout').includes('>▾</button>'));
    assert.ok(cardHtml(planePage(new Map(), one), 'fetch-row').includes('>▸</button>'));
    assert.strictEqual(toggleOpen(one, keyOf('shout')).open.size, 0);
    const all = unfoldAll(INITIAL_FOLD, planeCards().map((c) => cardKey(c.definition)));
    assert.strictEqual(all.open.size, 7);
    assert.strictEqual(foldAll(all).open.size, 0);
  });

  test('1 行に出す欄の切り替えは全カード共通(body の class)で、切り替えの欄に同じ状態を出す', () => {
    const withDoc = toggleLineField(toggleLineField(INITIAL_FOLD, 'doc'), 'tags');
    const html = planePage(new Map(), withDoc);
    assert.ok(html.includes('<body class="show-args show-effects show-doc">'));
    assert.ok(html.includes('data-line-field="doc" checked>doc (first line)'));
    assert.ok(html.includes('data-line-field="tags">tags'));
    assert.ok(cardHtml(html, 'fetch-row').includes('<span class="f f-doc">鍵の行を読むため。</span>'));
  });

  test('覚えた形へ書いて読み戻すと同じ。形の違う値・知らない欄は捨てて既定へ(開き直しても同じにするため)', () => {
    const state = toggleLineField(toggleOpen(INITIAL_FOLD, keyOf('shout')), 'location');
    const back = loadFold(JSON.parse(JSON.stringify(saveFold(state))));
    assert.deepStrictEqual([...back.open], [...state.open]);
    assert.deepStrictEqual([...back.line].sort(), [...state.line].sort());
    assert.deepStrictEqual([...loadFold(undefined).line], DEFAULT_LINE_FIELDS);
    assert.deepStrictEqual([...loadFold({ open: [1, 'k'], line: ['tags', 'nope'] }).open], ['k']);
    assert.deepStrictEqual([...loadFold({ open: [], line: ['tags', 'nope'] }).line], ['tags']);
  });

  test('callers / tests の数は索引の呼び出しを呼び先の完全修飾名で逆に引く(deftest は tests に数える)', () => {
    const rowText = planeCards().find((c) => c.definition.name === 'row-text');
    assert.ok(rowText !== undefined);
    const count = relationOf(buildCallGraph([planeIndex()]), rowText.definition.qualifiedName);
    // row-text を呼ぶのは shout・describe-row(定義)と test-row-text-is-the-text(deftest)。row-text が呼ぶ Hy の定義は無い
    assert.deepStrictEqual(count, { callers: 2, callees: 0, tests: 1 });
    const html = planePage(new Map(), toggleLineField(INITIAL_FOLD, 'relations'));
    assert.ok(cardHtml(html, 'row-text').includes('callers <b>2</b> · tests <b>1</b>'));
  });
});

suite('定義を読む面 — ラベルの表(V15・v5 2 節)', () => {
  test('面のラベルは v5 の表の英語(args / return type ほか)', () => {
    assert.strictEqual(LABELS.argsReturnType, 'args / return type');
    assert.strictEqual(LABELS.returnType, 'return type');
    assert.deepStrictEqual(
      [LABELS.showInLine, LABELS.effects, LABELS.docFirstLine, LABELS.callersTests, LABELS.location, LABELS.foldAll, LABELS.unfoldAll],
      ['show in line', 'effects', 'doc (first line)', 'callers / tests', 'location', 'fold all', 'unfold all']
    );
    assert.deepStrictEqual(
      [LABELS.source, LABELS.openInEditor, LABELS.handles, LABELS.handledBy, LABELS.callers, LABELS.callees, LABELS.tests, LABELS.types],
      ['source', 'open in editor', 'handles', 'handled by:', 'callers', 'callees', 'tests', 'types']
    );
    assert.deepStrictEqual([LABELS.axis, LABELS.hasTests, LABELS.noTests], ['axis', 'has tests', 'no tests']);
  });

  test('頁に旧の日本語のラベルが残っていない(説明と本体は source のまま日本語)', () => {
    const html = planePage(new Map());
    for (const old of ['引数', '答え', '使う effect', 'editor で開く', '軸:', '絞り込みを外す', '全部畳む', '置き場']) {
      assert.ok(!html.includes(old), `旧のラベル ${old} が残っている`);
    }
    assert.ok(html.includes('鍵の行を読むため。'));
  });
});

suite('定義を読む面 — .hy の既定の開き方(V16・v5)', () => {
  test('package.json の custom editor は .hy に既定で開く(priority default)', () => {
    const manifest: unknown = JSON.parse(fs.readFileSync(path.join(__dirname, '..', '..', '..', 'package.json'), 'utf8'));
    const editors = (manifest as { contributes: { customEditors: Array<{ viewType: string; priority: string; selector: Array<{ filenamePattern: string }> }> } })
      .contributes.customEditors;
    const plane = editors.find((e) => e.viewType === 'doeff-runner.readingPlane');
    assert.ok(plane !== undefined);
    assert.strictEqual(plane.priority, 'default');
    assert.deepStrictEqual(plane.selector, [{ filenamePattern: '*.hy' }]);
  });
});

suite('定義を読む面 — 長い signature の縦の表(V17・v6 2 節)', () => {
  const byName = (name: string): Card => {
    const card = planeCards().find((c) => c.definition.name === name);
    assert.ok(card !== undefined);
    return card;
  };

  test('引数 4 つ以上か型の文字の合計が 60 を超える見出しだけ縦の表(それ以外は 1 行のチップ)', () => {
    const tall = byName('describe-row').signature;
    const short = byName('fetch-row').signature;
    assert.ok(tall !== undefined && short !== undefined);
    assert.ok(isTallSignature(tall));
    assert.ok(!isTallSignature(short));
  });

  test('縦の表: 左に名・右に型のチップ(union は候補ごと・None は破線)・最後の行に return type の帯', () => {
    const card = cardHtml(planePage(new Map()), 'describe-row');
    assert.ok(card.includes('<div class="sig2"><div class="lab">args</div><span class="n">row</span><span class="tc"><span class="">Row</span><i>|</i><span class="none">None</span></span>'));
    assert.ok(card.includes('<div class="rt"><span class="k">return type</span><span class="tc"><span class="">str</span><i>|</i><span class="none">None</span></span></div>'));
    assert.ok(!cardHtml(planePage(new Map()), 'fetch-row').includes('class="sig2"'));
  });

  test('畳んだ 1 行: 引数 4 つ以上は名だけ(型は hover)+ return type、3 つ以下は名と型', () => {
    const html = planePage(new Map(), INITIAL_FOLD);
    assert.ok(cardHtml(html, 'describe-row').includes('(row, prefix, suffix, width) → <span class="r">str | None</span>'));
    assert.ok(cardHtml(html, 'describe-row').includes('title="row: Row | None\nprefix: str\nsuffix: str\nwidth: int"'));
    assert.ok(cardHtml(html, 'fetch-row').includes('(key: <span class="t">str</span>) → <span class="r">Row</span>'));
  });
});

suite('定義を読む面 — 呼び出しの依存の木(V19・v7 3 節)', () => {
  /** plane.hy と tree.hy の 2 file の索引の実出力(file を跨ぐ呼び・重複・循環・deftest を含む)。 */
  const graph = (): ReturnType<typeof buildCallGraph> => {
    const parsed = parseHyIndexJson(fs.readFileSync(path.join(FIXTURES, 'tree-index.json'), 'utf8'));
    if (parsed.tag !== 'ok') {
      assert.fail(`木の fixture を読めない: ${parsed.reason}`);
    }
    return buildCallGraph(parsed.document.files);
  };
  const names = (node: { readonly definition: { readonly name: string }; readonly children: readonly unknown[] }): unknown => ({
    name: node.definition.name,
    children: (node.children as Array<typeof node>).map(names)
  });

  test('callees ↓: 根から呼び先を辿る(file を跨ぐ)。同じ実体の 2 度目は ↺ で開かない。根の下に effect の和と節の数', () => {
    const tree = buildCallTree(graph(), { root: 'pkg.tree.show_both', direction: 'callees', depth: DEFAULT_TREE_DEPTH, showTests: false });
    assert.ok(tree !== undefined);
    assert.deepStrictEqual(names(tree.root), {
      name: 'show-both',
      children: [
        { name: 'shout', children: [{ name: 'fetch-row', children: [] }, { name: 'row-text', children: [] }] },
        { name: 'describe-row', children: [{ name: 'row-text', children: [] }] }
      ]
    });
    const again = tree.root.children[1].children[0];
    assert.strictEqual(again.seen, 'repeat');
    assert.deepStrictEqual(tree.effects, ['ReadInput']);
    assert.deepStrictEqual([tree.nodes, tree.repeats, tree.cycles], [6, 1, 0]);
  });

  test('callers ↑: 根を呼ぶ物を辿る。deftest は既定で隠し、切り替えで出す', () => {
    const hidden = buildCallTree(graph(), { root: 'pkg.plane.row_text', direction: 'callers', depth: DEFAULT_TREE_DEPTH, showTests: false });
    assert.ok(hidden !== undefined);
    assert.deepStrictEqual(names(hidden.root), {
      name: 'row-text',
      children: [
        { name: 'shout', children: [{ name: 'show-both', children: [] }] },
        { name: 'describe-row', children: [{ name: 'show-both', children: [] }] }
      ]
    });
    const shown = buildCallTree(graph(), { root: 'pkg.plane.row_text', direction: 'callers', depth: DEFAULT_TREE_DEPTH, showTests: true });
    assert.ok(shown !== undefined);
    assert.ok(shown.root.children.some((c) => c.definition.kind === 'deftest'));
  });

  test('循環は祖先に同じ実体がある 2 度目(cycle)として開かない', () => {
    const tree = buildCallTree(graph(), { root: 'pkg.tree.ping', direction: 'callees', depth: DEFAULT_TREE_DEPTH, showTests: false });
    assert.ok(tree !== undefined);
    assert.deepStrictEqual(names(tree.root), { name: 'ping', children: [{ name: 'pong', children: [{ name: 'ping', children: [] }] }] });
    assert.strictEqual(tree.root.children[0].children[0].seen, 'cycle');
    assert.strictEqual(tree.cycles, 1);
  });

  test('深さ d = 根から d 段下までの節を開き、その 1 段下は truncated(+ で 1 段ずつ広げる)', () => {
    const shallow = buildCallTree(graph(), { root: 'pkg.tree.show_both', direction: 'callees', depth: 0, showTests: false });
    assert.ok(shallow !== undefined);
    assert.deepStrictEqual(shallow.root.children.map((c) => [c.definition.name, c.truncated, c.children.length]), [
      ['shout', true, 0],
      ['describe-row', true, 0]
    ]);
    const deeper = buildCallTree(graph(), { root: 'pkg.tree.show_both', direction: 'callees', depth: 1, showTests: false });
    assert.ok(deeper !== undefined);
    assert.strictEqual(deeper.root.children[0].children.length, 2);
  });

  test('木の欄: 向き・深さ・tests の切り替え・節は畳んだ 1 行と同じ部品・2 度目は ↺・名を押すとカードへ', () => {
    const cards = planeCards();
    const tree = buildCallTree(graph(), { root: 'pkg.tree.show_both', direction: 'callees', depth: DEFAULT_TREE_DEPTH, showTests: false });
    assert.ok(tree !== undefined);
    const html = renderPage({
      place: 'pkg/plane.hy',
      state: { tag: 'cards', cards, selection: new Map() },
      glyphs: { effect: () => undefined },
      fold: INITIAL_FOLD,
      graph: graph(),
      tree: { tree, showTests: false },
      coloring: undefined,
      cspSource: 'vscode-resource:',
      nonce: 'n'
    });
    assert.ok(html.includes('<b>call tree</b><span class="k">root</span><b class="mono">show-both</b>'));
    assert.ok(html.includes('<button class="btn on" data-tree-dir="callees">callees ↓</button>'));
    assert.ok(html.includes('<span class="depth">3</span><button class="btn" id="tree-more">+</button>'));
    assert.ok(html.includes('<input type="checkbox" id="tree-tests">tests'));
    assert.ok(html.includes('nodes <b>6</b>(repeats 1 · cycles 0)'));
    assert.ok(html.includes('<button class="tname" data-reveal="pkg.plane.shout">shout</button>'));
    assert.ok(html.includes('↺ seen above'));
    // 開いている file の定義(shout)は linter の見出しで、他の file の定義(show-both)は索引の型の綴りで 1 行を描く
    assert.ok(html.includes('(key: <span class="t">str</span>) → <span class="r">str</span>'));
    assert.ok(html.includes('(key: <span class="t">str</span>, row: <span class="t">Row</span>) → <span class="r">str</span>'));
    // 入口: カードの帯の callers / callees と、左の欄の根の選び
    assert.ok(cardHtml(html, 'row-text').includes('data-tree-root="pkg.plane.row_text" data-tree-dir="callers">callers <b>2</b></button>'));
    assert.ok(html.includes('<select id="tree-root"><option value="">pick a root</option>'));
  });

  test('索引の型の綴り: 名だけの union は A | B に、他は書かれたまま', () => {
    assert.strictEqual(indexTypeText({ text: '(| Row None)', names: [] }), 'Row | None');
    assert.strictEqual(indexTypeText({ text: 'str', names: [] }), 'str');
    assert.strictEqual(indexTypeText({ text: '(get dict str int)', names: [] }), '(get dict str int)');
    assert.strictEqual(indexTypeText(null), '?');
  });
});

suite('定義を読む面 — 本体の文字(V6・V11 の一部・U5)', () => {
  test('本体の行は linter の bodies を描く: 行番号 = source の行・val / ⇐ の束縛の行は薄い背景・呼びは f(a)', () => {
    const html = planePage(new Map());
    const fetch = cardHtml(html, 'fetch-row');
    assert.ok(fetch.includes('<div class="bl bound"><span class="ln">17</span><span class="kw">val</span> <span class="b">Row</span> row <span class="arrow-bind">⇐</span> '));
    assert.ok(planeLines()[16].includes('(<- row Row (ReadInput key))'));
    const shout = cardHtml(html, 'shout');
    assert.ok(shout.includes('<span class="fn">fetch-row</span>(key)'));
  });

  test('制御の形(U3): when は語 + 字下げの段(linter の bodies の depth)', () => {
    const describe = cardHtml(planePage(new Map()), 'describe-row');
    assert.ok(describe.includes('<span class="ln">43</span><span class="kw">when</span> row is None'));
    assert.ok(describe.includes('<span class="ln">44</span>  <span class="kw">return</span> None'));
  });

  test('字下げ = "  " × 段 + " " × pad(腕の中の match の中身を match の語の列に揃える行)', () => {
    const cards = planeCards();
    const describe = cards.find((c) => c.definition.name === 'describe-row');
    assert.ok(describe !== undefined && describe.body !== undefined);
    const padded = { ...describe, body: { ...describe.body, lines: describe.body.lines.map((l, i) => (i === 1 ? { ...l, pad: 3 } : l)) } };
    const html = renderPage({
      place: 'pkg/plane.hy',
      state: { tag: 'cards', cards: cards.map((c) => (c === describe ? padded : c)), selection: new Map() },
      glyphs: { effect: () => undefined },
      fold: unfoldAll(INITIAL_FOLD, cards.map((c) => cardKey(c.definition))),
      graph: buildCallGraph([planeIndex()]),
      tree: undefined,
      coloring: undefined,
      cspSource: 'vscode-resource:',
      nonce: 'n'
    });
    assert.ok(cardHtml(html, 'describe-row').includes('<span class="ln">44</span>     <span class="kw">return</span> None'));
  });

  test('表に無い form は推測で描かず、目印つきの lisp のまま(v1 制約 2)', () => {
    const lisp = { text: '(cond [a b])', role: 'lisp' as const, range: null, effect: null, definition: null };
    const cards = planeCards();
    const describe = cards.find((c) => c.definition.name === 'describe-row');
    assert.ok(describe !== undefined && describe.body !== undefined);
    const marked = { ...describe, body: { ...describe.body, lines: [{ ...describe.body.lines[0], segments: [lisp] }] } };
    const html = renderPage({
      place: 'pkg/plane.hy',
      state: { tag: 'cards', cards: cards.map((c) => (c === describe ? marked : c)), selection: new Map() },
      glyphs: { effect: () => undefined },
      fold: unfoldAll(INITIAL_FOLD, cards.map((c) => cardKey(c.definition))),
      graph: buildCallGraph([planeIndex()]),
      tree: undefined,
      coloring: undefined,
      cspSource: 'vscode-resource:',
      nonce: 'n'
    });
    assert.ok(cardHtml(html, 'describe-row').includes(`<span class="lisp" title="${LABELS.lispAsIs}">(cond [a b])</span>`));
  });

  test('本体を持たない実体(defrecord・最上位の変数)には本体の欄を出さない', () => {
    const html = planePage(new Map());
    assert.ok(!cardHtml(html, 'Row').includes('<div class="body">'));
    assert.ok(!cardHtml(html, 'LIMIT').includes('<div class="body">'));
  });
});

suite('定義を読む面 — 違反を本体の行へ(V7・U5)', () => {
  test('linter の違反は、その source の行を描く本体の行に規則の名で出る(カードの頭の数も残る)', () => {
    const fetch = planeCards().find((c) => c.definition.name === 'fetch-row');
    assert.ok(fetch !== undefined);
    const onLine = { ...violationAt(fetch.definition, 'DOEFF142'), range: { start: { line: 16, character: 2 }, end: { line: 16, character: 30 } } };
    const cards = planeCards([onLine]);
    const html = renderPage({
      place: 'pkg/plane.hy',
      state: { tag: 'cards', cards, selection: new Map() },
      glyphs: { effect: () => undefined },
      fold: unfoldAll(INITIAL_FOLD, cards.map((c) => cardKey(c.definition))),
      graph: buildCallGraph([planeIndex()]),
      tree: undefined,
      coloring: undefined,
      cspSource: 'vscode-resource:',
      nonce: 'n'
    });
    const card = cardHtml(html, 'fetch-row');
    assert.ok(/<span class="ln">17<\/span>.*<span class="viol viol-major" title="DOEFF142: テストの違反">DOEFF142<\/span><\/div>/.test(card));
    assert.ok(!/<span class="ln">18<\/span>[^\n]*DOEFF142/.test(card.split('<span class="ln">18</span>')[1]?.split('</div>')[0] ?? ''));
  });
});

suite('定義を読む面 — 実体の種類ごとの欄と帯(V13・v2 2.1 節)', () => {
  /** entities.hy に hy-index 版 5 を当てた実出力のカード(linter の見出しは無し — 索引の欄だけで描ける物を確かめる)。 */
  const entitiesPage = (): { readonly html: string; readonly lines: string } => {
    const parsed = parseHyIndexJson(fs.readFileSync(path.join(FIXTURES, 'entities-index.json'), 'utf8'));
    if (parsed.tag !== 'ok') {
      assert.fail(`実体の fixture を読めない: ${parsed.reason}`);
    }
    const file = parsed.document.files[0];
    const cards = buildCards({
      definitions: file.definitions,
      signatures: [],
      bodies: [],
      violations: [],
      lines: fs.readFileSync(path.join(FIXTURES, 'entities.hy'), 'utf8').split(/\r?\n/),
      testsOf: () => 0,
      place: 'pkg/plane.hy'
    });
    const page = (fold: FoldState): string =>
      renderPage({
        place: 'pkg/entities.hy',
        state: { tag: 'cards', cards, selection: new Map() },
        glyphs: { effect: () => undefined },
        fold,
        graph: buildCallGraph(parsed.document.files),
        tree: undefined,
        coloring: undefined,
        cspSource: 'vscode-resource:',
        nonce: 'n'
      });
    return { html: page(unfoldAll(INITIAL_FOLD, cards.map((c) => cardKey(c.definition)))), lines: page(INITIAL_FOLD) };
  };

  test('defeffect: 欄のチップ → 答えの型。帯は used by(押すと木)と handlers', () => {
    const { html, lines } = entitiesPage();
    const card = cardHtml(html, 'ReadSlot');
    assert.ok(card.includes('<div class="sig"><span class="p"><span class="n">key</span><span class="t">str</span></span><span class="arrow">→</span><span class="ret">Slot | None</span></div>'));
    assert.ok(card.includes('data-tree-dir="callers">used by <b>1</b></button>: <button class="tname-sm" data-reveal="pkg.entities.slot_size">slot-size</button></span>'));
    assert.ok(card.includes('<span class="relgroup">handlers <b>1</b>: <button class="tname-sm" data-reveal="pkg.entities.slot_store">slot-store</button></span>'));
    assert.ok(cardHtml(lines, 'ReadSlot').includes('(key: <span class="t">str</span>) → <span class="r">Slot | None</span>'));
  });

  test('defrecord: 欄のチップ(名と型)。帯は returned by / accepted by', () => {
    const { html, lines } = entitiesPage();
    const card = cardHtml(html, 'Slot');
    assert.ok(card.includes('<span class="k">fields</span><div><span class="p"><span class="n">key</span><span class="t">str</span></span><span class="p"><span class="n">size</span><span class="t">int</span></span></div>'));
    assert.ok(card.includes('<span class="relgroup">returned by <b>0</b></span><span class="relgroup">accepted by <b>0</b></span>'));
    assert.ok(cardHtml(lines, 'Slot').includes('(key: <span class="t">str</span>, size: <span class="t">int</span>)'));
  });

  test('defenum: 値のチップ。1 行は A | B', () => {
    const { html, lines } = entitiesPage();
    assert.ok(cardHtml(html, 'Tone').includes('<span class="k">values</span><div><span class="p"><span class="n">LOUD</span></span><span class="p"><span class="n">QUIET</span></span></div>'));
    assert.ok(cardHtml(lines, 'Tone').includes('<span class="f f-args">LOUD | QUIET</span>'));
  });

  test('defhandler: 解く effect(handles)と使う effect。帯は installed at', () => {
    const { html, lines } = entitiesPage();
    const card = cardHtml(html, 'slot-store');
    assert.ok(card.includes('<span class="k">handles</span><div><span class="eff">ReadSlot</span></div>'));
    assert.ok(card.includes('<span class="k">effects</span><div><span class="eff">ReadRow</span></div>'));
    assert.ok(card.includes('data-tree-dir="callers">installed at <b>0</b></button>'));
    assert.ok(cardHtml(lines, 'slot-store').includes('<span class="f f-args">handles: ReadSlot</span>'));
  });

  test('契約の欄は型でない述語だけ(型の注記は引数と答えへ溶ける)。無い実体には出さない', () => {
    const { html } = entitiesPage();
    assert.ok(cardHtml(html, 'slot-size').includes('<span class="k">contract</span><div><code>pre: (&gt; limit 0)</code></div>'));
    assert.ok(!cardHtml(html, 'ReadSlot').includes('<span class="k">contract</span>'));
  });
});

suite('定義を読む面 — effect・type・tests・location の軸(V2・V3・U10)', () => {
  const EFFECT: PlaneAxis = { tag: 'effect' };
  const TYPE: PlaneAxis = { tag: 'type' };
  const TESTS: PlaneAxis = { tag: 'tests' };
  const LOCATION: PlaneAxis = { tag: 'location' };

  test('effect の軸 = 宣言した effect(1 つの定義が複数の値を持てる)', () => {
    assert.deepStrictEqual(names(visibleCards(planeCards(), select([[EFFECT, 'ReadInput']]))), ['fetch-row', 'shout']);
  });

  test('type の軸 = 引数と答えに書いた repo の中の型(組み込みの str などは数えない)', () => {
    assert.deepStrictEqual(names(visibleCards(planeCards(), select([[TYPE, 'Row']]))), ['fetch-row', 'row-text', 'describe-row']);
  });

  test('tests の軸 = その定義を呼ぶ deftest の有無(呼び出しの表から)', () => {
    assert.deepStrictEqual(names(visibleCards(planeCards(), select([[TESTS, LABELS.hasTests]]))), ['Row', 'row-text']);
  });

  test('location の軸 = file の dir の末尾 2 段', () => {
    assert.deepStrictEqual(valuesOf(planeCards()[0], LOCATION), ['pkg']);
    assert.strictEqual(locationOf('controllers/messaging/core/conversation_input.hy'), 'messaging/core');
    assert.strictEqual(locationOf('top.hy'), '.');
  });

  test('新しい軸も tag の軸と交差する: effect = ReadInput × type = Row → fetch-row', () => {
    assert.deepStrictEqual(names(visibleCards(planeCards(), select([[EFFECT, 'ReadInput'], [TYPE, 'Row']]))), ['fetch-row']);
  });

  test('defhandler は解く effect、defeffect は自分の名を effect の軸の値に持つ(その effect を解く handler・その effect 自身へ入れる)', () => {
    const parsed = parseHyIndexJson(fs.readFileSync(path.join(FIXTURES, 'entities-index.json'), 'utf8'));
    if (parsed.tag !== 'ok') {
      assert.fail(parsed.reason);
    }
    const cards = buildCards({
      definitions: parsed.document.files[0].definitions,
      signatures: [],
      bodies: [],
      violations: [],
      lines: fs.readFileSync(path.join(FIXTURES, 'entities.hy'), 'utf8').split(/\r?\n/),
      testsOf: () => 0,
      place: 'pkg/plane.hy'
    });
    assert.deepStrictEqual(names(visibleCards(cards, select([[EFFECT, 'ReadSlot']]))), ['ReadSlot', 'slot-store', 'slot-size']);
  });

  test('左の欄の見出しは axis: effect / type / tests / location(v5 の表)', () => {
    const html = planePage(new Map());
    for (const title of ['effect', 'type', 'tests', 'location']) {
      assert.ok(html.includes(`<h2>axis: ${title}</h2>`), title);
    }
    assert.ok(html.includes('data-axis="tests" data-value="has tests">has tests<small>2</small>'));
  });
});

suite('定義を読む面 — repo 全体の入口(U9)', () => {
  /** plane.hy と tree.hy の索引から、repo 全体の面と同じ作り方(source を持たない索引だけのカード・id は file の順で)でカードを作る。 */
  const workspaceCards = (): { readonly cards: Card[]; readonly graph: ReturnType<typeof buildCallGraph> } => {
    const parsed = parseHyIndexJson(fs.readFileSync(path.join(FIXTURES, 'tree-index.json'), 'utf8'));
    if (parsed.tag !== 'ok') {
      assert.fail(parsed.reason);
    }
    const graph = buildCallGraph(parsed.document.files);
    const cards = parsed.document.files.flatMap((file, index) =>
      buildCards({
        definitions: file.definitions,
        signatures: [],
        bodies: [],
        violations: [],
        lines: [],
        testsOf: (qn) => relationOf(graph, qn).tests,
        place: file.path.replace('/repo/', '')
      }).map((card) => ({ ...card, id: `f${index}-${card.id}` }))
    );
    return { cards, graph };
  };
  const ctxOf = (graph: ReturnType<typeof buildCallGraph>, fold: FoldState): CardContext => ({
    glyphs: { effect: () => undefined },
    fold,
    graph,
    coloringOf: () => undefined
  });
  const state = (cards: readonly Card[], selection: Selection, pinned: readonly string[], limit: number, facetLimit: number): WorkspaceState => ({
    tag: 'workspace',
    cards,
    selection,
    pinned,
    limit,
    facetLimit,
    coloringOf: () => undefined
  });

  test('全 file の定義で軸と数を作り、置き場の軸は file ごとの dir になる', () => {
    const { cards, graph } = workspaceCards();
    const listed = renderWorkspaceCards(state(cards, new Map(), [], 200, 40), ctxOf(graph, INITIAL_FOLD));
    assert.ok(listed.summary.startsWith(`${LABELS.definitions} ${cards.length}`));
    assert.ok(listed.axes.includes('data-axis="location" data-value="pkg">pkg'));
    assert.ok(new Set(cards.map((c) => c.id)).size === cards.length, 'id が file を跨いで重ならない');
  });

  test('索引だけのカードは source を持たず data-lazy(開く・source を押すとその file を読み込む)', () => {
    const { cards, graph } = workspaceCards();
    const html = renderWorkspaceCards(state(cards, new Map(), [], 200, 40), ctxOf(graph, INITIAL_FOLD)).html;
    assert.ok(/<section class="card" id="f1-d\d+" data-key="[^"]*" data-qn="pkg\.tree\.show_both" data-lazy>/.test(html));
  });

  test('積んだカードは上に「stacked」として残り、絞った先から外れても消えない', () => {
    const { cards, graph } = workspaceCards();
    const onlyTree = select([[{ tag: 'tag', key: 'role' }, 'program']]);
    const html = renderWorkspaceCards(state(cards, onlyTree, ['pkg.plane.row_text'], 200, 40), ctxOf(graph, INITIAL_FOLD)).html;
    const stackAt = html.indexOf(`<div class="stackhead">${LABELS.stacked}</div>`);
    const pinnedAt = html.indexOf('data-qn="pkg.plane.row_text"');
    const matchesAt = html.indexOf(`<div class="stackhead">${LABELS.matches}</div>`);
    assert.ok(stackAt >= 0 && stackAt < pinnedAt && pinnedAt < matchesAt);
    assert.ok(html.slice(matchesAt).includes('data-qn="pkg.tree.show_both"'));
  });

  test('絞った先は上限まで描き、残りは「showing N / M」(repo の定義は 1 万近いので全部は描かない)', () => {
    const { cards, graph } = workspaceCards();
    const html = renderWorkspaceCards(state(cards, new Map(), [], 3, 40), ctxOf(graph, INITIAL_FOLD)).html;
    assert.strictEqual((html.match(/<section class="card/g) ?? []).length, 3);
    assert.ok(html.includes(`${LABELS.showing} 3 / ${cards.length} — ${LABELS.narrowWithAxes}`));
  });

  test('値の多い軸は上位の値と選んだ値だけ見せ、残りは +N', () => {
    const { cards, graph } = workspaceCards();
    const html = renderWorkspaceCards(state(cards, select([[KIND, 'deftest']]), [], 200, 1), ctxOf(graph, INITIAL_FOLD)).axes;
    const kindAxis = html.split('<h2>')[1];
    assert.ok(kindAxis.includes('data-value="deftest"'), '選んだ値は上限を越えても見せる');
    assert.ok(/<span class="more">\+\d+<\/span>/.test(kindAxis));
  });

  test('linter の見出しが無い関数は索引の型と宣言した effect で描く(他の file の定義)', () => {
    const parsed = parseHyIndexJson(fs.readFileSync(path.join(FIXTURES, 'entities-index.json'), 'utf8'));
    if (parsed.tag !== 'ok') {
      assert.fail(parsed.reason);
    }
    const cards = buildCards({
      definitions: parsed.document.files[0].definitions,
      signatures: [],
      bodies: [],
      violations: [],
      lines: [],
      testsOf: () => 0,
      place: 'pkg/entities.hy'
    });
    const graph = buildCallGraph(parsed.document.files);
    const size = cards.find((c) => c.definition.name === 'slot-size');
    assert.ok(size !== undefined);
    const open = renderCard(size, ctxOf(graph, unfoldAll(INITIAL_FOLD, [cardKey(size.definition)])), false);
    assert.ok(open.includes('<div class="sig"><span class="p"><span class="n">key</span><span class="t">str</span></span><span class="p"><span class="n">limit</span><span class="t">int</span></span><span class="arrow">→</span><span class="ret">int</span></div>'));
    assert.ok(open.includes('<span class="k">effects</span><div><span class="eff">ReadSlot</span></div>'));
    assert.ok(open.includes('(key: <span class="t">str</span>, limit: <span class="t">int</span>) → <span class="r">int</span>'));
    assert.ok(open.includes('<span class="f f-effects"><span class="eff">ReadSlot</span></span>'));
  });
});

suite('定義を読む面 — 関係の帯の名と名の検索(U6・U10)', () => {
  test('帯は数(押すと木)と関係の名(押すとそのカードへ)。tests の名と types の数も', () => {
    const card = cardHtml(planePage(new Map()), 'row-text');
    assert.ok(card.includes('data-tree-dir="callers">callers <b>2</b></button>: <button class="tname-sm" data-reveal="pkg.plane.shout">shout</button> · <button class="tname-sm" data-reveal="pkg.plane.describe_row">describe-row</button></span>'));
    assert.ok(card.includes('<span class="relgroup">tests <b>1</b>: <button class="tname-sm" data-reveal="pkg.plane.test_row_text_is_the_text">test-row-text-is-the-text</button></span>'));
    assert.ok(card.includes('<span class="relgroup" title="Row">types <b>1</b></span>'));
  });

  test('名の検索は名と完全修飾名の一部(大文字と小文字を分けない)で絞り、軸の積に乗る', () => {
    const byName = setSearch(new Map(), 'ROW');
    assert.deepStrictEqual(names(visibleCards(planeCards(), byName)), ['Row', 'fetch-row', 'row-text', 'test-row-text-is-the-text', 'describe-row']);
    const withKind = toggle(byName, KIND, 'defk');
    assert.deepStrictEqual(names(visibleCards(planeCards(), withKind)), ['fetch-row', 'describe-row']);
    assert.strictEqual(setSearch(byName, '  ').size, 0);
  });

  test('検索の欄は今の検索の文字を持って描かれる(頁を描き直しても消えない)', () => {
    assert.ok(planePage(setSearch(new Map(), 'row')).includes('<input id="search" type="search" placeholder="search names" value="row">'));
  });
});
