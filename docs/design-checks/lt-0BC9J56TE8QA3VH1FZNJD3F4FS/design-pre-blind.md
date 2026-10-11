# 盲検の前の主張 — codex のターンを名乗らない知らせをどのターンへ積むか(依頼 lt-0BC9J56TE8QA3VH1FZNJD3F4FS)

- 対象の版: doeff `6e09cde279ca8885e2cdace00d79821bfd1bdd0f`(main)。
- 完了の範囲: 実装完了まで。直し(読みの thread がターンを立てる)は 42e67d88e(L2086)で main に在り、この依頼は順の検を 6e09cde27(L2093)で足した。
- card: ki-e154a953dee7。

## 1. 固定した要件

| # | 要件 | 出所 |
|---|---|---|
| R1 | codex の会話のターンが口座の利用枠で落ちた時、尽きた窓の戻りの刻が毎回 agora まで届く | 依頼の目的 |
| R2 | turn/started の後、走っているターンの印を置く前に読んだ枠の知らせ(account/rateLimits/updated)が、そのターンの記録に積まれる事を、absorb を順に呼んで決定的に確かめる | 依頼のする事 1・受入の条 c09f307b3 |
| R3 | ターンの id を持たない知らせを、終わったターンや outside に落とさない | 依頼のする事 2 |
| R4 | agora の結合の検 test_codex_usage_limit_on_agent_runtime.hy が負荷の下でも続けて 12 回緑 | 受入の条 cfd9ac1d3 |

## 2. 責務(module)

| id | 責務 | 持つ知識 | 隠す物 | 公開の口 |
|---|---|---|---|---|
| codex-lines | stdout の 1 行を型つきの記録へ分ける(純粋)。どの記録がターンを名乗るかを `TurnRecord` の 1 か所で決める | codex 0.162.1 の JSON-RPC の形・`TurnRecord` の集まり | JSON・wire の型 | `classify-line`・記録の型・`TurnRecord` |
| codex-handler | 記録を会話の状態へ積む(`absorb`)。走っているターンを立てる(`SessionRuntime.mark-running`)。ターンの記録ごとに枠の知らせを重ね、終わりに利用枠切れの事実を載せる | どのターンへ積むか・走っているターンの印 `current-turn`・`outside`・`late` | 子 process・読みの thread・lock | 公開 effect(CodexStartTurn・CodexReadTurnEvents ほか)・頁の end(TurnEnded.usage-limit) |
| codex-limits | 枠の知らせを枠ごとに重ね、利用枠切れの事実を作る(純粋) | 閾値 100・遅く戻る窓・名乗らない窓は前の値を消さない | 判定の中身 | `merged-limits`・`usage-limit-hit-of` |
| codex-adapter | doeff-agents の headless の codex の adapter。UsageLimitHit を AgentAccountLimit へ写す | 窓の長さ → 窓の名の表 | doeff-codex の型 | `AgentTurnFailed.account_limit` |
| agora-consumer | agora の agent runtime。AgentAccountLimit を読んで口座を外す | 口座・配る判断 | — | doeff-agents の公開型だけを読む |

## 3. 公開契約と所有

- `absorb(runtime, record)` は読みの thread が lock の中で 1 行ずつ呼ぶ。ターンの割り当ての判断はここだけ。
- 走っているターンの印 `current-turn` を書くのは `mark-running` だけ。呼ぶのは absorb の 2 か所(turn/start の答え `Response` が turn-id を名乗る時・`TurnStarted`)だけで、答えを待つ側(start-turn)は書かない。
- ターンを名乗らない記録(`RateLimits`・`ServerRequest`・その他)は `running-log`(current-turn の記録が終わっていなければそれ)へ、無ければ `outside` へ。
- ターンを名乗る記録(`TurnRecord`)は名乗ったターンへ。終わったターンを名乗る遅れた記録は走っているターンへ、無ければ `late`(次に立てるターンの頭)。
- `TurnLog.limits` はターンごと。ターンをまたいで持ち越さない。

## 4. 変更シナリオと主張(盲検の前に固定)

| id | 軸 | 変更 | 主張 | 予想の範囲(変える) | 変えない |
|---|---|---|---|---|---|
| S1 | concurrency | 読みの thread と答えを待つ thread の速さの差、または codex の行の順の差(turn/started が答えより先・枠の知らせが答えと turn/started の間)で、枠の知らせの届く時点がずれる | ターンを立てるのは読みの thread の absorb の中の mark-running だけで、答えを待つ側はターンの印を書かない。よって thread の間の時間の差は割り当てを変えず、行の順の差への対応は codex-handler の absorb の 1 か所で済む | codex-handler | codex-lines・codex-limits・codex-adapter・agora-consumer |
| S2 | effects | codex の版上げで、ターンを名乗らない新しい通知(口座や thread の断面)が増え、ターンの中で届く | ターンを名乗らない記録の割り当ては absorb の既定の枝(走っているターン・無ければ outside)が受けるので、新しい通知は codex-lines に記録を足すだけで走っているターンへ積まれる。ターンの間に届いた枠の知らせは次のターンへ持ち越さない | codex-lines(記録を足すなら) | codex-handler・codex-limits・codex-adapter・agora-consumer |
| S3 | simulation | 新しい行の順の失敗ケースを、thread や時間に頼らず決定的に足す | absorb は lock の中の状態と 1 記録だけで決まり、答えを待つ側の節はターンの割り当ての状態を変えないので、読みの thread の順は absorb の呼びの順で決定的に再現できる。足すのは検の file だけ | doeff-codex の検の file | codex-handler・codex-lines・codex-limits・codex-adapter・agora-consumer |
| X-hw | hardware | 対象外 | 行は JSON の文字で機体に依らない | — | — |
| X-st | storage | 対象外 | ターンの記録は process の memory の値(KEPT-TURNS 本まで)で保存しない。保存は agora の側 | — | — |
| X-dist | distribution | 対象外 | codex の process・読みの thread・handler は 1 つの Pod の 1 つの process の中。機体をまたぐ共有の状態は無い | — | — |

前提: codex は 1 つの stdout の行を 1 本の読みの thread で順に読む。absorb は lock の中で呼ばれる。

## 5. 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| ターンを名乗らない枠の知らせは、turn/start の答えの後のターンへ積まれる | 検(absorb を読みの順で呼ぶ) | `packages/doeff-codex/tests/test_limits.hy` の test-a-rate-limit-update-read-right-after-the-turn-start-answer-joins-that-turn | doeff の pytest(マージの列の検・日次) | 答えの側の mark-running だけを見る |
| 同じく turn/started の後のターンへ積まれ、終わった前のターンにも outside にも落ちない | 検(続きの会話・答えを読まない) | 同 file の test-a-rate-limit-update-read-right-after-turn-started-joins-that-turn | 同上 | turn/started の側の mark-running だけを見る |
| どの記録がターンを名乗るかは codex-lines の 1 か所 | 検 | `packages/doeff-codex/tests/test_turn_records.hy` | 同上 | 集まりと記録の型の一致を見る |
| 利用枠切れの事実が fake と本番の handler で同じ | 検 | `packages/doeff-codex/tests/test_scenarios.hy` | 同上 | 録った行の 1 つの順だけ |
| 答えを待つ側が current-turn を書かない | 無し(静的な規則は無い) | — | — | 型や lint では言えない。上の 2 つの検が、読みの thread だけで割り当てが決まる事を absorb の呼びだけで確かめる |
| agora まで刻が届く | 結合の検 | agora-controllers `controllers/agora_sim/tests/test_codex_usage_limit_on_agent_runtime.hy` | 印 real_world — 日次の pytest-edge | 壁の時計・本物の子 process |
