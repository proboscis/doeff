# doeff-claude-code

`claude -p`(Claude Code の CLI の print mode・stream-json の入出力)の process の寿命を受け持つ、doeff の effect と handler。

上の層(利用者の Program)は **会話の id と手番の参照** だけを持つ。process を起こす・stdin に書く・信号を送る・降ろす・
死んだ process を `--resume` で起こし直す、はすべてこの package の handler の中に閉じる。

## 公開 effect

| effect | すること | 成功の答え | 失敗の答え(型で返す) |
|---|---|---|---|
| `ClaudeStartTurn(origin, spec, input)` | 手番を始める(新しい会話・続き・枝分かれ) | `TurnStarted` | `SessionNotFound` / `SessionIdInUse` / `TurnInFlight` / `CarryRefused` / `LaunchFailed` / `AttachmentRefused` |
| `ClaudeInjectInput(turn, input)` | 走っている手番に入力を足す | `InputQueued` | `NoTurnInFlight` / `AttachmentRefused` |
| `ClaudeInterruptTurn(turn)` | 手番を止める | `InterruptRequested` | `NoTurnInFlight` |
| `ClaudeReadTurnEvents(turn, after-seq, wait-up-to)` | 手番の出来事(stdout の行)と終わりを読む | `TurnEventPage` | `UnknownTurn` |
| `ClaudeAnswerPermission(turn, request-id, answer)` | 道具の許可の問いに答える | `Answered` | `NoSuchRequest` |
| `ClaudeCloseSession(session-id, reason)` | 会話を閉じる(冪等) | `SessionClosed` | `ProcessStillAlive` |
| `ClaudeSessionStatus(home, cwd, session-id)` | 会話の状態と transcript の在否を読む | `SessionStatus` | — |
| `ClaudeExportSession(home, cwd, session-id)` | transcript の jsonl の写しを取り出す(`ResumeSession(carry=Rebuilt(写し))` で別の家へ持ち込める) | `SessionExported` | `SessionNotFound` |

`ClaudeExportSession` が写すのは transcript の jsonl 1 つだけ。`<session-id>/` の下の subagent の記録と `memory/` は写さない(残りの設計)。

型は `doeff_claude_code.values`(欄の値)・`doeff_claude_code.lines`(行の種類と手番の終わり)・`doeff_claude_code.effects`
(effect と答え)にある。手番の終わり(`ClaudeTurnEnd`)は手番ごとにちょうど 1 つ:
`Completed` / `Failed` / `Interrupted` / `BackendLost`(終わりの行を読む前に process が消えた — 次の手番は同じ `ResumeSession` で頼めばよい)。

## handler

- `doeff_claude_code.handler.claude-code-handler(host)` — 本番。`ClaudeCodeHost(command, clock)` を composition root が 1 つ作る。
  `command` = 実行ファイルと前置きの引数(例 `#("claude")`)、`clock` = 行の時刻を刻む関数(`doeff_claude_code.clock.clock-of` に
  doeff-time の時間の handler を渡して作る)。手番ごとに process を起こし、手番の終わりの行で降ろす。
- `doeff_claude_code.fake.fake-claude-code-handler(world)` — fake。`FakeClaudeWorld(responder)` の筋書き(入力の本文 → `FakeReply`)
  で同じ effect に memory の上で答える。doeff-time の時計で進むので、仮想の時計の下では一瞬で終わる。

どちらの handler も外側に doeff-time の時間の handler(本番 = `sync-time-handler`・模擬 = `sim-time-handler`)と doeff の scheduler を要る。

資格・PATH・HOME は `ClaudeHome.env` で composition root が渡す(handler は os.environ を読まない)。

## 検

```sh
uv run --no-sync pytest packages/doeff-claude-code/tests -m "not e2e"
```

`tests/test_scenarios.hy` の筋書きは 3 つの解釈器で走る: `fake`(fake + 仮想の時計)・`stub`(本番の handler + 替え玉の CLI
`tests/stub_cli/claude.hy`)・`real`(本番の handler + 本物の claude・印 `e2e`)。`real` は env `DOEFF_CLAUDE_CODE_REAL_CONFIG_DIR`
(個人の profile の CLAUDE_CONFIG_DIR)が在る時だけ走る:

```sh
DOEFF_CLAUDE_CODE_REAL_CONFIG_DIR=$HOME/.config/<個人の profile> uv run --no-sync pytest packages/doeff-claude-code/tests -m e2e
```
