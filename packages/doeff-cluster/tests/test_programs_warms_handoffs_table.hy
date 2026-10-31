;;; programs・warms・handoffs の欄が表(Table)で、書き手が「変わった行だけの書き」であることの検(T6b)。
;;;
;;; 時間では測らない。見る物は 3 つ: 残る行(鍵)、書きの無い拍で状態が元と同じ物(is)であること、保存の鍵と値。
(require doeff-hy.macros [deftest val <- defk])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

(import doeff_cluster [ClusterTiming])
(import doeff_cluster [ClusterState WorkerInfo]
        doeff_cluster._coordinator.intent.cluster_model [Drain HandoffPhase HandoffWatch ProgramRow WarmEntry])
(import doeff_cluster [sweep-due]
        doeff_cluster._coordinator.core.cluster_policy [sweep-warms warms-for])
(import doeff_cluster._coordinator.core.handoff_policy [handoff-deadline watch-handoffs])
(import doeff_cluster._coordinator.core.program_policy [sweep-programs PROGRAM-GRACE-MS])
(import doeff_cluster._coordinator.protocol.durable_kv [full-kv state-from-kv])
(import doeff_cluster._coordinator.protocol.state_json [state-to-json state-from-json])
(import doeff_hy._table [Table TableWrite table-of])
(import tests.test_handoff_deadline [Sim HANDOFF steps])
(import tests.test_quiet_due_placement_rollout [LONG-LEASE WINDOW-OVER-DEADLINE])

(val T (ClusterTiming))
(val SHA-A (* "a" 64))
(val SHA-B (* "b" 64))
(val PUT-MS 1000)
(val LIVE-WARM (WarmEntry :key "live" :runtime-env {} :needs #("net") :until-ms 5000 :holder "h"))
(val OLD-WARM (WarmEntry :key "old" :runtime-env {} :needs #("net") :until-ms 900 :holder "h"))


;; 参照の無い Program 2 つ(置いた刻は違う)を持つ状態。
(val PROGRAMS-STATE (ClusterState :programs (table-of #((TableWrite SHA-A (ProgramRow :blob "a" :versions {} :put-ms PUT-MS))
                                                         (TableWrite SHA-B (ProgramRow :blob "b" :versions {} :put-ms (+ PUT-MS 500)))))))


(deftest test-sweep-programs-drops-an-unreferenced-program-only-after-the-grace
  (val state PROGRAMS-STATE)
  (assert (is (sweep-programs state (+ PUT-MS PROGRAM-GRACE-MS)) state) "猶予の内に状態が変わった")
  (val later (sweep-programs state (+ PUT-MS 500 PROGRAM-GRACE-MS 1)))
  (assert (isinstance later.programs Table) later.programs)
  (assert (= (.keys later.programs) #()) later.programs)
  ;; 猶予を過ぎたのが片方だけなら、その行だけが消える(残る行は同じ物)。
  (val half (sweep-programs state (+ PUT-MS PROGRAM-GRACE-MS 1)))
  (assert (= (.keys half.programs) #(SHA-B)) half.programs)
  (assert (is (.row half.programs SHA-B) (.row state.programs SHA-B))))


(deftest test-sweep-warms-drops-the-expired-rows-and-keeps-the-state-when-none-expired
  (val state (ClusterState :warms (table-of #((TableWrite "live" LIVE-WARM) (TableWrite "old" OLD-WARM)))))
  (val swept (! (sweep-warms state 1000)))
  (assert (isinstance swept.warms Table) swept.warms)
  (assert (= (.keys swept.warms) #("live")) swept.warms)
  (assert (is (.row swept.warms "live") LIVE-WARM))
  (assert (is (! (sweep-warms swept 1000)) swept) "期限の切れた行が無い拍で状態が変わった"))


(deftest test-warms-for-does-not-offer-rows-to-a-draining-worker
  (val worker (WorkerInfo "w1" #("net") 0 :task-reserve 0 :background-room 10))
  (val warms (table-of #((TableWrite "live" LIVE-WARM) (TableWrite "old" OLD-WARM))))
  (val plain (ClusterState :workers (table-of #((TableWrite "w1" worker))) :warms warms))
  (assert (= (tuple (gfor o (warms-for plain "w1" 1000) o.key)) #("live")) "期限の内の行だけを配る")
  (val draining (ClusterState :workers (table-of #((TableWrite "w1" worker))) :warms warms
                              :drains (table-of #((TableWrite "w1" (Drain :worker "w1" :since-ms 0 :until-ms 9000))))))
  (assert (= (warms-for draining "w1" 1000) #()) "drain 中の worker に配った"))


(deftest test-sweep-due-reads-the-three-tables-by-size-not-by-truth
  (val empty (ClusterState))
  (assert (= (.size empty.programs) 0))
  (<- idle (sweep-due empty 0 T))
  (<- with-warm (sweep-due (ClusterState :warms (table-of #((TableWrite "live" LIVE-WARM)))) 0 T))
  (assert (!= idle with-warm) #(idle with-warm)))


(deftest test-watch-handoffs-starts-abandons-and-drops-the-watch-and-keeps-the-state-when-nothing-changes
  (val sim (Sim WINDOW-OVER-DEADLINE))
  (<- (steps sim 12))
  (sim.mark-broken "r2")
  (sim.redeclare (| WINDOW-OVER-DEADLINE {"revision" "r2"}))
  (<- (steps sim 10))
  (val state sim.state)
  (val watch (.row state.handoffs "writer-a"))
  (assert (= watch.phase HandoffPhase.WAITING) watch)
  (val job (next (gfor j state.jobs :if (= j.spec.name "writer-a") j)))
  (val due (handoff-deadline watch job))
  ;; 変わらない拍: 同じ状態(is)で、見張りの行も同じ物。
  (val quiet (watch-handoffs (- due 1) state LONG-LEASE))
  (assert (is quiet state) "見張りの変わらない拍で状態が作り直された")
  (assert (is (.row quiet.handoffs "writer-a") watch))
  ;; 諦める拍: その行だけが書かれる。
  (val abandoned (watch-handoffs due state LONG-LEASE))
  (assert (= (. (.row abandoned.handoffs "writer-a") phase) HandoffPhase.ABANDONED))
  (assert (is (watch-handoffs due abandoned LONG-LEASE) abandoned) "諦めた後の拍で状態が変わった")
  ;; 捨てる拍: 宣言の無い Service の見張りは消える。
  (val stale (HandoffWatch :declaration "x" :since-ms 0))
  (val orphan (ClusterState :handoffs (table-of #((TableWrite "gone" stale)))))
  (assert (= (.size (. (watch-handoffs 10 orphan T) handoffs)) 0))
  (val bare (ClusterState))
  (assert (is (watch-handoffs 10 bare T) bare) "見張りも入れ替えの Service も無い拍で状態が変わった"))


(deftest test-the-three-tables-keep-their-stored-keys-and-values
  (val handoff (HandoffWatch :declaration "x" :since-ms 7))
  (val state (ClusterState :programs (table-of #((TableWrite SHA-A (ProgramRow :blob "a" :versions {"v" "1"} :put-ms PUT-MS))))
                           :warms (table-of #((TableWrite "live" LIVE-WARM)))
                           :handoffs (table-of #((TableWrite "svc" handoff)))))
  (<- kv dict (full-kv state))
  (assert (in (+ "program/" SHA-A) kv) (sorted kv))
  (assert (in "warm/live" kv) (sorted kv))
  (assert (in "handoff/svc" kv) (sorted kv))
  (<- again ClusterState (state-from-kv kv 0))
  (assert (= again.programs state.programs))
  (assert (= again.warms state.warms))
  (assert (= again.handoffs state.handoffs))
  (<- data dict (state-to-json state))
  (assert (in SHA-A (get data "programs")))
  (assert (in "live" (get data "warms")))
  (assert (in "svc" (get data "handoffs")))
  (<- from-file ClusterState (state-from-json data 0))
  (assert (= from-file.programs state.programs))
  (assert (= from-file.warms state.warms))
  (assert (= from-file.handoffs state.handoffs)))
