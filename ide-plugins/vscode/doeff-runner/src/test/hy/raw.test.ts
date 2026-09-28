import * as assert from 'assert';
import { parseHyIndexJson, type HyFileIndex } from '../../hy/contract';
import { EffectGraph, type DefRef } from '../../hy/effects';
import { RawEffectIndex, summarize, summaryText } from '../../hy/rawEffects';
import { rawBadge, rawEvidenceLocations, rawHoverLines, rawLensTitle, rawRoleOf } from '../../hy/rawView';
import { carryOverCrossFile } from '../../hy/store';
import { loadDocument, readFixture } from './fixtures';

// raw-workspace.json は hy-index(Rust)の実出力 — 生の副作用の証拠は hy-index が集め、拡張は読むだけ。

const IO = '/raw/pkg/io_handlers.hy';

/** raw-workspace.json から表と証拠の係を作る。 */
function rawWorld(): { graph: EffectGraph; raw: RawEffectIndex; files: readonly HyFileIndex[] } {
  const document = loadDocument('raw-workspace.json');
  const graph = new EffectGraph(document.files.map((file) => ({ file, external: false })));
  return { graph, raw: new RawEffectIndex(graph), files: document.files };
}

/** 名前の定義を引く。 */
function def(graph: EffectGraph, filePath: string, name: string): DefRef {
  const found = graph.definitionsIn(filePath).find((r) => r.definition.name === name);
  assert.ok(found, `${filePath} に ${name} が無い`);
  return found;
}

suite('生の副作用の証拠の読み込み(版 3)', () => {
  test('全体の実行の出力は経由まで読める', () => {
    const document = loadDocument('raw-workspace.json');
    assert.strictEqual(document.version, 4);
    assert.strictEqual(document.rawVia, 'computed');
    assert.deepStrictEqual(document.rawCatalogProblems, []);
  });

  test('証拠の欄が契約に無い値なら、その file を理由つきで捨てる', () => {
    const text = readFixture('raw-workspace.json').replace('"category": "http"', '"category": "telepathy"');
    const parsed = parseHyIndexJson(text);
    assert.strictEqual(parsed.tag, 'ok');
    if (parsed.tag === 'ok') {
      assert.strictEqual(parsed.rejected.length, 1);
      assert.match(parsed.rejected[0].reason, /契約に無い値 "telepathy"/);
    }
  });

  test('直接の証拠と、経由の経路を表の参照に写した物', () => {
    const { graph, raw } = rawWorld();
    assert.deepStrictEqual(
      raw.mark(def(graph, IO, 'Fetch')).direct.map((e) => `${e.category} ${e.name} ${e.strength}`),
      ['http httpx.post strong']
    );
    assert.deepStrictEqual(
      raw.mark(def(graph, IO, 'ReadIt')).direct.map((e) => `${e.name} ${e.strength}`),
      ['.read_text weak', 'open strong']
    );
    const via = raw.mark(def(graph, IO, 'd1')).via;
    assert.deepStrictEqual(
      via.map((v) => `${v.through.map((d) => d.definition.name).join('>')} ${v.evidence.name}`),
      ['d2>d3>d4>d5 random.random']
    );
    assert.strictEqual(via[0].through[0].definition.kind, 'defn');
  });

  test('要約 — 弱い証拠だけの分類には「?」', () => {
    const { graph, raw } = rawWorld();
    assert.strictEqual(summaryText(summarize(raw.mark(def(graph, IO, 'file-handler')).direct)), 'file');
    assert.strictEqual(
      summaryText(summarize(raw.mark(def(graph, IO, 'ReadIt')).direct.filter((e) => e.strength === 'weak'))),
      'file?'
    );
  });
});

suite('生の副作用の印(事実の表示)', () => {
  test('役割 — handler と節は直接も経由も、defk / deff / defp は直接だけ', () => {
    assert.strictEqual(rawRoleOf('defhandler'), 'handler');
    assert.strictEqual(rawRoleOf('effect-clause'), 'handler');
    assert.strictEqual(rawRoleOf('defk'), 'program');
    assert.strictEqual(rawRoleOf('defn'), undefined);
  });

  test('パネルの印・注記の見出し・証拠の位置', () => {
    const { graph, raw } = rawWorld();
    const http = raw.mark(def(graph, IO, 'http-handler'));
    assert.deepStrictEqual(rawBadge(http, 'handler'), { tag: 'direct', text: '生: http' });
    assert.match(rawLensTitle(http, 'handler') ?? '', /^⚡ 生の副作用: http\(httpx\.post・\d+ 行\)$/);
    const via = raw.mark(def(graph, IO, 'via-handler'));
    assert.deepStrictEqual(rawBadge(via, 'handler'), { tag: 'via', text: '経由: async' });
    assert.strictEqual(rawLensTitle(via, 'handler'), '↳ 経由で生の副作用: async(slow-helper 経由)');
    assert.strictEqual(rawEvidenceLocations(via, 'handler').length, 1);
    const program = raw.mark(def(graph, IO, 'bad-program'));
    assert.match(rawLensTitle(program, 'program') ?? '', /^⚡ 生の副作用に直接触る: env\(os\.environ\.get・\d+ 行\)$/);
    assert.strictEqual(rawBadge(raw.mark(def(graph, IO, 'memory-handler')), 'handler'), undefined);
    assert.strictEqual(rawBadge(raw.mark(def(graph, IO, 'performs-external')), 'program'), undefined, 'effect を撃つだけなら証拠は無い');
  });

  test('hover の証拠の一覧(経由は経路つき)', () => {
    const { graph, raw } = rawWorld();
    const lines = rawHoverLines(raw.mark(def(graph, IO, 'via-handler')), 'handler', 8);
    assert.strictEqual(lines[0], '**生の副作用**');
    assert.ok(lines[1].startsWith('- 経由 async `asyncio.sleep` — ') && lines[1].endsWith('slow-helper)'), lines[1]);
  });
});

suite('部分の実行の結果に経由の証拠を引き継ぐ', () => {
  test('1 file の実行(経由なし)は、直前の全体の実行の経由を定義ごとに引き継ぎ、直接は新しい物を使う', () => {
    const { files } = rawWorld();
    const whole = files.find((f) => f.path === IO);
    assert.ok(whole);
    const partial: HyFileIndex = {
      ...whole,
      definitions: whole.definitions.map((d) => ({ ...d, raw: { direct: d.name === 'd1' ? d.raw.direct : [], via: [] } }))
    };
    const merged = carryOverCrossFile(whole, partial);
    const d1 = merged.definitions.find((d) => d.name === 'd1');
    const fetch = merged.definitions.find((d) => d.name === 'Fetch');
    assert.strictEqual(d1?.raw.via.length, 1);
    assert.deepStrictEqual(fetch?.raw.direct, [], '直接の証拠は新しい結果のまま');
    assert.strictEqual(carryOverCrossFile(undefined, partial), partial);
  });
});
