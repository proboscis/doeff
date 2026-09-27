import * as assert from 'assert';
import { HY_DEFINITION_KINDS } from '../../hy/contract';
import { buildOutline, hoverMarkdown, outlineKindOf, searchWorkspaceSymbols, type OutlineNode } from '../../hy/outline';
import { workspaceStore } from './fixtures';

/** 目次を「名前(種類)[子…]」の入れ子の文字列にして比べやすくする。 */
function shape(node: OutlineNode): string {
  const children = node.children.length > 0 ? `[${node.children.map(shape).join(', ')}]` : '';
  return `${node.name}(${node.kind})${children}`;
}

suite('Hy の目次', () => {
  test('container で入れ子にし、入れ物が無い項目は top level に置く', () => {
    const app = workspaceStore().get('/ws/pkg/app.hy');
    assert.ok(app);
    const outline = buildOutline(app.file);
    assert.deepStrictEqual(outline.map(shape), [
      'run-app(Function)',
      'helper(Function)',
      'Store(Class)[path(Field), save(Method)]',
      'Color(Enum)[RED(EnumMember)]',
      'app-handler(Object)[Ask(Event)]',
      'test-run-app(Function)',
      'orphan(Method)'
    ]);
  });

  test('detail に kind と引数、deftest は "test"', () => {
    const app = workspaceStore().get('/ws/pkg/app.hy');
    assert.ok(app);
    const outline = buildOutline(app.file);
    assert.strictEqual(outline[0].detail, 'defn [cfg]');
    assert.strictEqual(outline[2].children[1].detail, 'method [self row]');
    assert.strictEqual(outline[5].detail, 'test');
  });

  test('selectionRange は range の内側(full_range が名前を含まない時は名前の範囲を使う)', () => {
    const app = workspaceStore().get('/ws/pkg/app.hy');
    assert.ok(app);
    const run = buildOutline(app.file)[0];
    assert.deepStrictEqual(run.range.start, { line: 10, character: 0 });
    assert.deepStrictEqual(run.selectionRange.start, { line: 10, character: 6 });
  });

  test('契約の kind はどれも目次の種類へ対応づく', () => {
    const kinds = HY_DEFINITION_KINDS.map(outlineKindOf);
    assert.strictEqual(kinds.length, HY_DEFINITION_KINDS.length);
    assert.strictEqual(outlineKindOf('defhandler'), 'Object');
    assert.strictEqual(outlineKindOf('effect-clause'), 'Event');
    assert.strictEqual(outlineKindOf('defrecord'), 'Class');
    assert.strictEqual(outlineKindOf('variable'), 'Variable');
  });
});

suite('Hy の workspace の記号の検索と hover', () => {
  test('名前か mangled の名前に query の文字が順に現れる定義を集める', () => {
    const store = workspaceStore();
    const hits = (q: string): string[] =>
      searchWorkspaceSymbols(store, q, 100)
        .map((h) => `${h.path}:${h.definition.name}`)
        .sort();
    assert.deepStrictEqual(hits('format'), ['/ws/other/dup.hy:format-name', '/ws/pkg/util.hy:format-name']);
    assert.deepStrictEqual(hits('format_name'), ['/ws/other/dup.hy:format-name', '/ws/pkg/util.hy:format-name']);
    assert.deepStrictEqual(hits('fmtnm'), ['/ws/other/dup.hy:format-name', '/ws/pkg/util.hy:format-name']);
    assert.strictEqual(searchWorkspaceSymbols(store, '', 3).length, 3);
  });

  test('hover は kind・引数・module・docstring を出す', () => {
    const app = workspaceStore().get('/ws/pkg/app.hy');
    assert.ok(app);
    const text = hoverMarkdown(app.file.definitions[0], app.file.module);
    assert.ok(text.includes('(defn run-app [cfg])'));
    assert.ok(text.includes('`pkg.app`'));
    assert.ok(text.includes('アプリを走らせる'));
  });
});
