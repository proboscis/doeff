import * as assert from 'assert';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import type { HyIndexDocument } from '../../hy/contract';
import {
  sysPathEntry,
  type ExternalModuleQuery,
  type ExternalModuleResult,
  type ExternalModuleSource
} from '../../hy/external';
import {
  ExternalModuleCache,
  LOCATE_RETRY_AFTER_MS,
  type ChangeStamps,
  type LocateBatch,
  type ModuleLocation,
  type ModuleLocator
} from '../../hy/externalCache';
import type { HyIndexer, HyIndexOutcome, HyIndexRequest } from '../../hy/indexer';
import { searchWorkspaceSymbols } from '../../hy/outline';
import { FsPythonModuleSource } from '../../hy/pythonSource';
import { collectReferences, resolveDefinition, type DefinitionTarget } from '../../hy/resolve';
import { parseLocateOutput } from '../../hy/uvLocator';
import { loadDocument, workspaceStore } from './fixtures';

const APP = '/ws/pkg/app.hy';
const MEMORY = '/deps/src/doeff_records/memory.hy';

/** 行き先を「path:行:列」の短い文字列にして比べやすくする。 */
function where(target: DefinitionTarget): string {
  switch (target.tag) {
    case 'hy-definition':
      return `${target.path}:${target.definition.range.start.line}:${target.definition.range.start.character}`;
    case 'python-definition':
      return `${target.path}:${target.range.start.line}:${target.range.start.character}`;
    case 'hy-module':
    case 'python-module':
      return `${target.path}:module`;
  }
}

/** 決めた答えを返し、聞かれた問いを記録する外の module の口の偽物。 */
class FakeExternal implements ExternalModuleSource {
  readonly queries: ExternalModuleQuery[] = [];
  constructor(private readonly answers: ReadonlyMap<string, ExternalModuleResult>) {}

  /** module ごとに決めた答えを返す(決めていない module は環境に無い)。 */
  async lookup(query: ExternalModuleQuery): Promise<ExternalModuleResult> {
    this.queries.push(query);
    return this.answers.get(query.module) ?? { tag: 'not-found' };
  }
}

suite('Hy の定義へ移動 — workspace の外の module(c\')', () => {
  let tmp = '';
  let external: FakeExternal;
  let python: FsPythonModuleSource;

  suiteSetup(() => {
    tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'hy-ext-'));
    fs.writeFileSync(path.join(tmp, 'tools.py'), 'import sys\n\ndef shout(text):\n    return text.upper()\n');
    const memory = loadDocument('external-memory.json').files[0];
    external = new FakeExternal(
      new Map<string, ExternalModuleResult>([
        ['doeff_records.memory', { tag: 'hy', path: MEMORY, file: memory }],
        ['outside_py.tools', { tag: 'python', path: path.join(tmp, 'tools.py') }],
        ['broken.pkg', { tag: 'unavailable', reason: 'uv が終了コード 2 で終わった' }]
      ])
    );
    python = new FsPythonModuleSource(() => [], undefined); // workspace の中の .py は無い(外の段だけを見る)
  });

  suiteTeardown(() => {
    fs.rmSync(tmp, { recursive: true, force: true });
  });

  /** app.hy の中の記号を、偽物の外の口で解決する。 */
  function resolveInApp(name: string, qualifier: string | null) {
    return resolveDefinition(workspaceStore(), python, external, { filePath: APP, name, qualifier });
  }

  test('名前で import した外の Hy の class(MemoryStore)は、外の file の定義へ', async () => {
    const r = await resolveInApp('MemoryStore', null);
    assert.strictEqual(r.tier, 'import-external');
    assert.strictEqual(r.module, 'doeff_records.memory');
    assert.deepStrictEqual(r.targets.map(where), [`${MEMORY}:20:10`]);
  });

  test('問い合わせは workspace root で、workspace の索引に無い import の module をまとめて prefetch する', async () => {
    external.queries.length = 0;
    await resolveInApp('MemoryStore', null);
    const query = external.queries[0];
    assert.strictEqual(query.root, '/ws');
    assert.ok(query.prefetch.includes('doeff_records.memory'));
    assert.ok(query.prefetch.includes('outside_py.tools'));
    assert.ok(!query.prefetch.includes('pkg.util'), 'workspace の Hy の module は聞かない');
    assert.ok(query.prefetch.includes('pkg.sibling') === false);
  });

  test('module の別名 + dotted(mem.make-store)・別名そのもの(mem)・class の member(MemoryStore.put-row)', async () => {
    assert.deepStrictEqual((await resolveInApp('make-store', 'mem')).targets.map(where), [`${MEMORY}:42:6`]);
    assert.deepStrictEqual((await resolveInApp('mem', null)).targets.map(where), [`${MEMORY}:module`]);
    assert.deepStrictEqual((await resolveInApp('put-row', 'MemoryStore')).targets.map(where), [`${MEMORY}:22:8`]);
  });

  test('外の Python の module は Python の名前探しで def の行へ', async () => {
    const r = await resolveInApp('shout', null);
    assert.strictEqual(r.tier, 'import-external');
    assert.deepStrictEqual(r.targets.map(where), [`${path.join(tmp, 'tools.py')}:2:4`]);
  });

  test('外の file の定義は workspace の記号の検索と参照の一覧に混ざらない', () => {
    const store = workspaceStore();
    assert.deepStrictEqual(searchWorkspaceSymbols(store, 'MemoryStore', 10), []);
    const refs = collectReferences(store, 'MemoryStore', 'doeff_records.memory', true);
    assert.ok(refs.every((ref) => !ref.path.startsWith('/deps/')));
  });

  test('聞けなかった理由は problems に出し、黙って空にしない', async () => {
    const unavailable = new FakeExternal(
      new Map<string, ExternalModuleResult>([['doeff_records.memory', { tag: 'unavailable', reason: 'uv が見つからない' }]])
    );
    const r = await resolveDefinition(workspaceStore(), python, unavailable, {
      filePath: APP,
      name: 'MemoryStore',
      qualifier: null
    });
    assert.strictEqual(r.tier, 'none');
    assert.deepStrictEqual(r.problems, ['uv が見つからない']);
  });
});

/** 決めた答えを返し、何を何回聞かれたかを数える置き場所の口の偽物。 */
class FakeLocator implements ModuleLocator {
  readonly calls: string[][] = [];
  failWith: string | undefined;
  constructor(private readonly locations: Map<string, ModuleLocation>) {}

  /** 聞かれた module の並びを記録し、決めた答え(か失敗)を返す。 */
  async locate(_root: string, modules: readonly string[]): Promise<LocateBatch> {
    this.calls.push([...modules]);
    if (this.failWith !== undefined) {
      return { tag: 'failed', reason: this.failWith };
    }
    return {
      tag: 'ok',
      locations: new Map(modules.map((m) => [m, this.locations.get(m) ?? { tag: 'missing' }]))
    };
  }
}

/** 1 file の索引の依頼を記録し、外の memory.hy の fixture を返す hy-index の偽物。 */
class FakeIndexer implements HyIndexer {
  readonly requests: HyIndexRequest[] = [];
  constructor(private readonly document: HyIndexDocument) {}

  /** 依頼を記録して fixture の索引を返す。 */
  async index(request: HyIndexRequest): Promise<HyIndexOutcome> {
    this.requests.push(request);
    return { tag: 'ok', document: this.document, rejected: [] };
  }
}

/** 指紋と file の時刻をテストから書き換えられる合図の偽物。 */
class FakeStamps implements ChangeStamps {
  fingerprint = 'lock-1';
  stamp = 'mtime-1';
  /** 環境の指紋を返す。 */
  async projectFingerprint(): Promise<string> {
    return this.fingerprint;
  }
  /** file の時刻を返す。 */
  async fileStamp(): Promise<string> {
    return this.stamp;
  }
}

suite('workspace の外の module の cache', () => {
  let locator: FakeLocator;
  let indexer: FakeIndexer;
  let stamps: FakeStamps;
  let clock: number;
  let cache: ExternalModuleCache;

  setup(() => {
    locator = new FakeLocator(
      new Map<string, ModuleLocation>([
        ['doeff_records.memory', { tag: 'found', origin: MEMORY }],
        ['plain.tool', { tag: 'found', origin: '/deps/src/plain/tool.py' }],
        ['native.ext', { tag: 'found', origin: '/deps/native/ext.cpython-314-darwin.so' }],
        ['bad.pkg', { tag: 'error', reason: 'bad.pkg の場所を引けない: ImportError: x' }]
      ])
    );
    indexer = new FakeIndexer(loadDocument('external-memory.json'));
    stamps = new FakeStamps();
    clock = 1_000_000;
    cache = new ExternalModuleCache(locator, indexer, stamps, () => clock);
  });

  /** /ws の環境に module を聞く。 */
  function ask(module: string, prefetch: readonly string[] = []): Promise<ExternalModuleResult> {
    return cache.lookup({ root: '/ws', module, prefetch });
  }

  test('prefetch と合わせて子 process 1 回で聞き、同じ module は聞き直さない', async () => {
    await ask('plain.tool', ['plain.tool', 'doeff_records.memory', 'native.ext']);
    await ask('doeff_records.memory');
    await ask('native.ext');
    assert.deepStrictEqual(locator.calls, [['plain.tool', 'doeff_records.memory', 'native.ext']]);
  });

  test('.hy は sys.path の入口を root にして 1 file の索引を取り、時刻が同じ間は取り直さない', async () => {
    const first = await ask('doeff_records.memory');
    assert.strictEqual(first.tag, 'hy');
    assert.deepStrictEqual(indexer.requests, [{ tag: 'files', root: '/deps/src', files: [MEMORY] }]);
    await ask('doeff_records.memory');
    assert.strictEqual(indexer.requests.length, 1);
    stamps.stamp = 'mtime-2';
    await ask('doeff_records.memory');
    assert.strictEqual(indexer.requests.length, 2);
    assert.strictEqual(cache.cachedFile(MEMORY)?.module, 'doeff_records.memory');
  });

  test('.py は Python、.so のような読めない実体と環境に無い module は not-found', async () => {
    assert.deepStrictEqual(await ask('plain.tool'), { tag: 'python', path: '/deps/src/plain/tool.py' });
    assert.deepStrictEqual(await ask('native.ext'), { tag: 'not-found' });
    assert.deepStrictEqual(await ask('no.such'), { tag: 'not-found' });
  });

  test('lock file か pyproject が変わったら(指紋が変わったら)聞き直す', async () => {
    await ask('plain.tool');
    stamps.fingerprint = 'lock-2';
    await ask('plain.tool');
    assert.strictEqual(locator.calls.length, 2);
  });

  test('uv の失敗は理由を 1 度だけ返し、待ちの間は聞かず、待ちが明けたら聞き直す', async () => {
    locator.failWith = 'uv が見つからない';
    const first = await ask('plain.tool');
    assert.strictEqual(first.tag, 'unavailable');
    assert.match(first.tag === 'unavailable' ? first.reason : '', /uv が見つからない/);
    assert.deepStrictEqual(await ask('plain.tool'), { tag: 'skipped' });
    assert.strictEqual(locator.calls.length, 1);
    locator.failWith = undefined;
    clock += LOCATE_RETRY_AFTER_MS + 1;
    assert.strictEqual((await ask('plain.tool')).tag, 'python');
    assert.strictEqual(locator.calls.length, 2);
  });

  test('module ごとの引けない理由も 1 度だけ返す', async () => {
    assert.strictEqual((await ask('bad.pkg')).tag, 'unavailable');
    assert.deepStrictEqual(await ask('bad.pkg'), { tag: 'skipped' });
  });

  test('sys.path の入口 — module の区切りの数だけ上る(package の __init__ は 1 つ多く)', () => {
    assert.strictEqual(sysPathEntry('/deps/src/doeff_records/memory.hy', 'doeff_records.memory'), '/deps/src');
    assert.strictEqual(sysPathEntry('/deps/src/a/b/__init__.hy', 'a.b'), '/deps/src');
    assert.strictEqual(sysPathEntry('/deps/src/top.hy', 'top'), '/deps/src');
  });

  test('uv の問い合わせの答えを読む(形が違えば理由つきで失敗)', () => {
    const ok = parseLocateOutput(
      'noise\n{"a.b": {"origin": "/x/a/b.hy"}, "c": {"missing": true}, "d": {"error": "ImportError: y"}}\n',
      ['a.b', 'c', 'd', 'e']
    );
    assert.strictEqual(ok.tag, 'ok');
    if (ok.tag === 'ok') {
      assert.deepStrictEqual(ok.locations.get('a.b'), { tag: 'found', origin: '/x/a/b.hy' });
      assert.deepStrictEqual(ok.locations.get('c'), { tag: 'missing' });
      assert.strictEqual(ok.locations.get('d')?.tag, 'error');
      assert.strictEqual(ok.locations.get('e')?.tag, 'error');
    }
    assert.strictEqual(parseLocateOutput('not json', ['a']).tag, 'failed');
    assert.strictEqual(parseLocateOutput('[1]', ['a']).tag, 'failed');
  });
});
