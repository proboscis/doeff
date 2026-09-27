// テストが fixture の JSON(契約 版 1 の形)を契約の読み込みの入口から読むための助け。

import * as assert from 'assert';
import * as fs from 'fs';
import * as path from 'path';
import { parseHyIndexJson, type HyIndexDocument } from '../../hy/contract';
import { HyIndexStore } from '../../hy/store';

/** fixture の dir(out/test/hy から見て拡張の root の test-fixtures/hy)。 */
export const FIXTURE_DIR = path.join(__dirname, '..', '..', '..', 'test-fixtures', 'hy');

/** fixture の file の中身を文字列で読む。 */
export function readFixture(name: string): string {
  return fs.readFileSync(path.join(FIXTURE_DIR, name), 'utf8');
}

/** 正しい fixture を契約の入口で読み、読めたことを確かめて document を返す。 */
export function loadDocument(name: string): HyIndexDocument {
  const parsed = parseHyIndexJson(readFixture(name));
  if (parsed.tag !== 'ok') {
    assert.fail(`fixture ${name} を読めない: ${parsed.reason}`);
  }
  assert.deepStrictEqual(parsed.rejected, []);
  return parsed.document;
}

/** workspace.json を root 全体の索引として入れた store を作る。 */
export function workspaceStore(): HyIndexStore {
  const document = loadDocument('workspace.json');
  const store = new HyIndexStore();
  store.replaceRoot(document.root, document.files);
  return store;
}
