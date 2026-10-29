import * as assert from 'assert';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { AGENTIC_CLI, AGENTIC_INSTALL_HINT, listWorkflows, parseWorkflows } from '../../workflows/agenticCli';

// Python の CLI が記録の dir に対して書く `ps --json`。doeff-agentic の test
// (packages/doeff-agentic/tests/test_cli.py)が、本物の handler で走らせた出力とこの file が同じ事を確かめる。
const PS_FIXTURE = path.join(__dirname, '..', '..', '..', '..', '..', 'test-fixtures', 'doeff-agentic-ps.json');

/** `ps --json` にだけ見本を返し、ほかの引数では exit 2 で落ちる実行 file を一時の dir に置き、その path を返す。 */
function standInCli(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'doeff-agentic-'));
  const file = path.join(dir, AGENTIC_CLI);
  fs.writeFileSync(
    file,
    ['#!/bin/sh', '[ "$*" = "ps --json" ] || { echo "unexpected: $*" >&2; exit 2; }', `cat '${PS_FIXTURE}'`, ''].join('\n'),
    { mode: 0o755 }
  );
  return file;
}

suite('doeff-agentic の CLI — workflow の一覧', () => {
  test('呼ぶのは pip install doeff-agentic が入れる実行 file doeff-agentic', () => {
    assert.strictEqual(AGENTIC_CLI, 'doeff-agentic');
    assert.strictEqual(AGENTIC_INSTALL_HINT, 'pip install doeff-agentic');
  });

  test('一覧は ps --json が書く workflow の配列を、更新の新しい順のまま読む', async () => {
    const workflows = await listWorkflows(standInCli());

    assert.deepStrictEqual(
      workflows.map((workflow) => [workflow.id, workflow.status, workflow.current_agent]),
      [
        ['b7e1d4f', 'blocked', 'fixer'],
        ['a3f8b2c', 'running', 'reviewer']
      ]
    );
    assert.deepStrictEqual(
      workflows[1].agents.map((agent) => [agent.name, agent.status, agent.session_name, agent.pane_id]),
      [['reviewer', 'running', 'doeff-wf-reviewer', '%3']]
    );
  });

  test('配列でない答えは投げる(CLI が無い時と同じく、一覧は読めない)', () => {
    assert.throws(() => parseWorkflows('{"ok": true}'), /Expected workflows array from doeff-agentic ps/);
  });
});
