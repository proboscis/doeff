;; 版の判定 version-state と欄 status.version(2026-09-29・#1013 — #884 の設計 4.1 節): coordinator が Service ごとに「指定の版が
;; 実際に仕事をしているか」を 5 値(Current・Updating・Blocked・Stopped・Unknown)で答える。running-process の ok は handoff の途中で
;; 「新しい版が仕事をしている」を意味しない(旧い版が退避名で仕事を続ける)ので、version-state は running-process・入れ替えの見張り・
;; 停止の述語を呼んで組み立てる。
;;   1. 網羅: worker の phase(13 値)と NotReady の理由の種類(11 値)の全部の値が 5 値のどれかへ写る(値を足して写し忘れると赤)。
;;      running-process の各分岐が種類を名乗り、version-state がその種類で答える(本物の respond で状態を作る)。
;;   2. 入れ替えの模擬の世界(test_handoff_deadline の Sim): r1 → 壊れた r2 の 1 周。5 秒後 = Updating・期限の後 = Blocked・
;;      recreate の起動中 = Updating。
;;   3. drain の模擬(test_drain の Coord): 置き先が「前の担い手の停止を待つ」間は Updating。
;;   4. 停止の述語は 1 つ: target-view と version-state が同じ関数(service-stopped)を呼ぶ(止まっている・止めている途中)。
(require doeff-hy.macros [deftest defk val <-])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState RefusedJob RolloutTarget VersionState NotReadyKind UnplacedKind])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.shared.intent.job_model [JobPhase] doeff_cluster.shared.core.job_rules [spec-hash])
(import doeff_cluster.coordinator.core.cluster_policy [unplaced-jobs])
(import doeff_cluster.coordinator.core.api_policy [target-view])
(import doeff_cluster.coordinator.protocol.request_bodies [responded])
(import doeff_cluster.coordinator.core.api_policy :as api-policy)
(import doeff_cluster.coordinator.core.resource_policy :as resource-policy)
(import doeff_cluster.coordinator.core.resource_policy [version-state running-process live-processes not-ready-version phase-version
                                       unplaced-not-ready snapshot])
(import tests.program_rows [SAMPLE-RUN])
(import tests.test_handoff_deadline [Sim steps HANDOFF RECREATE TIMEOUT-SECONDS])
(import tests.test_drain [Coord service running-writer])

(val T (ClusterTiming))
(val SPEC {"revision" "r1" "needs" ["net"] "run" SAMPLE-RUN})
(val START 1000000)             ; 状態を読んだ時刻(0)から lease を十分に過ぎた時刻 — 起動直後の Unknown に入らない


;; --- 設計 4.1 の表(写し先)---------------------------------------------------------------------

;; phase が running でない担い手の行の phase → 状態(ENV-FAILED は再試行するかで分かれる・RUNNING は下の註)。
(val PHASE-STATES
  {JobPhase.PREPARING VersionState.UPDATING
   JobPhase.PROBING VersionState.UPDATING
   JobPhase.STARTING VersionState.UPDATING
   JobPhase.STOPPING VersionState.UPDATING
   JobPhase.STOPPED VersionState.UPDATING              ; 担い手がまだ宣言を受け取っていない
   JobPhase.BACKOFF VersionState.BLOCKED
   JobPhase.CODE-FAILED VersionState.BLOCKED
   JobPhase.PROBE-FAILED VersionState.BLOCKED
   JobPhase.STOP-UNCONFIRMED VersionState.BLOCKED
   JobPhase.FINISHED VersionState.BLOCKED               ; service の process が終わった — 想定の外
   JobPhase.HANDOFF-ABANDONED VersionState.BLOCKED
   ;; running-process は phase が running の行を「running でない」と答えない(届かない組)。届いたら様子が分からないと答える。
   JobPhase.RUNNING VersionState.UNKNOWN})

;; phase を見ない理由の種類 → 状態。
(val KIND-STATES
  {NotReadyKind.NO-DECLARATION VersionState.UPDATING    ; 宣言を消した直後で process がまだ生きている
   NotReadyKind.NO-REPLICAS VersionState.UPDATING       ; replicas 0 で止めている途中
   NotReadyKind.WAITING-PREVIOUS-HOLDER VersionState.UPDATING
   NotReadyKind.NO-ELIGIBLE-WORKER VersionState.BLOCKED
   NotReadyKind.NO-ROOM VersionState.BLOCKED
   NotReadyKind.CARRIER-SILENT VersionState.UPDATING    ; 担い手の報告が途絶えて移し替え中
   NotReadyKind.REVISION-MISMATCH VersionState.UPDATING
   NotReadyKind.NO-INSTANCE VersionState.BLOCKED        ; process の世代を報告しない古い worker
   NotReadyKind.SPEC-MISMATCH VersionState.UPDATING
   NotReadyKind.PLACEMENT-MISMATCH VersionState.UPDATING})


;; --- 本物の respond で状態を作る道具 -------------------------------------------------------------

(defk call [state method path [body None] [now START] [actor "c-me"]]
  {:pre [(: state ClusterState) (: method str) (: path str) (: body (| (get dict #(str object)) None)) (: now int) (: actor (| str None))] :post [(: % ClusterState)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "coordinator の本物の返事(api_policy.respond)へ要求を 1 件送り、次の状態を返す。"
  (val answered (responded state (! (http-request method path {} body :actor actor)) now T))
  (assert (< (get answered 1) 300) #(method path (get answered 1) (get answered 2)))
  (get answered 0))


(defk beat [state worker [rows None] [now START] [capacity 10]]
  {:pre [(: state ClusterState) (: worker str) (: rows (| (get list (get dict #(str object))) None)) (: now int) (: capacity int)] :post [(: % ClusterState)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "worker の heartbeat(rows = 担い手の行)。"
  (! (call state "POST" "/heartbeat" {"name" worker "provides" ["net"] "capacity" capacity "versions" {} "statuses" (or rows [])}
           :now now :actor None)))


(defk row-of [state [phase "running"] [extra None] [name "w"]]
  {:pre [(: state ClusterState) (: phase str) (: extra (| (get dict #(str object)) None)) (: name str)] :post [(: % (get dict #(str object)))]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "担い手の行: 今の宣言の spec で起こした process(handlers.status-row と同じ欄)。extra で欄を差し替える。"
  (val job (next (gfor j state.jobs :if (= j.spec.name name) j)))
  (val a (.get state.placements name))
  (| {"name" name "phase" phase "runningRevision" job.spec.revision "desiredRevision" job.spec.revision "pid" 100
      "attempts" 1 "detail" "" "instance" "1-a" "specHash" (spec-hash job.spec) "placement" (if a a.generation None)}
     (or extra {})))


(defk declared []
  {:pre [] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "Service w の宣言だけ(worker はまだ居ない)。"
  (! (call (ClusterState) "POST" "/resources/Service" {"name" "w" "spec" SPEC})))


(defk placed []
  {:pre [] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "Service w を atlas に置いた状態(atlas はまだ何も動かしていない)。"
  (! (beat (! (declared)) "atlas")))


(defk reporting [[extra None] [phase "running"]]
  {:pre [(: extra (| (get dict #(str object)) None)) (: phase str)] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "atlas が w の行(phase・差し替えの欄)を報告した状態。"
  (val s (! (placed)))
  (! (beat s "atlas" [(! (row-of s phase extra))])))


(defk version-of [state [name "w"] [now START]]
  {:pre [(: state ClusterState) (: name str) (: now int)] :post [(: % (get dict #(str object)))] :tags {:context "doeff-cluster-test" :role "entry"}}
  "資源の口(GET /resources/Service/<名>)の status.version。"
  (val answered (responded state (! (http-request "GET" (+ "/resources/Service/" name) {} None :actor None)) now T))
  (assert (= (get answered 1) 200) (get answered 2))
  (get answered 2 "status" "version"))


(defk pairs [version]
  {:pre [(: version dict)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "status.version の running → #(版 退いたか) の列(並びを問わない)。"
  (sorted (gfor p (get version "running") #((get p "revision") (get p "retired")))))


(defk answer [state [now START]]
  {:pre [(: state ClusterState) (: now int)] :post [(: % (get tuple #((| NotReadyKind None) VersionState)))] :tags {:context "doeff-cluster-test" :role "entry"}}
  "Service w の #(running-process の理由の種類(ok なら None) version-state の状態)。"
  (val proc (running-process state "w" now T))
  #((.get proc "kind") (. (version-state state "w" now T) state)))


(defk reported-phase [phase retryable]
  {:pre [(: phase JobPhase) (: retryable bool)] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "担い手 atlas が今の宣言の spec の行を phase で報告した状態(ENV-FAILED は失敗の種類と再試行するかを載せる)。"
  (! (reporting (if (= phase JobPhase.ENV-FAILED) {"failureKind" "sync-failed" "retryable" retryable} {}) phase.value)))


(defk scaled-to-zero [rows]
  {:pre [(: rows (| (get list (get dict #(str object))) None))] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "w を replicas 0 に書き換えた後、atlas が rows(w の process がまだ生きている行・空なら止め終えた)を報告した状態。"
  (val reported (! (reporting)))
  (val row (! (row-of reported)))
  (val rescaled (! (call reported "PUT" "/resources/Service/w" {"spec" (| SPEC {"replicas" 0})
                                                                "resourceVersion" (. (get reported.meta "Service/w") resource-version)})))
  (! (beat rescaled "atlas" (if (is rows None) [row] rows))))


;; --- 1. 網羅 -----------------------------------------------------------------------------------

(deftest test-every-phase-and-every-not-ready-kind-maps-to-one-of-the-five-states
  ;; 閉じた型の全部の値を回す: 値を足して写し忘れると match が何も返さず赤になる。
  (assert (= (len JobPhase) 13))
  (assert (= (len NotReadyKind) 11))
  (assert (= (len UnplacedKind) 3))
  (assert (= (sfor s VersionState s.value) #{"Current" "Updating" "Blocked" "Stopped" "Unknown"}))
  (for [phase JobPhase retryable [True False]]
    (assert (isinstance (phase-version phase retryable) VersionState) #(phase retryable)))
  (for [kind NotReadyKind phase (+ (list JobPhase) [None]) retryable [True False]]
    (assert (isinstance (not-ready-version kind phase retryable) VersionState) #(kind phase retryable)))
  (for [kind UnplacedKind]
    (assert (isinstance (unplaced-not-ready kind) NotReadyKind) kind)))


(deftest test-the-mapping-follows-the-design-table
  (for [#(phase want) (.items PHASE-STATES) retryable [True False]]
    (assert (= (phase-version phase retryable) want) #(phase retryable)))
  ;; 実行環境の準備の失敗: 再試行する(一時の)失敗は Updating、再試行しない失敗は Blocked。
  (assert (= (phase-version JobPhase.ENV-FAILED True) VersionState.UPDATING))
  (assert (= (phase-version JobPhase.ENV-FAILED False) VersionState.BLOCKED))
  (assert (= (set PHASE-STATES) (- (set JobPhase) #{JobPhase.ENV-FAILED})) "表が phase を 1 つ落としている")
  (for [#(kind want) (.items KIND-STATES) phase (+ (list JobPhase) [None])]
    (assert (= (not-ready-version kind phase False) want) #(kind phase)))
  (assert (= (set KIND-STATES) (- (set NotReadyKind) #{NotReadyKind.NOT-RUNNING})) "表が種類を 1 つ落としている")
  ;; phase が running でない: 担い手の行にまだ載っていない(宣言を受け取っていない)は Updating、載っていれば phase で写す。
  (assert (= (not-ready-version NotReadyKind.NOT-RUNNING None False) VersionState.UPDATING))
  (for [phase JobPhase retryable [True False]]
    (assert (= (not-ready-version NotReadyKind.NOT-RUNNING phase retryable) (phase-version phase retryable)) phase))
  ;; 置き先が無い理由の 3 種は、そのまま NotReady の種類になる。
  (assert (= (dfor k UnplacedKind k (unplaced-not-ready k))
             {UnplacedKind.WAITING-PREVIOUS-HOLDER NotReadyKind.WAITING-PREVIOUS-HOLDER
              UnplacedKind.NO-ELIGIBLE-WORKER NotReadyKind.NO-ELIGIBLE-WORKER
              UnplacedKind.NO-ROOM NotReadyKind.NO-ROOM})))


(deftest test-every-reported-phase-of-the-carrier-gives-one-of-the-five-states
  ;; 担い手が今の宣言の spec の行を phase ごとに報告した世界で、version-state が 5 値のどれかを答える(本物の heartbeat の読み)。
  (for [phase JobPhase retryable [True False]]
    (assert (isinstance (get (! (answer (! (reported-phase phase retryable)))) 1) VersionState) phase)
    (assert (= (! (answer (! (reported-phase phase retryable))))
               (if (= phase JobPhase.RUNNING)
                   #(None VersionState.CURRENT)
                   #(NotReadyKind.NOT-RUNNING (phase-version phase retryable))))
            #(phase retryable))))


(deftest test-running-process-names-the-kind-of-every-not-ready-branch
  ;; running-process の NotReady / Unknown の 11 の分岐が、それぞれ種類を名乗る。理由の文・ok・state は今までどおり。
  ;; 宣言が無い: 消した直後で、担い手の上で process がまだ生きている。
  (val running (! (reporting)))
  (val deleted (! (beat (! (call running "DELETE" "/resources/Service/w")) "atlas" [(! (row-of running))])))
  (assert (= (! (answer deleted)) #(NotReadyKind.NO-DECLARATION VersionState.UPDATING)))
  ;; replicas 0: 止めている途中(process がまだ生きている)。
  (assert (= (! (answer (! (scaled-to-zero None)))) #(NotReadyKind.NO-REPLICAS VersionState.UPDATING)))
  ;; 置き先が無い: 前の担い手の停止を待つ(置いていない zeus の上で w がまだ動いている)。
  (val waiting (! (beat (! (declared)) "zeus" [{"name" "w" "phase" "running" "runningRevision" "r1" "instance" "0-z" "specHash" "old"}])))
  (assert (not-in "w" waiting.placements))
  (assert (= (get (unplaced-jobs START waiting T) "w") "前の担い手が止め終えるのを待っている"))
  (assert (= (! (answer waiting)) #(NotReadyKind.WAITING-PREVIOUS-HOLDER VersionState.UPDATING)))
  ;; 置き先が無い: 置ける worker が無い・空きが無い(理由の文は今までどおり)。
  (assert (= (! (answer (! (declared)))) #(NotReadyKind.NO-ELIGIBLE-WORKER VersionState.BLOCKED)))
  (assert (.startswith (get (running-process (! (declared)) "w" START T) "reason") "置き先が無い: 置ける worker が無い"))
  (val full (! (beat (! (declared)) "atlas" :capacity 0)))
  (assert (= (! (answer full)) #(NotReadyKind.NO-ROOM VersionState.BLOCKED)))
  (assert (= (get (running-process full "w" START T) "reason") "置き先が無い: 置ける worker に空きが無い"))
  ;; 担い手の報告が古い: 移し替えの期限の内は Unknown(分からない)、過ぎた後(調停の前)は NotReady。
  (val silent (+ START (* 2 T.lease-ms)))
  (val gone (+ START T.reassign-after-ms T.lease-ms))
  (assert (= (get (running-process running "w" silent T) "state") "Unknown"))
  (assert (= (! (answer running silent)) #(NotReadyKind.CARRIER-SILENT VersionState.UNKNOWN)))
  (assert (= (get (running-process running "w" gone T) "state") "NotReady"))
  (assert (= (! (answer running gone)) #(NotReadyKind.CARRIER-SILENT VersionState.UPDATING)))
  ;; phase が running でない: 担い手の行にまだ載っていない。
  (assert (= (get (running-process (! (placed)) "w" START T) "reason") "担い手 atlas の上で まだ起動していない"))
  (assert (= (! (answer (! (placed)))) #(NotReadyKind.NOT-RUNNING VersionState.UPDATING)))
  ;; 版の違い・process の世代を報告しない・設定の指紋の違い・割り当ての世代の違い。
  (for [#(extra want) [[{"runningRevision" "r0"} #(NotReadyKind.REVISION-MISMATCH VersionState.UPDATING)]
                       [{"instance" None} #(NotReadyKind.NO-INSTANCE VersionState.BLOCKED)]
                       [{"specHash" "0000"} #(NotReadyKind.SPEC-MISMATCH VersionState.UPDATING)]
                       [{"placement" 99} #(NotReadyKind.PLACEMENT-MISMATCH VersionState.UPDATING)]]]
    (assert (= (! (answer (! (reporting extra)))) want) extra))
  ;; ok の答えは種類を持たない(今までと同じ欄)。
  (val proc (running-process running "w" START T))
  (assert (and (get proc "ok") (not-in "kind" proc)) proc))




(deftest test-a-worker-phase-the-coordinator-does-not-know-is-unknown
  ;; coordinator より新しい worker が知らない phase を報告した: 既定の状態へ黙って倒さず、分からないと答える。
  (val verdict (version-state (! (reporting {} "rebooting")) "w" START T))
  (assert (= verdict.state VersionState.UNKNOWN) verdict)
  (assert (in "rebooting" verdict.reason) verdict))


(deftest test-status-version-carries-state-reason-and-running
  ;; Current: running-process が ok で、退いた旧い process が生きていない。理由は空。
  (val current (! (reporting)))
  (assert (= (! (version-of current)) {"state" "Current" "reason" "" "running" [{"revision" "r1" "retired" False}]}))
  ;; state は snapshot に入る(変わった時に出来事と resourceVersion が進む)。reason と running は snapshot に入れない。
  (assert (= (get (snapshot current START T) "Service/w" "status" "version") {"state" "Current"}))
  (assert (any (gfor e current.audit (= (.get e.changes "status.version") [{"state" "Updating"} {"state" "Current"}])))
          (lfor e current.audit e.changes))
  ;; 既存の欄は今までどおり(ready・process)。
  (val body (get (responded current (! (http-request "GET" "/resources/Service/w" {} None :actor None)) START T) 2))
  (assert (= (get body "status" "ready") "Ready"))
  (assert (= (get body "status" "process" "runningRevision") "r1"))
  ;; running の母集団は still-live-somewhere と同じ: lease の内に報告した全部の worker の、名が一致する行と退いた行。
  ;; phase は process が生きている物(RUNNING・STOPPING・STOP-UNCONFIRMED)だけ。沈黙した worker の行は入れない。
  (val mixed (! (beat current "zeus" [{"name" "w" "phase" "stopping" "runningRevision" "r0" "instance" "0-z"}
                                      {"name" "w#retired-0" "retiredFrom" "w" "phase" "stop-unconfirmed" "runningRevision" "rA"}
                                      {"name" "w#retired-1" "retiredFrom" "w" "phase" "backoff" "runningRevision" "rB"}
                                      {"name" "other" "phase" "running" "runningRevision" "rX"}])))
  (assert (= (sorted (gfor p (live-processes mixed "w" START T) #(p.revision p.retired)))
             [#("r0" False) #("r1" False) #("rA" True)]))
  ;; 退いた旧い process が生きている間は、running-process が ok でも Current にしない。
  (assert (= (! (answer mixed)) #(None VersionState.UPDATING)))
  (val late (+ START (* 2 T.lease-ms)))
  (val quiet (! (beat mixed "atlas" [(! (row-of mixed))] :now late)))
  (assert (= (! (pairs (! (version-of quiet :now late)))) [#("r1" False)]))
  (assert (= (get (! (version-of quiet :now late)) "state") "Current")))


(deftest test-a-refused-declaration-is-blocked-until-it-is-deleted
  ;; 受け付けていない宣言の行(state.refused)は、DELETE で消すまで Blocked の行として残る。既存の欄 refused は今までどおり。
  (val state (ClusterState :refused {"old" (RefusedJob :name "old" :row {"name" "old" "revision" "r9"} :reason "旧い形の行")}))
  (val verdict (version-state state "old" START T))
  (assert (= verdict.state VersionState.BLOCKED) verdict)
  (assert (in "旧い形の行" verdict.reason) verdict)
  (val body (get (responded state (! (http-request "GET" "/resources/Service" {} None :actor None)) START T) 2))
  (val item (next (gfor i (get body "items") :if (= (get i "name") "old") i)))
  (assert (= (get item "status" "refused") "旧い形の行"))
  (assert (= (get item "status" "version" "state") "Blocked") item))


;; --- 2. 入れ替えの模擬の世界 ---------------------------------------------------------------------

(deftest test-a-handoff-to-a-broken-revision-is-updating-then-blocked-after-the-deadline
  (val sim (Sim HANDOFF))
  (<- (steps sim 12))
  (assert (= (get (sim.status) "version") {"state" "Current" "reason" "" "running" [{"revision" "r1" "retired" False}]})
          (sim.status))
  (sim.mark-broken "r2")
  (sim.redeclare (| HANDOFF {"revision" "r2"}))
  ;; 5 秒後: 新しい版 r2 は動いているが Ready でない。旧い版 r1 は退避名で仕事を続けている。running-process は ok と答える
  ;; (入れ替えの合図の意味)が、版の判定は Updating。
  (<- (steps sim 5))
  (val during (get (sim.status) "version"))
  (assert (get (running-process sim.state "writer-a" sim.now T) "ok"))
  (assert (= (get (sim.status) "handoff" "phase") "WaitingReady"))
  (assert (= (get during "state") "Updating") during)
  (assert (= (! (pairs during)) [#("r1" True) #("r2" False)]) during)
  (assert (in "r2" (get during "reason")) during)
  ;; 期限の後: 入れ替えを諦めた(新しい版を止め、旧い版 r1 が動き続けている)= Blocked。
  (<- (steps sim (+ TIMEOUT-SECONDS 15)))
  (val after (get (sim.status) "version"))
  (assert (= (get (sim.status) "handoff" "phase") "Abandoned"))
  (assert (= (get after "state") "Blocked") after)
  (assert (in "入れ替えを諦めた" (get after "reason")) after)
  (assert (= (! (pairs after)) [#("r1" True)]) after))


(deftest test-a-recreate-service-is-updating-while-the-new-revision-starts
  (val sim (Sim RECREATE))
  (<- (steps sim 12))
  (sim.mark-broken "r2")
  (sim.redeclare (| RECREATE {"revision" "r2"}))
  (<- (steps sim 5))
  (val starting (get (sim.status) "version"))
  (assert (= (get (sim.status) "process" "phase") "starting") (sim.status))
  (assert (= (get starting "state") "Updating") starting)
  (assert (= (get starting "running") []) starting)
  ;; r2 が動き出した後は Current — Current は健康(readiness)を含まない(壊れた r2 は NotReady のまま)。
  (<- (steps sim 20))
  (assert (= (get (sim.status) "ready") "NotReady"))
  (assert (= (get (sim.status) "version" "state") "Current") (sim.status)))


;; --- 3. drain の模擬 -----------------------------------------------------------------------------

(defk coord-version [c name]
  {:pre [(: c Coord) (: name str)] :post [(: % (get dict #(str object)))] :tags {:context "doeff-cluster-test" :role "entry"}}
  "drain の模擬の coordinator の、Service name の status.version。"
  (get (c.call "GET" (+ "/resources/Service/" name)) "status" "version"))


(deftest test-a-placement-waiting-for-the-previous-holder-to-stop-is-updating
  ;; drain で止めて移す(recreate の Service): 移す先の zeus が戻ると置き先を外し、旧い担い手 atlas が止め終えるまで置かない。
  (val c (Coord))
  (c.call "POST" "/resources/Service" {"name" "r" "spec" (! (service {"update" "recreate"}))} :expect 201)
  (c.beat "atlas" ["r"])
  (assert (= (get (! (coord-version c "r")) "state") "Current"))
  (c.advance 12)
  (c.beat "atlas" ["r"])
  (c.call "POST" "/workers/atlas/drain" {} :actor "drain@atlas")
  (c.beat "zeus")
  (c.beat "atlas" ["r"])
  (assert (not-in "r" c.state.placements))
  (assert (= (get (running-process c.state "r" c.now T) "kind") NotReadyKind.WAITING-PREVIOUS-HOLDER))
  (val waiting (! (coord-version c "r")))
  (assert (= (get waiting "state") "Updating") waiting)
  (assert (in "前の担い手が止め終えるのを待っている" (get waiting "reason")) waiting)
  (assert (= (! (pairs waiting)) [#("r1" False)]) waiting)
  ;; 旧い担い手が止め終えた → zeus に置く。zeus がまだ起こしていない間も Updating、動き出したら Current。
  (c.beat "atlas" [])
  (assert (= (c.placed "r") "zeus"))
  (assert (= (get (! (coord-version c "r")) "state") "Updating"))
  (c.beat "zeus" ["r"])
  (assert (= (get (! (coord-version c "r")) "state") "Current")))


(deftest test-a-surged-handoff-writer-stays-current-and-running-lists-both-processes
  ;; drain で並べた置き先(surge)の process も running に入る(同じ版・退いていない)。版は指定どおりなので Current。
  (<- c (running-writer))
  (c.call "POST" "/workers/atlas/drain" {} :actor "drain@atlas")
  (c.beat "zeus" ["w"])
  (c.beat "atlas" ["w"])
  (val version (! (coord-version c "w")))
  (assert (= (get version "state") "Current") version)
  (assert (= (! (pairs version)) [#("r1" False) #("r1" False)]) version))


;; --- 4. 停止の述語は 1 つ -------------------------------------------------------------------------

(defk stop-predicate-calls [state target calls]
  {:pre [(: state ClusterState) (: target RolloutTarget) (: calls (get list tuple))] :post [(: % (get tuple #(bool int VersionState int)))] :tags {:context "doeff-cluster-test" :role "entry"}}
  "target-view と version-state が同じ停止の述語を呼ぶ事を数えるため: #(target-view の stopped 述語を呼んだ回数 version-state の状態
   述語を呼んだ回数)。calls = 差し替えた述語が呼ばれるたびに伸びる列(数える前に空にする)。"
  (.clear calls)
  (val view (target-view state target {} START T))
  (val by-view (len calls))
  (.clear calls)
  (val verdict (version-state state "w" START T))
  #(view.stopped by-view verdict.state (len calls)))


(deftest test-target-view-and-version-state-call-the-same-stop-predicate [monkeypatch]
  ;; target-view(Rollout の相手の観測)と version-state が、同じ名前の述語 service-stopped を呼ぶ。述語を差し替えた spy が
  ;; 両方から呼ばれ、止めている途中・止まっている の 2 場面で両方の答えが揃う。
  (assert (is api-policy.service-stopped resource-policy.service-stopped) "target-view が別の停止の述語を持っている")
  (val calls [])
  (val original resource-policy.service-stopped)
  (val target (RolloutTarget :kind "Service" :name "w"))
  (defn #^ bool spy [#^ ClusterState state #^ str name #^ int now #^ ClusterTiming timing]
    "停止の述語の代わり: 呼ばれた事を記録して本物を呼ぶ(target-view と version-state が素の関数として呼ぶ)。"
    (.append calls #(state name now timing))
    (original state name now timing))
  (.setattr monkeypatch api-policy "service_stopped" spy)
  (.setattr monkeypatch resource-policy "service_stopped" spy)
  ;; 止めている途中: replicas 0 だが atlas の上で w がまだ動いている。
  (val stopping (! (stop-predicate-calls (! (scaled-to-zero None)) target calls)))
  (assert (= #((get stopping 0) (get stopping 2)) #(False VersionState.UPDATING)) stopping)
  (assert (and (>= (get stopping 1) 1) (>= (get stopping 3) 1)) stopping)
  ;; 止まっている: replicas 0・置き先が無い・どこにも生きていない。
  (val stopped (! (stop-predicate-calls (! (scaled-to-zero [])) target calls)))
  (assert (= #((get stopped 0) (get stopped 2)) #(True VersionState.STOPPED)) stopped)
  (assert (and (>= (get stopped 1) 1) (>= (get stopped 3) 1)) stopped)
  (assert (= (. (version-state (! (scaled-to-zero [])) "w" START T) reason) "止めている")))
