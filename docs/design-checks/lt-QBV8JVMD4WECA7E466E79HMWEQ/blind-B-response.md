<!-- 盲検 B の未加工の返答(Agent tool の返り値の本文。harness が付けた各行の字下げだけを外した)。
     起動: Agent tool・subagent_type general-purpose・model=opus(claude-opus-5-5 の階層)・effort=xhigh・新しい文脈(fork / resume なし)・
     機体 = この会話の Pod・agentId a79f40bfb66d196ef・所要 1170377 ms・tool の呼び 45 回・subagent_tokens 137910。
     実際に走った model は Agent tool の答えから観測できない(要求値のみ)。 -->

## 結果

検査を通るのに責務を侵す変更を 1 つ作れました。実測では、変更後も提示の 4 検査はすべて通ります。一方で、自分で書いた探りの test(提示の検査には含まれない)を本物の `run-coordinator` で走らせると、変更前は通り、変更後は落ちます。

### 1. 機能要求と差分

**要求**: 設計は「lease 以下の止まりは見つけない(期限の近い worker が消えて戻る)」を残る代償として受け入れています。そこへ次の要求が来た想定です。「機体が 5〜10 秒止まっただけで、配る担当へ消えと戻りが届く。抑えてほしい。ただし保存の詰まり(busy)は今の lease の閾のままにする。遅い disk で歩ごとにずらすと、死んだ worker の判定が延びるため」。

**差分**(`program.hy` の `coordinator-step` だけ。8 行足して 1 行消す):
```diff
-  (<- resumed ClusterState (stall-resumed state now (! (stall-of pace now timing))))
+  ;; 機体の短い止まり(lease 以下 — stall-of は見つけない)でも、期限の近い worker が消えて戻る(配る担当へ消えと戻り)。予定の起きる刻
+  ;; からの寝過ごしが返事の遅れの線(HEARTBEAT-LAG-MS — 生死の lease の手前で名指す線)を越えた歩は、寝過ごした分だけ経過に数えない。
+  ;; 保存の詰まり(前の歩の長さ)は従来どおり lease を越えた時だけ(遅い disk で歩ごとにずらすと、死んだ worker の判定が延びる)。
+  (val overslept (if (or (is pace None) (is pace.planned-ms None)) 0 (max 0 (- now pace.planned-ms))))
+  (val stall (or (! (stall-of pace now timing))
+                 (when (> overslept HEARTBEAT-LAG-MS)
+                   (Stall :busy-ms 0 :overslept-ms overslept :gap-ms overslept))))
+  (<- resumed ClusterState (stall-resumed state now stall))
```

普通の実装者が書きそうだと考える理由は次のとおりです。
- `stall-of` もその判断の test も、ADR の law(閾 = lease)も触らずに足せる。
- `HEARTBEAT-LAG-MS` は同じ file に「lease の手前で名指す線」として既に在る。
- `Stall` も既に import されている。

### 2. 本来の所有者と、侵す契約

本来の所有者は M1(`api_policy.stall-of`)です。止まりの定義(予定の外の長さ = 前の歩の長さ + 寝過ごし)、閾(lease-ms)、gap の意味(前の歩の判断の刻から今まで)を M1 が持ちます。

変更後は、M2(`program.hy`、役は program)が次の 3 つを持つことになります。
- 2 つ目の止まりの定義: 寝過ごしだけで見る。
- 2 つ目の閾: `HEARTBEAT-LAG-MS`。本来は返事の遅れの log の閾で、観測のための知識です。それが生死の判断に結び付きます。
- 別の意味の gap: gap = 寝過ごし。

また `Stall` を M1 の外で組むので、`due_model.hy` が定めた欄の意味から外れます。
- gap-ms は「前の歩の判断の刻から今まで」のはずが、そうならない。
- busy-ms が 0 に固定され、止まりの log の行が保存の詰まりと機体の止まりを読み分けられなくなる。

崩れる主張は 3 つです。
- **S1**: 「閾と内訳の定義は stall-of 1 か所に在り、閾を変えるのは M1 とその判断の test だけ」が成り立たない。閾を変えるには program.hy も直す必要がある。
- **M1 の不変条件**: 「ずらした後の worker の沈黙 = 前の歩の時点の沈黙」が、M2 の道では崩れる。実測では、w2 の沈黙が前の歩の時点では 0 ms、ずらした後は 5001 ms でした。
- **判断の test の主張**: 「lease ちょうどは止まりでない」は `stall-of` についてだけ正しく、系の振る舞いとしては 5 秒の寝過ごしで止まりと扱われる。

形の上の公開契約(HTTP・heartbeat・WorkerGone/WorkerBack の形・保存の鍵)は変わりません。

推測(未測)ですが、5 秒を越える寝過ごしのたびに semaphore の行の版が +1 されます(`clocks-shifted` の `holders-shifted`)。盤の compare-and-set の client が 409 を受ける回数が増えるはずです。

### 3. 各検査が拒否しない理由(どれも実測で通過)

| 検査 | 結果 | 通る理由 |
|---|---|---|
| 1. `test_coordinator_stall.hy` | 6 passed | import するのは api_policy と cluster_policy だけで、program.hy も `coordinator-step` も通らない。`stall-of` は変えていない |
| 2. `test_coordinator_stall_on_sim.hy` | 2 passed | 180 秒の止まりは `or` の先、つまり M1 の道で捕まる。仮想の時計では他の歩が予定ちょうどに起きる。止まりの log の行は 2 本とも gap ≠ 寝過ごし(180000 と 175010)で、M2 の道は一度も通っていない(実測) |
| 3. `static_stub --check program.hy` | exit 0 | 上の階層の定義・import・引数の型を変えていないので、作られる .pyi が同じ |
| 4. defk の :pre / :post | 違反なし | 調べるのは型と条件だけ(`Stall` の int の欄、gap > 0、答えが ClusterState)で、`Stall` がどこで作られたかは見ない。assert が効いていることも確かめた(`sys.flags.optimize` = 0) |

### 4. 両方を確かめる最小の手順と期待結果

1. e8d9481 の写しで `uv sync --group dev` を実行する。
2. 提示の 3 つの命令をそのまま走らせる。変更前・変更後とも通る(実測)。
3. 探りの test `packages/doeff-cluster/tests/test_zz_probe_short_oversleep.hy` を置く(全文は末尾)。中身は次のとおり。
   - 土台は `test_coordinator_heartbeat_lag.hy` と同じで、本物の `run-coordinator` を SimClock(仮想の時計)と台本の受付で回す。
   - 台本: w1 が名乗る → 5 秒後に w2 が名乗る → 次の起き(w1 の期限 + 1 ms)を 7 秒寝過ごす → 止める。
   - 確かめる事: 同じ刻みで `stall-of` が None を返し、`STALL-LOG` の行が出ない。
4. 期待と実測:
   - **変更前**: 1 passed。止まりの log の行は無く、w1・w2 とも沈黙になる(これが要求の言う w2 の瞬き)。
   - **変更後**: 1 failed。
     - `m1_stall_of: null` なのに、log に `gap_ms=7000 busy_ms=0 overslept_ms=7000` が出る。
     - w2 の最後の連絡が t0+5000 から t0+12000 へずれ、沈黙は w1 だけになる。
   - 同じ 1 回の pytest で、模擬の test 2 本は通り、探りだけが落ちる(変更した code が読まれていることも、これで確かめた)。

### 5. 未確認の前提と不足

- 提示されていない検査(doeff-linter や architecture.hy の層の規則など)は走らせていません。それらが拒否するかどうかは分かりません。
- 全数は走らせていないので、他の test が落ちるかどうかは不明です。
- 本番で 5〜10 秒の機体の止まりがどのくらい起きるかは、材料に無く分かりません。
- 探りの test は私が足したもので、提示の検査ではありません。
- 手順を 1 つ変えました。`/tmp` の下に worktree を作ろうとしたところ、環境の hook(worktree-placement-guard)が止めました。代わりに `git archive e8d9481` で `mktemp -d` の dir に写し、`git init`・`git add` だけ(commit なし)で差分を取りました。
  - 共有の作業木と、その git の worktree の登記は変えていません(終わりに確かめた)。
  - 一時の dir は丸ごと消しました。
- 途中で 1 回 `-p no:cacheprovider` を付けて走らせました。上の結果は、付けずに提示どおりの命令で取り直したものです。

**探りの test の全文**(落ちた時の値を出す PROBE の print は、この全文から省いています):
```hy
(require doeff-hy.macros [deftest defk defhandler <- val var])
(import datetime [datetime timezone])
(import doeff [run with-handlers])
(import doeff_core_effects.effects [SlogEffect])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_events [MemoryBroker])
(import doeff_cluster.shared.intent.protocol [ClusterTiming NextRequests Reply Request CoordinatorStopRequested])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ClusterNaming SaveState])
(import doeff_cluster.coordinator.intent.due_model [StepPace])
(import doeff_cluster.coordinator.core.api_policy [stall-of])
(import doeff_cluster.coordinator.core.program [run-coordinator STALL-LOG])
(import doeff_cluster.coordinator.entry.handler_sets [memory-notices])
(import doeff_cluster.coordinator.protocol.request_bodies [request-bodies])
(import doeff_cluster.coordinator.protocol.replies [reply-bodies])
(import doeff_cluster.coordinator.protocol.kube [ObjectWatches kube-unavailable])
(val T (ClusterTiming))
(val START (datetime 2026 10 13 2 22 0 :tzinfo timezone.utc))
(defclass Script [] (defn __init__ [self hbs] (setv self.hbs hbs self.takes 0 self.done False self.stalls [])))
(defhandler scripted [#^ Script s]
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (NextRequests [timeout-seconds limit]
    (setv s.takes (+ s.takes 1))
    (cond (= s.takes 1) (resume [(get s.hbs 0)])
          (= s.takes 2) (do (<- (Delay 5.0)) (resume [(get s.hbs 1)]))
          (= s.takes 3) (do (<- (Delay (+ timeout-seconds 7.0))) (resume []))
          True (do (setv s.done True) (resume []))))
  (Reply [request status body] (resume None))
  (SaveState [before after] (resume None))
  (CoordinatorStopRequested [] (resume s.done))
  (SlogEffect [] (when (= effect.msg STALL-LOG) (setv s.stalls (+ s.stalls [(dict effect.kwargs)]))) (resume None)))
(defk hb [name] {:pre [(: name str)] :post [(: % Request)] :tags {:context "doeff-cluster-test" :role "judgment"}} "hb"
  (! (http-request "POST" "/heartbeat" {} {"name" name "boot" (+ name "-b1") "provides" ["net"] "capacity" 10 "taskReserve" 0
                                           "versions" {"python" "3.14.0" "doeff" "1"}} :actor name :peer name :queued-ms 0)))
(defk ran [s] {:pre [(: s Script)] :post [(: % Script)] :tags {:context "doeff-cluster-test" :role "entry"}} "run"
  (run (scheduled ((sim-time-handler :clock (SimClock :current-time START))
                    ((scripted s) (request-bodies (reply-bodies (with-handlers (memory-notices (MemoryBroker))
                      ((kube-unavailable "no kube" (ObjectWatches) (ObjectWatches)) (run-coordinator (ClusterState) T (ClusterNaming))))))))))
  s)
(deftest test-a-short-oversleep-within-the-lease-is-not-a-stall-of-the-running-coordinator
  (<- s Script (ran (Script [(! (hb "w1")) (! (hb "w2"))])))
  (val t0 (int (* 1000 (.timestamp START))))
  (val planned (+ t0 T.lease-ms 1))
  (assert (is (! (stall-of (StepPace :judged-ms (+ t0 5000) :waited-ms (+ t0 5000) :planned-ms planned) (+ planned 7000) T)) None))
  (assert (= s.stalls []) s.stalls))
```

status: done — 検査を通る責務違反の例 1 件を、通過と違反の両方の実測つきで返した
WAIT: work — 利用者の次の入力
