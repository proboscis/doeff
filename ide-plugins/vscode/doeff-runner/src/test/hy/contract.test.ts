import * as assert from 'assert';
import { parseHyIndexJson } from '../../hy/contract';
import { mangle, mangleDotted } from '../../hy/mangle';
import { HyIndexStore } from '../../hy/store';
import { loadDocument, readFixture } from './fixtures';

suite('Hy 索引の契約の読み込み', () => {
  test('契約どおりの fixture は全 file が読める', () => {
    const document = loadDocument('workspace.json');
    assert.strictEqual(document.version, 5);
    assert.strictEqual(document.files.length, 11);
    const app = document.files.find((f) => f.module === 'pkg.app');
    assert.ok(app);
    assert.strictEqual(app.definitions[0].fullRange.end.line, 14);
    assert.strictEqual(app.imports[0].isRequire, false);
    const effects = document.files.find((f) => f.module === 'pkg.effects');
    assert.ok(effects);
    assert.deepStrictEqual(effects.definitions[1].bases, ['doeff.EffectBase']);
    assert.deepStrictEqual(effects.calls[0], {
      callee: 'PutRow',
      mangled: 'PutRow',
      qualifier: null,
      range: { start: { line: 16, character: 8 }, end: { line: 16, character: 14 } },
      caller: 4,
      performed: true,
      target: 'pkg.effects.PutRow'
    });
    assert.strictEqual(effects.definitions[0].qualifiedName, 'pkg.effects.PutRow');
  });

  test('呼び出しの target は索引の qualifiedName で呼び先の定義を引け、同じ一致の逆で呼び手を引ける(版 4)', () => {
    const document = loadDocument('workspace.json');
    const byQualified = new Map<string, string[]>();
    for (const file of document.files) {
      for (const def of file.definitions) {
        byQualified.set(def.qualifiedName, [...(byQualified.get(def.qualifiedName) ?? []), `${file.path}#${def.name}`]);
      }
    }
    const callers = new Map<string, string[]>();
    for (const file of document.files) {
      for (const call of file.calls) {
        if (call.target !== null && call.caller !== null && byQualified.has(call.target)) {
          callers.set(call.target, [...(callers.get(call.target) ?? []), file.definitions[call.caller].qualifiedName]);
        }
      }
    }
    assert.deepStrictEqual(byQualified.get('pkg.effects.save_row'), ['/ws/pkg/effects.hy#save-row']);
    assert.ok((callers.get('pkg.effects.PutRow') ?? []).length > 0, JSON.stringify([...callers.entries()]));
  });

  test('effect 節の handles は解く effect の完全修飾名を持ち、解く handler は読む側が逆に引ける(版 5)', () => {
    const document = loadDocument('workspace.json');
    const handlers = new Map<string, string[]>();
    for (const file of document.files) {
      for (const def of file.definitions) {
        if (def.handles !== null && def.handles.target !== null && def.container !== null) {
          handlers.set(def.handles.target, [...(handlers.get(def.handles.target) ?? []), def.container]);
        }
        assert.strictEqual(def.handles !== null, def.kind === 'effect-clause', `${def.name} (${def.kind})`);
      }
    }
    const putRow = document.files.flatMap((f) => f.definitions).find((d) => d.qualifiedName === 'pkg.effects.PutRow');
    assert.ok(putRow);
    assert.ok((handlers.get(putRow.qualifiedName) ?? []).length > 0, JSON.stringify([...handlers.entries()]));
  });

  test('版 5 の欄(effects・param_types・answer_type・contracts)を読む', () => {
    const parsed = parseHyIndexJson(
      JSON.stringify({
        version: 5,
        root: '/ws',
        raw_via: 'not-computed',
        raw_catalog_problems: [],
        files: [
          {
            path: '/ws/m.hy',
            module: 'm',
            definitions: [
              {
                name: 'run-it',
                mangled: 'run_it',
                qualified_name: 'm.run_it',
                kind: 'defk',
                range: { start: { line: 0, character: 6 }, end: { line: 0, character: 12 } },
                full_range: { start: { line: 0, character: 0 }, end: { line: 3, character: 1 } },
                container: null,
                docstring: null,
                params: ['request'],
                bases: [],
                raw: { direct: [], via: [] },
                tags: null,
                effects: [{ name: 'ReadInput', target: 'intent.ReadInput' }],
                param_types: [{ name: 'request', type: { text: 'InputRequest', names: [{ name: 'InputRequest', target: 'types.InputRequest' }] } }],
                answer_type: { text: '(| Judgment None)', names: [{ name: 'Judgment', target: null }, { name: 'None', target: null }] },
                contracts: [{ side: 'pre', text: '(> budget 0)' }],
                handles: null
              }
            ],
            imports: [],
            references: [],
            calls: [],
            errors: []
          }
        ]
      })
    );
    assert.strictEqual(parsed.tag, 'ok');
    if (parsed.tag !== 'ok') {
      return;
    }
    assert.deepStrictEqual(parsed.rejected, []);
    const def = parsed.document.files[0].definitions[0];
    assert.deepStrictEqual(def.effects, [{ name: 'ReadInput', target: 'intent.ReadInput' }]);
    assert.strictEqual(def.paramTypes[0].type.names[0].target, 'types.InputRequest');
    assert.strictEqual(def.answerType?.text, '(| Judgment None)');
    assert.deepStrictEqual(def.contracts, [{ side: 'pre', text: '(> budget 0)' }]);
  });

  test('版 1 の JSON は全体を理由つきで捨てる(版 5 だけを受け付ける)', () => {
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

  test('欄の欠け・契約に無い kind・負の行・基底・呼び出し元の添字・完全修飾名の欠けはその file だけ理由つきで捨てる', () => {
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
    assert.strictEqual(parsed.rejected.length, 13);
    assert.match(reasons.get('/ws/no_module.hy') ?? '', /"module" が無い/);
    assert.match(reasons.get('/ws/unknown_kind.hy') ?? '', /契約に無い kind "defwhatever"/);
    assert.match(reasons.get('/ws/missing_is_require.hy') ?? '', /"is_require" が無い/);
    assert.match(reasons.get('/ws/negative_line.hy') ?? '', /0 以上の整数でない/);
    assert.match(reasons.get('/ws/missing_bases.hy') ?? '', /"bases" が無い/);
    assert.match(reasons.get('/ws/bases_on_defn.hy') ?? '', /defn は基底を持たない/);
    assert.match(reasons.get('/ws/caller_out_of_range.hy') ?? '', /caller: definitions の添字でない/);
    assert.match(reasons.get('/ws/missing_calls.hy') ?? '', /"calls" が無い/);
    assert.match(reasons.get('/ws/missing_qualified_name.hy') ?? '', /"qualified_name" が無い/);
    assert.match(reasons.get('/ws/missing_target.hy') ?? '', /"target" が無い/);
    assert.match(reasons.get('/ws/missing_effects.hy') ?? '', /"effects" が無い/);
    assert.match(reasons.get('/ws/handles_on_defn.hy') ?? '', /defn は effect を解かない/);
    assert.match(reasons.get('/ws/bad_contract_side.hy') ?? '', /契約に無い値 "during"/);
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
