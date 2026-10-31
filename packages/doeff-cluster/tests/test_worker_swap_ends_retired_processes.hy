;; 同じ名の worker の新しい世代を先に起こす入れ替え(本番の Deployment の RollingUpdate — AL-D2)で、退いた process の寿命の上限
;; (宣言の readiness の retiredSeconds)を宣言しない handoff の job の、前の世代の process が、新しい世代の process が Ready になった後に
;; 止められて終わる。retiredSeconds の無い handoff の job は、同じ世代の入れ替えで「新の Ready で旧を止める」—
;; 世代をまたぐ入れ替えでも同じ決まりで止める。
;;
;; 出自: 10-12 の zeus の作り直しで、旧い Pod 5d6d78459d-zl8pw が 15:13:48 から 21:19:12 まで 6 時間 5 分 Terminating のまま残った。
;; 旧い世代に残った退いた process 2 本は退く知らせ(AwaitRetirement)を待たずに待ち受けを開いて動き続ける service で、
;; retiredSeconds も宣言しない。worker の判断 retired-actions は寿命の無い退いた process を「元の
;; job の新しい process が Ready と数えられたら止める」が、前の世代の worker の上には新しい process が居ない(新しい process は新しい世代の
;; worker の上)ので止める時が来ず、旧い Pod の preStop は drain の上限 DRAIN_DEADLINE(21900 秒)まで待った。
;;
;; 筋書き: worker w1 だけが job server(needs cluster-net・update handoff・retiredSeconds 無し・退く知らせを待たずに動き続ける —
;; tests/fixtures/working_programs.hy の serving-hosts)を持てる。server が動き出した後に w1 の新しい世代を先に起こし(RollWorker)、新しい
;; Pod が Ready になった後(readinessProbe の代役)に旧い世代の preStop(DrainGeneration)を回し、旧い世代を止める(StopElder)。
;; 求める姿: 前の世代の server は、新しい世代の server が Ready になった後、止めの猶予(stop-grace)の内に止められて終わり、旧い Pod の
;; preStop は RETIRE-BOUND-MS の内に drained を受ける。新しい世代の server は動き続ける。
;; 直す前: 前の世代の server は止められず、preStop は drained を受けない(筋書きの上限 120 秒を越えて「旧い Pod の drain が終わらない」)。
(require doeff-hy.macros [deftest defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff_cluster [now-epoch-ms])
(import doeff_cluster [PlainText])
(import doeff_cluster [sim-cluster SimWorker SimProcess ProcessesOf RollWorker DrainGeneration StopElder ReadCoordinator])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.working_programs [serving-hosts])

(val W1 (SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :task-reserve 0))
;; 新しい Pod が Ready になってから旧い Pod の preStop が drained を受けるまでの上限: 前の世代の worker が印(supersededBy)を次の heartbeat の
;; 返事で受けて退かせ、止め(TERM — 止めの猶予 stop-grace 10 秒の後は KILL)で終わり、その終わりを次の heartbeat で coordinator が読むまで。
(val RETIRE-BOUND-MS 30000)


(defrecord RetireSeen
  "筋書きが読んだ姿: processes = server の process(起きた順)・switched-ms = 旧い Pod の preStop が始まった刻(新しい Pod が Ready になった
   刻)・drained-ms = 旧い Pod の preStop が drained を受けた刻。"
  (#^ (get tuple #(SimProcess ...)) processes)
  (#^ int switched-ms)
  (#^ int drained-ms))


(defk generation-drained [name boot]
  {:pre [(: name str) (: boot str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "旧い Pod の preStop の代役: 世代 boot の drain を 1 秒ごとに頼み直し、drained を受けた刻を返す(上限 120 回)。"
  (var drained None)
  (var tries 0)
  (while (and (is drained None) (< tries 120))
    (<- answer dict (DrainGeneration name boot))
    (assert (= (get answer "status") 200) answer)
    (if (get answer "body" "drain" "drained")
        (do (<- at int (now-epoch-ms))
            (:= drained at))
        (do (:= tries (+ tries 1))
            (<- (Delay 1.0)))))
  (assert (is-not drained None) "旧い Pod の drain が終わらない")
  drained)


(defk generation-ready [name elder]
  {:pre [(: name str) (: elder str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本番の新しい Pod の readinessProbe の代役: coordinator が名 name の今の世代を elder でない世代と読み、ready と答えるまで 1 秒ごとに
   待つ(上限 30 回)。"
  (var boot elder)
  (var ready False)
  (var tries 0)
  (while (and (or (= boot elder) (not ready)) (< tries 30))
    (<- read (| dict PlainText) (ReadCoordinator (+ "/workers/" name)))
    (:= boot (if (isinstance read dict) (.get read "boot" elder) elder))
    (:= ready (and (isinstance read dict) (is (.get read "ready") True)))
    (when (or (= boot elder) (not ready))
      (:= tries (+ tries 1))
      (<- (Delay 1.0))))
  (assert (and (!= boot elder) ready) "新しい世代が Ready にならない")
  boot)


(defk rolled-while-serving []
  {:pre [] :post [(: % RetireSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: server が動き出した後(4 秒)に w1 の新しい世代を先に起こし、新しい Pod が Ready になった後に旧い世代の preStop(drained
   まで)と止めを回し、2 秒待って読む。"
  (<- (Delay 4.0))
  (<- elder (| str None) (RollWorker "w1"))
  (assert (is-not elder None))
  (<- _new str (generation-ready "w1" elder))
  (<- switched int (now-epoch-ms))
  (<- drained int (generation-drained "w1" elder))
  (<- (StopElder "w1" elder))
  (<- (Delay 2.0))
  (<- processes tuple (ProcessesOf "server"))
  (RetireSeen :processes (tuple (sorted processes :key (fn [p] p.started-ms))) :switched-ms switched :drained-ms drained))


(deftest test-a-retired-handoff-process-without-a-declared-lifetime-ends-once-the-new-generation-runs-it
  (<- seen RetireSeen (sim-cluster :notice-broker (MemoryBroker) :workers #(W1) (serving-hosts sim-foundation) (rolled-while-serving)))
  (assert (>= (len seen.processes) 2) seen.processes)
  (val elder (get seen.processes 0))
  ;; 前の世代の server は止められて終わり、旧い Pod の preStop は上限の内に drained を受けた。
  (assert (is-not elder.ended-ms None) elder)
  (assert (<= (- seen.drained-ms seen.switched-ms) RETIRE-BOUND-MS) #(seen.switched-ms seen.drained-ms elder))
  ;; 新しい世代の server は動き続ける。
  (assert (any (gfor p (cut seen.processes 1 None) (is p.ended-ms None))) seen.processes))
