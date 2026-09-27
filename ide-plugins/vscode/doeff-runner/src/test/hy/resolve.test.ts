import * as assert from 'assert';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { symbolAt } from '../../hy/cursor';
import { findPythonDefinitions, type PythonModuleSource } from '../../hy/python';
import { FsPythonModuleSource } from '../../hy/pythonSource';
import {
  absoluteModule,
  collectReferences,
  resolveDefinition,
  type DefinitionResolution,
  type DefinitionTarget
} from '../../hy/resolve';
import { workspaceStore } from './fixtures';

const APP = '/ws/pkg/app.hy';

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

/** Python の source を持たない口(Hy だけの解決のテスト用 — 呼ばれたら空を返す)。 */
const NO_PYTHON: PythonModuleSource = {
  findModuleFiles: async () => [],
  readText: async (filePath) => ({ tag: 'unreadable', reason: `${filePath} は無い` })
};

/** app.hy の中の記号を解決する。 */
function resolveInApp(name: string, qualifier: string | null, python = NO_PYTHON): Promise<DefinitionResolution> {
  return resolveDefinition(workspaceStore(), python, { filePath: APP, name, qualifier });
}

suite('Hy のカーソルの記号', () => {
  test('括弧の中の記号と dotted の区切りを取り出す', () => {
    assert.deepStrictEqual(symbolAt('  (format-name x)', 5), { name: 'format-name', qualifier: null, start: 3, end: 14 });
    assert.deepStrictEqual(symbolAt('(L.lib-fn 1)', 4), { name: 'lib-fn', qualifier: 'L', start: 3, end: 9 });
    assert.deepStrictEqual(symbolAt('(L.lib-fn 1)', 1), { name: 'L', qualifier: null, start: 1, end: 2 });
    assert.deepStrictEqual(symbolAt('(a.b.c)', 6), { name: 'c', qualifier: 'a.b', start: 5, end: 6 });
    assert.deepStrictEqual(symbolAt('(.save store)', 3), { name: 'save', qualifier: null, start: 2, end: 6 });
    assert.deepStrictEqual(symbolAt('#^ int x', 1), undefined);
    assert.strictEqual(symbolAt('(f :key 1)', 5), undefined);
    assert.strictEqual(symbolAt('(f 42)', 4), undefined);
  });
});

suite('Hy の定義へ移動', () => {
  test('a. 同じ file の定義を先に選ぶ(他の file の同名は返さない)', async () => {
    const r = await resolveInApp('helper', null);
    assert.strictEqual(r.tier, 'same-file');
    assert.deepStrictEqual(r.targets.map(where), ['/ws/pkg/app.hy:16:6']);
  });

  test('a. 同じ file の入れ物の member(Color.RED)', async () => {
    const r = await resolveInApp('RED', 'Color');
    assert.strictEqual(r.tier, 'same-file');
    assert.deepStrictEqual(r.targets.map(where), ['/ws/pkg/app.hy:32:14']);
  });

  test('b. import の名前で import 先の module の定義へ', async () => {
    const r = await resolveInApp('format-name', null);
    assert.strictEqual(r.tier, 'import-hy');
    assert.strictEqual(r.module, 'pkg.util');
    assert.deepStrictEqual(r.targets.map(where), ['/ws/pkg/util.hy:3:6']);
  });

  test('b. import の別名(parse → parse-it)', async () => {
    const r = await resolveInApp('parse', null);
    assert.strictEqual(r.tier, 'import-hy');
    assert.deepStrictEqual(r.targets.map(where), ['/ws/pkg/util.hy:8:6']);
  });

  test('b. module の別名 + dotted(L.lib-fn)と、別名そのもの(L → module の file)', async () => {
    const dotted = await resolveInApp('lib-fn', 'L');
    assert.strictEqual(dotted.tier, 'import-hy');
    assert.deepStrictEqual(dotted.targets.map(where), ['/ws/pkg/lib.hy:2:6']);
    const alias = await resolveInApp('L', null);
    assert.deepStrictEqual(alias.targets.map(where), ['/ws/pkg/lib.hy:module']);
  });

  test('b. 名前で import した class の member(Record.name)と、名前で import した submodule(util.format-name)', async () => {
    const member = await resolveInApp('name', 'Record');
    assert.deepStrictEqual(member.targets.map(where), ['/ws/pkg/models.hy:2:5']);
    const submodule = await resolveInApp('format-name', 'util');
    assert.deepStrictEqual(submodule.targets.map(where), ['/ws/pkg/util.hy:3:6']);
  });

  test('b. 相対 import(.sibling)は書いた file の package を基準に解く', async () => {
    const r = await resolveInApp('sib-fn', null);
    assert.deepStrictEqual(r.targets.map(where), ['/ws/pkg/sibling.hy:0:6']);
    const store = workspaceStore();
    const app = store.get(APP);
    const init = store.get('/ws/pkg/__init__.hy');
    assert.ok(app && init);
    assert.strictEqual(absoluteModule(app.file, '..top'), 'top');
    assert.strictEqual(absoluteModule(init.file, '.util'), 'pkg.util');
  });

  suite('c. import 先が Python の module', () => {
    let tmp = '';
    let python: FsPythonModuleSource;

    suiteSetup(() => {
      tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'hy-nav-'));
      fs.mkdirSync(path.join(tmp, 'numpy_like'));
      fs.writeFileSync(
        path.join(tmp, 'numpy_like', 'core.py'),
        [
          'import math',
          '',
          'class Holder:',
          '    def compute(self):',
          '        return 1',
          '',
          'async def compute(x):',
          '    return x',
          '',
          'max_size: int = 3',
          'if max_size == 3:',
          '    pass'
        ].join('\n')
      );
      fs.mkdirSync(path.join(tmp, 'src', 'pyp'), { recursive: true });
      fs.writeFileSync(path.join(tmp, 'src', 'pyp', '__init__.py'), 'def thing():\n    pass\n');
      python = new FsPythonModuleSource(() => [tmp], undefined);
    });

    suiteTeardown(() => {
      fs.rmSync(tmp, { recursive: true, force: true });
    });

    test('名前の import は Python の top level の def へ(字下げの method より先)', async () => {
      const r = await resolveInApp('compute', null, python);
      assert.strictEqual(r.tier, 'import-python');
      assert.strictEqual(r.module, 'numpy_like.core');
      assert.deepStrictEqual(r.targets.map(where), [`${path.join(tmp, 'numpy_like', 'core.py')}:6:10`]);
    });

    test('Hy の名前は mangle して Python の代入を探す(max-size → max_size: int =)', async () => {
      const r = await resolveInApp('max-size', null, python);
      assert.deepStrictEqual(r.targets.map(where), [`${path.join(tmp, 'numpy_like', 'core.py')}:9:0`]);
    });

    test('dotted の module 名(numpy_like.core.compute)と、src の下の package の __init__.py(P.thing)', async () => {
      const dotted = await resolveInApp('compute', 'numpy_like.core', python);
      assert.deepStrictEqual(dotted.targets.map(where), [`${path.join(tmp, 'numpy_like', 'core.py')}:6:10`]);
      const init = await resolveInApp('thing', 'P', python);
      assert.deepStrictEqual(init.targets.map(where), [`${path.join(tmp, 'src', 'pyp', '__init__.py')}:0:4`]);
    });

    test('名前が無い時は workspace 全体の段へ回り、それも無ければ module の file を返す', async () => {
      const r = await resolveInApp('nothing-here', 'numpy_like.core', python);
      assert.strictEqual(r.tier, 'module-only');
      assert.deepStrictEqual(r.targets.map(where), [`${path.join(tmp, 'numpy_like', 'core.py')}:module`]);
    });
  });

  test('d. 見つからなければ workspace 全体の同名の定義を全部返す(module は定まらない)', async () => {
    const r = await resolveDefinition(workspaceStore(), NO_PYTHON, {
      filePath: '/ws/other/free.hy',
      name: 'helper',
      qualifier: null
    });
    assert.strictEqual(r.tier, 'workspace');
    assert.strictEqual(r.module, null);
    assert.deepStrictEqual(r.targets.map(where).sort(), ['/ws/other/dup.hy:0:6', '/ws/pkg/app.hy:16:6']);
  });

  test('どこにも無い名前は空', async () => {
    const r = await resolveInApp('no-such-name', null);
    assert.strictEqual(r.tier, 'none');
    assert.deepStrictEqual(r.targets, []);
  });

  test('Python の定義の探し方: async def・注釈つき代入・== は代入でない', () => {
    const text = 'x = 1\nif x == 2:\n    y = 3\nasync def go():\n    pass\nz: int=4\n';
    assert.deepStrictEqual(findPythonDefinitions(text, 'x').map((l) => l.line), [0]);
    assert.deepStrictEqual(findPythonDefinitions(text, 'go').map((l) => [l.line, l.character]), [[3, 10]]);
    assert.deepStrictEqual(findPythonDefinitions(text, 'z').map((l) => l.line), [5]);
    assert.deepStrictEqual(findPythonDefinitions(text, 'y'), []);
  });
});

suite('Hy の参照の一覧', () => {
  /** 参照を「path:行:列」の並びにする。 */
  function refs(name: string, module: string | null, includeDeclaration: boolean): string[] {
    return collectReferences(workspaceStore(), name, module, includeDeclaration)
      .map((r) => `${r.path}:${r.range.start.line}:${r.range.start.character}`)
      .sort();
  }

  test('module が定まれば import と qualifier で絞る(別の module の同名は落とし、絞れない参照は残す)', () => {
    assert.deepStrictEqual(refs('format-name', 'pkg.util', true), [
      '/ws/other/free.hy:1:3', // import も定義も無い file — 絞れないので名前で数える
      '/ws/other/free.hy:3:7', // self.format-name — qualifier を解けないので名前で数える
      '/ws/pkg/app.hy:11:3', // import した名前
      '/ws/pkg/app.hy:51:8', // util.format-name(名前で import した submodule)
      '/ws/pkg/util.hy:12:4', // 定義した module の中の参照
      '/ws/pkg/util.hy:3:6' // 定義
    ]);
  });

  test('includeDeclaration が偽なら定義の位置を出さない', () => {
    assert.ok(!refs('format-name', 'pkg.util', false).includes('/ws/pkg/util.hy:3:6'));
  });

  test('module が定まらなければ名前だけで全 file から集める', () => {
    assert.deepStrictEqual(refs('format-name', null, true), [
      '/ws/other/dup.hy:3:6',
      '/ws/other/dup.hy:6:3',
      '/ws/other/free.hy:1:3',
      '/ws/other/free.hy:3:7',
      '/ws/other/user.hy:2:3',
      '/ws/pkg/app.hy:11:3',
      '/ws/pkg/app.hy:51:8',
      '/ws/pkg/util.hy:12:4',
      '/ws/pkg/util.hy:3:6'
    ]);
  });

  test('module の別名の dotted 参照(L.lib-fn)を定義と合わせて集める', () => {
    assert.deepStrictEqual(refs('lib-fn', 'pkg.lib', true), ['/ws/pkg/app.hy:13:5', '/ws/pkg/lib.hy:2:6']);
  });
});
