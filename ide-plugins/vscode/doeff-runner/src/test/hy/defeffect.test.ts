import * as assert from 'assert';
import { EffectGraphSource } from '../../hy/effects';
import { NO_EXTERNAL_FILES } from '../../hy/external';
import { outlineKindOf } from '../../hy/outline';
import { HyIndexStore } from '../../hy/store';
import { loadDocument } from './fixtures';

// defeffect(doeff-hy の effect の型・常に EffectBase を継ぐ)を契約の kind として読み、目次と effect の判定に載せる。
// fixture defeffect.json は doeff-indexer hy-index の実出力(root を /ws に置き換えた物)。
suite('Hy の defeffect', () => {
  test('defeffect は契約の kind で、基底 EffectBase・:fields の名・docstring・タグを持つ', () => {
    const document = loadDocument('defeffect.json');
    const [effect, program] = document.files[0].definitions;
    assert.strictEqual(effect.kind, 'defeffect');
    assert.deepStrictEqual(effect.bases, ['EffectBase']);
    assert.deepStrictEqual(effect.params, ['profile', 'ttl']);
    assert.strictEqual(effect.docstring, '預かり所から token を借りる');
    assert.deepStrictEqual(effect.tags, { context: 'custody', role: 'intent' });
    assert.deepStrictEqual(program.tags, { context: 'custody', role: 'program' });
    assert.strictEqual(outlineKindOf('defeffect'), 'Struct');
  });

  test('defeffect の型は effect として判じられ、撃つ所から引ける', () => {
    const document = loadDocument('defeffect.json');
    const store = new HyIndexStore();
    store.replaceRoot(document.root, document.files);
    const graph = new EffectGraphSource(store, NO_EXTERNAL_FILES).current();
    const [effectRef] = graph.definitionsIn('/ws/pkg/intent.hy');
    assert.ok(effectRef);
    assert.strictEqual(graph.isEffectClass(effectRef), true);
    assert.strictEqual(graph.effect('BorrowToken')?.name, 'BorrowToken');
  });
});
