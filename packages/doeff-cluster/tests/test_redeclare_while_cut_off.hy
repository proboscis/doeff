;; 置ける worker が 1 台の job を、担い手の網が切れている間(か machine ごと落ちている間)に新しい版で宣言し直すと、担い手が戻った後に
;; その版で起き Ready になる。宣言する側は、置ける worker が 1 台も live でない job の Ready を待たずに宣言だけを coordinator の Service に
;; 残して先へ進む — 宣言へのそろいは、戻った worker が heartbeat の返事で Service に残った版を受けて起こす、という coordinator と worker の
;; 振る舞いに任せる。その振る舞いを模擬の世界(本物の coordinator と本物の worker を仮想の時計で回す sim-cluster)で確かめる。
;;   筋書きは 3 つ: 網の途絶が長い方の柵(ClusterTiming.keep-fence-ms・240 秒)より短い(旧い版は印で動き続け、戻ってから入れ替わる)・
;;   長い(旧い版は柵で止まり、戻ってから新しい版が起きる)・machine ごと落ちて同じ名の worker が起き直す。どれも条 C2 one-place-per-job
;;   (入れ替えを宣言しない job は同時に 2 つの worker の上で走らない — architecture.hy)を process の記録で判じる。
(require doeff-hy.macros [deftest defk <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff_cluster [now-epoch-ms])
(import doeff_cluster [ClusterTiming])
(import doeff_cluster [sim-cluster SimWorker ProcessesOf ReadinessOf ReadCoordinator CutWorker KillWorker StartWorker Redeclare]
        doeff_cluster._sim.local [ServiceReadiness])
(import doeff_cluster._coordinator.core.coordinator_invariants [one-place-per-job ProcessSpan])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [pulses pulses-v2])

;; 本番と同じ時間の設定(生存の窓 10 秒・fence 20 秒・長い方の柵 240 秒)。
(val T (ClusterTiming))

;; 置ける worker が 1 台だけの世界(pulse の needs は cluster-net — 他に移せる先が無い)。
(val LONE #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]))))


(defrecord AwaySeen
  "担い手の不在の間に宣言し直した筋書きの読み: before = 宣言し直しの前の pulse の process の列・declared = 宣言した Service の名・
   back-ms = 担い手が戻った刻・mid = 不在の最中の process の列・mid-version = 不在の最中の Service の status.version・after = 戻って
   落ち着いた後の process の列・readiness = 戻った後の Service の ready・version = 戻った後の Service の status.version。"
  (#^ tuple before)
  (#^ tuple declared)
  (#^ int back-ms)
  (#^ tuple mid)
  (#^ dict mid-version)
  (#^ tuple after)
  (#^ ServiceReadiness readiness)
  (#^ dict version))


(defk cut-and-redeclare [seconds]
  {:pre [(: seconds float)] :post [(: % AwaySeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 10 秒待って pulse の担い手 w1 の網を seconds 秒切り、5 秒後に版 sim-2(pulses-v2 — environ に STEP を足した)を宣言し
   直す。切って seconds - 5 秒後(網が戻る前)と、網が戻って 30 秒後に読む。"
  (<- (Delay 10.0))
  (<- before tuple (ProcessesOf "pulse"))
  (<- (CutWorker "w1" seconds))
  (<- (Delay 5.0))
  (<- declared tuple (Redeclare (pulses-v2 sim-foundation) :revision "sim-2"))
  (<- (Delay (- seconds 10.0)))
  (<- mid tuple (ProcessesOf "pulse"))
  (<- mid-service dict (ReadCoordinator "/resources/Service/pulse"))
  (<- (Delay 5.0))
  (<- back-ms int (now-epoch-ms))
  (<- (Delay 30.0))
  (<- after tuple (ProcessesOf "pulse"))
  (<- readiness ServiceReadiness (ReadinessOf "pulse"))
  (<- service dict (ReadCoordinator "/resources/Service/pulse"))
  (AwaySeen :before before :declared declared :back-ms back-ms :mid mid :mid-version (get mid-service "status" "version") :after after
            :readiness readiness :version (get service "status" "version")))


(defk down-and-redeclare [seconds]
  {:pre [(: seconds float)] :post [(: % AwaySeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き(machine ごと落ちる): 10 秒待って pulse の担い手 w1 を node ごと落とし、5 秒後に版 sim-2 を宣言し直す。落として seconds 秒後に
   同じ名の w1 を起こす。起こす直前と、起こして 30 秒後に読む。"
  (<- (Delay 10.0))
  (<- before tuple (ProcessesOf "pulse"))
  (<- _killed int (KillWorker "w1"))
  (<- (Delay 5.0))
  (<- declared tuple (Redeclare (pulses-v2 sim-foundation) :revision "sim-2"))
  (<- (Delay (- seconds 5.0)))
  (<- mid tuple (ProcessesOf "pulse"))
  (<- mid-service dict (ReadCoordinator "/resources/Service/pulse"))
  (<- back-ms int (now-epoch-ms))
  (<- (StartWorker "w1"))
  (<- (Delay 30.0))
  (<- after tuple (ProcessesOf "pulse"))
  (<- readiness ServiceReadiness (ReadinessOf "pulse"))
  (<- service dict (ReadCoordinator "/resources/Service/pulse"))
  (AwaySeen :before before :declared declared :back-ms back-ms :mid mid :mid-version (get mid-service "status" "version") :after after
            :readiness readiness :version (get service "status" "version")))


(defk spans-of [processes]
  {:pre [(: processes tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの process の記録を条 C2 の判断に渡す区間の列にするため。"
  (tuple (gfor p processes (ProcessSpan :worker p.worker :started-ms p.started-ms :ended-ms p.ended-ms))))


(defk assert-the-returned-worker-runs-the-new-version [seen]
  {:pre [(: seen AwaySeen)] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "戻った担い手が宣言し直した版で起きた事を確かめるため: 不在の最中に新しい版の process は起きておらず、版の判定も Current でない
   (coordinator は担い手の報告を受けていない)・戻った後に動いている process は
   w1 の上の 1 つで、旧い版と違う指紋を持ち、担い手が戻った後に起きた・Service は Ready で、版の判定は Current(動いているのは版
   sim-2 だけ)・どの時点でも 2 か所で走らない(条 C2)。"
  (assert (= seen.declared #("pulse")) seen.declared)
  (val old (get seen.before 0))
  (assert (= (lfor p seen.mid :if (!= p.spec-hash old.spec-hash) p) []) seen.mid)
  (assert (= #((!= (get seen.mid-version "state") "Current") (get seen.mid-version "running")) #(True [])) seen.mid-version)
  (val live (lfor p seen.after :if (is p.exit-code None) p))
  (assert (= (len live) 1) seen.after)
  (val new (get live 0))
  (assert (= new.worker "w1") new)
  (assert (!= new.spec-hash old.spec-hash) #(old new))
  (assert (>= new.started-ms seen.back-ms) #(seen.back-ms new))
  (assert (= seen.readiness.state "Ready") seen.readiness)
  (assert (= #((get seen.version "state") (get seen.version "running")) #("Current" [{"revision" "sim-2" "retired" False}])) seen.version)
  (<- spans tuple (spans-of seen.after))
  (<- broken tuple (one-place-per-job spans))
  (assert (= broken #()) broken)
  True)


(deftest test-a-lone-job-redeclared-during-a-short-cut-runs-the-new-version-after-the-cut
  ;; 網の途絶 120 秒(長い方の柵より短い): 置ける worker が 1 台の job は担い手の網が切れても印で動き続け、途絶の最中に宣言し直した版は
  ;; 担い手に届かない(旧い版だけが動いている)。網が戻った後の heartbeat の返事で担い手は新しい版を受け、入れ替えて Ready になる。
  (<- seen AwaySeen (sim-cluster :notice-broker (MemoryBroker) :timing (ClusterTiming) (pulses sim-foundation) (cut-and-redeclare 120.0)
                                 :workers LONE))
  (val old (get seen.before 0))
  (assert (= (lfor p seen.mid #(p.instance p.exit-code)) [#(old.instance None)]) seen.mid)
  (<- (assert-the-returned-worker-runs-the-new-version seen))
  ;; 旧い版は網が戻った後に入れ替えで止まった(途絶の最中には止まっていない)。
  (val old-now (get (lfor p seen.after :if (= p.instance old.instance) p) 0))
  (assert (= old-now.exit-code -15) old-now)
  (assert (>= old-now.ended-ms seen.back-ms) #(seen.back-ms old-now)))


(deftest test-a-lone-job-redeclared-during-a-cut-past-the-long-fence-starts-the-new-version-after-the-cut
  ;; 網の途絶 300 秒(長い方の柵 240 秒より長い): 旧い版は途絶が柵を越えた所で担い手が自分で止め(止めの合図 -15)、網が戻るまで job は
  ;; どこでも動かない。網が戻った後、担い手は Service に残った新しい版で job を起こし、Ready になる。
  (<- seen AwaySeen (sim-cluster :notice-broker (MemoryBroker) :timing (ClusterTiming) (pulses sim-foundation) (cut-and-redeclare 300.0)
                                 :workers LONE))
  (val old (get seen.before 0))
  (assert (= (lfor p seen.mid #(p.instance p.exit-code)) [#(old.instance -15)]) seen.mid)
  (val stopped (get seen.mid 0))
  (assert (< (+ stopped.started-ms T.keep-fence-ms) stopped.ended-ms seen.back-ms) #(seen.back-ms stopped))
  (<- (assert-the-returned-worker-runs-the-new-version seen)))


(deftest test-a-lone-job-redeclared-while-its-machine-is-down-starts-the-new-version-when-the-worker-returns
  ;; machine ごと落ちる(node ごと死に、120 秒後に同じ名の worker が起き直す): 置ける worker が 1 台も live でない間に宣言し直した版は、
  ;; 起き直した worker が最初の heartbeat の返事で受けて起こし、Ready になる。
  (<- seen AwaySeen (sim-cluster :notice-broker (MemoryBroker) :timing (ClusterTiming) (pulses sim-foundation) (down-and-redeclare 120.0)
                                 :workers LONE))
  (val old (get seen.before 0))
  (assert (= (lfor p seen.mid #(p.instance p.exit-code)) [#(old.instance -9)]) seen.mid)
  (<- (assert-the-returned-worker-runs-the-new-version seen)))
