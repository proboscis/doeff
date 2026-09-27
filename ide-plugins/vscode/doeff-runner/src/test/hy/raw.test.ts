import * as assert from 'assert';
import type { Resolve } from '../../hy/callGraph';
import { EffectGraph, type DefRef } from '../../hy/effects';
import { NO_EXTERNAL_MODULES } from '../../hy/external';
import type { PythonModuleSource } from '../../hy/python';
import { DEFAULT_RAW_CATALOG, mergeRawCatalog, type RawCatalogEntry } from '../../hy/rawCatalog';
import { matchesPattern, RawEffectIndex, summarize, summaryText, type RawEvidence } from '../../hy/rawEffects';
import { rawBadge, rawHoverLines, rawLensTitle, rawProgramDiagnostics, rawRoleOf } from '../../hy/rawView';
import { resolveDefinition } from '../../hy/resolve';
import { HyIndexStore } from '../../hy/store';
import { loadDocument } from './fixtures';

const IO = '/raw/pkg/io_handlers.hy';

/** Python の source を持たない口。 */
const NO_PYTHON: PythonModuleSource = {
  findModuleFiles: async () => [],
  readText: async (filePath) => ({ tag: 'unreadable', reason: `${filePath} は無い` })
};

/** raw-workspace.json から表と判定の係を作る(目録は既定か、渡した物)。 */
function rawWorld(catalog: readonly RawCatalogEntry[] = DEFAULT_RAW_CATALOG): { graph: EffectGraph; raw: RawEffectIndex } {
  const document = loadDocument('raw-workspace.json');
  const store = new HyIndexStore();
  store.replaceRoot(document.root, document.files);
  const graph = new EffectGraph(document.files.map((file) => ({ file, external: false })));
  const resolve: Resolve = (query) => resolveDefinition(store, NO_PYTHON, NO_EXTERNAL_MODULES, query);
  return { graph, raw: new RawEffectIndex(graph, catalog, resolve) };
}

/** 名前の定義を引く。 */
function def(graph: EffectGraph, filePath: string, name: string): DefRef {
  const found = graph.definitionsIn(filePath).find((r) => r.definition.name === name);
  assert.ok(found, `${filePath} に ${name} が無い`);
  return found;
}

/** 証拠を「分類 名前(強さ) 行」にする。 */
function show(e: RawEvidence): string {
  return `${e.category} ${e.name}${e.strength === 'weak' ? '?' : ''} ${e.range.start.line}`;
}

suite('生の副作用の目録', () => {
  test('dotted の名前は区切りの境目で前方一致、末尾 * は区切りの中の前方一致', () => {
    assert.ok(matchesPattern('httpx.post', 'httpx'));
    assert.ok(matchesPattern('httpx', 'httpx'));
    assert.ok(!matchesPattern('httpx_extra.post', 'httpx'));
    assert.ok(matchesPattern('os.execv', 'os.exec*'));
    assert.ok(!matchesPattern('time.time_ns', 'time.time'));
    assert.ok(matchesPattern('os.environ.get', 'os.environ'));
  });

  test('設定で足す — dotted・.method・builtin:、知らない分類と配列でない値は理由を返す', () => {
    const merged = mergeRawCatalog(DEFAULT_RAW_CATALOG, {
      http: ['my_http_lib'],
      time: ['.tick', 'builtin:now_ms'],
      bogus: ['x'],
      env: 'os.getcwd'
    });
    const http = merged.catalog.find((e) => e.category === 'http');
    const time = merged.catalog.find((e) => e.category === 'time');
    assert.ok(http?.patterns.includes('my_http_lib'));
    assert.deepStrictEqual(time?.methods, [{ name: 'tick', context: [] }]);
    assert.deepStrictEqual(time?.builtins, ['now_ms']);
    assert.strictEqual(merged.problems.length, 2);
    assert.match(merged.problems[0], /分類 "bogus" は知らない/);
    assert.match(merged.problems[1], /rawSideEffects.env が配列でない/);
    assert.deepStrictEqual(mergeRawCatalog(DEFAULT_RAW_CATALOG, undefined).problems, []);
  });
});

suite('生の副作用の判定 — 直接', () => {
  test('修飾つきの呼び出し(httpx.post)は強い http、例外の型(httpx.ReadTimeout)は数えない', () => {
    const { graph, raw } = rawWorld();
    assert.deepStrictEqual(raw.direct(def(graph, IO, 'Fetch')).map(show), ['http httpx.post 12']);
  });

  test('import 経由の名前 — module の import(time.monotonic)と from-import の class(datetime.now)', () => {
    const { graph, raw } = rawWorld();
    assert.deepStrictEqual(raw.direct(def(graph, IO, 'Now')).map(show), [
      'time time.monotonic 18',
      'time datetime.datetime.now 19'
    ]);
  });

  test('from x import y の名前(sleep → asyncio.sleep)と、別名の module(sp.run → subprocess.run)', () => {
    const { graph, raw } = rawWorld();
    assert.deepStrictEqual(raw.direct(def(graph, IO, 'slow-helper')).map(show), ['async asyncio.sleep 34']);
    assert.deepStrictEqual(raw.direct(def(graph, IO, 'loop-b')).map(show), ['process subprocess.run 43']);
  });

  test('組み込み open は呼び出しの頭だけ強く、pathlib の method 名(.read-text)は弱い', () => {
    const { graph, raw } = rawWorld();
    assert.deepStrictEqual(raw.direct(def(graph, IO, 'ReadIt')).map(show), ['file .read_text? 24', 'file open 25']);
    assert.deepStrictEqual(summaryText(summarize(raw.direct(def(graph, IO, 'ReadIt')))), 'file');
  });

  test('局所の変数 open((setv open …))は、pathlib の無い file では何としても数えない', () => {
    const { graph, raw } = rawWorld();
    assert.deepStrictEqual(raw.direct(def(graph, '/raw/pkg/plain_open.hy', 'uses-open-var')), []);
  });

  test('dotted の途中の区切りは数えず、終端を完全な名前で数える(os.environ.get)', () => {
    const { graph, raw } = rawWorld();
    assert.deepStrictEqual(raw.direct(def(graph, IO, 'bad-program')).map(show), ['env os.environ.get 47']);
  });

  test('handler は自分の節の証拠も含む(full_range の中)', () => {
    const { graph, raw } = rawWorld();
    assert.deepStrictEqual(raw.direct(def(graph, IO, 'http-handler')).map(show), ['http httpx.post 12']);
    assert.deepStrictEqual(raw.direct(def(graph, IO, 'memory-handler')), []);
  });

  test('設定で足した名前と method を数える', () => {
    const merged = mergeRawCatalog(DEFAULT_RAW_CATALOG, { http: ['my_http_lib'], time: ['.tick'] });
    const before = rawWorld();
    assert.deepStrictEqual(before.raw.direct(def(before.graph, IO, 'custom-program')), []);
    const after = rawWorld(merged.catalog);
    assert.deepStrictEqual(after.raw.direct(def(after.graph, IO, 'custom-program')).map(show), [
      'http my_http_lib.get 55',
      'time .tick? 56'
    ]);
  });
});

suite('生の副作用の判定 — 経由', () => {
  test('呼ぶ定義が生に触るなら経由(経路つき)', async () => {
    const { graph, raw } = rawWorld();
    const mark = await raw.mark(def(graph, IO, 'via-handler'));
    assert.deepStrictEqual(mark.direct, []);
    assert.deepStrictEqual(
      mark.via.map((v) => `${v.through.map((d) => d.definition.name).join('>')} ${show(v.evidence)}`),
      ['slow-helper async asyncio.sleep 34']
    );
  });

  test('循環(loop-a ⇄ loop-b)は止まり、相手の証拠は 1 度だけ', async () => {
    const { graph, raw } = rawWorld();
    const mark = await raw.mark(def(graph, IO, 'loop-a'));
    assert.deepStrictEqual(
      mark.via.map((v) => `${v.through.map((d) => d.definition.name).join('>')} ${show(v.evidence)}`),
      ['loop-b process subprocess.run 43']
    );
  });

  test('深さの上限 4 — d1 から d5(4 段目)の random は届き、d6(5 段目)の uuid は届かない', async () => {
    const { graph, raw } = rawWorld();
    const mark = await raw.mark(def(graph, IO, 'd1'));
    assert.deepStrictEqual(
      mark.via.map((v) => `${v.through.map((d) => d.definition.name).join('>')} ${show(v.evidence)}`),
      ['d2>d3>d4>d5 random random.random 71']
    );
  });
});

suite('生の副作用の印の出し方', () => {
  test('役割 — handler と節は handler、defk / deff / defp はプログラム、他は印なし', () => {
    assert.strictEqual(rawRoleOf('defhandler'), 'handler');
    assert.strictEqual(rawRoleOf('effect-clause'), 'handler');
    assert.strictEqual(rawRoleOf('defk'), 'program');
    assert.strictEqual(rawRoleOf('defn'), undefined);
  });

  test('パネルの印・注記の見出し — 直接・経由・プログラム', async () => {
    const { graph, raw } = rawWorld();
    const http = await raw.mark(def(graph, IO, 'http-handler'));
    assert.deepStrictEqual(rawBadge(http, 'handler'), { tag: 'direct', text: '生: http' });
    assert.strictEqual(rawLensTitle(http, 'handler'), '⚡ 生の副作用: http(httpx.post・13 行)');
    const file = await raw.mark(def(graph, IO, 'file-handler'));
    assert.strictEqual(rawLensTitle(file, 'handler'), '⚡ 生の副作用: file(.read_text・25 行)');
    const via = await raw.mark(def(graph, IO, 'via-handler'));
    assert.deepStrictEqual(rawBadge(via, 'handler'), { tag: 'via', text: '経由: async' });
    assert.strictEqual(rawLensTitle(via, 'handler'), '↳ 経由で生の副作用: async(slow-helper 経由)');
    const program = await raw.mark(def(graph, IO, 'bad-program'));
    assert.deepStrictEqual(rawBadge(program, 'program'), { tag: 'direct', text: '生: env' });
    assert.match(rawLensTitle(program, 'program') ?? '', /^⚠ 生の副作用に直接触っています: env\(os\.environ\.get・48 行\)/);
    assert.strictEqual(rawBadge(await raw.mark(def(graph, IO, 'memory-handler')), 'handler'), undefined);
    assert.strictEqual(rawBadge(await raw.mark(def(graph, IO, 'good-program')), 'program'), undefined);
  });

  test('弱い証拠だけの分類には「?」', () => {
    const merged = mergeRawCatalog(DEFAULT_RAW_CATALOG, { time: ['.tick'] });
    const { graph, raw } = rawWorld(merged.catalog);
    assert.strictEqual(summaryText(summarize(raw.direct(def(graph, IO, 'custom-program')))), 'time?');
  });

  test('hover の証拠の一覧と、問題の一覧の警告(defk だけ・handler は対象外)', async () => {
    const { graph, raw } = rawWorld();
    const lines = rawHoverLines(await raw.mark(def(graph, IO, 'via-handler')), 'handler', 8);
    assert.deepStrictEqual(lines, ['**生の副作用**', '- 経由 async `asyncio.sleep` — 35 行(slow-helper)']);
    const diagnostics = rawProgramDiagnostics(graph.definitionsOfKind(['defk', 'defhandler'], false), (ref) => raw.direct(ref));
    assert.deepStrictEqual(
      diagnostics.map((d) => `${d.range.start.line} ${d.message.split('に直接')[0]}`),
      ['47 defk bad-program が生の副作用(env: os.environ.get)']
    );
  });
});
