# 割り込みの本文の実 CLI 物理(段 8 lane 4x・agora-redesign #56 の Phase 0 プローブ)

headless の claude が**走っている手番の途中に届いた本文をどう扱うか**を実物の CLI で測った記録。
resume-physics.md と同じ役割 — 偽 CLI(tests/headless_stubs/claude)が真似る本物の測定値を凍結する。
測定の手順(script は測定機の一時 file — API を撃ち実クォータを消費するので repo には置かない): `claude -p
--input-format stream-json --output-format stream-json --verbose --include-partial-messages --model <model>
--permission-mode bypassPermissions` を stdin / stdout の pipe で起こし、`{"type":"user","message":{"role":"user",
"content":<本文>}}` の行を書く(stdin は閉じない)。stdout の行を `time.time()` の相対時刻つきで記録し、1 通目の
本文の途中(1 通目から 12 秒後)に 2 通目を書く。env から `CLAUDECODE` / `HERDR_*` / `CLAUDE_CODE_SESSION_ID` 等の
入れ子の印を外す(親の session の hook を継がない)。

測定日 2026-09-13。claude Claude Code 2.1.270(model claude-sonnet-5・`--permission-mode bypassPermissions`)、
機体 = 会社 Mac(個人 profile の家)。

## claude — `-p --input-format stream-json --output-format stream-json`

- **stdin の user の行は走っている手番に注入される(割り込み)**: 「`sleep 4` を Bash で 12 回、数ごとに 1 回」を
  頼み、2 回目の Bash の途中(t = 12.15 s)に `{"type":"user","message":{"role":"user","content":"INTERRUPT: stop
  counting right now. Reply with exactly the word INTERRUPTED-ACK …"}}` を stdin へ書いた。CLI はその Bash の
  `tool_result`(t = 16.16 s)の**次の境界で本文を model に見せ**、assistant が `INTERRUPTED-ACK` と答え
  (t = 17.8 s)、その後に `{"type":"result", "num_turns": 3}` が 1 度だけ出た(t = 18.09 s)。
  → 割り込みの本文は**同じ手番の中**で処理され、result は 1 つ(手番は 1 つのまま)。
  注入した user の行は stdout に echo されない(stdout の `user` の行は tool_result だけ)。
- **process は result の後も生きる(温かい)**: stdin を閉じない限り process は降りず(result から 84 秒後の
  close まで生存)、次の user の行が次の手番になる。2 手番目は同じ `session_id` で `system/init` の行がもう 1 度出て、
  2 つ目の `result` が出た(`run-multiturn-*.jsonl`: FIRST-DONE → SECOND-DONE・同じ session_id)。stdin の EOF で
  process は 0 で降りる(≈ 0.5 s)。
- **止める合図は従来どおり SIGINT**(result を出さずに降りる)— 旧の作法(本文 + EOF・1 手番 1 process)は
  `--input-format stream-json` を付けないと同じで、割り込みの口が無い。
- 含意(sessionhost の物理): claude の headless は `--input-format stream-json` の**温かい process**へ
  (impls/headless_argv.hy の CLAUDE-HEADLESS-FLAGS)。手番の本文 = user の行(閉じない)、割り込みの本文 = 同じ
  user の行を手番の途中に(`ClaudeDialogue.inject` — `in_flight` の間だけ。手番の外に書くと**次の手番**になるので
  器は断り、呼び手が queued へ倒す)。
- 追記(段 12 lane 12e・agora-redesign #517・実弾 2026-09-17 19:4x): result の後も stdin が開いていると、CLI は
  自分の background task / Monitor の完了(`<task-notification>`)で model を**手番の外で**起こし直し tool を撃つ
  (同じ会話の 2 つの process が本番に作用・stream が閉じた後なので記録に載らない)。⇒ **手番の終わり = 対話の
  終わり = process の終わり**: 器は result の行で stdin に EOF を出して降ろす(`ClaudeDialogue._end` の
  `Step.close` → `HeadlessProcess.retire`・EOF で降りない process は猶予の後に SIGTERM → SIGKILL)。次の手番は
  毎回 `--resume <sid>` の新しい process(`accepts_turn` は result の後は偽)。温かい claude(同じ process への
  次の手番)は退役。codex(app-server・turn/start の無い手番は起きない)は温かいまま。

## codex — app-server(実測なし・依頼書の指定どおり)

- 割り込みの本文 = `turn/interrupt` を送り、interrupted の `turn/completed` を手番の終わりとして報告せずに、同じ
  thread へ本文の `turn/start` を積む(`CodexDialogue.inject`・host から見て手番は 1 つのまま)。app-server の
  `turn/interrupt` の受理形は codex 側の物理(turn の status = interrupted)で、この便では実 CLI を撃っていない —
  deftest(tests/test_sessionhost_headless.py の codex の inject)が Dialogue の作法を撃つ。実測は codex の家が
  在る機体で `codex app-server` に対して撃つ(TODO・agora-redesign #56 に登記)。

## 記録

- `/tmp/stage8-interrupt-phase0/run-stream-1789278477.jsonl`(割り込み・t 付きの stdout の要約)と
  `run-multiturn-1789278615.jsonl`(2 手番)— 測定機の一時 file(残らない)。要点は上の数字。

## 追記 2026-09-14 — 停止の合図(control_request interrupt)と注入の行の運命(段 10 lane 10n・agora-redesign #93 便 1)

測定日 2026-09-14。claude Claude Code 2.1.270(model claude-sonnet-5・`--permission-mode bypassPermissions`・argv は上と
同じ)、機体 = 会社 Mac(個人 profile の家)。script は測定機の一時 file(`~/.cache/lane10n/probe/probe2.py`・repo には
置かない)。stdin の user の行に **`uuid`**(record の最上位の欄)を付けると、CLI はその行の運命を
`{"type":"command_lifecycle","command_uuid":<uuid>,"state":…}` の行で名乗る(`system/init` の `capabilities` に
`msg_lifecycle_v1`・`interrupt_receipt_v1`・`interrupt_cancel_queued_v1` が出る)。state の閉語彙 = queued(命令の列に
入った)/ started(手番に汲まれた = **model がその本文を読む拍**)/ completed / cancelled / discarded / refused
(bundle の記述: 走っている手番に畳まれた行の completed は result の**前**、新しい手番になった行の completed は
result の**後**)。uuid の無い行は lifecycle を名乗らない(今日の `ClaudeDialogue.inject` は uuid を付けていない)。

### 場面 A — 長い道具の途中に注入 → 8 秒で control_request interrupt(`run-escalate-1789376904.jsonl`)

「`python3 -c 'import time; time.sleep(45)'` を Bash で前景で 1 回」を頼み、Bash の途中(tool_use から 4.1 s)に uuid つきの
user の行(INTERRUPT: … INTERRUPTED-ACK)を書いた。

| t(s) | 出来事 |
|---|---|
| 15.386 | stdin: user の行(uuid = U) |
| 15.388 | stdout: `command_lifecycle U queued`(**1 ms**) |
| 15.4〜23.4 | started は出ない(道具が走っている — 境界が来ない) |
| 23.487 | stdin: `{"type":"control_request","request_id":R,"request":{"subtype":"interrupt"}}` |
| 23.489 | stdout: `{"type":"control_response","response":{"subtype":"success","request_id":R,"response":{"still_queued":[U]}}}`(**2 ms**) |
| 23.492 | stdout: `user` の tool_result(is_error・"The user doesn't want to proceed with this tool use…")+ `user` の text "[Request interrupted by user for tool use]" |
| 23.493 | stdout: **`result` subtype = `error_during_execution`・is_error = true・num_turns = 3・result = ""**・errors = ["[ede_diagnostic] … stop_reason=tool_use"]・同じ session_id |
| 23.494 | stdout: `command_lifecycle <1 通目の uuid> cancelled` |
| 23.495 | stdout: **`command_lifecycle U started`**(停止から **6 ms**) |
| 24.268 | stdout: `system/init`(同じ session_id) |
| 25.082 | stdout: assistant "INTERRUPTED-ACK" |
| 25.320 | stdout: `result` success・num_turns = 1・"INTERRUPTED-ACK" → `command_lifecycle U completed` |
| 25.333〜27.118 | 3 手番目(THIRD-DONE)も同じ process・同じ session_id で通る。stdin の EOF で exit 0 |

→ **確定 1 のとおり**: 停止の合図で今の手番(道具)は止まり(result は `error_during_execution` / is_error — `interrupted` という
subtype は無い)、注入済みの user の行は `still_queued` に名指され、**同じ session の次の手番として即座に走る**(started まで
6 ms・model の返答まで 1.6 s)。process は降りない・session_id は保たれる。CLI の手番は 2 つ(result が 2 つ)。
含意: 停止の合図を出した後の `result`(is_error)は失敗ではなく「止めた段の終わり」で、`still_queued` に注入の uuid が在れば
host から見た手番はまだ終わっていない(codex の inject と同じ — interrupted の終わりを手番の終わりとして報告しない)。
`still_queued` に無ければ(abort の瞬間に畳みの途中だった uuid は次の手番にならない — bundle の記述)手番の終わりとして報告する。

### 場面 B — `priority: "now"` を付けた注入(control_request なし・`run-now-1789377012.jsonl`)

同じ長い道具の途中に `{"type":"user","uuid":U,"priority":"now","message":…}` を書いた。`queued` は 2 ms で出るが、
**道具は止まらず 45 秒走り切った**(tool_result は t = 54.05)。その境界で CLI は元の手番を**畳まずに切り**(result は
success・num_turns = 2・result = "" — model は tool_result を見ずに手番が終わる・1 通目の uuid は `cancelled`)、U を
新しい手番として走らせた(started → INTERRUPTED-ACK → completed)。→ `priority: "now"` は「次の境界で元の手番を捨てて
自分を先頭に」であって即時の停止ではない。即時に止める口は control_request interrupt だけ。この lane では使わない。

### 場面 C — 短い道具を繰り返す手番の途中に注入(control_request なし・`run-fold-1789377097.jsonl`)

「`sleep 4` を Bash で 6 回・1 回ずつ」の 2 回目の Bash の途中(t = 13.95)に uuid つきの行を書いた。`queued` 1 ms →
その Bash の tool_result(t = 17.18)の **0.46 s 後に `started`**(t = 17.65)→ 同じ手番の中で assistant "INTERRUPTED-ACK"
(t = 18.64)→ `completed`(t = 18.890・result の直前)→ `result` success・num_turns = 3・result = "INTERRUPTED-ACK"(1 つ)。
→ 09-13 の実測の再現 + lifecycle の裏づけ: 道具の境界で畳まれた行は **`started` が「model が読んだ」の印**で、手番は 1 つ。

### 09-14 の第 1 走(uuid なし・hook が `sleep 45` を background に倒した場合・`run-1789376581.jsonl`)

道具が即座に終わって(background 化)手番が生成に入った後(tool_result の 0.8 s 後)に注入した行は、**その手番には畳まれず**
(assistant は注入と無関係の本文を出して result・num_turns = 3)、result の 0.5 s 後に `system/init` がもう 1 度出て
注入の行が次の手番として走った(INTERRUPTED-ACK・num_turns = 1)。→ 注入が畳まれるのは**次の道具の境界が来る時だけ**で、
道具の無い生成の途中に注入した行は手番の終わりで自動的に次の手番になる(host が result で手番を閉じると、その次の手番は
誰の job でもない手番になる — uuid の lifecycle〔result の時点で `queued` のままの注入〕がこれを見分ける材料)。
また「注入の後に assistant の出来事が在る」は読んだ証拠にならない(この走で偽陽性: t = 16.3 の本文は注入を読んでいない)。

### 含意(lane 10n の設計への写し)

- 読んだ証拠 = 注入の行の `command_lifecycle started`(uuid を付ける — `ClaudeDialogue.inject` が採番)。assistant の出来事の
  有無では判定しない。
- 停止の合図 = `control_request` subtype `interrupt`(`ClaudeDialogue.escalate`)。答え `control_response.still_queued` に
  注入の uuid が在れば、続く `result`(is_error・`error_during_execution`)は手番の終わりとして報告しない(次の手番が
  即座に走る)。無ければ報告する。
- codex は注入の段が無い(turn/interrupt → turn/start・今日どおり)。
- 注入から読むまでの遅れ(道具の境界): 境界の 0.46 s 後。停止から次の手番の始まりまで: 6 ms。停止の受理: 2 ms。
- `uuid` の欄は UUID の形でなくてよい(`run-uuidfmt.log`: `msg-01K5…` も `acp/message/abc def` も `command_lifecycle` が
  同じ綴りで名乗り返す・uuid 無しの行は lifecycle を名乗らない)。→ 注入の uuid = **ACP の Message の id そのもの**にできる
  (agentd と Dialogue の間に対応表が要らない・events file の lifecycle の行が messageId を名指す)。
