;; 宣言し直しで置き先の worker が替わる入れ替え(handoff)の Service も、新しい worker の process が Ready になってから旧い worker の
;; process を止める。
;;   直す前は、宣言の needs が替わって旧い担い手が条件を満たさなくなると、coordinator が置き先を外し(held-placements)、旧い担い手が
;;   止め終えたと報告してから新しい worker に置いた(place-jobs の still-live-somewhere)。新しい process が Ready になるまで Ready の書き手が
;;   居なかった(本番 2026-11-02: 起き上がりに数分の同期が要る service の置き先を 2 台の worker の間で替える宣言し直しが 5 回あり、毎回
;;   6〜9 分 Ready の process が 1 つも無かった)。
;;   直した後は drain と同じ形で並べる: 条件を満たす別の worker へ並べて置き(surge)、そこで新しい process が Ready になってから置き先を
;;   付け替え、旧い担い手は次の heartbeat で止める。並べている間、旧い担い手には版を据え置く印(holdVersion)を返し、旧い担い手の上に
;;   新しい版を起こさない。系は本物の coordinator と本物の worker の判断の上で、仮想の時計で走らせる(sim-cluster)。
(require doeff-hy.macros [deftest defk <- val])
(require doeff-hy.record [defrecord])
(require doeff-cluster.macros [defsystem])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff_cluster [now-epoch-ms])
(import doeff_cluster [ClusterTiming])
(import doeff_cluster [sim-cluster SimWorker Redeclare ProcessesOf ReportsOf])
(import doeff_cluster._worker.core.invariants [handoff-keeps-a-ready-writer])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [beacon-program late-beacon-program])


;; 起き上がりの遅い版は Ready を報告するまで 20 秒かかる(本番の数分の同期の代わり — 旧い担い手が先に止まると、
;; この秒の間 Ready の書き手が居ない)。defsystem の Program の引数は literal しか受けないので、宣言ごとに 20.0 と書く。


(defsystem beacon-on-a [#^ Callable foundation]
  "入れ替えの beacon(版 1)を能力 host-a の worker に置く宣言"
  (beacon (beacon-program foundation "beacon/x" 1.0) :replicas 1 :peak-mib 200 :settle-mib 100 :needs #{"cluster-net" "host-a"}
          :readiness {"windowSeconds" 5} :update "handoff" :environ {"STEP" "1"}))


(defsystem beacon-on-b [#^ Callable foundation]
  "版 2(起き上がりに 20 秒かかる)を能力 host-b の worker に置く宣言 — 版と置き先が同時に替わる(本番の宣言し直しの形)"
  (beacon (late-beacon-program foundation "beacon/x" 2.0 20.0) :replicas 1 :peak-mib 200 :settle-mib 100
          :needs #{"cluster-net" "host-b"} :readiness {"windowSeconds" 5} :update "handoff" :environ {"STEP" "1"}))


(defsystem beacon-slow-on-a [#^ Callable foundation]
  "起き上がりに 20 秒かかる beacon(版 1)を能力 host-a の worker に置く宣言"
  (beacon (late-beacon-program foundation "beacon/x" 1.0 20.0) :replicas 1 :peak-mib 200 :settle-mib 100
          :needs #{"cluster-net" "host-a"} :readiness {"windowSeconds" 5} :update "handoff" :environ {"STEP" "1"}))


(defsystem beacon-slow-on-b [#^ Callable foundation]
  "beacon-slow-on-a と同じ版を能力 host-b の worker に置く宣言 — 置き先だけが替わる"
  (beacon (late-beacon-program foundation "beacon/x" 1.0 20.0) :replicas 1 :peak-mib 200 :settle-mib 100
          :needs #{"cluster-net" "host-b"} :readiness {"windowSeconds" 5} :update "handoff" :environ {"STEP" "1"}))


(defrecord Moved
  "置き先を替える宣言し直しの筋書きの読み: 宣言し直した刻・前と後の beacon の process の列・後の報告の列。"
  (#^ int redeclared-at)
  (#^ tuple before)
  (#^ tuple after)
  (#^ tuple reports))


(defk redeclare-elsewhere [settle-seconds next-system after-seconds]
  {:pre [(: settle-seconds float) (: next-system "System") (: after-seconds float)] :post [(: % Moved)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: settle 秒待って beacon を読み、next-system を新しい版で宣言し直し(置き先を替える宣言し直しは新しい版で書く)、after 秒後に
   beacon の process と報告を読む。"
  (<- (Delay settle-seconds))
  (<- before tuple (ProcessesOf "beacon"))
  (<- redeclared-at int (now-epoch-ms))
  (<- (Redeclare next-system :revision "sim-2"))
  (<- (Delay after-seconds))
  (<- after tuple (ProcessesOf "beacon"))
  (<- reports tuple (ReportsOf "beacon"))
  (Moved :redeclared-at redeclared-at :before before :after after :reports reports))


(defk two-hosts []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "能力の違う worker 2 台(wa = host-a・wb = host-b — 本番の 2 台の機体の代わり)。"
  #((SimWorker :name "wa" :provides (frozenset ["cluster-net" "host-a"]))
    (SimWorker :name "wb" :provides (frozenset ["cluster-net" "host-b"]))))


(defk first-ready-at [reports instance]
  {:pre [(: reports tuple) (: instance str)] :post [(: % (| int None))] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "世代 instance の最初の Ready の報告の刻(無ければ None)。"
  (min (gfor r reports :if (and (= r.instance instance) (= r.kind "readiness") r.ready) r.at) :default None))


(defk assert-moved-without-a-gap [seen]
  {:pre [(: seen Moved)] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "置き先を替える入れ替えの読みが条を満たすか確かめるため: 旧い担い手 wa の process は止まり、その止まりは wb の新しい process の
   最初の Ready の後 — Ready の書き手が途切れない(条 W1)。wa に宣言し直しの後に起きた process は無い(旧い担い手に新しい版を起こさない)。"
  (assert (= (len seen.before) 1) seen.before)
  (val old (get seen.before 0))
  (assert (= old.worker "wa") old)
  (val old-now (get (lfor p seen.after :if (= p.instance old.instance) p) 0))
  (val fresh-on-a (lfor p seen.after :if (and (= p.worker "wa") (>= p.started-ms seen.redeclared-at)) p))
  (assert (= fresh-on-a []) fresh-on-a)
  (val moved (lfor p seen.after :if (and (= p.worker "wb") (is p.exit-code None)) p))
  (assert (= (len moved) 1) seen.after)
  (val new (get moved 0))
  (<- new-ready (| int None) (first-ready-at seen.reports new.instance))
  (assert (is-not new-ready None) seen.reports)
  (assert (= old-now.exit-code -15) old-now)
  ;; 旧い担い手の止まりは、新しい worker の process の最初の Ready の後(本番の空き = 新しい job の同期 − 旧い job の止まり ≤ 0)。
  (assert (<= new-ready old-now.ended-ms) #(new-ready old-now))
  ;; 条 W1(worker の :invariants): 世代ごとの #(最初の Ready の報告の刻 終わった刻)の列で、Ready の書き手の途切れが無い。
  (val lifetimes (tuple (gfor p seen.after
                              #((min (gfor r seen.reports :if (and (= r.instance p.instance) (= r.kind "readiness") r.ready) r.at)
                                     :default None)
                                p.ended-ms))))
  (<- gaps tuple (handoff-keeps-a-ready-writer lifetimes))
  (assert (= gaps #()) #(gaps lifetimes))
  True)


(deftest test-a-handoff-service-redeclared-onto-another-worker-stops-the-old-process-after-the-new-one-is-ready
  ;; 失敗ケース(本番 2026-11-02 の形): 版と置き先が同時に替わる宣言し直し。新しい版は起き上がりに 20 秒。直す前は wa の旧が
  ;; 先に止まり、wb の新が Ready になるまで 20 秒余り Ready の書き手が居なかった。直した後は wb の新が Ready になってから wa の旧を止める。
  (<- workers tuple (two-hosts))
  (<- seen Moved (sim-cluster :notice-broker (MemoryBroker) :timing (ClusterTiming) (beacon-on-a sim-foundation)
                              (redeclare-elsewhere 10.0 (beacon-on-b sim-foundation) 60.0)
                              :workers workers))
  (<- (assert-moved-without-a-gap seen)))


(deftest test-a-handoff-service-whose-needs-alone-change-moves-without-a-gap
  ;; 失敗ケース: 版は同じで置き先(needs)だけが替わる宣言し直し(本番の :needs を替える commit の形)。起き上がりに 20 秒。
  (<- workers tuple (two-hosts))
  (<- seen Moved (sim-cluster :notice-broker (MemoryBroker) :timing (ClusterTiming) (beacon-slow-on-a sim-foundation)
                              (redeclare-elsewhere 40.0 (beacon-slow-on-b sim-foundation) 60.0)
                              :workers workers))
  (<- (assert-moved-without-a-gap seen)))
