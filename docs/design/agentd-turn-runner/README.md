# agentd を doeff の中で置き換える — 今の責務の一覧・呼び名・10 件の決定の下書き

> **状態: 下書き(決定ではない)。** operator の指示(2026-09-25 逐語 "so before we go on to 'replace' agentd, we need to
> define what it is and if we need it that, given fable5.1's analysis right?")で置き換えの実装は止めた。先に今の agentd の
> 責務を 1 つずつ「要るか・要るなら誰の関心か(手番のスケジューラ / agent 実行 task / ACP / 別の係)・それでも独立した
> 『手番の実行係』が要るか」で判定する(構成のレビュー・会話 7fb8632d が Fable 5.1 に頼む)。下の 2〜5 節は
> 「独立した実行係を置く」前提の下書きで、その判定の結果で書き直す。着地の登記(doeff L367)は取り下げ済み。

- 書いた会話: c-01M369MCR3BRGFK80EZ8C5C1QG(2026-09-25)
- 土台の調査: 報告書「agentd とは何か — sessionhost / acp / agentd の今の姿と目標の構成」(2026-09-25・Fable 5.1・
  `/tmp/report-agentd-target-architecture-2026-09-25.html`)
- 関係する文書: herdr-hud `docs/design-checks/simplify-irai-2026-09-25/design.md`(依頼の抽象の設計 第 4 版 —
  手番のスケジューラの持ち主 = 会話 7fb8632d)

## 0. 今の agentd(`doeff-sessionhost join --role agentd`)の責務の一覧 — レビューの材料

事実だけを並べる(判定の欄は空けてある)。行番号は doeff origin/main(2026-09-25 11:22・e8bd8ea0)の
`packages/doeff-agents/src/doeff_agents/sessionhost/acp/agentd.hy`。判断はどれも `judgment.hy` の純関数、I/O は `handlers.py`。

| # | 責務 | 置き場(defk) | ACP の資源の読み書き | 周期・きっかけ | 判定(要るか / 誰の関心か) |
|---|---|---|---|---|---|
| 1 | 機体の登録と生存 | `join-tick`(706)・`lease-heartbeat`(903) | node を作成・status.lease・capabilities・observations を書く(state / conditions / withdraw は acp-scheduling) | 30 秒・期限 90 秒 | |
| 2 | 起こせる agent の種類の申告と取り下げ | `observe-agent-kinds`(830)・`withdraw-node-capabilities`(857) | node の capabilities | 拍ごと・host に届かない拍 | |
| 3 | pane の席(operator の対話の pane)の観測 | `observe-pane-seats`(805)— dotfiles `ai pane-sessions --json` | node の observations | 拍ごと | |
| 4 | transcript の在処の観測 | `observe-transcripts`(965) | node の observations.transcripts | 拍ごと | |
| 5 | 手番を拾う(claim)と実行方法の判断 | `receive-bound-jobs`(5090)・`receive-fresh-bindings`(5227)・`claim-job`(1924)・`start-claimed`(1665)。判断 `bound-to-me`・`next-arm-for-job`(launch / send / resume / rehydrate / defer) | agent-job を読み、phase Running・sessionHandle を CAS で書く(binding は書かない) | SSE で起きる・受け付けは拍の外の係 | |
| 6 | 入力の組み立て(prompt) | `mail-of`(2134)・`take-carried-input(s)`(2169・2203)・`mark-inputs-read`(2231)・`turn_input.hy`・`reply_channel.hy`(出どころの推し量り) | message を読む・conversation-input を taken → read に CAS | 手番ごと | |
| 7 | 資格を借りて返す | `borrow-lease`(1228)・`return-lease`(1201)・`retry-unanswered-borrow`(1861)・journal `leases.json` | profile の boundary を読む | 手番ごと・更新は `stream-job-slow` | |
| 8 | host への起動 / 続き / 再開 / 引き継ぎ | `incarnate`(1472)・`bind-conversation-home`(2066)・`history-for`(1352)ほか | conversation の status.home を書く | 手番ごと | |
| 9 | turn-record(見出し・実況・終わり) | `ensure-turn-record`(2859)・`adopt-existing-record`(2482)・`append-entries`(2591)・`end-turn-record`(3026)・`finalize-job`(3093) | turn-record の作成と status(agentd だけが書く)・本文は record :8874・中継 frame は `POST /api/streams/agentd/<session>` | 手番の間ずっと | |
| 10 | 記録の本文の送り待ち(spool) | `spool-record-bodies`(2719)・`flush-record-spool`(2736) | —(record service へ) | 拍ごと | |
| 11 | 手番の観測と終わりの判定 | `observe-job-fast`(3801)・`observe-job-slow`(3843)・`recover-job`(3864)・`settle-record`(3527) | agent-job を Ended・result | 拍ごと | |
| 12 | 割り込み・取り消し・取り下げの配達 | `deliver-interrupts`(4332)・`cancel-jobs`(4087)・`withdraw-jobs`(4113)・`interrupt-job`(3937) | agent-job の spec.cancel・phase Withdrawn を読む | 拍ごと | |
| 13 | 着かなかった Ended の書き直し | `record-unrecorded-ends`(4054) | agent-job を Ended | 拍ごと | |
| 14 | 走っている turn-record の巡回(対の job が終端なら ended) | `sweep-turn-records`(5278) | turn-record を ended | 周期 | |
| 15 | cache ping(prompt cache の温め直し)の実行 | `worker_loop.hy` の `cache-worker-loop`・`cache_maintenance.hy`・`cache_live.hy` → host RPC `session.cache-ping` | cache-operation の status を CAS・turn-record の cacheObservation | 1 秒 | |
| 16 | 残量(usage)の観測 | `observe-profiles`(990)・`observe-held-profiles`(1058)— dotfiles `ai usage --json` | profile の status.observed | 300 秒 | |
| 17 | drain(計画された停止) | `drain_marker.py`・判断 `declared-capacity-of` | node の capacity を 0 と名乗る | 印の file がある間 | |
| 18 | 温かい session の片付け | `retire-sessions`(691) | — | 拍ごと | |
| 19 | 会話の memory の畳み | `fold-memories`(3408)・`fold-one-memory`(3181)・`retire-one-memory`(3358) | agent-memory を作成・更新 | 周期 | |
| 20 | 要約(summarize)の手番 | `trigger-summarize`(1427)・`claim-summarize-job`(4500)ほか・子 process `claude -p` | summary・summarize の agent-job を作成 | きっかけ次第 | |
| 21 | verify の手番(script を走らせる) | `claim-verify-job`(4781)・`observe-command`(4853)ほか・子 process `sh -c` | verify の agent-job を Running / Ended | Bound の時 | |
| 22 | 担い手の publish | `PublishWorker` — dotfiles `ai route publish-worker --json` | — | 周期 | |

host(`--role host`)の責務は別の一覧: claude / codex の CLI の起動と観測(`headless_process.py`・`headless_protocol.py`・
`impls/headless_argv.hy`)・session の台帳(sqlite)・出来事の置き場(`headless_events.py` / `headless_outbox.py`)・
cache ping の 1 回きりの process(`cache_host.hy`)・report_result の MCP 中継(`relaymain.py`)・blue/green の区画(`host_slots.py`)・
TUI の経路(`launch.hy`・`policy.hy`)。

## 1. 呼び名(目標の図で使う語)

| 目標の名 | 意味 | 今の何に当たるか |
|---|---|---|
| 手番のスケジューラ(作る・置く) | 入力の台帳を見て agent-job を組み(作る)、機体と口座に割り当てる(置く)。message を知らない | acp-messaging の Decide.hs(作る)・agora-controllers `controllers/scheduling`(置く) |
| 手番の実行係(turn runner) | k3s の doeff service。自分に置かれた手番を取り、実行方法(起動 / 続き / 再開 / 引き継ぎ)を決め、資格を借り、機体へ「agent 実行 task」を送り、turn-record と出来事を書く。cache ping・drain・残量の観測・memory の畳みも持つ | 今の `doeff-sessionhost join --role agentd`(`sessionhost/acp/`)の判断の全部 |
| agent 実行 task(CLI の親) | 機体の doeff worker が受ける短い task。claude / codex の CLI を子 process として起こし、stdin に入力を流し、stdout の 1 行ごとに出来事の effect(`HeadlessEventAppend`)を出し、終わりの行を返す。本番と模擬の境界はこの task の中の子 process | `--role host` の `headless_process.py`・`headless_protocol.py`・`impls/headless_argv.hy`・`drivers.py` |
| 機体の常駐 | Mac にも pool の pod にも doeff worker 1 process だけ(label role=agent)。socket も sqlite も持たない | launchd の `com.masui.acp-agentd` + `com.masui.acp-sessionhost`(+ `-b`)/ pod の container agentd + host |
| 出来事の記録 | effect `HeadlessEventAppend` 1 つ。本番 = OTLP → ClickHouse(hot / warm / cold)。模擬 = メモリ | `headless_events.py` の置き場(file は当面の Mac の形)・`headless_outbox.py` |
| ACP | agent-control-plane の 1 つの意味だけ。当面は wire(HTTP)越しの相手。最終的には doeff の service になり、Program 側の effect は変えずに handler を wire → in-process に替える | — |

- 消える名前: agentd・sessionhost・host・relay(report_result の MCP 中継)・host slot。
- 残る名前: ACP・custody・record・doeff worker・agora_sim。
- 改名はまだしない(実行ファイル・module の名は R6 の片付けまで今のまま)。今の `sessionhost/acp/` は「agentd = ACP への腕」
  (同じ dir の README.md)。

## 2. 決まっていない点 10 件 — 決定の下書き

(下書き・0 節の判定待ち。)どれも戻せる決定として、報告書の推奨を採り、operator の既存の指示とぶつかる所は指示に合わせる案(該当は「指示との照合」に書く)。「独立した実行係を置く」前提が判定で崩れたら、1・2・5・7 は書き直しになる。

### 決定の下書き 1 — 機体の生存の正本

- 案: 当面は、手番の実行係が doeff coordinator の worker の生存(heartbeat)を ACP の node の行へ写す。
  スケジューラは ACP だけを読む形を保つ。ACP が doeff の service になった時にこの写しを消す。
  R1〜R3 の模擬の間は、今の agentd の Program のまま node の lease を実行係が書く(Program を変えない)。
- 理由: スケジューラ(会話 7fb8632d の持ち分)が node の行だけで配置を決めている。読み手を 2 つに割ると、
  写しの遅れで「生きている機体に置かない / 死んだ機体に置く」が起きる。書き手を 1 つ(実行係)にして写す向きを固定する。
- 指示との照合: ACP は純粋な制御面に保つ(2026-09-15)— engine には何も足さず、行の書き手は app のまま。
- 戻し方: 写しの handler を外し、今の agentd の `join-tick`・`lease-heartbeat` の書きへ戻す。

### 決定の下書き 2 — agent 実行 task の寿命

- 案: 「依頼 id で 2 度実行しない + 送り直された時は CLI の transcript から `--resume`」で受ける。
  手番の途中で task が切れる回数を模擬で測り、多ければ「実行係の入れ替えは走っている task が 0 の周まで待つ」
  (ACP ADR 9932b9 と同じ規則)を coordinator の入れ替えに足す。
- 理由: task は呼び手(実行係)に寿命が縛られる。host slot(blue/green)と drain 14400 秒はその回避策だったが、
  目標では host が消えるので、同じ保証を依頼 id の冪等と resume で持つ。doeff worker の実験の記録(94 行)と同じ形。
- 戻し方: 冪等の門を外し、入れ替えの待ちを既定にする。

### 決定の下書き 3 — report_result の MCP 中継(relaymain)

- 案: headless の経路では外す。結果は stream-json の result 行(claude)/ `turn/completed`(codex)から取る。
  ADR-DOE-AGENTS-005「report_result のデータ経路だけ」は TUI 向けの規則として残し、headless 専用の経路を実行係の ADR に書く(R6)。
- 理由: headless の CLI は終わりの行を必ず出す。中継は host の socket を前提にしており、host が消えると届け先が無い。
- 戻し方: agent 実行 task の argv に MCP の設定を戻し、中継先を task の中の口にする。

### 決定の下書き 4 — 実況(events_since)の読み口

- 案: 実況は出来事の記録の hot 層を effect(`HeadlessEventsSince`)で読む。本番の handler = ClickHouse の hot 層、
  模擬 = メモリ。画面サーバーも同じ effect で読む。第 4 版の「host の RPC で読む」の行は改訂する(改訂は第 4 版の持ち主の会話 7fb8632d が
  自分の変更で行う — 合意済み 2026-09-25)。
- 理由: host が消えるので RPC の口が無くなる。出来事は既に置き場の effect を通っている(doeff 094424fc・e8bd8ea0)。
- 戻し方: handler を host の RPC 読みへ戻す(effect の形は同じ)。

### 決定の下書き 5 — 実行係の粒度

- 案: 全機体で 1 service。書き手は lease-fence で 1 つ。機体は task の `requires`(role=agent・host=<名>)で選ぶ。
  混むことが模擬の測りで分かったら機体ごとに割る。
- 理由: 判断の状態(借りた資格の journal・走っている手番の表)を 1 か所に置け、機体が増えても service の数が増えない。
- 指示との照合: 実行係は Mac で走らせない(operator 2026-09-23 "turn-runner is not to be run on macs")— k3s の service。
- 戻し方: node 名で分けた service を宣言する(Program は同じ)。

### 決定の下書き 6 — fake の店が 2 つある件

- 案: agora_sim の店へ FakeAcp の検め(principal・書き手の名簿・schema)を移して 1 つにする。移し先は ACP の repo の
  fake の店(`clients/hy/acp_client/runtime/handlers_fake.hy` の正本 — agora_sim はその写し)で、持ち主は agora_sim の取りまとめ役
  (合意済み 2026-09-25 — ただし移し先は ACP の repo の fake の店で、区画 G が書き手の名簿・schema を足している最中)。実行係の模擬はその検めが入った店でも通るように書く。
- 理由: 契約の検めが 2 か所にあると、片方でだけ通る書き(本番で断られる書き)を模擬が見逃す。
- 戻し方: 実行係の模擬を `sessionhost/acp/fake.py` の FakeAcp に向け直す。

### 決定の下書き 7 — 実行係の code の置き場

- 案: 判断の純関数(`judgment.hy`)と agent 実行 task の親(argv の組み立て・stream-json の解釈・終わりの判定)は
  doeff の `doeff-agents` に残す(CLI の知識はそこの物)。service の宣言と handler の組は agora-controllers
  (`controllers/turn_runner/`・模擬の組は `controllers/agora_sim/`)。doeff-agents は agora を知らないまま。
  doeff クラスタの汎用の仕組み(RemoteJob・remote-inline / remote-cluster・defservice)は doeff の新しい package
  `doeff_cluster` へ移る途中なので、agora-controllers の `controllers/worker/` は編集せず import するだけにする(切り出しの担当と合意済み)。
- 理由: 他の service と同じ流儀(semgrep の対象・模擬の決まり)に乗る。doeff-agents に agora の語彙が入らない。
- 戻し方: service の宣言を doeff-agents 側へ移す(Program は同じ)。

### 決定の下書き 8 — cache-operation の行を作る側

- 案: 当面は今のまま(acp-messaging / acp-cache-keepalive が作る)。実行係は行を実行するだけ。模擬に ping の筋書きを足す。
- 理由: 作り手を動かすとスケジューラの設計(第 4 版)に波及する。実行の側の置き換えと同時に動かさない。
- 戻し方: 不要(今と同じ)。

### 決定の下書き 9 — 所有者不明の常駐 `doeff-sessionhost serve`(Mac・PID 48736)

- 案: 止めない・消さない。読むだけで正体を調べて記録し(下の 4 節)、誰が使っているかを 1 日の socket の接続で測ってから
  止めるかを決める。止める・unit を消すのは operator の明示の指示があった時だけ(自分で作っていない物は消さない規則)。
- 戻し方: 不要。

### 決定の下書き 10 — pool の pod の home と checkout

- 案: home の PVC(seat-home-state)は残す。cache と transcript の身元は git の状態 + env で持つ(operator 決定 2026-09-21)。
  checkout は agent 実行 task の effect `PrepareWorkdir` に寄せ、会話ごとの worktree にする(operator 決定 2026-09-22)。
  sidecar の dotfiles-follow は worker の code_prepare に置き換える。
- 理由: transcript は CLI 自身の物で、pod の世代をまたいで残す必要がある。checkout は手番の要る時に task が用意すれば常駐の sidecar が要らない。
- 戻し方: sidecar を戻す。

### 資格の受け渡し(決定 7・10 に付く注意)

実行係が custody から借りるのは access token だけで、task の引数として機体へ渡す(task の中で 0600 の file にするだけ)。
refresh token は写さない・運ばない(operator 2026-09-21)。

## 3. 本番の影の実行(報告書の R4)は採らない — 記録からの backtest に替える

- operator の指示(2026-09-25 逐語): "shadowing doesnt make sense because the current running system is already not reliable" /
  "with doeff we can always backtest from logs"。
- 替わりの形: 本番の記録を入力にした backtest と業務の不変量で確かめる。
  - 入力 = ACP の行の断面(agent-job・turn-record・conversation-input・node・profile)と、出来事の記録
    (ClickHouse `agentd_records.headless_events`・effect の記録)。
  - 手元の模擬(agora_sim)で、本物の実行係の Program をその入力で回す。ACP の読みは断面から答え、書きは突き合わせ(本番へは書かない)。
    CLI の process の代わりは、記録された stream-json の行をそのまま返す筋書き(同じ手番の同じ行)。
  - 判定 = 業務の不変量(下)の破りの一覧と、本番で実際に書かれた行と模擬が書いた行の差。
- 本番へ出す条件(R5 の前提): 模擬の筋書きと不変量が緑 + 直近の本番の記録の backtest で破り 0。
  配備は模擬で安定させてから(operator の決まり "never deploy until we stabilize our impl with sim environment on doeff")。

### 業務の不変量(agora_sim で回す)

1. 同じ手番を 2 度実行しない(turn-record の誕生の CAS)。
2. Bound の手番は必ず Running になるか、死んだ機体の手番は Pending へ戻る(戻すのは配置)。
3. 送信から手番の開始まで 2 秒以内(仮想の時計で測る)。
4. taken の入力の行は必ず turn-record を持つ。
5. 借りた token は必ず返す。
6. 出来事の量 × 機体数が ClickHouse の受けを超えない。
7. 実行係を入れ替えても手番を失わない(送り直し + resume)。

## 4. 所有者不明の常駐(PID 48736)の正体 — 2026-09-25 に読むだけで調べた結果

- 命令: `~/.local/share/uv/tools/doeff-agents/bin/doeff-sessionhost --db ~/.local/state/doeff/agentd.sqlite
  --socket /tmp/doeff-agentd-kento.sock --max-running 10 serve`。親 = launchd(PID 1・孤児)・cwd = `~/repos/agent-control-plane`・
  起動 2026-09-19 02:23。
- launchd の unit ではない(`~/Library/LaunchAgents` に該当なし・`launchctl list` に無い)。
- 起こしたもの: doeff-agents の client(`agentd_client.py` の `_agentd_command` — 既定の socket `/tmp/doeff-agentd-$USER.sock` に
  daemon が無ければ `serve` を detached で起こす自動起動)。agent-control-plane の checkout の中で doeff-agents の API を使った何か
  (uv tool 版の doeff-agents)が起こした旧い形の単独の常駐(ACP の腕を持たない)。
- 使われているか: session の台帳は 0 行・command 0 行(一度も session を起こしていない)。排他の lease は更新され続けている。
  log(`~/.local/state/doeff/agentd.log`)には、同じ既定の DB で別の `serve` を起こそうとして「lease is active: owner_pid=48736」で
  断られた記録が 09-20〜09-21 と 09-24 23:40 UTC(`~/repos/doeff/.venv` の `doeff-sessionhost` から)にある。
- 動かし続ける必要: 今の手番の経路(`com.masui.acp-agentd` + `com.masui.acp-sessionhost`)は別の socket と DB を使うので、この常駐に依存していない。
  ただし既定の socket へ繋ぐ client が他に居るかは 1 日の接続を測るまで断定しない(決定 9)。

## 5. 移る順番(R0〜R6)— 今の状態

| 順 | すること | 今回 |
|---|---|---|
| R0 | 呼び名をこの文書と図に写す・`sessionhost/acp/README.md` に「agentd = ACP への腕」 | 下書きのみ(登記は取り下げ) |
| R1 | 実行係の Program(今の agentd の Program そのもの)を agora_sim に載せ、fake の agent を置き換える。業務の不変量と反例 | 止めた(operator 2026-09-25) |
| R2 | 境界を CLI の process まで下げる: agent 実行 task の親を子 process の effect の上に書き、模擬では筋書きの fake が stream-json を返す | 止めた |
| R3 | 出来事の effect(`HeadlessEventAppend` / `HeadlessEventsSince`)を task の親から出す。模擬 = メモリ | 止めた |
| R4 | (影の実行は採らない)本番の記録からの backtest(3 節) | 止めた |
| R5 | 1 台ずつ入れ替え | しない(模擬で安定させてから) |
| R6 | 片付け(sessionhost/acp・host slot・relaymain・TUI の経路の退役・ADR の置き換え・PID 48736 の整理) | しない |
