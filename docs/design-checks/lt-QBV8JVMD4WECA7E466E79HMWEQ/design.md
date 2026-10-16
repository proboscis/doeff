# 設計本文: coordinator が process の中の自分の止まりの直後は lease 切れを数えない(doeff-cluster)

- 依頼: lt-QBV8JVMD4WECA7E466E79HMWEQ(card ki-d033e5f969cf・親 card ki-b4d94c618094)
- 完了の範囲: **実装完了まで**(doeff main に着地済み)。配備(本番の coordinator の版の引き上げ)は範囲の外。
- 基準版(変更前): afe373773ebea07f8df1cf784ccd14cdc29c52e9
- 実装版(変更後・main に着地): e8d9481453ebea5511faa94ff2c7e109080fc45c(マージの列 L2333)
- repo: proboscis/doeff。対象は packages/doeff-cluster と docs/adr。

## 1. 固定した要件(受入条件)

1. 模擬で coordinator を 3 分止めた後、再開後に WorkerGone が 0 回で、配る担当の判断に no-runs-on-worker が出ない(doeff の側では「置き先が移らない」で見る)test が通る。
2. 本当に沈黙した worker は、止まりの猶予が明けた後に従来どおり WorkerGone になる test が通る。
3. doeff の docs/adr に決定が 1 句で記録され main に入っている。

前提(上流の決定・変えない): coordinator が自分の止まりを検出し、止まりの直後は lease 切れを数えない。置き場(atlas)は変えない。agora-controllers 側は変えない。起動直後の猶予と同じ考えを「前の歩からの経過が lease を越えた時」へ広げる。

利用状況: coordinator は 1 台。worker は heartbeat を lease-ms/4(既定 2.5 秒)ごとに送る。coordinator は要求が無い間、次の期限(生きている worker の lease の期限 + 1 ms など — ADR-DOE-CLUSTER-002)まで受付を待つ。lease-ms = 10 秒・worker の自己停止(fence)= 20 秒・移し替え(reassign-after-ms)= 60 秒(条 C4: 移し替えは worker の自己停止より後)。

## 2. 責務(module)

| id | 実体 | 責務 | 所有する知識 | 隠す知識 | 公開の口 | effects | 寿命 | 不変条件 |
|---|---|---|---|---|---|---|---|---|
| M1 stall-judgment | coordinator/core/api_policy.hy の `stall-of`・`clocks-shifted` | 前の歩の刻みと今の刻から「止まりか」を決め、止まっていた長さだけ時計をずらした状態を作る | 止まりの定義(予定の外の長さ = 前の歩の長さ + 予定の起きる刻からの寝過ごし)・閾(lease-ms)・ずらす物の一覧(worker の最後の連絡と印・task の lease・Rollout の段の起点・入れ替えの期限・盤の lease・alive-ms) | 閾と内訳の求め方、ずらす欄の一覧 | `stall-of(pace: StepPace\|None, now: int, timing) -> Stall\|None`・`clocks-shifted(state, now, gap>0) -> ClusterState` | なし(純粋な判断 — 時計も I/O も読まない) | 状態を持たない(歩ごとに呼ばれる) | ずらしは期限を後ろへだけ動かす(移し替えを早めない)。予定どおりの起き(期限ちょうど)は止まりでない。ずらした後の worker の沈黙 = 前の歩の時点の沈黙 |
| M2 loop-pacing | coordinator/core/program.hy の `run-coordinator`・`coordinator-step`・`stall-resumed`、wake_policy.hy の `planned-wake-ms` | 歩の刻み(判断の刻・待ち始めた刻・予定の起きる刻)を測って次の歩へ渡し、調停(tick)の前に M1 の答えを当てて log に 1 行出す | 刻みをいつ測るか(壁の時計の読みの位置)・ずらしを調停の前に当てる順・保存は歩の前の状態との差 | 刻みの測り方 | `StepPace`(coordinator/intent/due_model.hy)・`coordinator-step` の任意の引数 `:pace` | 壁の時計の読み(now-epoch-ms)・slog | 調停ループの変数(前の歩の判断の刻)— process の一生の内 | ずらしは tick より前・SaveState(歩の前の状態, 歩の後の状態)が差としてずらしを耐久にする |
| M3 liveness | coordinator/core/cluster_policy.hy の `alive`・`liveness-deadline`・`note-liveness`・`liveness-moves` | 最後の連絡 + 窓で生死を判じ、沈黙の集合の差から WorkerGone / WorkerBack を出す | 生死の比べ(now <= last-seen + window) | なし(変更なし) | 既存のまま | なし | なし | 出来事は歩の前後の沈黙の集合の差だけから出る |
| M4 restart-resume | api_policy.hy の `resume-after-downtime`・coordinator/entry/main.hy の読み直し | 起き直しの時に、最後の生存の印(alive-ms)からの長さを止まりとしてずらす | alive-ms からの長さの求め方 | なし | 既存のまま(中身は M1 の clocks-shifted を呼ぶ) | 置き場の読み書き(entry) | 起動の 1 度 | 止まりの後に alive-ms = now を耐久にするので、止まりの直後の再起動で二重にずらさない |
| M5 sim-world | sim/local.hy の `StallCoordinator`・`stall-taken`、coordinator/protocol/request_queue.hy の `stalled-until` | 模擬で coordinator の機体ごとの止まりを作る(受付を閉じ・並んだ要求を切り・取りの後に調停ループを明けまで止める) | 止まりの模擬の仕方 | 模擬の仕組み(coordinator の code は知らない) | 検の effect `StallCoordinator(seconds)` | Delay・CompletePromise(sim の scheduler と時計) | 1 回の止まり | coordinator の code(M1〜M4)に模擬だけの分岐を持ち込まない |

公開契約で変えない物: coordinator の HTTP の口・worker との heartbeat の取り交わし・WorkerGone / WorkerBack の形・保存の鍵の形・agora-controllers。

## 3. 変更シナリオと事前の主張(盲検の前に固定)

| id | 軸 | 適用 | 変える要求 | 主張 | 予想する波及 | 変えない |
|---|---|---|---|---|---|---|
| S1 | hardware | 適用 | 置き場や WAL の disk が変わり(zeus へ移す・遅い disk)、保存の詰まりの長さの分布が変わったので、止まりと数える閾(例: 前の歩の長さの閾を client-reply-ms へ)を変える | 閾と内訳の定義は M1 の stall-of 1 か所に在り、変えるのは M1 とその判断の test だけ | M1 | M2・M3・M4・M5 |
| S2 | storage | 適用 | 保存の形(durable_kv の鍵・置き場の実装)を変える | ずらしは歩の前後の状態の差(SaveState)で耐久になり、M1・M2 は置き場を知らない。置き場を変えても M1・M2 は変わらず、ずらしは差として必ず保存に載る(止まりの直後の再起動で二重にずらさない) | durable_kv / wal_store だけ | M1・M2・M3・M5 |
| S3 | simulation | 適用 | 決定的な模擬(仮想の時計)で、機体ごとの止まりを本物の coordinator の code のまま再現する | 止まりの検出は時刻を時計の effect でだけ読む(M2)ので、模擬は M5 に検の effect と受付の列の欄を足すだけで済み、M1〜M4 に模擬だけの分岐は無い | M5 | M1・M2・M3・M4 |
| S4 | effects | 除外 | — | 理由: この変更が足す外の副作用は既存の slog の 1 行だけ。止まりを知らせの broker へ出す等の外の副作用は要求に無い | — | — |
| S5 | distribution | 除外 | — | 理由: coordinator は設計上 1 台(README「coordinator(1 台・状態は追記の log + snapshot)」)。止まりの検出は 1 process の中の判断で、複数台化は要求に無い | — | — |
| S6 | concurrency | 除外 | — | 理由: 調停ループは 1 本の Program で歩を順に回し、受付の HTTP の thread(変更なし)は要求を列に並べるだけ。ずらしは歩の頭(要求の判断の前)に 1 度当たり、thread をまたぐ共有の状態を足さない | — | — |

前提: 生きている worker が居る間は、予定の起きる刻は最大でも前の歩から lease + 1 ms 先(liveness-due)。lease-ms 以下の止まりは見つけない(直す前と同じ)。止まりが歩ごとに続く間は経過を数えない(その間は死んだ worker の判定も延びる — 止まりの log が歩ごとに出る)。

## 4. 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 止まりの定義が 1 か所(M1) | 型: StepPace / Stall の defrecord(欄の型つき)・stall-of の :pre / :post(答えは Stall か None)。判断の test が閾の境(lease ちょうど / +1 ms)・期限ちょうどの起き(止まりでない)・期限なしの待ちを縛る | coordinator/intent/due_model.hy・api_policy.hy・tests/test_coordinator_stall.hy | pytest(マージの列の門・日次)・ADR-DOE-CLUSTER-002 の law a-coordinator-stall-is-not-silence の enforced-by | 閾を別の module に写す実装を静的に断る規則は無い(test が答えの意味を縛るだけ) |
| M1 は純粋(時計・I/O を読まない) | MODULE-TAGS {:role "judgment"}・defk の :tags {:role "judgment"}。時刻は引数 now / pace でだけ受ける | api_policy.hy | doeff-cluster の linter / architecture.hy の層の規則(既存) | この環境に doeff-linter が無く、手元では当てていない |
| ずらしは期限を後ろへだけ(C4 を破らない) | clocks-shifted の式(+ gap・min now)と、sim の test(止まりの後も置き先が同じ worker の同じ世代) | api_policy.hy・tests/test_coordinator_stall_on_sim.hy | pytest | 全部の期限の欄を網羅したかは test で縛っていない(起き直しと同じ一覧を共有する事で揃える) |
| 調停の前に当てる・保存に載る(M2) | coordinator-step の順(stall-resumed → tick)・SaveState(state, next) | program.hy | sim の test(実の run-coordinator を回す) | 再起動と組み合わせた test は無い |
| coordinator に模擬の分岐を持ち込まない(M5) | sim の効果は sim/local.hy の世界と observe-requests の見張りに置き、coordinator は本番と同じ要求だけを受ける(ADR-DOE-CLUSTER-002 R7) | sim/local.hy・request_queue.hy | sim の test・直す前の形での失敗の確認 | 静的に断る規則は無い |

## 5. 実行の材料

- 判断の test: `uv run pytest packages/doeff-cluster/tests/test_coordinator_stall.hy`
- 模擬の test: `uv run pytest packages/doeff-cluster/tests/test_coordinator_stall_on_sim.hy`
- 作業木: /home/ubuntu/.worktrees/doeff-wt-stall-design-check(HEAD = 実装版 e8d948145)。変更の全体: `git -C <作業木> diff afe373773 e8d948145`
