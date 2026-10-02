;; 移せる先の無い job を担い手の途絶でも外さず、返事の印で worker が止めない(#2804)。本番の Service はどれも置ける worker が 1 台しか
;; なく、担い手の途絶(処理の止まり 47 秒・網の途絶 2 分 — 2026-10-02 13:26・13:53)で置き先を外しても他へ移らず、止めて起こし直す
;; 損だけが残った。印の在る job も、途絶が長い方の柵(ClusterTiming.keep-fence-ms・240 秒)を越えたら止める — 分断の最中に k8s が作る
;; 同じ名の worker の新しい世代(早くても約 350 秒後)と重ならないため。
;;
;; 前半 = 本体の判断を直に呼ぶ検(置き先・返事の印・約束の外し方・readiness・保存・worker の fence の判断・返事の読み)。
;; 後半 = 模擬の世界(本物の coordinator と本物の worker を仮想の時計で回す sim-cluster)の途絶の筋書きと、条 C2 one-place-per-job
;; (入れ替えを宣言しない job は同時に 2 つの worker の上で走らない — architecture.hy)を筋書きの process の記録で判じる検。
(require doeff-hy.macros [deftest defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass replace])  ; defrecord の展開が名指す
(import pytest)
(import doeff_time [Delay])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterJob WorkerInfo WorkerReport Placement ClusterState KeepMark Drain])
(import doeff_cluster.coordinator.intent.request_bodies [StatusRow])
(import doeff_cluster.coordinator.core.cluster_policy [place-jobs reconcile unplaced-jobs])
(import doeff_cluster.coordinator.core.resource_policy [running-process delete-resource Refused])
(import doeff_cluster.coordinator.core.coordinator_invariants [one-place-per-job ProcessSpan])
(import doeff_cluster.coordinator.protocol.durable_kv [full-kv state-from-kv])
(import doeff_cluster.coordinator.protocol.request_bodies [responded])
(import doeff_cluster.coordinator.protocol.replies [spec-json])
(import doeff_cluster.worker.protocol.declared [declared-job-spec])
(import doeff_cluster.worker.core.policy [kept-when-cut-off])
(import doeff_cluster.sim.local [sim-cluster SimWorker SimProcess SimReadiness ProcessesOf ReadinessOf CutWorker StallWorker StartWorker
                             DrainWorker Redeclare KillWorker])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [pulses lone-pulses wide-pulses])
(import tests.program_rows [SAMPLE-RUN])

;; 本番と同じ時間の設定(生存の窓 10 秒・fence 20 秒・移し替え 45 秒)。
(val T (ClusterTiming))


;; --- 本体の判断 ---------------------------------------------------------------------------------------------------

(defk job-of [name needs]
  {:pre [(: name str) (: needs tuple)] :post [(: % ClusterJob)] :tags {:context "doeff-cluster-test" :role "program"}}
  "検の宣言 1 つ(入れ替えでない service)を作るため。"
  (ClusterJob (JobSpec name "m" #() "rev") :needs needs))


(defk worker-of [name seen provides]
  {:pre [(: name str) (: seen int) (: provides tuple)] :post [(: % WorkerInfo)] :tags {:context "doeff-cluster-test" :role "program"}}
  "検の worker 1 台(最後の連絡の時刻 seen・提供する能力 provides)を作るため。"
  (WorkerInfo name provides 10 seen))


(defk promise-to [job worker]
  {:pre [(: job str) (: worker str)] :post [(: % KeepMark)] :tags {:context "doeff-cluster-test" :role "program"}}
  "job を worker に「途絶しても動かし続けてよい」印で渡した約束(時刻 0)を作るため。"
  (KeepMark :job job :worker worker :boot None :since-ms 0))


(deftest test-a-job-with-no-other-place-keeps-its-placement-when-its-holder-is-silent-for-60-seconds
  ;; 受入 1: 他に置ける worker が無い job は、担い手が 60 秒沈黙しても置き先を外さない(以前は移し替えの 45 秒で外し、担い手が戻ると
  ;; 返事から job が消えて止め、置き直して起こし直した)。
  (<- job ClusterJob (job-of "a" #("net")))
  (<- w1 WorkerInfo (worker-of "w1" 0 #("net")))
  (val state (ClusterState #(job) {"w1" w1} {"a" (Placement "a" "w1" 1 0)}))
  (assert (= (get (place-jobs 60000 state T) "a") (Placement "a" "w1" 1 0))))


(deftest test-a-job-with-another-place-still-moves-after-the-reassign-deadline
  ;; 受入 2: 他に置ける worker が在る job は今までどおり 45 秒(移し替えの期限)で外して移す — 担い手は fence(20 秒)で先に止まっている。
  (<- job ClusterJob (job-of "a" #("net")))
  (<- w1 WorkerInfo (worker-of "w1" 0 #("net")))
  (<- w2 WorkerInfo (worker-of "w2" 50000 #("net")))
  (val state (ClusterState #(job) {"w1" w1 "w2" w2} {"a" (Placement "a" "w1" 1 0)}))
  (assert (= (. (get (place-jobs 45000 state T) "a") worker) "w1"))
  (val moved (get (place-jobs 45001 state T) "a"))
  (assert (= #(moved.worker moved.generation) #("w2" 2)) moved))


(deftest test-a-promised-job-stays-with-its-silent-holder-when-a-capable-worker-joins
  ;; 条件 2 の (a): 印の約束の在る job は、担い手の途絶の間に能力の合う 2 台目が加わっても他へ置かない(担い手は印で動き続けている)。
  ;; 約束が外れた後(担い手が印の無い返事を受けたと知らせた後)は、今までどおり時間の柵で移す。
  (<- job ClusterJob (job-of "a" #("net")))
  (<- w1 WorkerInfo (worker-of "w1" 0 #("net")))
  (<- w2 WorkerInfo (worker-of "w2" 60000 #("net")))
  (<- mark KeepMark (promise-to "a" "w1"))
  (val promised (ClusterState #(job) {"w1" w1 "w2" w2} {"a" (Placement "a" "w1" 1 0)} :keep-marks {"a" mark}))
  (assert (= (. (get (place-jobs 60000 promised T) "a") worker) "w1"))
  (val released (replace promised :keep-marks {}))
  (assert (= (. (get (place-jobs 60000 released T) "a") worker) "w2"))
  ;; 宣言から消えて置き先を外した後に宣言し直した job も、約束の担い手にだけ置く(担い手が古い宣言の process を動かしているかもしれない)。
  (val unplaced (replace promised :placements {}))
  (assert (not-in "a" (place-jobs 60000 unplaced T)))
  (assert (in "前の担い手" (get (unplaced-jobs 60000 unplaced T) "a"))))


(deftest test-a-promised-job-stays-with-its-holder-when-its-needs-change
  ;; 条件 2 の (b): 宣言の needs が変わって担い手が条件を外れ、他の worker が置けるようになっても、印の約束の在る間は担い手から移さない。
  ;; 約束が外れた後は、条件を外れた担い手から外して置ける worker へ置く。
  (<- job ClusterJob (job-of "a" #("wide")))
  (<- w1 WorkerInfo (worker-of "w1" 0 #("lone")))
  (<- w2 WorkerInfo (worker-of "w2" 60000 #("wide")))
  (<- mark KeepMark (promise-to "a" "w1"))
  (val promised (ClusterState #(job) {"w1" w1 "w2" w2} {"a" (Placement "a" "w1" 1 0)} :keep-marks {"a" mark}))
  (assert (= (. (get (place-jobs 60000 promised T) "a") worker) "w1"))
  (assert (= (. (get (place-jobs 60000 (replace promised :keep-marks {}) T) "a") worker) "w2")))


(deftest test-deleting-the-silent-holder-releases-its-promise-and-the-job-moves
  ;; 条件 2 の (c): 置ける唯一の worker が退く(Worker の削除)。生きている担い手は消せない(移し替えの期限の内は断る)。期限を過ぎて
  ;; 消すと約束は外れ(消した Worker はもう動いていないという明示の宣言)、置き先は置ける worker へ移る。
  (<- job ClusterJob (job-of "a" #("net")))
  (<- w1 WorkerInfo (worker-of "w1" 0 #("net")))
  (<- w2 WorkerInfo (worker-of "w2" 60000 #("net")))
  (<- mark KeepMark (promise-to "a" "w1"))
  (val promised (ClusterState #(job) {"w1" w1 "w2" w2} {"a" (Placement "a" "w1" 1 0)} :keep-marks {"a" mark}))
  (with [(pytest.raises Refused)]
    (delete-resource promised "Worker" "w1" {} "operator" 30000 T))
  (val deleted (delete-resource promised "Worker" "w1" {} "operator" 60000 T))
  (val after (reconcile 60000 deleted T))
  (assert (= after.keep-marks {}) after.keep-marks)
  (assert (= (. (get after.placements "a") worker) "w2") after.placements))


(deftest test-a-draining-holder-keeps-a-promised-job-until-it-drops-the-mark
  ;; 条件 2 の (c): 置ける唯一の worker が drain(preStop)に入った後に置ける worker が加わっても、印の約束の在る間は担い手から移さない。
  ;; 担い手が印を持たないと知らせて約束が外れたら drain の規則どおり外し、担い手が止め終えたと報告するまで他へ置かない。
  (<- job ClusterJob (job-of "a" #("net")))
  (<- w1 WorkerInfo (worker-of "w1" 60000 #("net")))
  (<- w2 WorkerInfo (worker-of "w2" 60000 #("net")))
  (<- mark KeepMark (promise-to "a" "w1"))
  (val running (WorkerReport :at 60000 :endpoint None :jobs #((StatusRow :name "a" :phase "running"))))
  (val promised (ClusterState #(job) {"w1" w1 "w2" w2} {"a" (Placement "a" "w1" 1 0)} :statuses {"w1" running}
                              :drains {"w1" (Drain "w1" 0 999999)} :keep-marks {"a" mark}))
  (assert (= (. (get (place-jobs 60000 promised T) "a") worker) "w1"))
  (val released (replace promised :keep-marks {}))
  (assert (not-in "a" (place-jobs 60000 released T)))
  (val stopped (replace released :statuses {"w1" (WorkerReport :at 60000 :endpoint None :jobs #((StatusRow :name "a" :phase "stopped")))}))
  (assert (= (. (get (place-jobs 60000 stopped T) "a") worker) "w2")))


(defk beat-body [name statuses kept]
  {:pre [(: name str) (: statuses list) (: kept (| list None))] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "heartbeat の本文 1 つ(kept = 今持っている印の名の列 — None なら欄を書かない古い worker)を組むため。"
  (| {"name" name "provides" ["net"] "capacity" 10 "versions" {"python" "3.14.0"} "statuses" statuses}
     (if (is kept None) {} {"keptWhenCutOff" kept})))


(defk heard [state body now]
  {:pre [(: state ClusterState) (: body dict) (: now int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "heartbeat 1 つを受け口と同じ解きと判断に通した #(次の状態 status 返事の JSON) を得るため。"
  (responded state (http-request "POST" "/heartbeat" {} body) now T))


(deftest test-the-reply-marks-a-job-with-no-other-place-and-the-mark-is-released-only-by-the-holder
  ;; 返事の契約: 他へ移せない job の行に keepWhenCutOff を付け、印を渡した事実を約束として状態に残す(返事の前に保存される)。約束が
  ;; 外れるのは担い手の今の世代の heartbeat が印を持たないと知らせた時だけ — 印の無い返事を送っただけでは外さない(届いていないかも
  ;; しれない)。古い worker(本文に keptWhenCutOff の欄が無い)には約束しない。
  (<- empty dict (beat-body "w1" [] []))
  (<- first tuple (heard (ClusterState) empty 0))
  (val declared (responded (get first 0) (http-request "PUT" "/jobs" {} {"jobs" [{"name" "s0" "run" SAMPLE-RUN "revision" "r" "needs" ["net"]}]}
                                                      :actor "test")
                           0 T))
  (<- running dict (beat-body "w1" [{"name" "s0" "phase" "running" "revision" "r"}] []))
  (<- marked tuple (heard (get declared 0) running 1000))
  (val row (get (get marked 2) "jobs" 0))
  (assert (= #((get row "name") (.get row "keepWhenCutOff")) #("s0" True)) row)
  (assert (= (. (get (. (get marked 0) keep-marks) "s0") worker) "w1"))
  ;; 2 台目が加わると移せる: 2 台目への返事に s0 は無く、担い手への返事は印を外すが、担い手が印を持つと知らせる間は約束を残す。
  (<- second dict (beat-body "w2" [] []))
  (<- joined tuple (heard (get marked 0) second 2000))
  (assert (= (get (get joined 2) "jobs") []) (get joined 2))
  (<- holding dict (beat-body "w1" [{"name" "s0" "phase" "running" "revision" "r"}] ["s0"]))
  (<- unmarked tuple (heard (get joined 0) holding 3000))
  (assert (not-in "keepWhenCutOff" (get (get unmarked 2) "jobs" 0)) (get unmarked 2))
  (assert (in "s0" (. (get unmarked 0) keep-marks)))
  ;; 担い手が印を持たないと知らせた heartbeat で約束が外れる。
  (<- dropped tuple (heard (get unmarked 0) running 4000))
  (assert (= (. (get dropped 0) keep-marks) {}) (. (get dropped 0) keep-marks))
  ;; 古い worker: 返事の印は読まれないので、約束を状態に残さない(担い手は今までどおり fence で止める)。
  (<- old dict (beat-body "w1" [{"name" "s0" "phase" "running" "revision" "r"}] None))
  (<- alone tuple (heard (get declared 0) old 1000))
  (assert (= (. (get alone 0) keep-marks) {}) (. (get alone 0) keep-marks)))


(deftest test-a-silent-holder-of-a-marked-job-is-unknown-not-notready
  ;; readiness: 印を渡した担い手が移し替えの期限を過ぎて沈黙しても、process は動き続けている見込みなので Unknown(監視が止まりと
  ;; 読まない)。印の無い job は担い手が fence で止めているので NotReady のまま。
  (<- job ClusterJob (job-of "a" #("net")))
  (<- w1 WorkerInfo (worker-of "w1" 0 #("net")))
  (<- mark KeepMark (promise-to "a" "w1"))
  (val report (WorkerReport :at 0 :endpoint None :jobs #((StatusRow :name "a" :phase "running"))))
  (val plain (ClusterState #(job) {"w1" w1} {"a" (Placement "a" "w1" 1 0)} :statuses {"w1" report}))
  (assert (= (get (running-process plain "a" 60000 T) "state") "NotReady"))
  (val kept (running-process (replace plain :keep-marks {"a" mark}) "a" 60000 T))
  (assert (= (get kept "state") "Unknown") kept)
  (assert (in "印" (get kept "reason")) kept)
  ;; 印の在る job も、担い手は途絶が長い方の柵(keep-fence-ms 240 秒)を越えたら止めるので、その後は NotReady。
  (assert (= (get (running-process (replace plain :keep-marks {"a" mark}) "a" 241000 T) "state") "NotReady")))


(deftest test-promises-survive-a-coordinator-restart
  ;; 約束は保存する(鍵 keep/<名>)— coordinator を置き場から作り直しても、印を渡した担い手から job を他へ移さない。
  (<- mark KeepMark (promise-to "a" "w1"))
  (val state (ClusterState :keep-marks {"a" mark}))
  (val kv (full-kv state))
  (assert (in "keep/a" kv) kv)
  (assert (= (. (state-from-kv kv 5000) keep-marks) {"a" mark})))


(deftest test-a-worker-keeps-a-marked-job-through-a-two-minute-cut-and-stops-an-unmarked-one-after-the-fence
  ;; 受入 3: 印の在る job は coordinator に 2 分届かなくても止めない。印の無い job は今までどおり fence(20 秒)を越えたら止める。
  ;; 返事の行に欄が無ければ(古い coordinator)印は無い = 今までどおり。
  (<- marked JobSpec (declared-job-spec {"name" "a" "entry" "m" "revision" "r" "keepWhenCutOff" True}))
  (<- plain JobSpec (declared-job-spec {"name" "b" "entry" "m" "revision" "r"}))
  (assert (and marked.keep-when-cut-off (not plain.keep-when-cut-off)))
  ;; fence の判断(heartbeat_rules.desired-when-unreachable)が fence を越えた途絶で残す job を選ぶ述語 — 印の無い job は fence を越えたら
  ;; 止める(残らない)。今持っている印の知らせ(heartbeat の keptWhenCutOff)は模擬の筋書きと返事の検が通しで確かめる。
  (assert (= (kept-when-cut-off #(marked plain) 120000 T.keep-fence-ms) #(marked)))
  (assert (= (kept-when-cut-off #(plain) 20001 T.keep-fence-ms) #()))
  ;; 返事の綴り(印の無い行は欄を書かない — 古い worker が見る行は今までと同じ)。
  (<- marked-row dict (spec-json marked))
  (<- plain-row dict (spec-json plain))
  (assert (= (get marked-row "keepWhenCutOff") True))
  (assert (not-in "keepWhenCutOff" plain-row) plain-row))


(deftest test-a-marked-job-is-stopped-once-the-cut-outlasts-the-long-fence
  ;; 長い方の柵(査読の決め): 印の在る job も、途絶が keep-fence-ms(240 秒)を越えたら止める — 2 分の途絶では止めず、241 秒の途絶では
  ;; 止める(同じ名の worker の新しい世代が来る約 350 秒後より先)。長い方の柵は fence より長くなければ時間の設定として受けない。
  (<- marked JobSpec (declared-job-spec {"name" "a" "entry" "m" "revision" "r" "keepWhenCutOff" True}))
  (assert (= (kept-when-cut-off #(marked) 120000 T.keep-fence-ms) #(marked)))
  (assert (= (kept-when-cut-off #(marked) 241000 T.keep-fence-ms) #()))
  (assert (= T.keep-fence-ms 240000))
  (with [(pytest.raises ValueError)]
    (ClusterTiming :fence-ms 20000 :keep-fence-ms 20000)))


;; --- 模擬の世界の途絶の筋書き ------------------------------------------------------------------------------------------

(defrecord CutSeen
  "途絶の筋書きの読み: host = 途絶させた worker・mid = 途絶の最中の process の列・readiness = 途絶の最中の Service の ready・
   after = 途絶が明けた後の process の列。"
  (#^ str host)
  (#^ tuple mid)
  (#^ SimReadiness readiness)
  (#^ tuple after))


(defk spans-of [processes]
  {:pre [(: processes tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの process の記録を条 C2 の判断に渡す区間の列にするため。"
  (tuple (gfor p processes (ProcessSpan :worker p.worker :started-ms p.started-ms :ended-ms p.ended-ms))))


(defk cut-for [seconds]
  {:pre [(: seconds float)] :post [(: % CutSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き(13:53 型): 8 秒待って pulse の担い手の網を seconds 秒切り、切って 60 秒後(移し替えの 45 秒の後)と明けて 30 秒後に読む。"
  (<- (Delay 8.0))
  (<- before tuple (ProcessesOf "pulse"))
  (val host (. (get before 0) worker))
  (<- (CutWorker host seconds))
  (<- (Delay 60.0))
  (<- mid tuple (ProcessesOf "pulse"))
  (<- readiness SimReadiness (ReadinessOf "pulse"))
  (<- (Delay (+ (- seconds 60.0) 30.0)))
  (<- after tuple (ProcessesOf "pulse"))
  (CutSeen :host host :mid mid :readiness readiness :after after))


(defk stall-for [seconds]
  {:pre [(: seconds float)] :post [(: % CutSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き(13:26 型): 8 秒待って pulse の担い手の処理を seconds 秒止め(heartbeat が送られない)、止まりの終わり際と明けて 30 秒後に読む。"
  (<- (Delay 8.0))
  (<- before tuple (ProcessesOf "pulse"))
  (val host (. (get before 0) worker))
  (<- (StallWorker host seconds))
  (<- (Delay (- seconds 1.0)))
  (<- mid tuple (ProcessesOf "pulse"))
  (<- readiness SimReadiness (ReadinessOf "pulse"))
  (<- (Delay 31.0))
  (<- after tuple (ProcessesOf "pulse"))
  (CutSeen :host host :mid mid :readiness readiness :after after))


(deftest test-a-lone-job-keeps-running-through-a-two-minute-cut
  ;; 受入 6(網の途絶 2 分・13:53 型): 置ける worker が 1 台の job は、担い手の網が 2 分切れても process を止めず(印 — fence でも止めない)、
  ;; coordinator も置き先を外さず(移せる先が無い)、明けた後も起こし直さない。途絶の最中の ready は Unknown(止まりと読まない)。
  (<- seen CutSeen (sim-cluster (pulses sim-foundation) (cut-for 120.0)))
  (assert (= (len seen.after) 1) seen.after)
  (assert (is (. (get seen.after 0) exit-code) None) seen.after)
  (assert (= seen.readiness.state "Unknown") seen.readiness)
  (<- spans tuple (spans-of seen.after))
  (<- broken tuple (one-place-per-job spans))
  (assert (= broken #()) broken))


(deftest test-a-lone-job-keeps-running-through-a-47-second-stall
  ;; 受入 6(処理の止まり 47 秒・13:26 型): 担い手の処理が止まり heartbeat が 47 秒送られなくても、置ける worker が 1 台の job の置き先を
  ;; 外さない — 再開した担い手への返事に job が載り続け、起こし直さない。
  (<- seen CutSeen (sim-cluster (pulses sim-foundation) (stall-for 47.0)))
  (assert (= (len seen.after) 1) seen.after)
  (assert (is (. (get seen.after 0) exit-code) None) seen.after)
  (<- spans tuple (spans-of seen.after))
  (<- broken tuple (one-place-per-job spans))
  (assert (= broken #()) broken))


(deftest test-a-lone-job-is-stopped-by-the-long-fence-and-restarted-in-place-after-a-250-second-cut
  ;; 長い方の柵: 置ける worker が 1 台の job も、担い手の網が keep-fence-ms(240 秒)を越えて切れていれば止まる(止めの合図 -15)。
  ;; coordinator は置き先を保つので、明けた後は同じ worker で起き直す — 2 か所では走らない。
  (<- seen CutSeen (sim-cluster (pulses sim-foundation) (cut-for 250.0)))
  (<- spans tuple (spans-of seen.after))
  (<- broken tuple (one-place-per-job spans))
  (assert (= broken #()) broken)
  (val stopped (get seen.after 0))
  (val again (get seen.after -1))
  (assert (= #(stopped.worker stopped.exit-code again.worker again.exit-code) #(seen.host -15 seen.host None)) seen.after)
  ;; 止まったのは途絶(8 秒後から)が 240 秒を越えた後で、網が戻る(258 秒後)より前。
  (val lived (- stopped.ended-ms stopped.started-ms))
  (assert (< T.keep-fence-ms lived (+ 8000 250000)) #(lived stopped)))


(defrecord PartitionSeen
  "分断の筋書きの読み: host = 分断した worker・evicted = 追い出しで node ごと落とした時に動いていた process の数・after = 新しい世代が
   起きた後の process の列。"
  (#^ str host)
  (#^ int evicted)
  (#^ tuple after))


(defk partition-then-recreate []
  {:pre [] :post [(: % PartitionSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き(分断の最中の同じ名の新しい世代): 8 秒待って pulse の担い手の網を 352 秒切る(分断 — 担い手の process は動き続けうる)。
   k8s が届かない node の Pod を追い出して同じ名の新しい世代を作るのは早くても約 350 秒後なので、切って 351 秒後に担い手を node ごと
   落とし(追い出し)、網が戻った後に同じ名の新しい世代を起こして 30 秒後に読む。"
  (<- (Delay 8.0))
  (<- before tuple (ProcessesOf "pulse"))
  (val host (. (get before 0) worker))
  (<- (CutWorker host 352.0))
  (<- (Delay 351.0))
  (<- evicted int (KillWorker host))
  (<- (Delay 2.0))
  (<- (StartWorker host))
  (<- (Delay 30.0))
  (<- after tuple (ProcessesOf "pulse"))
  (PartitionSeen :host host :evicted evicted :after after))


(defk partitioned-spans [processes]
  {:pre [(: processes tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "分断の読み替えをした条 C2 の区間の列を作るため: 模擬の世界の追い出し(KillWorker)は担い手の process を -9 で止めるが、本物の分断では
   k8s の追い出しは届かない node の上の process を止めない — 追い出しで -9 で終わった process は走り続けていたと読む(終わり = None)。"
  (tuple (gfor p processes (ProcessSpan :worker p.worker :started-ms p.started-ms :ended-ms (if (= p.exit-code -9) None p.ended-ms)))))


(deftest test-a-same-name-generation-after-a-partition-does-not-overlap-the-old-process
  ;; 長い方の柵(査読の決め): 分断の最中に k8s が同じ名の worker の新しい世代を作っても(早くても約 350 秒後)、印の在る job の古い
  ;; process は keep-fence-ms(240 秒)で既に止まっていて、新しい世代の process と重ならない(条 C2)。新しい世代は印を持たないと知らせる
  ;; ので約束が外れ、置き先を引き継いで起こす。
  (<- seen PartitionSeen (sim-cluster (pulses sim-foundation) (partition-then-recreate)))
  (<- spans tuple (partitioned-spans seen.after))
  (<- broken tuple (one-place-per-job spans))
  (assert (= broken #()) broken)
  (assert (= seen.evicted 0) seen)
  (val old (get seen.after 0))
  (val new (get seen.after -1))
  (assert (= #(old.exit-code new.worker new.exit-code) #(-15 seen.host None)) seen.after)
  (assert (< old.ended-ms new.started-ms) seen.after))


(defk cut-then-join []
  {:pre [] :post [(: % CutSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き(条件 2 の (a)): w1 の上の pulse の網を 120 秒切り、10 秒後に能力の合う w2 を起こす。切って 70 秒後に読み、明けた後 30 秒
   待ってから、もう一度 w1 の網を 90 秒切る(今度は移せる先が在る — 時間の柵で移る)。最後に全部の process を読む。"
  (<- (Delay 8.0))
  (<- (CutWorker "w1" 120.0))
  (<- (Delay 10.0))
  (<- (StartWorker "w2"))
  (<- (Delay 60.0))
  (<- mid tuple (ProcessesOf "pulse"))
  (<- readiness SimReadiness (ReadinessOf "pulse"))
  (<- (Delay 80.0))
  (<- (CutWorker "w1" 90.0))
  (<- (Delay 100.0))
  (<- after tuple (ProcessesOf "pulse"))
  (CutSeen :host "w1" :mid mid :readiness readiness :after after))


(val JOINING #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]))
               (SimWorker :name "w2" :provides (frozenset ["cluster-net"]) :starts-down True)))


(deftest test-a-capable-worker-joining-during-a-cut-does-not-run-the-job-twice
  ;; 受入 5・条件 2 の (a): 途絶の最中に能力の合う 2 台目が加わっても、印を渡した担い手から job を移さない(担い手は印で動き続けて
  ;; いる)。明けた後、担い手は印の無い返事を受けて印を持たないと知らせ、保証は時間の柵へ戻る — 次の途絶では fence(20 秒)で止まり、
  ;; 移し替え(45 秒)の後に 2 台目へ移る。どの時点でも 2 か所で走らない(条 C2)。
  (<- seen CutSeen (sim-cluster (pulses sim-foundation) (cut-then-join) :workers JOINING))
  (<- spans tuple (spans-of seen.after))
  (<- broken tuple (one-place-per-job spans))
  (assert (= broken #()) broken)
  (assert (= (lfor p seen.mid p.worker) ["w1"]) seen.mid)
  (assert (is (. (get seen.mid 0) exit-code) None) seen.mid)
  (assert (= seen.readiness.state "Unknown") seen.readiness)
  (val first (get seen.after 0))
  (val moved (get seen.after -1))
  (assert (= #(first.worker first.exit-code) #("w1" -15)) seen.after)
  (assert (= #(moved.worker moved.exit-code) #("w2" None)) seen.after))


(defk cut-then-widen []
  {:pre [] :post [(: % CutSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き(条件 2 の (b)): w1(lone を持つ唯一の worker)の上の pulse の網を 120 秒切り、10 秒後に needs を cluster-net へ広げた宣言で
   宣言し直す(w2 にも置けるようになる)。切って 70 秒後と、明けて 30 秒後に読む。"
  (<- (Delay 8.0))
  (<- (CutWorker "w1" 120.0))
  (<- (Delay 10.0))
  (<- (Redeclare (wide-pulses sim-foundation)))
  (<- (Delay 60.0))
  (<- mid tuple (ProcessesOf "pulse"))
  (<- readiness SimReadiness (ReadinessOf "pulse"))
  (<- (Delay 80.0))
  (<- after tuple (ProcessesOf "pulse"))
  (CutSeen :host "w1" :mid mid :readiness readiness :after after))


(val WIDENING #((SimWorker :name "w1" :provides (frozenset ["lone" "cluster-net"]))
                (SimWorker :name "w2" :provides (frozenset ["cluster-net"]))))


(deftest test-widened-needs-during-a-cut-do-not-run-the-job-twice
  ;; 受入 5・条件 2 の (b): 途絶の最中に宣言の needs が変わって置ける worker が増えても、印を渡した担い手から job を移さない。明けた後も
  ;; 担い手は条件を満たすので、そのまま動かし続ける(起こし直さない・2 か所で走らない)。
  (<- seen CutSeen (sim-cluster (lone-pulses sim-foundation) (cut-then-widen) :workers WIDENING))
  (<- spans tuple (spans-of seen.after))
  (<- broken tuple (one-place-per-job spans))
  (assert (= broken #()) broken)
  (assert (= (lfor p seen.mid p.worker) ["w1"]) seen.mid)
  (assert (= (lfor p seen.after #(p.worker p.exit-code)) [#("w1" None)]) seen.after))


(defk join-then-drain []
  {:pre [] :post [(: % CutSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き(条件 2 の (c)): w1 の上の pulse(移せる先が無い — 印あり)の脇に w2 を起こし、w1 の drain を頼む。30 秒後に読む。"
  (<- (Delay 8.0))
  (<- (StartWorker "w2"))
  (<- (Delay 2.0))
  (<- (DrainWorker "w1"))
  (<- (Delay 30.0))
  (<- after tuple (ProcessesOf "pulse"))
  (<- readiness SimReadiness (ReadinessOf "pulse"))
  (CutSeen :host "w1" :mid #() :readiness readiness :after after))


(deftest test-draining-the-holder-moves-the-job-only-after-it-stops
  ;; 条件 2 の (c): 置ける唯一の worker だった担い手が drain に入ると(脇に置ける worker が加わった後)、担い手は印の無い返事を受けて
  ;; 印を持たないと知らせ、job を止め、止め終えた後に他へ置かれる — 2 か所で走らない。
  (<- seen CutSeen (sim-cluster (pulses sim-foundation) (join-then-drain) :workers JOINING))
  (val old (get seen.after 0))
  (val new (get seen.after -1))
  (assert (= #(old.worker old.exit-code) #("w1" -15)) seen.after)
  (assert (= #(new.worker new.exit-code) #("w2" None)) seen.after)
  (<- spans tuple (spans-of seen.after))
  (<- broken tuple (one-place-per-job spans))
  (assert (= broken #()) broken))


(deftest test-an-old-worker-without-marks-still-stops-at-the-fence-and-is-restarted-in-place
  ;; 受入 4(古い worker と新しい coordinator の組): 印を知らない worker は今までどおり fence で止める。coordinator は移せる先が無いので
  ;; 置き先を保ち、明けた後に同じ worker で起こし直す(以前と同じ止まりと起こし直し)— 2 か所では走らない。
  (<- seen CutSeen (sim-cluster (pulses sim-foundation) (cut-for 120.0)
                                :workers #((SimWorker :name "old" :provides (frozenset ["cluster-net"]) :ignores-keep-marks True))))
  (val stopped (get seen.after 0))
  (val again (get seen.after -1))
  (assert (= #(stopped.worker stopped.exit-code) #("old" -15)) seen.after)
  (assert (= #(again.worker again.exit-code) #("old" None)) seen.after)
  (assert (!= stopped.instance again.instance) seen.after)
  (<- spans tuple (spans-of seen.after))
  (<- broken tuple (one-place-per-job spans))
  (assert (= broken #()) broken))


(deftest test-a-counterexample-worker-that-ignores-the-fence-breaks-c2
  ;; 失敗ケース(条 C2): 印の無い job も途絶で止めない壊れた worker(ignores-fence)では、移せる先の在る job が移し替えの後に 2 か所で
  ;; 走り、C2 が重なりを名指す — 本物の worker の fence(印の無い job は止める)がそれを防いでいることの裏返し。
  (val workers #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :ignores-fence True)
                 (SimWorker :name "w2" :provides (frozenset ["cluster-net"]) :ignores-fence True)))
  (<- seen CutSeen (sim-cluster (pulses sim-foundation) (cut-for 90.0) :workers workers))
  (<- spans tuple (spans-of seen.after))
  (<- broken tuple (one-place-per-job spans))
  (assert (= (len broken) 1) broken)
  (assert (= (sorted #((. (get broken 0) first worker) (. (get broken 0) second worker))) ["w1" "w2"]) broken))
