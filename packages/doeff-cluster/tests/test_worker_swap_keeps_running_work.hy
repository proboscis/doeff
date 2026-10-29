;; worker の作り直し(本番の worker の Pod の作り直し — 値の変更や doeff-cluster の差の配備)で、他の worker へ移せない handoff の job が
;; 走らせている仕事を切らない(AL-D2 — 本番の作り替えが走っている仕事を巻き込まない)。
;;
;; 筋書き: worker w1 だけが job host(needs cluster-net・update handoff・12 秒の仕事 — tests/fixtures/working_programs.hy)を持てる。仕事の
;; 3 秒目に w1 を、本番の Deployment の RollingUpdate(maxSurge 1)と同じ順で入れ替える: 新しい世代を先に起こし(RollWorker)、新しい世代が
;; coordinator に名乗れた(本番の新しい Pod の readinessProbe が通った)後に、旧い世代の Pod の preStop(DrainGeneration — drained まで頼み
;; 直す本番の drain_client.await-drained と同じ)を回し、旧い世代を止める(StopElder)。
;; 求める姿: 新しい世代の host の process が Ready になると、旧い世代の process は止められずに退く知らせを受け、仕事を終わりまで回して
;; (盤に done)自分で終わる(盤に left・exit 0)。旧い Pod の preStop はその後に drained を返す。新しい世代の process は動き続ける。
;; 直す前(fc578dca6 — 本番の Recreate と同じ swap-a の順): 最初の世代の process は 6.01 秒に exit -15 で終わり、仕事の done が無かった。
;; 世代を先に起こす順でも、退いた世代への返事の印(supersededBy)と旧い Pod の drain の待ち(SupersededRun)が無ければ、旧い Pod の preStop が
;; 即座に drained を返し、止めで仕事が切れる(doeff L1973 の前)。
(require doeff-hy.macros [deftest defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.sim.local [sim-cluster SimWorker SimProcess ProcessesOf SharedRows RollWorker DrainGeneration StopElder ReadCoordinator])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.working_programs [WORK-PREFIX working-hosts])

(val W1 (SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :task-reserve 0))


(defrecord RollSeen
  "筋書きが読んだ姿: processes = host の process(起きた順)・rows = 盤の work/ の行・drained-ms = 旧い Pod の preStop が drained を受けた刻。"
  (#^ (get tuple #(SimProcess ...)) processes)
  (#^ dict rows)
  (#^ int drained-ms))


(defk drained-generation [name boot]
  {:pre [(: name str) (: boot str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "旧い Pod の preStop の代役: 世代 boot の drain を 2 秒ごとに頼み直し、drained を受けた刻を返す(上限 120 回 — 本番の drain_client と同じ形)。"
  (var drained None)
  (var tries 0)
  (while (and (is drained None) (< tries 120))
    (<- answer dict (DrainGeneration name boot))
    (assert (= (get answer "status") 200) answer)
    (if (get answer "body" "drain" "drained")
        (do (<- at int (now-epoch-ms))
            (:= drained at))
        (do (:= tries (+ tries 1))
            (<- (Delay 2.0)))))
  (assert (is-not drained None) "旧い Pod の drain が終わらない")
  drained)


(defk new-generation-named [name elder]
  {:pre [(: name str) (: elder str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本番の新しい Pod の readinessProbe の代役: coordinator が名 name の今の世代を退いた世代 elder でない世代と読むまで 1 秒ごとに待ち、
   その世代を返す(上限 30 回)。"
  (var boot elder)
  (var tries 0)
  (while (and (= boot elder) (< tries 30))
    (<- view dict (ReadCoordinator (+ "/workers/" name)))
    (:= boot (.get view "boot" elder))
    (when (= boot elder)
      (:= tries (+ tries 1))
      (<- (Delay 1.0))))
  (assert (!= boot elder) "新しい世代が coordinator に名乗れない")
  boot)


(defk rolled-during-the-work []
  {:pre [] :post [(: % RollSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 仕事の 3 秒目に w1 の新しい世代を先に起こし、旧い世代の preStop(drained まで)と止めの後、2 秒待って読む。"
  (<- (Delay 3.0))
  (<- elder (| str None) (RollWorker "w1"))
  (assert (is-not elder None))
  (<- _new str (new-generation-named "w1" elder))
  (<- drained int (drained-generation "w1" elder))
  (<- (StopElder "w1" elder))
  (<- (Delay 2.0))
  (<- processes tuple (ProcessesOf "host"))
  (<- rows dict (SharedRows (+ WORK-PREFIX "/")))
  (RollSeen :processes (tuple (sorted processes :key (fn [p] p.started-ms))) :rows rows :drained-ms drained))


(deftest test-replacing-the-worker-of-an-unmovable-handoff-job-lets-its-running-work-finish
  (<- seen RollSeen (sim-cluster :notice-broker (MemoryBroker) :workers #(W1) (working-hosts sim-foundation) (rolled-during-the-work)))
  (val first (get seen.processes 0))
  (val row (fn [kind] (.get seen.rows (.format "{}/{}/{}" WORK-PREFIX first.instance kind))))
  (assert (is-not (row "begun") None) seen.rows)
  ;; 最初の世代の仕事は終わりまで回り、退く知らせの後に自分で終わる(止めで切られない)。
  (assert (is-not (row "done") None) #(first seen.rows))
  (assert (is-not (row "left") None) #(first seen.rows))
  (assert (= first.exit-code 0) first)
  ;; 旧い Pod の preStop は、退いた process が終わった後に drained を受けた。
  (assert (>= seen.drained-ms (get (row "left") "at")) #(seen.drained-ms seen.rows))
  ;; 新しい世代の process は動き続ける。
  (assert (any (gfor p (cut seen.processes 1 None) (is p.ended-ms None))) seen.processes))
