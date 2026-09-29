import * as assert from 'assert';
import * as fs from 'fs';
import * as path from 'path';
import { parseHyIndexJson, type HyFileIndex } from '../../hy/contract';
import { parseLintJson, type LintReport } from '../../lint/contract';
import { cardKey, INITIAL_FOLD, unfoldAll } from '../../read/fold';
import { buildCards } from '../../read/model';
import { renderPage } from '../../read/render';
import { destinationOf, entityLink, resolveEntity } from '../../read/resolve';
import { buildCallGraph, relationOf, type CallGraph } from '../../read/tree';

// v12(operator 2026-09-29 "jump to definition by clicking each entities like effect/class/record etc from reading view and the
// source view... every location of plugin")— 実体の名 → 定義の解決を 1 つの関数に集め、カードの頭のチップと本体の文字の名を押せる
// ようにした(agora-redesign #910 U19a・#1203)。材料は model.test.ts と同じ test-fixtures/read の実出力。

const FIXTURES = path.join(__dirname, '..', '..', '..', 'test-fixtures', 'read');

/** 索引の実出力の全 file を契約の入口で読む。 */
function indexFiles(name: string): readonly HyFileIndex[] {
  const parsed = parseHyIndexJson(fs.readFileSync(path.join(FIXTURES, name), 'utf8'));
  if (parsed.tag !== 'ok') {
    assert.fail(`索引の fixture を読めない: ${parsed.reason}`);
  }
  return parsed.document.files;
}

/** linter の実出力(plane.hy)を契約の入口で読む。 */
function planeLint(): LintReport {
  const parsed = parseLintJson(fs.readFileSync(path.join(FIXTURES, 'plane-lint.json'), 'utf8'));
  if (parsed.tag !== 'ok') {
    assert.fail(`linter の fixture を読めない: ${parsed.reason}`);
  }
  return parsed.report;
}

/** plane.hy を全部開いて描いた頁と、その呼び出しの表。 */
function planePage(): { readonly html: string; readonly graph: CallGraph } {
  const files = indexFiles('plane-index.json');
  const file = files.find((f) => f.path === '/repo/pkg/plane.hy');
  assert.ok(file !== undefined);
  const graph = buildCallGraph(files);
  const lint = planeLint();
  const cards = buildCards({
    definitions: file.definitions,
    signatures: lint.signatures,
    bodies: lint.bodies,
    bindings: lint.bindings,
    violations: [],
    lines: fs.readFileSync(path.join(FIXTURES, 'plane.hy'), 'utf8').split(/\r?\n/),
    testsOf: (qn) => relationOf(graph, qn).tests,
    place: 'pkg/plane.hy'
  });
  const html = renderPage({
    place: 'pkg/plane.hy',
    state: { tag: 'cards', cards, selection: new Map() },
    glyphs: { effect: () => undefined },
    fold: unfoldAll(INITIAL_FOLD, cards.map((c) => cardKey(c.definition))),
    graph,
    tree: undefined,
    coloring: undefined,
    cspSource: 'vscode-resource:',
    nonce: 'n'
  });
  return { html, graph };
}

/** 頁の中の 1 枚のカードの HTML。 */
function cardHtml(html: string, name: string): string {
  const found = html.split('<section ').find((part) => part.includes(`<span class="name">${name}</span>`));
  assert.ok(found !== undefined, `カード ${name} が無い`);
  return found;
}

suite('定義を読む面 — 名 → 定義の解決(v12・U19a)', () => {
  test('linter の位置(path と名の範囲の頭)は索引の定義に当たり、null(組み込み・解けない名)は押せない', () => {
    const { graph } = planePage();
    const row = { path: '/repo/pkg/plane.hy', range: { start: { line: 6, character: 11 }, end: { line: 6, character: 14 } } };
    assert.deepStrictEqual(resolveEntity({ tag: 'location', location: row }, graph), ['pkg.plane.Row']);
    assert.deepStrictEqual(resolveEntity({ tag: 'location', location: null }, graph), []);
    assert.deepStrictEqual(resolveEntity({ tag: 'location', location: { ...row, path: '/elsewhere.hy' } }, graph), []);
  });

  test('索引の target は定義にある時だけ(Python の関数など索引に無い完全修飾名は押せない)', () => {
    const { graph } = planePage();
    assert.deepStrictEqual(resolveEntity({ tag: 'target', target: 'pkg.plane.fetch_row' }, graph), ['pkg.plane.fetch_row']);
    assert.deepStrictEqual(resolveEntity({ tag: 'target', target: 'builtins.str' }, graph), []);
    assert.deepStrictEqual(resolveEntity({ tag: 'target', target: null }, graph), []);
  });

  test('名だけで引く時、同名が複数なら候補を全部返す(押した時に選ばせる)・dotted な名は最後の区切りで引く', () => {
    const graph = buildCallGraph(indexFiles('classes-index.json'));
    assert.deepStrictEqual([...resolveEntity({ tag: 'name', name: 'placed-version' }, graph)].sort(), ['pkg.classes.placed_version', 'pkg.classes_other.placed_version']);
    assert.deepStrictEqual(resolveEntity({ tag: 'name', name: 'models.PlacedVersion' }, graph), ['pkg.classes.PlacedVersion']);
    assert.deepStrictEqual(resolveEntity({ tag: 'name', name: 'str' }, graph), []);
  });

  test('effect の名だけで引く時は defeffect に限る(handler の同名の effect 節には当てない)', () => {
    const graph = buildCallGraph(indexFiles('entities-index.json'));
    assert.deepStrictEqual(resolveEntity({ tag: 'effect-name', name: 'ReadSlot' }, graph), ['pkg.entities.ReadSlot']);
  });

  test('entityLink: 候補があれば data-reveal(空白区切り)、無ければ今の見た目のまま', () => {
    assert.strictEqual(entityLink('Row', ['pkg.plane.Row'], 't'), '<span class="t ent" data-reveal="pkg.plane.Row">Row</span>');
    assert.strictEqual(entityLink('f', ['a.f', 'b.f']), '<span class="ent" data-reveal="a.f b.f">f</span>');
    assert.strictEqual(entityLink('str', [], 't'), '<span class="t">str</span>');
    assert.strictEqual(entityLink('str', []), 'str');
  });
});

suite('定義を読む面 — チップと本体の名を押せる(v12・U19a)', () => {
  test('引数と答えの型のチップ: repo の型(Row)は押せ、組み込みの型(str)は押せない', () => {
    const card = cardHtml(planePage().html, 'fetch-row');
    assert.ok(card.includes('<span class="ret" title="return type"><span class="ent" data-reveal="pkg.plane.Row">Row</span></span>'));
    assert.ok(card.includes('<span class="n">key</span><span class="t">str</span>'));
    assert.ok(!card.includes('data-reveal="builtins'));
  });

  test('本体の文字: 呼び(fetch-row)と型(Row)は押せ、束縛の名(局所の名)は押せない', () => {
    const shout = cardHtml(planePage().html, 'shout');
    assert.ok(shout.includes('<span class="fn ent" data-reveal="pkg.plane.fetch_row">fetch-row</span>'));
    assert.ok(shout.includes('<span class="b ent" data-reveal="pkg.plane.Row">Row</span>'));
    assert.ok(!/data-reveal="[^"]*"><span class="var"[^>]*>row</.test(shout), '局所の束縛 row は押せない');
  });

  test('defclass の欄の型・基底・method、defenum の値、defhandler の effect は押せる', () => {
    const graph = buildCallGraph(indexFiles('entities-index.json'));
    const entities = indexFiles('entities-index.json')[0];
    const cards = buildCards({
      definitions: entities.definitions,
      signatures: [],
      bodies: [],
      bindings: [],
      violations: [],
      lines: fs.readFileSync(path.join(FIXTURES, 'entities.hy'), 'utf8').split(/\r?\n/),
      testsOf: () => 0,
      place: 'pkg/entities.hy'
    });
    const html = renderPage({
      place: 'pkg/entities.hy',
      state: { tag: 'cards', cards, selection: new Map() },
      glyphs: { effect: () => undefined },
      fold: unfoldAll(INITIAL_FOLD, cards.map((c) => cardKey(c.definition))),
      graph,
      tree: undefined,
      coloring: undefined,
      cspSource: 'vscode-resource:',
      nonce: 'n'
    });
    assert.ok(cardHtml(html, 'Tone').includes('<span class="n ent" data-reveal="pkg.entities.Tone.LOUD">LOUD</span>'));
    assert.ok(/<span class="eff ent" data-reveal="pkg\.entities\.ReadSlot" title="[^"]*">ReadSlot<\/span>/.test(cardHtml(html, 'slot-store')));
  });

  test('頁の script: 名の click は候補の一覧と Cmd / Ctrl の有無を面へ送る', () => {
    const { html } = planePage();
    assert.ok(html.includes("vscode.postMessage({ type: 'reveal', qualifiedNames, editor: event.metaKey || event.ctrlKey })"));
  });
});

suite('定義を読む面 — 押した名の行き先(v12・U19a)', () => {
  test('素の click はその定義のカード、Cmd / Ctrl + click は text editor の定義の名の範囲', () => {
    const { graph } = planePage();
    assert.deepStrictEqual(destinationOf(graph, 'pkg.plane.Row', false), { tag: 'card', path: '/repo/pkg/plane.hy', qualifiedName: 'pkg.plane.Row', line: undefined });
    assert.deepStrictEqual(destinationOf(graph, 'pkg.plane.Row', true), {
      tag: 'editor',
      path: '/repo/pkg/plane.hy',
      range: { start: { line: 6, character: 11 }, end: { line: 6, character: 14 } }
    });
    assert.strictEqual(destinationOf(graph, 'pkg.nowhere', false), undefined);
  });

  test('入れ子の定義(enum の値)は入れ物のカードへ行き、その行を光らせる', () => {
    const graph = buildCallGraph(indexFiles('entities-index.json'));
    const loud = graph.definitions.get('pkg.entities.Tone.LOUD');
    assert.ok(loud !== undefined);
    assert.deepStrictEqual(destinationOf(graph, 'pkg.entities.Tone.LOUD', false), {
      tag: 'card',
      path: loud.path,
      qualifiedName: 'pkg.entities.Tone',
      line: loud.definition.fullRange.start.line
    });
  });
});
