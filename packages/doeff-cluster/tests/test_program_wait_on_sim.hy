;; worker は Program の file が手元に無い間、job を起こさずに待つ — 模擬の世界(本物の coordinator と本物の worker の判断を仮想の時計で回す sim-cluster)の筋書き。
;;
;; 実例: worker の世代の入れ替わりと coordinator の起き直しが重なった時、worker は heartbeat の返事で宣言を受けたが /programs/<sha> に届かず、
;; 手元に Program の file が無いまま service を起こした。子の入口(job_entry)は file を読めずに終了 code 3 で落ち、backoff の後の起こし直しで
;; また落ちた(12 秒の間に 3 回)。届かない間は取り寄せも止まるので、落ちる回数は途絶の長さで決まっていた。
;;   - /programs に届かない間と、それに重ねて coordinator 全部に届かない間(網の途絶・coordinator の起き直し)は process を 1 つも起こさない。
;;   - 取れた後は 1 度だけ起こす(終了 code 3 の process は 0・backoff を踏まない)。
;;   - 待っている間の状態の行は preparing で、取れない訳を添える。
;; 直す前の赤: 故障の間に exit-code 3 の process が並ぶ。
(require doeff-hy.macros [deftest defk <- val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff [EffectBase])
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff_cluster [ClusterTiming])
(import doeff_cluster [sim-cluster SimWorker ProcessesOf FailRoute CutWorker StopCoordinator Redeclare ReadCoordinator]
        doeff_cluster._sim.local [declaration-of])
(import doeff_cluster [system-of])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [pulses])

(val JOB "pulse")
(val WORKER "w1")
(val WORKERS #((SimWorker :name WORKER :provides (frozenset #("cluster-net")))))
(val NO-JOBS (system-of "no-jobs" #()))
(val FAULT-SECONDS 40.0)


(defrecord WaitSeen
  "筋書きの読み: during = 故障の間(途絶の前後の 2 度)に見た process・row = 故障の間の状態の行・after = 故障の後の process。"
  {:tags {:context "doeff-cluster-test" :role "type"}}
  (#^ tuple during)
  (#^ dict row)
  (#^ tuple after))


(defk job-row [state]
  {:pre [(: state dict)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "GET /state の本文から、筋書きの job の worker の状態の行を読むため。"
  (val rows (lfor row (get (get (get state "statuses") WORKER) "jobs") :if (= (get row "name") JOB) row))
  (assert rows state)
  (get rows 0))


(defk wait-through [outage]
  {:pre [(: outage EffectBase)] :post [(: % WaitSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 宣言の前に /programs の口を FAULT-SECONDS 秒の間 503 にして宣言し、故障の間に outage(coordinator へ届かない出来事)を重ね、
   故障の間と明けた後の process を読むため。"
  (<- declared (declaration-of (pulses sim-foundation) "sim" {} None {}))
  (for [sha declared.programs]
    (<- (FailRoute "GET" (+ "/programs/" sha) 503 FAULT-SECONDS)))
  (<- (Redeclare (pulses sim-foundation)))
  (<- (Delay 5.0))
  (<- early tuple (ProcessesOf JOB))
  (<- state dict (ReadCoordinator "/state"))
  (<- row dict (job-row state))
  (<- outage)
  (<- (Delay 20.0))
  (<- late tuple (ProcessesOf JOB))
  (<- (Delay FAULT-SECONDS))
  (<- after tuple (ProcessesOf JOB))
  (WaitSeen :during (+ early late) :row row :after after))


(defk assert-waited [seen]
  {:pre [(: seen WaitSeen)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "故障の間は process を起こさず、状態の行が取れない訳を添え、明けた後は 1 度だけ起きて動いている事を確かめるため。"
  (assert (= seen.during #()) seen.during)
  (assert (= (get seen.row "phase") "preparing") seen.row)
  (assert (in "Program" (get seen.row "detail")) seen.row)
  (assert (= (len seen.after) 1) seen.after)
  (assert (is (. (get seen.after 0) exit-code) None) seen.after)
  None)


(deftest test-a-service-waits-for-its-program-while-the-program-route-fails
  ;; heartbeat は届くが /programs には届かない間(起きた直後の coordinator の前の 5xx の形)。
  (<- seen WaitSeen (sim-cluster NO-JOBS (wait-through (Delay 0.0)) :workers WORKERS :notice-broker (MemoryBroker) :timing (ClusterTiming)))
  (<- (assert-waited seen)))


(deftest test-a-service-waits-for-its-program-while-the-worker-is-cut-off
  ;; 宣言を受けた後に網が切れる(途絶の間は最後に受けた宣言で判じ、取り寄せは止まる — 前の版はこの間も起こして落ち続けた)。
  (<- seen WaitSeen (sim-cluster NO-JOBS (wait-through (CutWorker WORKER 15.0)) :workers WORKERS :notice-broker (MemoryBroker) :timing (ClusterTiming)))
  (<- (assert-waited seen)))


(deftest test-a-service-waits-for-its-program-while-the-coordinator-restarts
  ;; 宣言を受けた後に coordinator が起き直す(本番の実例の形 — 届かない間の heartbeat は ConnectError)。
  (<- seen WaitSeen (sim-cluster NO-JOBS (wait-through (StopCoordinator 15.0)) :workers WORKERS :notice-broker (MemoryBroker) :timing (ClusterTiming)))
  (<- (assert-waited seen)))
