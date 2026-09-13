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
  器は断り、呼び手が queued へ倒す)。次の手番は同じ process へ(`accepts_turn`)、process が降りていれば
  `--resume <sid>` で起こし直す(従来の道)。

## codex — app-server(実測なし・依頼書の指定どおり)

- 割り込みの本文 = `turn/interrupt` を送り、interrupted の `turn/completed` を手番の終わりとして報告せずに、同じ
  thread へ本文の `turn/start` を積む(`CodexDialogue.inject`・host から見て手番は 1 つのまま)。app-server の
  `turn/interrupt` の受理形は codex 側の物理(turn の status = interrupted)で、この便では実 CLI を撃っていない —
  deftest(tests/test_sessionhost_headless.py の codex の inject)が Dialogue の作法を撃つ。実測は codex の家が
  在る機体で `codex app-server` に対して撃つ(TODO・agora-redesign #56 に登記)。

## 記録

- `/tmp/stage8-interrupt-phase0/run-stream-1789278477.jsonl`(割り込み・t 付きの stdout の要約)と
  `run-multiturn-1789278615.jsonl`(2 手番)— 測定機の一時 file(残らない)。要点は上の数字。
