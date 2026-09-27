import * as assert from 'assert';
import { parseHyIndexJson } from '../../hy/contract';
import { loadDocument, readFixture } from './fixtures';

// defrecord の頭の辞書 {:tags … :check […]}(doeff-hy・agora-redesign #798)を契約の tags と checks として読む。
// fixture defrecord.json は doeff-indexer hy-index の実出力(root を /ws に置き換えた物)。
suite('Hy の defrecord の頭の辞書', () => {
  test('頭の辞書の :tags はタグ、:check は checks になり、頭の辞書の無い形はどちらも null', () => {
    const document = loadDocument('defrecord.json');
    const [chat, value, plain] = document.files[0].definitions;
    assert.strictEqual(chat.kind, 'defrecord');
    assert.strictEqual(chat.docstring, 'chat の id');
    assert.deepStrictEqual(chat.tags, { context: 'chat', role: 'type' });
    assert.deepStrictEqual(chat.checks, ['(CHAT-ID-PATTERN.fullmatch value)']);
    assert.strictEqual(value.container, 'ChatId');
    assert.strictEqual(plain.tags, null);
    assert.strictEqual(plain.checks, null);
  });

  test('defrecord 以外の定義が checks を持つ出力は契約違反', () => {
    const broken = readFixture('defrecord.json').replace('"kind": "defrecord"', '"kind": "defclass"');
    const parsed = parseHyIndexJson(broken);
    assert.strictEqual(parsed.tag, 'ok');
    if (parsed.tag === 'ok') {
      assert.strictEqual(parsed.document.files.length, 0);
      assert.match(parsed.rejected[0]?.reason ?? '', /作る時の検めを持たない/);
    }
  });
});
