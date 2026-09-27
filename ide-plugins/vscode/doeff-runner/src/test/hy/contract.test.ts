import * as assert from 'assert';
import { parseHyIndexJson } from '../../hy/contract';
import { mangle, mangleDotted } from '../../hy/mangle';
import { HyIndexStore } from '../../hy/store';
import { loadDocument, readFixture } from './fixtures';

suite('Hy 索引の契約の読み込み', () => {
  test('契約どおりの fixture は全 file が読める', () => {
    const document = loadDocument('workspace.json');
    assert.strictEqual(document.version, 1);
    assert.strictEqual(document.files.length, 9);
    const app = document.files.find((f) => f.module === 'pkg.app');
    assert.ok(app);
    assert.strictEqual(app.definitions[0].fullRange.end.line, 14);
    assert.strictEqual(app.imports[0].isRequire, false);
  });

  test('版が違う JSON は全体を理由つきで捨てる', () => {
    const parsed = parseHyIndexJson(readFixture('bad-version.json'));
    assert.strictEqual(parsed.tag, 'rejected');
    assert.match(parsed.tag === 'rejected' ? parsed.reason : '', /版が違う/);
  });

  test('JSON として壊れた出力は捨てる', () => {
    const parsed = parseHyIndexJson(readFixture('truncated.json'));
    assert.strictEqual(parsed.tag, 'rejected');
    assert.match(parsed.tag === 'rejected' ? parsed.reason : '', /JSON として読めない/);
  });

  test('最上位の欄が欠けた JSON は捨てる(空の files で埋めない)', () => {
    const parsed = parseHyIndexJson(readFixture('missing-top-level-files.json'));
    assert.strictEqual(parsed.tag, 'rejected');
    assert.match(parsed.tag === 'rejected' ? parsed.reason : '', /"files"/);
  });

  test('欄の欠け・契約に無い kind・負の行の file はその file だけ理由つきで捨てる', () => {
    const parsed = parseHyIndexJson(readFixture('broken-files.json'));
    assert.strictEqual(parsed.tag, 'ok');
    if (parsed.tag !== 'ok') {
      return;
    }
    assert.deepStrictEqual(
      parsed.document.files.map((f) => f.path),
      ['/ws/good.hy']
    );
    assert.deepStrictEqual(parsed.document.files[0].errors, ['3 行目: 括弧が閉じていない']);
    const reasons = new Map(parsed.rejected.map((r) => [r.path, r.reason]));
    assert.strictEqual(parsed.rejected.length, 4);
    assert.match(reasons.get('/ws/no_module.hy') ?? '', /"module" が無い/);
    assert.match(reasons.get('/ws/unknown_kind.hy') ?? '', /契約に無い kind "defwhatever"/);
    assert.match(reasons.get('/ws/missing_is_require.hy') ?? '', /"is_require" が無い/);
    assert.match(reasons.get('/ws/negative_line.hy') ?? '', /0 以上の整数でない/);
  });
});

suite('Hy の mangle', () => {
  test('- を _ にし、先頭の - は残す', () => {
    assert.strictEqual(mangle('format-name'), 'format_name');
    assert.strictEqual(mangle('-private-fn'), '-private_fn');
    assert.strictEqual(mangleDotted('pkg.my-mod.fn-a'), 'pkg.my_mod.fn_a');
  });
});

suite('Hy の索引の置き場', () => {
  test('root の置き換えは、結果に無くなった file を落とし、別の root は残す', () => {
    const document = loadDocument('workspace.json');
    const store = new HyIndexStore();
    store.replaceRoot('/ws', document.files);
    store.upsert('/other-root', [{ ...document.files[0], path: '/other-root/x.hy', module: 'x' }]);
    store.replaceRoot('/ws', document.files.slice(0, 2));
    assert.deepStrictEqual(
      store.entries().map((e) => e.file.path).sort(),
      ['/other-root/x.hy', '/ws/pkg/app.hy', '/ws/pkg/util.hy']
    );
    assert.strictEqual(store.byModule('pkg.util').length, 1);
    store.removeUnder('/ws/pkg');
    assert.deepStrictEqual(store.entries().map((e) => e.file.path), ['/other-root/x.hy']);
  });
});
