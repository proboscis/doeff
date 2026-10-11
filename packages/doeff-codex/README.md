# doeff-codex

`codex app-server`(OpenAI の Codex の CLI の JSON-RPC の口・stdio)を headless で走らせる層 2 の部品。claude の層 2 の
doeff-claude-code と同じ置き場で、codex に固有の物だけを持つ。上の層(doeff-agents の adapter)はここの型だけを読み、process の寿命
(Popen・信号・降ろす手順)は上の層に出さない。

## この package が持つ物

| module | 何のため | 中身 |
|---|---|---|
| `doeff_codex.effects` | 上の層が codex のターンを effect で頼むため(公開の口) | 下の「公開 effect」の表 |
| `doeff_codex.values` | effect の欄の値 | `CodexSessionSpec`(環境・作業の dir・model・許可の方針・sandbox・考えの深さ・圧縮の閾値)・`CodexHome`・`CodexInput`(文字と画像)・`CodexImage`・`FreshThread`・`ResumeThread`・`CodexTurn`・`CodexEvent` |
| `doeff_codex.handler` | 本番の handler — 公開 effect に app-server の子 process で答えるため | `codex-handler`・`CodexHost`(composition root が 1 つ作る状態の持ち主) |
| `doeff_codex.fake` | fake の handler — 公開 effect に筋書きの答えで memory の上で答えるため | `fake-codex-handler`・`FakeCodexWorld`・`FakeReply`(`usage-limit` = 利用枠切れの事実 `UsageLimitHit` — 在れば誤りの種類 `usageLimitExceeded` の失敗で終える) |
| `doeff_codex.limits` | ターンの中の枠の知らせを重ね、利用枠切れで終わったターンの事実を作るため(純粋 — handler が使う) | `merged-limits`・`usage-limit-hit-of` |
| `doeff_codex.rpc` | app-server を起こす argv と、stdin へ書く要求・答えの 1 行を作るため | `app-server-argv`・`initialize-request`・`initialized-notification`・`thread-start-request`・`thread-resume-request`・`turn-start-request`・`turn-steer-request`・`turn-interrupt-request`・`server-response-line`・閉じた語彙 `ApprovalPolicy`・`SandboxMode` |
| `doeff_codex.lines` | stdout の 1 行を、上の層が読む型つきの記録へ分けるため | `classify-line`(1 行 → `CodexLine`)と記録の型 |
| `doeff_codex.process` | app-server の子 process を、ターンをまたいで生かして行を運ぶため(handler だけが持つ内部の器) | `CodexProcess`(stdin の書き手・stdout と stderr の読み手の thread・降ろす梯子) |

## 公開 effect

| effect | すること | 成功の答え | 失敗の答え(型で返す) |
|---|---|---|---|
| `CodexStartTurn(origin, spec, input)` | ターンを始める(`FreshThread` = 新しい会話・`ResumeThread(thread-id)` = 続き・`input` = 文字と画像) | `TurnStarted(turn)` | `ThreadUnknown` / `TurnInFlight` / `LaunchFailed` / `RequestRefused` |
| `CodexSteerTurn(turn, input)` | 走っているターンに入力を足す(turn/steer — codex はターンの次の区切りで読み、同じターンが続く) | `Steered` | `NoTurnInFlight` / `RequestRefused` / `LaunchFailed` |
| `CodexInterruptTurn(turn)` | 走っているターンを止める(終わりは出来事の `TurnEnded` — 状態 INTERRUPTED) | `InterruptRequested` | `NoTurnInFlight` |
| `CodexReadTurnEvents(turn, after-seq, wait-up-to)` | ターンの出来事(`CodexEvent` — seq と行の記録)と終わりを、新しい出来事か終わりが来るまで待って読む | `TurnEventPage(events, next-seq, end)` | `UnknownTurn` |
| `CodexAnswerRequest(turn, request-id, result)` | codex からの要求(出来事の `ServerRequest`)に答える | `Answered` | `NoSuchRequest` |
| `CodexCloseSession(thread-id, reason)` | 会話を閉じる(冪等 — 走っているターンは `BackendLost` で終わる) | `SessionClosed(was-running)` | `ProcessStillAlive` |
| `CodexLaunchCount(thread-id)` | 検の口: 会話のために起こした process の数 | 数 | — |

ターンの終わり(頁の `end`)はターンごとにちょうど 1 つ: codex が出した `TurnEnded`(状態 completed・interrupted・failed)か、
終わりの行の前に process が消えた `BackendLost`。同じ会話・同じ宣言の続きは生きた process を使い回し、宣言が違うか process が無ければ
新しい process で `thread/resume` する(判断は handler の start-turn の 1 か所)。どちらの handler も外側に doeff-time の時間の handler
(本番 = `sync-time-handler`・模擬 = `sim-time-handler`)と doeff の scheduler が要る。

ターンの頁に載る記録(本番の handler の振り分け):

| 記録 | 載るターン |
|---|---|
| ターンを名乗る記録(`lines.TurnRecord` — どの記録が名乗るかは lines が 1 か所で決める) | 名乗ったターン |
| 終わったターンを名乗る遅れた記録(後ろで走った命令の終わりなど — 録った行 `background-command`) | 走っているターン。無ければ取っておき、次のターンを始めた時にその頭に積む(上の層は終わったターンをもう読まない) |
| ターンを名乗らない記録(口座の枠の知らせ `RateLimits`・codex からの要求など) | 走っているターン(無ければ頁に載せない) |
| 会話の thread でない thread を名乗る記録(子の agent — `collabAgentToolCall` の spawnAgent が同じ process の別の thread で走らせ、子のターンの始まりと終わり・答えを同じ stdout に出す。録った行 `other-tools`) | 載せない(ターンの外 — 子のターンの始まりで走っているターンを替えず、子の答えを会話の答えにしない) |

会話の宣言(`CodexSessionSpec`)の欄が、どの要求のどの欄に載るか:

| 宣言の欄 | 載る要求と欄 | None の時 |
|---|---|---|
| `model`・`approval-policy`・`sandbox` | `thread/start`・`thread/resume` の `model`・`approvalPolicy`・`sandbox` | 送らない(codex の既定) |
| `auto-compact-token-limit` | `thread/start`・`thread/resume` の `config` の `model_auto_compact_token_limit` | config を送らない |
| `effort` | `turn/start` の `effort` | 送らない |

借りた口座(`CodexHome.auth-json` — 貸し手が封じた auth.json の中身)を持つ宣言の process は、元の CODEX_HOME の auth.json を
書き換えず、その process だけの家(資格の家 — `doeff_codex.credential_home`)で走る。codex は口座を `$CODEX_HOME/auth.json` から
だけ読み、同じ機体で別の口座のターンが並んで走るため。

| 家の中身 | 形 | 訳 |
|---|---|---|
| 家の dir | `<元の CODEX_HOME>/.credential-homes/<乱数>`・権限 0700 | 他の利用者から読めない |
| `auth.json` | 借りた口座の中身・作る時から権限 0600 | codex が口座を読む唯一の file |
| `config.toml` | 元の `config.toml` への link(元に在る時だけ) | 元の設定のまま走る |
| `sessions` | 元の `sessions` への link | 会話の記録を元に書く — 家を消しても次の process が同じ thread を `thread/resume` で続けられる |

家は process が降りた時(降ろす手順の最後と、process の終わりの callback の両方)に中身ごと消える。codex が家の中に作る状態の
sqlite・log は家と一緒に消える。形は本物の codex 0.162.1 で確かめた(家を消した後に別の家から同じ thread を続け、続きの呼びが前の
ターンの発言と答えを運んだ)。`CodexHome` の env と auth-json は repr に出さない。

## 行の記録(`classify-line` の答え)

| 記録 | 元の行 | 欄 |
|---|---|---|
| `TextDelta` | `item/agentMessage/delta` | 答えの文字の途中(thread・ターン・item の id と差分の文字) |
| `AgentMessageDone` | `item/completed`(item の種類 agentMessage) | 答えの全文 |
| `ReasoningDelta` | `item/reasoning/textDelta`・`item/reasoning/summaryTextDelta` | 考えている間の差分(要約か本文か) |
| `ItemStarted` / `ItemDone` | `item/started` / `item/completed`(agentMessage 以外) | item の種類の名と id(上の層が「考えている」「道具を呼んでいる」を出す材料)と、道具の種類の item の中身 `tool`(下の表・ほかの種類は None) |
| `TurnStarted` / `TurnEnded` | `turn/started` / `turn/completed` | ターンの始まりと終わり(状態 `TurnStatus` = completed・interrupted・failed・inProgress、失敗の文と HTTP の status、利用枠切れの事実 `usage-limit` — 下の段落) |
| `TurnError` | `error` | ターンの誤り(文・誤りの種類・HTTP の status・codex が繰り返すか) |
| `TokenUsage` | `thread/tokenUsage/updated` | この呼びの分(last)と thread の累積(total)の token の数 |
| `RateLimits` | `account/rateLimits/updated` | 口座の利用枠の名と窓(`RateLimitWindow` = 使った割合・長さ〔分〕・戻りの刻〔epoch 秒〕— 名乗らない窓は None)。thread もターンも名乗らない口座の通知で、handler は走っているターンに積む(turn/start の答えを読んだ時点でそのターンを走っているターンにする)。利用枠切れ(誤りの種類 `usageLimitExceeded`)では誤りの通知より先に届く(実測 `tests/recorded/codex-0.162.1/usage-limit`) |
| `ThreadStarted` | `thread/started` | thread の id |
| `Response` / `ErrorResponse` | 要求への答え(id の在る行) | 要求の id と、答えが名乗る thread・ターンの id / 誤りの code と文 |
| `ServerRequest` | codex からの要求(id と method の両方が在る行 — 道具の許可など) | 答えに使う id と method と、中を読まずに運ぶ params |
| `Other` | 語彙の外の通知 | method の名だけ(発明しない) |
| `Unparsed` | JSON として読めない行・形の合わない行 | method(読めた時)と訳 |

道具の種類の item の中身(`ItemStarted.tool` / `ItemDone.tool` — 始まりと終わりの時点の中身):

| item の種類 | 記録 | 欄 | 始まりと終わりの違い(録った行で確かめた) |
|---|---|---|---|
| `commandExecution` | `CommandRun` | 命令の文(codex が包んだ形のまま — 例 `/bin/bash -lc 'echo hi'`)・dir・状態 `ToolStatus`・出力(stdout と stderr を合わせた aggregatedOutput)・終了 code | 出力と終了 code は終わりで届く(始まりは None)。終了 code が 0 でない命令は状態 failed |
| `fileChange` | `FilePatch` | ファイルごとの変更 `FileEdit`(path・種類 add/delete/update・diff・移す先)・状態 `ToolStatus` | 始まりから変えるファイルと中身を持つ |
| `webSearch` | `WebSearch` | 検索の語・動きの種類(search・openPage・findInPage・other)・語の列・頁・頁の中の語 | 始まりは語が空で動きは other — 語は終わりで届く |
| `mcpToolCall` | `McpCall` | MCP の server の名・道具の名・引数(凍らせた JSON — 形は道具が決める)・状態・結果の text の block をつないだ文・text でない block の種類・誤りの文 | 結果と誤りは終わりで届く(始まりは None) |
| `dynamicToolCall` | `DynamicCall` | 道具の名・namespace・引数(凍らせた JSON)・状態・答えの inputText をつないだ文・文でない項目の種類・答え手が名乗った成否 | 答えと成否は終わりで届く。codex は呼びを要求 `item/tool/call` で答え手へ頼む(thread/start の `dynamicTools` に宣言した時だけ — 試験の口 experimentalApi) |
| `collabAgentToolCall` | `AgentCall` | 動きの名(spawnAgent・wait など)・状態・相手の子の thread の id・子への頼み・子の model・子ごとの最後の状態 `AgentState`(状態の名と最後の答え) | 子を始める動きの始まりは相手が空で model は空の綴り(None と読む)— 子の id・model・状態は終わりで届く |
| `imageView` | `ImageView` | 画像の path | 始まりと終わりが同じ(状態も結果も持たない) |

道具の状態 `ToolStatus` は completed・failed・declined・interrupted(子の agent の道具だけ)・inProgress。
ほかの item の種類は中身を読まない(`tool` は None)。
道具の中身の使う欄が無い・型が違う item は、method と訳を持つ `Unparsed` にする(道具の中身の無い item に読み替えない)。

利用枠切れの事実 `UsageLimitHit`(`minutes` = 口座が待つ窓の長さ〔分〕・`resets-at` = その窓が戻る刻〔epoch 秒〕・`text` = codex の誤りの文)は、handler がターンの終わり(`TurnEnded.usage-limit`)に載せる。行の分類では None。誤りの種類が `usageLimitExceeded` の時だけ作り、窓と刻はそのターンの枠の知らせ(枠の名ごとに重ねる — 名乗らない窓は前の値を消さない)のうち使った割合 100 以上の窓から読む。尽きた窓が 2 つ以上なら遅く戻る方。尽きた窓が無い・戻りの刻を名乗らない・知らせが無ければ長さも刻も None(刻を発明しない)。窓は長さで名乗る(窓の名は上の層の語)。fake は `FakeReply.usage-limit` の事実をそのまま終わりに載せる。

この版の語彙に無い値(版で増えたターンの状態・道具の状態・誤りの種類)は `UnknownValue` に中を読まずに入れて運ぶ。既知の値や None に読み替えず、
行も落とさない — 知らない状態でもターンの終わり(`TurnEnded`)は必ず届く(上の層がターンの終わりを待ち続けないため)。

## 起動の形を app-server にした訳

答えの文字の途中が要る(利用者 2026-09-10「どの会話も、文字が届くたびに 1 文字ずつ更新されない」)。codex 0.162.1 の `codex exec --json`
は答えを `item.completed` で丸ごと 1 行に出し、途中を出さない。app-server は `item/agentMessage/delta` で途中を出し、1 つの process で
ターンを続け、`turn/interrupt` で途中で止められる。実測は `tests/recorded/codex-0.162.1`(検 `test_lines.hy` の
`test-exec-json-carries-no-text-deltas`)。

## 録った実物の行

`tests/recorded/codex-0.162.1/` は、版を固定した本物の codex(release rust-v0.162.1 の linux musl・tar.gz の sha256
`86f268d81b898f3e144c5ecff2ad2fda2c5802e042d6195b72538fe0fa125057`)を、手元の偽の上流(Responses API の SSE)につないで録った行。
口座は要らない(答えの中身だけが偽物で、行の形は本物の binary が出した物)。`tool-calls.*`(道具の呼びのターン — 偽の上流が
`exec_command` で命令の成功・失敗・apply_patch を呼び、web 検索の後に答える。命令は本当に走る)は 2026-10-11 に同じ版で、筋書きを選ぶ形で足した。
`other-tools.*`(ほかの道具の呼びのターン — 録りの MCP の server `scripts/record_mcp_server.py` の道具・thread に宣言した動的な道具・
画像を見る道具・子の agent を始めて待つ道具を呼ぶ。子の agent の thread の行も混ざる)は 2026-10-12 に同じ版で足した。
版を上げる時は録り直す:

```sh
uv run --no-project python -I packages/doeff-codex/scripts/record_app_server_lines.py <codex の binary> packages/doeff-codex/tests/recorded/codex-<版> <作業の dir> [筋書き…]
```

筋書きの名(two-turns・interrupt・rate-limit・usage-limit・tool-calls・background-command・other-tools)を続けて書くと、その筋書きだけを録る(既定は全部と `codex exec --json` の比べ)。

## 検

```sh
uv run --no-sync pytest packages/doeff-codex/tests
```

`tests/test_scenarios.hy` の筋書きは 2 つの解釈器で走る: `fake`(fake の handler + 仮想の時計)・`stub`(本番の handler + 替え玉の
app-server `tests/stub_cli/codex_app_server.py` — 録った実物の行を、id を差し替えて返す)。ほかの検(行の分類・要求の組み立て・
子 process の器)は handler を被せない `plain` で走る。
