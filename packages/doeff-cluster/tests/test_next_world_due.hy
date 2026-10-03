;; sim の世界の次の予定の刻の問い NextWorldDue(#3078 の子 3 = #3094)。行き止まりの見張り(#3078 の子 1)は、業務の task が全部出来事を
;; 待って止まっていても、世界の予定(網の切れ・口の故障・worker の処理の止まりが明ける刻・止まった coordinator を作り直す刻・頼まれた
;; coordinator の止まりと落ち)がまだ来るなら行き止まりにしない — その「まだ来る」を世界の session の値から 1 つの問いで答える。
;;
;; 前半 = 判断 next-world-due を値で呼ぶ検。後半 = 模擬の世界(sim-cluster)の筋書きで、網の切れと coordinator の作り直しの刻を世界が
;; 答え、明けた後は None になることを見る検(作り直しの刻を覚えない形・明けた刻を数え続ける形は赤)。
(require doeff-hy.macros [deftest defk <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass replace])  ; defrecord の展開が名指す
(import doeff_time [Delay])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.shared.intent.cluster_control [CrashCoordinator])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.sim.local [sim-cluster SimIntake SimPauses NextWorldDue CutWorker ProcessesOf CoordinatorRuns
                             fresh-truth next-world-due])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [pulses])

;; 判断の検の「いま」(epoch ms)と、本番と同じ時間の設定。
(val NOW 1000000)
(val T (ClusterTiming))


(defk intake-with [cuts failing]
  {:pre [(: cuts dict) (: failing dict)] :post [(: % SimIntake)] :tags {:context "doeff-cluster-test" :role "program"}}
  "網の切れ cuts と口の故障 failing だけを持つ受付の値を作るため(ほかの欄は空)。"
  (SimIntake :cuts cuts :failing failing :held #() :reports #()))


(defk stalled-host [name until]
  {:pre [(: name str) (: until int)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "処理が刻 until まで止まる worker name 1 台の宿の表を作るため。"
  (<- truth (fresh-truth name 1 0 T))
  {name (replace truth :stalled-until-ms until)})


;; --- 判断 -----------------------------------------------------------------------------------------------------------

(deftest test-nothing-scheduled-is-none
  ;; 予定が何も無ければ None(見張りはこの時だけ行き止まりと判じる)。
  (<- due (next-world-due (! (intake-with {} {})) {} (SimPauses :queued #()) NOW))
  (assert (is due None) due))


(deftest test-the-earliest-end-ahead-is-the-due
  ;; 網の切れ(+5 秒)・口の故障(+8 秒)・worker の止まり(+3 秒)のうち最も早い刻。
  (<- hosts dict (stalled-host "w1" (+ NOW 3000)))
  (<- due (next-world-due (! (intake-with {"w2" (+ NOW 5000)} {#("POST" "/heartbeat") #(503 (+ NOW 8000))}))
                          hosts (SimPauses :queued #()) NOW))
  (assert (= due (+ NOW 3000)) due))


(deftest test-ends-already-passed-are-not-counted
  ;; 明けた網の切れは表に残るが数えない。止まりの値 0(止まっていない)も数えない。
  (<- hosts dict (stalled-host "w1" 0))
  (<- due (next-world-due (! (intake-with {"w2" (- NOW 1)} {#("POST" "/heartbeat") #(503 NOW)})) hosts (SimPauses :queued #()) NOW))
  (assert (is due None) due))


(deftest test-a-requested-stop-or-crash-is-due-now
  ;; 頼まれた coordinator の止まり・落ちは次の歩・次の保存で起きる(刻を持たない)— 残っている間は今。効いた止まりの秒がまだ取り出されて
  ;; いなくても今。
  (<- quiet SimIntake (intake-with {} {}))
  (assert (= (! (next-world-due quiet {} (SimPauses :queued #(#("stop" 5.0))) NOW)) NOW))
  (assert (= (! (next-world-due quiet {} (SimPauses :queued #(#("crash" 5.0))) NOW)) NOW))
  (assert (= (! (next-world-due quiet {} (SimPauses :queued #() :downtime 5.0) NOW)) NOW)))


(deftest test-the-restart-of-a-stopped-coordinator-is-due
  ;; 止まった coordinator を作り直す刻(DowntimeOf が書く)は、まだ来ていなければ予定。過ぎていれば数えない。
  (<- quiet SimIntake (intake-with {} {}))
  (assert (= (! (next-world-due quiet {} (SimPauses :queued #() :restart-ms (+ NOW 20000)) NOW)) (+ NOW 20000)))
  (assert (is (! (next-world-due quiet {} (SimPauses :queued #() :restart-ms NOW) NOW)) None)))


;; --- 模擬の世界の筋書き -------------------------------------------------------------------------------------------------

(defrecord DueSeen
  "筋書きの読み: during = 予定の最中に世界が答えた次の予定の刻・expected = その刻の正しい値(epoch ms)・after = 予定が明けた後の答え。"
  (setv #^ (| int None) during None)
  (setv #^ int expected 0)
  (setv #^ (| int None) after None))


(defk cut-due []
  {:pre [] :post [(: % DueSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 8 秒待って pulse の担い手の網を 30 秒切り、すぐ次の予定の刻を問い(切れが明ける刻のはず)、明けて 1 秒後にもう一度問う。"
  (<- (Delay 8.0))
  (<- before tuple (ProcessesOf "pulse"))
  (<- (CutWorker (. (get before 0) worker) 30.0))
  (<- cut-at int (now-epoch-ms))
  (<- during (| int None) (NextWorldDue cut-at))
  (<- (Delay 31.0))
  (<- later int (now-epoch-ms))
  (<- after (| int None) (NextWorldDue later))
  (DueSeen :during during :expected (+ cut-at 30000) :after after))


(defk restart-due []
  {:pre [] :post [(: % DueSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 8 秒待って coordinator の落ちを頼み(止まっている秒 20)、落ちた後に次の予定の刻を問い(落ちた刻 + 20 秒 = 作り直しの刻の
   はず)、作り直した後にもう一度問う。"
  (<- (Delay 8.0))
  (<- (CrashCoordinator 20.0))
  (<- (Delay 5.0))
  (<- runs tuple (CoordinatorRuns))
  (val ended (. (get runs 0) ended-ms))
  (<- now int (now-epoch-ms))
  (<- during (| int None) (NextWorldDue now))
  (<- (Delay 25.0))
  (<- later int (now-epoch-ms))
  (<- after (| int None) (NextWorldDue later))
  (DueSeen :during during :expected (if (is ended None) -1 (+ ended 20000)) :after after))


(deftest test-the-world-answers-the-end-of-a-cut
  ;; 網の切れの最中は、世界が切れの明ける刻を答える。明けた後は予定が無い(明けた切れを数え続けない)。
  (<- seen DueSeen (sim-cluster (pulses sim-foundation) (cut-due)))
  (assert (= seen.during seen.expected) seen)
  (assert (is seen.after None) seen))


(deftest test-the-world-answers-the-restart-of-a-crashed-coordinator
  ;; 落ちた coordinator を作り直すまでの間は、世界が作り直しの刻を答える(Delay の中だけに消えない)。作り直した後は予定が無い。
  (<- seen DueSeen (sim-cluster (pulses sim-foundation) (restart-due)))
  (assert (= seen.during seen.expected) seen)
  (assert (is seen.after None) seen))
