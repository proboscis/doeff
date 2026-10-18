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
| `ClaudeWarmSession(origin, spec)` | 会話の process を最初の入力の前に起動し、入力を書かずに待たせる(`origin` は `FreshSession` / `ResumeSession`) | `SessionWarmed` | `SessionNotFound` / `SessionIdInUse` / `TurnInFlight` / `CarryRefused` / `LaunchFailed` |
| `ClaudeReplaceSessionCredential(session-id, credential)` | 会話の生きた process の借りた資格を替える(手番の途中でも次の API の呼びから新しい token を使う) | `CredentialReplaced` | `NoSuchSession` / `SessionProcessDown` / `HomeCredentialSession` |

会話と手番の外の 1 回きりの問い(構造化の答えを 1 つ受けて降りる — 会話の id も記録も残さない):

| effect | すること | 答え |
|---|---|---|
| `ClaudePrintOnce(home, model, system_prompt, json_schema, input, timeout_seconds, credential=None, thinking_tokens=None, effort=None)` | CLI の print mode を `--output-format json --json-schema` で 1 回走らせる。本文は標準入力・道具なし・設定の源なし(`--setting-sources ""`)・家の MCP なし・記録なし | `PrintAnswered`(structured_output・usage・cost_usd・models・elapsed_ms・api_ms)/ `PrintFailed`(exit_code・api_status・detail・stderr_tail)/ `PrintTimedOut`(seconds・stderr_tail) |

借りた資格(`credential`)は会話の process と同じく、その process だけの設定 dir の `.credentials.json` に置いて、降りたら dir ごと消す。
`thinking_tokens` は子の env の `MAX_THINKING_TOKENS`(0 = 考えない)。待ちの上限(`timeout_seconds`)は CLI の起動と終わりの秒
(1 回 4〜6 秒)を含み、越えたら process の組を止めて `PrintTimedOut`。本番 = `doeff_claude_code.print_once.claude-print-handler(command)`・
fake = `doeff_claude_code.fake.fake-claude-print-handler(respond)`(respond = 宣言 → 答えの Program)。本番の handler は外側に doeff の
scheduler が要る(時間の handler は要らない)。

`ClaudeExportSession` が写すのは transcript の jsonl 1 つだけ。`<session-id>/` の下の subagent の記録と `memory/` は写さない(残りの設計)。

`ClaudeWarmSession` は、起動してから入力を受けられるまでの秒を入力の前に済ませるための effect。後に来た同じ会話の `ClaudeStartTurn`
は、起動条件(argv・cwd・env)が同じならその process に入力を書き、違えば停止して再起動する。新しい会話を事前起動した時は、最初の
ターンも同じ id の `FreshSession` で頼む(CLI は入力の前に会話の記録を作らない)。最初の入力の前に CLI が出してよい行は SessionStart
の hook の開始と応答だけで、ほかの行を出した process は停止する。ターンなしで片づける時は `ClaudeCloseSession`。

借りた資格(`ClaudeSessionSpec.credential` = `LentCredential` — access token・期限・契約の種類と階級)は子の env に置かない。handler は
その process だけの設定 dir(`<家の設定 dir>/.credential-homes/<乱数>/`・0700 — 家の設定 dir の直下の名への symlink を張る)に
`.credentials.json`(0600)で置き、子の `CLAUDE_CONFIG_DIR` をそこへ向け、process が降りたら dir を消す(`credential_home.hy`)。
起動条件のキーに token と期限は入らないので、資格だけが替わった手番は生きた process を使い回し、その手番の資格を同じ file に書く。
`credential` の無い宣言は今までどおり家の設定 dir の資格で起こす。

型は `doeff_claude_code.values`(欄の値)・`doeff_claude_code.lines`(行の種類と手番の終わり)・`doeff_claude_code.effects`
(effect と答え)にある。手番の終わり(`ClaudeTurnEnd`)は手番ごとにちょうど 1 つ:
`Completed` / `Failed` / `Interrupted` / `BackendLost`(終わりの行を読む前に process が消えた — 次の手番は同じ `ResumeSession` で頼めばよい)。
手番の途中で process が降りた終わり(`BackendLost` と、注入を待って飲んだ result の `Failed`)は、process の終了 code と stderr の末尾を
欄 `exit_code`・`stderr_tail` で持つ(stderr の末尾は `lines.STDERR_TAIL_CHARS` 字まで — 越えた分は頭を捨てる・#4207)。fake の
`FakeReply(lose=…)` は `lose_exit_code`・`lose_stderr` で同じ欄に載せる値を名乗る(名乗らなければ欄は None)。

## handler

- `doeff_claude_code.handler.claude-code-handler(host)` — 本番。`ClaudeCodeHost(command, clock, live_limit, credential_floor_seconds)` を composition root が 1 つ作る(生かす CLI の本数の上限と、借りた資格の期限の手前で止める床の秒 — どちらも既定なし・#3672)。
  `command` = 実行ファイルと前置きの引数(例 `#("claude")`)、`clock` = 行の時刻を刻む関数(`doeff_claude_code.clock.clock-of` に
  doeff-time の時間の handler を渡して作る)。手番ごとに process を起こし、手番の終わりの行で降ろす。
- `doeff_claude_code.fake.fake-claude-code-handler(world)` — fake。`FakeClaudeWorld(responder)` の筋書き(入力の本文 → `FakeReply`)
  で同じ effect に memory の上で答える。doeff-time の時計で進むので、仮想の時計の下では一瞬で終わる。返事を作る時に効果を出したい
  筋書きは `FakeClaudeWorld(respond=<kleisli>)`(入力の本文 → `FakeReply` の Program — 効果は fake の handler の外側が答える)。
- 生かす本数の上限(#4072 の E1b): 本番の handler は上限(`live_limit`)を越える起動でも process を止めず、待たせず、失敗にもしない。
  越える起動の時だけ log の 1 行(名 `LIVE-LIMIT-LOG`・event `live-limit-exceeded`)と知らせ `ClaudeLiveLimitExceeded`
  (`session_id`・起動の後の本数 `live`・`limit`・事前起動か `warm` — 答え None)を出す。止める CLI を選ぶのは上の層のホストで、
  ホストは外側にこの知らせの答え手を置く。fake は `FakeClaudeWorld(..., live_limit=n)` で同じ上限を持ち、同じ知らせを出す
  (log の行は出さない。`live_limit` を渡さない世界は上限を宣言しない)。
  `responder` と `respond` はちょうど 1 つ。
- 上限を宣言しないホスト(#4282): `ClaudeCodeHost` の `live_limit` は 1 以上の整数か `None`。`None` は「上限を宣言しない」
  (使い手が同時に生かす本数を別の物差し — 機体の memory の余白 — で決める時)で、本番の handler は起動の後の本数を上限と比べず、
  log の行も知らせ `ClaudeLiveLimitExceeded` も出さない(`None` を 0 や大きい数に言い換えない)。既定は無い — 上限を宣言しない
  呼び手も `None` を明示で渡す。整数を渡すホストの検査(1 以上・bool を断る)と振る舞いは同じ。

どちらの handler も外側に doeff-time の時間の handler(本番 = `sync-time-handler`・模擬 = `sim-time-handler`)と doeff の scheduler を要る。

資格・PATH・HOME は `ClaudeHome.env` で composition root が渡す(handler は os.environ を読まない)。

## 検

```sh
uv run --no-sync pytest packages/doeff-claude-code/tests -m "not e2e"
```

1 回きりの print の本番の handler は、替え玉の CLI `tests/stub_cli/claude_print.py`(素の Python — API は撃たない)で
`tests/test_print_once.hy` が確かめる。

`tests/test_scenarios.hy` の筋書きは 3 つの解釈器で走る: `fake`(fake + 仮想の時計)・`stub`(本番の handler + 替え玉の CLI
`tests/stub_cli/claude.hy`)・`real`(本番の handler + 本物の claude・印 `e2e`)。`real` は env `DOEFF_CLAUDE_CODE_REAL_CONFIG_DIR`
(個人の profile の CLAUDE_CONFIG_DIR)が在る時だけ走る:

```sh
DOEFF_CLAUDE_CODE_REAL_CONFIG_DIR=$HOME/.config/<個人の profile> uv run --no-sync pytest packages/doeff-claude-code/tests -m e2e
```
