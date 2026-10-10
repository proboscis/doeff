# doeff-codex

`codex app-server`(OpenAI の Codex の CLI の JSON-RPC の口・stdio)を headless で走らせる層 2 の部品。claude の層 2 の
doeff-claude-code と同じ置き場で、codex に固有の物だけを持つ。上の層(doeff-agents の adapter)はここの型だけを読み、process の寿命
(Popen・信号・降ろす手順)は上の層に出さない。

## この package が持つ物

| module | 何のため | 中身 |
|---|---|---|
| `doeff_codex.rpc` | app-server を起こす argv と、stdin へ書く要求の 1 行を作るため | `app-server-argv`・`initialize-request`・`initialized-notification`・`thread-start-request`・`thread-resume-request`・`turn-start-request`・`turn-interrupt-request`・閉じた語彙 `ApprovalPolicy`・`SandboxMode` |
| `doeff_codex.lines` | stdout の 1 行を、上の層が読む型つきの記録へ分けるため | `classify-line`(1 行 → `CodexLine`)と記録の型 |
| `doeff_codex.process` | app-server の子 process を、ターンをまたいで生かして行を運ぶため | `CodexProcess`(stdin の書き手・stdout と stderr の読み手の thread・降ろす梯子) |

公開の effect と handler(ターンを始める・止める・出来事を読む)はまだ無い — 次の単位で、doeff-claude-code の `ClaudeStartTurn` などと
同じ形で足す。

## 行の記録(`classify-line` の答え)

| 記録 | 元の行 | 欄 |
|---|---|---|
| `TextDelta` | `item/agentMessage/delta` | 答えの文字の途中(thread・ターン・item の id と差分の文字) |
| `AgentMessageDone` | `item/completed`(item の種類 agentMessage) | 答えの全文 |
| `ReasoningDelta` | `item/reasoning/textDelta`・`item/reasoning/summaryTextDelta` | 考えている間の差分(要約か本文か) |
| `TurnStarted` / `TurnEnded` | `turn/started` / `turn/completed` | ターンの始まりと終わり(状態 `TurnStatus` = completed・interrupted・failed・inProgress、失敗の文と HTTP の status) |
| `TurnError` | `error` | ターンの誤り(文・誤りの種類・HTTP の status・codex が繰り返すか) |
| `TokenUsage` | `thread/tokenUsage/updated` | この呼びの分(last)と thread の累積(total)の token の数 |
| `ThreadStarted` | `thread/started` | thread の id |
| `Response` / `ErrorResponse` | 要求への答え(id の在る行) | 要求の id と、答えが名乗る thread・ターンの id / 誤りの code と文 |
| `ServerRequest` | codex からの要求(id と method の両方が在る行 — 道具の許可など) | 答えに使う id と method |
| `Other` | 語彙の外の通知 | method の名だけ(発明しない) |
| `Unparsed` | JSON として読めない行・形の合わない行 | method(読めた時)と訳 |

## 起動の形を app-server にした訳

答えの文字の途中が要る(利用者 2026-09-10「どの会話も、文字が届くたびに 1 文字ずつ更新されない」)。codex 0.162.1 の `codex exec --json`
は答えを `item.completed` で丸ごと 1 行に出し、途中を出さない。app-server は `item/agentMessage/delta` で途中を出し、1 つの process で
ターンを続け、`turn/interrupt` で途中で止められる。実測は `tests/recorded/codex-0.162.1`(検 `test_lines.hy` の
`test-exec-json-carries-no-text-deltas`)。

## 録った実物の行

`tests/recorded/codex-0.162.1/` は、版を固定した本物の codex(release rust-v0.162.1 の linux musl・tar.gz の sha256
`86f268d81b898f3e144c5ecff2ad2fda2c5802e042d6195b72538fe0fa125057`)を、手元の偽の上流(Responses API の SSE)につないで録った行。
口座は要らない(答えの中身だけが偽物で、行の形は本物の binary が出した物)。版を上げる時は録り直す:

```sh
uv run --no-project python -I packages/doeff-codex/scripts/record_app_server_lines.py <codex の binary> packages/doeff-codex/tests/recorded/codex-<版> <作業の dir>
```

## 検

```sh
uv run --no-sync pytest packages/doeff-codex/tests
```
