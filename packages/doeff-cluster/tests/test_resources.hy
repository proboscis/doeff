;; coordinator の資源の口: 資源ごとの compare-and-set・送り手と出来事の記録・所有者だけが消せる・旧い PUT /jobs の写し・
;; readiness・盤の行ごとの版。
(require doeff-hy.macros [deftest defk <- val var])
(import doeff_cluster.shared.intent.protocol [ClusterTiming PlainText Request])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState])
(import doeff_cluster.coordinator.core.cluster_policy [board-changes job-from-json] doeff_cluster.coordinator.protocol.state_json [state-to-json state-from-json board-rows-of])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.shared.core.job_rules [spec-hash])
(import doeff_cluster.coordinator.core.api_policy [tick plan-rollouts])
(import doeff_cluster.coordinator.protocol.request_bodies [responded])
(import doeff_cluster.coordinator.core.resource_policy [LEGACY-OWNER adopt-legacy])
(import tests.program_rows [SAMPLE-RUN program-placed program-run])
(import doeff [run])

(setv T (ClusterTiming))
(setv V {"python" "3.14.0"})
(setv SPEC {"revision" "r1" "needs" ["net"] "run" SAMPLE-RUN})

(defn #^ Request req [#^ str method #^ str path #^ (| dict None) [body None] #^ (| dict None) [query None] #^ (| str None) [actor "c-me"]]
  (http-request method path (or query {}) body :actor actor :peer "10.0.0.9"))

(defn #^ tuple call [#^ ClusterState state #^ str method #^ str path #^ (| dict None) [body None] #^ (| dict None) [query None]
            #^ (| str None) [actor "c-me"] #^ int [now 1000]]
  (responded state (req method path body query actor) now T))

(defn #^ ClusterState beat [#^ ClusterState state #^ str name #^ int now #^ (| list None) [statuses None]]
  (get (call state "POST" "/heartbeat" {"name" name "provides" ["net"] "capacity" 10 "versions" V "statuses" (or statuses [])}
             :actor None :now now) 0))

(defn #^ int rv [#^ ClusterState state #^ str kind #^ str name]
  (. (get (. state meta) (+ kind "/" name)) resource-version))


(deftest test-service-writes-are-compare-and-set-per-resource
  (val reply-1 (call (ClusterState) "POST" "/resources/Service" {"name" "a" "spec" SPEC}))
  (val s (get reply-1 0))
  (var status (get reply-1 1))
  (assert (= status 201))
  (setv v1 (rv s "Service" "a"))
  ;; 版の無い書きは断る(読んだ版を付けて書く)
  (val reply-2 (call s "PUT" "/resources/Service/a" {"spec" (| SPEC {"revision" "r2"})}))
  (:= status (get reply-2 1))
  (var body (get reply-2 2))
  (assert (= status 400) body)
  ;; 正しい版なら通り、generation が進む
  (val reply-3 (call s "PUT" "/resources/Service/a" {"spec" (| SPEC {"revision" "r2"}) "resourceVersion" v1}))
  (val s2 (get reply-3 0))
  (:= status (get reply-3 1))
  (:= body (get reply-3 2))
  (assert (= status 200) body)
  (assert (= #((get body "generation") (get body "spec" "revision")) #(2 "r2")))
  (assert (> (rv s2 "Service" "a") v1))
  ;; 古い版(同じ v1 を読んだ別の作業係)の書きは 409。いまの版を返す。
  (val reply-4 (call s2 "PUT" "/resources/Service/a" {"spec" (| SPEC {"revision" "r3"}) "resourceVersion" v1}
                                :actor "c-other"))
  (val s3 (get reply-4 0))
  (:= status (get reply-4 1))
  (:= body (get reply-4 2))
  (assert (= status 409) body)
  (assert (= (get body "current") (rv s2 "Service" "a")))
  (assert (is s3 s2))
  ;; もう在る名前は作れない
  (assert (= (get (call s2 "POST" "/resources/Service" {"name" "a" "spec" SPEC}) 1) 409)))


(deftest test-an-idle-tick-returns-the-same-state-and-advances-no-version
  ;; 変化の無い拍(#1356): 調停(tick)も Rollout の計画(plan-rollouts)も状態そのものを返し、版の番号も出来事も進まない。
  ;; 版を付ける stamp は同じ object なら資源の写しを作らずに返すので、この同一性が 1 秒ごとの拍の計算を省く。
  ;; 反例: 割り当てか Rollout を毎拍作り直した dict で返すと、中身が同じでも別の object になり赤。
  (val reply-5 (call (ClusterState) "POST" "/resources/Service" {"name" "a" "spec" SPEC}))
  (var s (get reply-5 0))
  (val status (get reply-5 1))
  (assert (= status 201))
  (:= s (tick (beat s "atlas" 1000) 1000 T))
  (assert (in "a" s.placements) s.placements)
  (setv idle (tick s 1500 T))
  (assert (is idle s))
  (assert (= #(idle.revision idle.audit-seq (len idle.events)) #(s.revision s.audit-seq (len s.events))))
  (assert (is (get (plan-rollouts s 1500 T) 0) s)))


(deftest test-every-write-records-the-actor-and-the-versions
  (val reply-6 (call (ClusterState) "POST" "/resources/Service" {"name" "a" "spec" SPEC} :actor "c-01ABC"))
  (var s (get reply-6 0))
  (val reply-7 (call s "PUT" "/resources/Service/a" {"spec" (| SPEC {"replicas" 0}) "resourceVersion" (rv s "Service" "a")}
                       :actor "rollout-script"))
  (:= s (get reply-7 0))
  (val reply-8 (call s "GET" "/events" None {"kind" "Service" "name" "a"}))
  (var body (get reply-8 2))
  (setv events (lfor e (get body "events") :if (in (get e "verb") #("create" "update")) e))
  (assert (= (lfor e events #((get e "verb") (get e "actor"))) [#("create" "c-01ABC") #("update" "rollout-script")]))
  (setv update (get events 1))
  (assert (= (get update "fromVersion") (get events 0 "toVersion")))
  (assert (= (get update "changes" "spec.replicas") [1 0]))
  ;; 送り手の無い資源の書きは断る
  (val reply-9 (call s "POST" "/resources/Service" {"name" "b" "spec" SPEC} :actor None))
  (var status (get reply-9 1))
  (:= body (get reply-9 2))
  (assert (= status 400) body)
  (assert (in "X-Actor" (get body "error")))
  ;; 盤と task は旧い client でも通し、送り元の番地で記録する
  (val reply-10 (run (program-placed s V :now 1000)))
  (:= s (get reply-10 0))
  (val sha (get reply-10 1))
  (val reply-11 (call s "POST" "/tasks" {"program" sha "revision" "r" "needs" ["net"]} :actor None))
  (:= s (get reply-11 0))
  (:= status (get reply-11 1))
  (assert (= status 200))
  (assert (= (. (get s.audit -1) actor) "coordinator"))   ; 置き先の決め(調停)
  (assert (in "anonymous@10.0.0.9" (lfor e s.audit e.actor))))


(deftest test-only-the-owner-or-an-explicit-force-delete-removes-a-declaration
  (setv #(s _ _) (call (ClusterState) "POST" "/resources/Service" {"name" "shadow" "spec" SPEC} :actor "c-shadow-owner"))
  (val reply-12 (call s "DELETE" "/resources/Service/shadow" :actor "c-someone-else"))
  (val s2 (get reply-12 0))
  (var status (get reply-12 1))
  (val body (get reply-12 2))
  (assert (= status 403) body)
  (assert (is s2 s))
  (val reply-13 (call s "DELETE" "/resources/Service/shadow" None {"force" "true"} :actor "c-someone-else"))
  (val s3 (get reply-13 0))
  (:= status (get reply-13 1))
  (assert (= status 200))
  (assert (= (len s3.jobs) 0))
  (assert (= (. (get s3.audit -1) verb) "delete"))
  (val reply-14 (call s "DELETE" "/resources/Service/shadow" :actor "c-shadow-owner"))
  (val s4 (get reply-14 0))
  (:= status (get reply-14 1))
  (assert (= status 200)))


(deftest test-legacy-put-jobs-never-deletes-and-refuses-stale-rows
  ;; 今夜の実弾: 一覧を丸ごと置き換える PUT /jobs が他の作業係の行を黙って消しうる。移行の間の写し:
  ;; 一覧に無い行は消さない・版の無い行で既存を変えない・古い版の行は 409(1 行でも競合すれば何も書かない)。
  (val reply-15 (call (ClusterState) "POST" "/resources/Service" {"name" "shadow-a" "spec" SPEC} :actor "c-shadow"))
  (var s (get reply-15 0))
  (val reply-16 (call s "POST" "/resources/Service" {"name" "shadow-b" "spec" SPEC} :actor "c-coord"))
  (:= s (get reply-16 0))
  ;; shadow-b だけの一覧で PUT(shadow-a の行が無い)
  (setv #(_ _ view) (call s "GET" "/state"))
  (setv row (next (gfor j (get view "jobs") :if (= (get j "name") "shadow-b") j)))
  (val reply-17 (call s "PUT" "/jobs" {"jobs" [(| row {"revision" "r9"})]} :actor "c-coord"))
  (val s2 (get reply-17 0))
  (var status (get reply-17 1))
  (var body (get reply-17 2))
  (assert (= status 200) body)
  (assert (= (sorted (lfor j s2.jobs j.spec.name)) ["shadow-a" "shadow-b"]))
  (assert (= (get body "untouched") ["shadow-a"]))
  ;; 同じ古い行(版が古い)をもう一度送ると 409 で、何も変えない
  (val reply-18 (call s2 "PUT" "/jobs" {"jobs" [(| row {"revision" "r10"})]} :actor "c-coord"))
  (val s3 (get reply-18 0))
  (:= status (get reply-18 1))
  (:= body (get reply-18 2))
  (assert (= status 409) body)
  (assert (is s3 s2))
  ;; 版の無い行で既存を変えようとすると 409
  (val reply-19 (call s2 "PUT" "/jobs" {"jobs" [{"name" "shadow-a" "revision" "rX" "needs" ["net"] "run" SAMPLE-RUN}]}
                            :actor "c-coord"))
  (:= status (get reply-19 1))
  (assert (= status 409))
  ;; 送り手の無い PUT /jobs は断る
  (assert (= (get (call s2 "PUT" "/jobs" {"jobs" []} :actor None) 1) 400)))


(deftest test-legacy-state-file-is-adopted-with-versions-and-a-legacy-owner
  (setv legacy {"jobs" [{"name" "turn-runner" "revision" "r" "needs" ["net"] "pin" None "run" SAMPLE-RUN}]
                "assignments" {} "workers" [] "tasks" [] "nextTask" 1 "board" {"k" 1}}) ; 改名の前の file の形
  (setv s (adopt-legacy (! (state-from-json legacy 1000)) 1000 T))
  (assert (= (. (get s.jobs 0) owner) LEGACY-OWNER))
  (assert (is-not (rv s "Service" "turn-runner") None))
  (assert (= (. (get s.audit -1) actor) "migration"))
  (assert (= (dfor #(k row) (.items s.board) k row.version) {"k" 1}))
  ;; 新しい形で書き直した物に盤は入らない(盤は行ごとの file)
  (assert (not-in "board" (state-to-json s)))
  ;; 誰でも 1 度だけ所有者を引き取れる。引き取った後は他の送り手が変えられない
  (val reply-20 (call s "PUT" "/resources/Service/turn-runner"
                             {"spec" {"revision" "r" "needs" ["net"] "run" SAMPLE-RUN "owner" "c-lab"} "resourceVersion" (rv s "Service" "turn-runner")}
                             :actor "c-lab"))
  (val s2 (get reply-20 0))
  (var status (get reply-20 1))
  (assert (= status 200))
  (val reply-21 (call s2 "PUT" "/resources/Service/turn-runner"
                            {"spec" {"revision" "r" "needs" ["net"] "run" SAMPLE-RUN "owner" "c-thief"} "resourceVersion" (rv s2 "Service" "turn-runner")}
                            :actor "c-thief"))
  (:= status (get reply-21 1))
  (assert (= status 403)))


(deftest test-board-rows-have-their-own-versions-and-only-written-rows-are-saved
  (setv big (dfor i (range 16) (.format "shadow-a/rows/{:02x}" i) (* "x" 1000)))
  (setv s (ClusterState :board (! (board-rows-of big {}))))
  (val reply-22 (call s "PUT" "/board/writer-a/cycle" {"value" {"n" 1}} :actor None))
  (val s2 (get reply-22 0))
  (var status (get reply-22 1))
  (var body (get reply-22 2))
  (assert (= #(status (get body "resourceVersion")) #(200 1)))
  ;; 書き直しが要るのは書いた行だけ(大きな shadow の 16 区画は書き直さない)
  (assert (= (board-changes s s2) ["writer-a/cycle"]))
  ;; 行の版で compare-and-set
  (val reply-23 (call s2 "PUT" "/board/writer-a/cycle" {"value" {"n" 2} "expectVersion" 0}))
  (:= status (get reply-23 1))
  (:= body (get reply-23 2))
  (assert (= #(status (get body "resourceVersion")) #(409 1)))
  (val reply-24 (call s2 "PUT" "/board/writer-a/cycle" {"value" {"n" 2} "expectVersion" 1}))
  (val s3 (get reply-24 0))
  (:= status (get reply-24 1))
  (:= body (get reply-24 2))
  (assert (= #(status (get body "resourceVersion")) #(200 2)))
  (setv #(_ _ rows) (call s3 "GET" "/board" None {"prefix" "writer-a/" "withVersions" "1"}))
  (assert (= rows {"writer-a/cycle" {"value" {"n" 2} "resourceVersion" 2}})))


;; --- readiness: 今の宣言で今動いている process の報告だけを数える -----------------------------------------------

(defn #^ str hash-of [#^ dict spec #^ str [name "w"]]
  "coordinator が今の宣言から計算する spec の指紋(worker が process を起こした時に渡すのと同じ関数)。"
  (spec-hash (. (job-from-json (| spec {"name" name})) spec)))


(defn #^ list running-row [#^ dict spec #^ str instance #^ int [placement 1] #^ int [attempts 1] #^ str [phase "running"] #^ str [name "w"]]
  "worker の heartbeat の状態の行(worker_policy.statuses → handlers.status-row と同じ欄)。"
  [{"name" name "phase" phase "runningRevision" (get spec "revision") "desiredRevision" (get spec "revision")
    "pid" 100 "attempts" attempts "detail" "" "instance" instance "specHash" (hash-of spec name) "placement" placement}])


(defn #^ dict ready-report [#^ dict spec #^ str instance #^ str [worker "atlas"] #^ int [placement 1] #^ int [attempt 1] #^ bool [ready True]
                            #^ str [name "w"]]
  "service の process の ReportReady の本文(shared/protocol/service_report.hy の service-report-of と同じ欄 — 子 process が受け取った世代を載せる)。"
  {"worker" worker "pid" 7 "revision" (get spec "revision") "instance" instance "attempt" (str attempt)
   "specHash" (hash-of spec name) "placement" placement "ready" ready "reason" "拍を終えた"})


(defn #^ str ready-of [#^ ClusterState state #^ int now #^ str [name "w"]]
  (get (get (call state "GET" (+ "/resources/Service/" name) :now now) 2) "status" "ready"))


(defn #^ ClusterState report [#^ ClusterState state #^ dict body #^ int now #^ str [name "w"] #^ str [kind "readiness"]]
  (setv #(s status reply) (call state "POST" (.format "/resources/Service/{}/{}" name kind) body :actor None :now now))
  (assert (= status 200) reply)
  s)


(deftest test-service-readiness-needs-a-recent-report-from-the-current-holder
  (setv spec (| SPEC {"readiness" {"windowSeconds" 10}}))
  (val reply-25 (call (ClusterState) "POST" "/resources/Service" {"name" "w" "spec" spec}))
  (var s (get reply-25 0))
  (:= s (beat s "atlas" 1000))
  (setv running (running-row spec "1-aaa"))
  (:= s (beat s "atlas" 2000 running))
  ;; process は動いているが、まだ「準備できた」の報告が無い(起動直後の猶予の後は NotReady)
  (:= s (beat s "atlas" 20000 running))
  (assert (= (ready-of s 20000) "NotReady"))
  (:= s (report s (ready-report spec "1-aaa") 20000))
  (:= s (beat s "atlas" 20000 running))
  (assert (= (ready-of s 25000) "Ready"))
  ;; window(10 秒)を過ぎて報告が途絶えると NotReady
  (:= s (beat s "atlas" 30500 running))
  (assert (= (ready-of s 30500) "NotReady"))
  ;; 別の版からの報告は数えない
  (:= s (report s (| (ready-report spec "1-aaa") {"revision" "old" "specHash" (hash-of (| spec {"revision" "old"}))}) 31000))
  (assert (= (ready-of s 31000) "NotReady"))
  ;; 世代を持たない報告(以前の版の process)は数えない
  (:= s (report s {"worker" "atlas" "pid" 7 "revision" "r1" "ready" True} 31500))
  (assert (= (ready-of s 31500) "NotReady")))


(deftest test-a-config-only-change-is-not-ready-until-the-new-process-reports
  ;; 2026-09-24 05:11:12 の実弾の形: 版は同じで設定だけを変えた。止めた前の process の Ready の報告(同じ worker・同じ版)が window に
  ;; 残っていても数えない。新しい process が最初に報告するまで NotReady。
  ;; 設定は Program の job では宣言の :environ(子の環境変数 — spec-hash に入る)で渡す(ADR-DOE-CLUSTER-001 改訂 1 の G)。
  (setv dry (| SPEC {"readiness" {"windowSeconds" 120} "run" SAMPLE-RUN "environ" {"APPLY" "0"}})
        wet (| dry {"environ" {"APPLY" "1"}}))
  (assert (!= (hash-of dry) (hash-of wet)))
  (val reply-26 (call (ClusterState :started-ms -1000000) "POST" "/resources/Service" {"name" "w" "spec" dry} :now 1000))
  (var s (get reply-26 0))
  (:= s (beat s "atlas" 1000))                                   ; 割り当てを受ける(まだ何も動いていない)
  (:= s (beat s "atlas" 1500 (running-row dry "1-dry")))
  (:= s (report s (ready-report dry "1-dry") 2000))
  (:= s (beat s "atlas" 3000 (running-row dry "1-dry")))
  (assert (= (ready-of s 3000) "Ready"))
  ;; 設定だけを変える(版つきの PUT)。worker はまだ前の process を動かしている → 前の設定の process なので NotReady
  (val reply-27 (call s "PUT" "/resources/Service/w" {"spec" wet "resourceVersion" (rv s "Service" "w")} :now 4000))
  (:= s (get reply-27 0))
  (val status (get reply-27 1))
  (val body (get reply-27 2))
  (assert (= status 200) body)
  (:= s (beat s "atlas" 4000 (running-row dry "1-dry")))
  (assert (= (ready-of s 4000) "NotReady"))
  (assert (in "前の宣言" (get (call s "GET" "/resources/Service/w" :now 4000) 2 "status" "readyReason")))
  ;; worker が前の process を止めている間も、前の process の(window の中の)報告は数えない
  (:= s (report s (ready-report dry "1-dry") 5000))
  (:= s (beat s "atlas" 5000 (running-row dry "1-dry" :phase "stopping")))
  (assert (= (ready-of s 5000) "NotReady"))
  ;; 新しい process が running になった(同じ worker・同じ版・試行 2)。まだ 1 拍も終えていない → NotReady(前の報告は 3 秒前で window の中)
  (:= s (beat s "atlas" 8000 (running-row wet "2-wet" :attempts 2)))
  (assert (= (ready-of s 8000) "NotReady"))
  (assert (in "2-wet" (get (call s "GET" "/resources/Service/w" :now 8000) 2 "status" "readyReason")))
  ;; 止め終えた前の process の報告が遅れて届いても数えない
  (:= s (report s (ready-report dry "1-dry") 9000))
  (assert (= (ready-of s 9000) "NotReady"))
  ;; 新しい process が最初の拍を終えて報告した → Ready
  (:= s (report s (ready-report wet "2-wet" :attempt 2) 28000))
  (:= s (beat s "atlas" 28000 (running-row wet "2-wet" :attempts 2)))
  (assert (= (ready-of s 28000) "Ready")))


(deftest test-a-report-that-arrives-before-the-heartbeat-counts-once-the-heartbeat-catches-up
  ;; 新しい process の最初の報告が、担い手の heartbeat(新しい process の世代を載せる)より先に届く順。報告は世代ごとに残すので、
  ;; heartbeat が追いついた時に数える。
  ;; 新しい宣言 = 同一性(呼んだ関数の引数)を変えた Program の job(spec の指紋が変わる)。
  (setv spec (| SPEC {"readiness" {"windowSeconds" 30}}) new (| spec {"run" (run (program-run "m:f" "--x"))}))
  (val reply-28 (call (ClusterState :started-ms -1000000) "POST" "/resources/Service" {"name" "w" "spec" spec} :now 1000))
  (var s (get reply-28 0))
  (:= s (beat s "atlas" 1000))
  (:= s (beat s "atlas" 1500 (running-row spec "1-a")))
  (:= s (report s (ready-report spec "1-a") 2000))
  (val reply-29 (call s "PUT" "/resources/Service/w" {"spec" new "resourceVersion" (rv s "Service" "w")} :now 3000))
  (:= s (get reply-29 0))
  (:= s (report s (ready-report new "2-b" :attempt 2) 6000))   ; 新しい process の報告が先
  (:= s (report s (ready-report spec "1-a") 6500))              ; 前の process の最後の報告が後から(残す列は世代ごと)
  (assert (= (ready-of s 6500) "NotReady"))                       ; heartbeat はまだ前の process を載せている
  (:= s (beat s "atlas" 7000 (running-row new "2-b" :attempts 2)))
  (assert (= (ready-of s 7000) "Ready")))


(deftest test-a-move-to-another-worker-is-not-ready-until-the-new-holder-process-reports
  ;; 担い手の worker が沈黙して別の worker へ移った。前の担い手の報告(window の中)も、戻ってきた前の担い手の遅れた報告も数えない。
  ;; 前の担い手へ戻った時も、前の process の報告は数えない(割り当ての世代と process の世代が違う)。
  (setv spec (| SPEC {"readiness" {"windowSeconds" 120}}))
  (val reply-30 (call (ClusterState :started-ms -1000000) "POST" "/resources/Service" {"name" "w" "spec" spec} :now 1000))
  (var s (get reply-30 0))
  (:= s (beat s "atlas" 1000))
  (:= s (beat s "zeus" 1000))
  (:= s (beat s "atlas" 1500 (running-row spec "1-atlas")))
  (assert (= (. (get s.placements "w") worker) "atlas") s.placements)
  (:= s (report s (ready-report spec "1-atlas") 2000))
  (:= s (beat s "atlas" 2000 (running-row spec "1-atlas")))
  (assert (= (ready-of s 2000) "Ready"))
  ;; atlas が沈黙(45 秒)→ zeus へ移る(割り当ての世代 2)
  (:= s (beat s "zeus" 48000))
  (assert (= #((. (get s.placements "w") worker) (. (get s.placements "w") generation)) #("zeus" 2)) s.placements)
  (assert (= (ready-of s 48000) "NotReady"))
  (:= s (beat s "zeus" 50000 (running-row spec "1-zeus" :placement 2)))
  (assert (= (ready-of s 50000) "NotReady"))                           ; zeus の process はまだ報告していない
  (:= s (report s (ready-report spec "1-atlas") 51000))              ; 凍っていた atlas の process の遅れた報告
  (assert (= (ready-of s 51000) "NotReady"))
  (:= s (report s (ready-report spec "1-zeus" :worker "zeus" :placement 2) 60000))
  (:= s (beat s "zeus" 60000 (running-row spec "1-zeus" :placement 2)))
  (assert (= (ready-of s 60000) "Ready"))
  ;; zeus が沈黙し atlas へ戻る(割り当ての世代 3)。atlas の worker は起動し直して試行の番号が 1 に戻った — 名は新しい
  (:= s (beat s "atlas" 106000))
  (assert (= #((. (get s.placements "w") worker) (. (get s.placements "w") generation)) #("atlas" 3)) s.placements)
  (:= s (beat s "atlas" 107000 (running-row spec "1-atlas-again" :placement 3)))
  (:= s (report s (ready-report spec "1-atlas") 107500))             ; 最初の atlas の process の古い報告(同じ試行の番号 1)
  (assert (= (ready-of s 107500) "NotReady"))
  (:= s (report s (ready-report spec "1-atlas-again" :placement 3) 110000))
  (assert (= (ready-of s 110000) "Ready")))


(deftest test-metrics-are-exported-only-for-the-current-process-with-production-names
  (setv spec (| SPEC {"readiness" {"windowSeconds" 30}}) new (| spec {"run" (run (program-run "m:f" "--apply"))}))
  (val reply-31 (call (ClusterState :started-ms -1000000) "POST" "/resources/Service" {"name" "w" "spec" spec} :now 1000))
  (var s (get reply-31 0))
  (:= s (beat s "atlas" 1000))
  (:= s (beat s "atlas" 1500 (running-row spec "1-a")))
  (defn #^ dict metrics-body [#^ str instance #^ dict spec #^ float writes #^ int [attempt 1]]
    (| (ready-report spec instance :attempt attempt)
       {"metrics" {"counters" {"app_condition_writes" writes} "gauges" {"queue_depth" 2.0}
                   "durations" {"reconcile_pass" {"sum" 1.5 "count" 3}}}}))
  (defn #^ str text-of [#^ ClusterState state #^ int now]
    (setv #(_ status body) (call state "GET" "/metrics" :now now))
    (assert (= status 200))
    (assert (isinstance body PlainText) body)
    body.text)
  (:= s (report s (metrics-body "1-a" spec 4.0) 2000 :kind "metrics"))
  (setv text (text-of s 2000))
  ;; 本番の Deployment と同じ名(counter は _total・duration は _seconds_sum / _count)・label は service と worker
  (assert (in "# TYPE app_condition_writes_total counter" text) text)
  (assert (in "app_condition_writes_total{service=\"w\",worker=\"atlas\"} 4.0" text) text)
  (assert (in "queue_depth{service=\"w\",worker=\"atlas\"} 2.0" text) text)
  (assert (in "reconcile_pass_seconds_count{service=\"w\",worker=\"atlas\"} 3" text) text)
  ;; 報告は資源の状態を変えない(版は進まない)
  (assert (= (rv s "Service" "w") (rv (report s (metrics-body "1-a" spec 5.0) 2500 :kind "metrics") "Service" "w")))
  ;; 設定を変えて新しい process が running になった後は、前の process の計器を出さない(新しい process の報告を待つ)
  (val reply-32 (call s "PUT" "/resources/Service/w" {"spec" new "resourceVersion" (rv s "Service" "w")} :now 3000))
  (:= s (get reply-32 0))
  (:= s (beat s "atlas" 4000 (running-row new "2-b" :attempts 2)))
  (:= s (report s (metrics-body "1-a" spec 9.0) 4500 :kind "metrics"))
  (assert (not-in "app_condition_writes_total" (text-of s 4500)))
  (:= s (report s (metrics-body "2-b" new 1.0 :attempt 2) 5000 :kind "metrics"))
  (assert (in "app_condition_writes_total{service=\"w\",worker=\"atlas\"} 1.0" (text-of s 5000)))
  ;; 古い報告(拍が止まった process)は出さない
  (:= s (beat s "atlas" 200000 (running-row new "2-b" :attempts 2)))
  (assert (not-in "app_condition_writes_total" (text-of s 200000)))
  ;; 形の正しくない報告は断る
  (setv #(_ status _) (call s "POST" "/resources/Service/w/metrics"
                            (| (ready-report new "2-b" :attempt 2) {"metrics" {"counters" {"bad name" 1.0}}}) :actor None :now 5000))
  (assert (= status 400)))


(deftest test-a-silent-carrier-is-unknown-until-its-jobs-move-away
  ;; 2026-09-25: 担い手の heartbeat が途絶えただけ(移し替えの 45 秒の内)は「分からない」— Rollout は失敗と数えない。
  ;; 移し替えの期限を過ぎたら NotReady(job は他へ移る)。
  (setv spec (| SPEC {"readiness" {"windowSeconds" 10}}))
  (val reply-33 (call (ClusterState) "POST" "/resources/Service" {"name" "w" "spec" spec}))
  (var s (get reply-33 0))
  (:= s (beat s "atlas" 19000))
  (setv running (running-row spec "1-aaa"))
  (:= s (beat s "atlas" 20000 running))
  (:= s (report s (ready-report spec "1-aaa") 20000))
  (assert (= (ready-of s 21000) "Ready"))
  (assert (= (ready-of s 40000) "Unknown"))                ; 20 秒 heartbeat が無い
  (assert (= (ready-of s (+ 20000 (. T reassign-after-ms) 1)) "NotReady")))


;; --- 準備と計器の報告は観測の表に在る(#2756 J2)— 外の形は前と同じ・版も保存も動かさない ------------------------------------
;; 報告は ClusterState.observations の readiness・metrics の表(記録 ReadinessReport・MetricsReport)に在る。GET /resources/Service の
;; status.lastReadiness と GET /metrics の本文は前と同じ形で綴り、保存の差分(durable_kv.durable-delta)には何も出ない。

(import json)
(import doeff_cluster.coordinator.protocol.durable_kv [durable-delta])

(defk last-readiness-json [state now]
  {:pre [(: state ClusterState) (: now int)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "GET /resources/Service/w の status.lastReadiness を、欄の順と値の型を保った JSON の文字列にするため(形を byte の単位で比べる)。"
  (val answered (call state "GET" "/resources/Service/w" :now now))
  (assert (= (get answered 1) 200) answered)
  (json.dumps (get answered 2 "status" "lastReadiness") :ensure-ascii False))


(deftest test-the-last-readiness-json-keeps-the-report-shape
  ;; 欄の順は worker・pid・revision・instance・attempt・specHash・placement・at・ready・reason・role(#2756 の前に状態の readiness の行を
  ;; そのまま写していた形)。attempt は送られた型のまま(文字列なら文字列・整数なら整数)・送らなかった欄は null・報告が無ければ null。
  (val spec (| SPEC {"readiness" {"windowSeconds" 10}}))
  (var s (get (call (ClusterState) "POST" "/resources/Service" {"name" "w" "spec" spec}) 0))
  (<- unreported str (last-readiness-json s 1000))
  (assert (= unreported "null") unreported)
  (:= s (report s (ready-report spec "1-aaa" :attempt 2) 2000))
  (<- sent-string str (last-readiness-json s 2000))
  (assert (= sent-string
             (+ "{\"worker\": \"atlas\", \"pid\": 7, \"revision\": \"r1\", \"instance\": \"1-aaa\", \"attempt\": \"2\", "
                "\"specHash\": \"" (hash-of spec) "\", \"placement\": 1, \"at\": 2000, \"ready\": true, \"reason\": \"拍を終えた\", "
                "\"role\": \"active\"}"))
          sent-string)
  ;; 整数の attempt・世代を持たない旧い process の報告(欠けた欄は null)・待機の役。
  (:= s (report s {"worker" "atlas" "revision" "r1" "ready" False "attempt" 3 "role" "standby"} 3000))
  (<- sent-int str (last-readiness-json s 3000))
  (assert (= sent-int
             (+ "{\"worker\": \"atlas\", \"pid\": null, \"revision\": \"r1\", \"instance\": null, \"attempt\": 3, "
                "\"specHash\": null, \"placement\": null, \"at\": 3000, \"ready\": false, \"reason\": \"\", \"role\": \"standby\"}"))
          sent-int))


;; 下の検の状態の GET /metrics の本文(#2756 の前の coordinator — 計器の報告を写像で持っていた頃 — で取った物)。
(val METRICS-TEXT
  (.join "" (gfor line #("# TYPE aa_reads_total counter"
                         "aa_reads_total{service=\"w\",worker=\"atlas\"} 2.5"
                         "# TYPE doeff_worker_board_bytes gauge"
                         "doeff_worker_board_bytes 0.0"
                         "# TYPE doeff_worker_board_expiring_rows gauge"
                         "doeff_worker_board_expiring_rows 0.0"
                         "# TYPE doeff_worker_board_max_bytes gauge"
                         "doeff_worker_board_max_bytes 67108864.0"
                         "# TYPE doeff_worker_board_max_rows gauge"
                         "doeff_worker_board_max_rows 20000.0"
                         "# TYPE doeff_worker_board_rows gauge"
                         "doeff_worker_board_rows 0.0"
                         "# TYPE doeff_worker_env_cold_start_total counter"
                         "doeff_worker_env_cold_start_total 0"
                         "# TYPE doeff_worker_open_tasks gauge"
                         "doeff_worker_open_tasks 0.0"
                         "# TYPE doeff_worker_service_last_metrics_age_seconds gauge"
                         "doeff_worker_service_last_metrics_age_seconds{service=\"w\"} 0.5"
                         "# TYPE doeff_worker_service_metrics_age_seconds gauge"
                         "doeff_worker_service_metrics_age_seconds{service=\"w\",worker=\"atlas\"} 0.5"
                         "# TYPE doeff_worker_service_ready gauge"
                         "doeff_worker_service_ready{service=\"w\"} 1.0"
                         "# TYPE doeff_worker_service_ready_replicas gauge"
                         "doeff_worker_service_ready_replicas{service=\"w\"} 1.0"
                         "# TYPE doeff_worker_service_spec_replicas gauge"
                         "doeff_worker_service_spec_replicas{service=\"w\"} 1.0"
                         "# TYPE doeff_worker_service_standby gauge"
                         "doeff_worker_service_standby{service=\"w\"} 0.0"
                         "# TYPE doeff_worker_service_unplaced gauge"
                         "doeff_worker_service_unplaced{service=\"w\"} 0.0"
                         "# TYPE doeff_worker_worker_heartbeat_age_seconds gauge"
                         "doeff_worker_worker_heartbeat_age_seconds{worker=\"atlas\"} 1.5"
                         "# TYPE queue_depth gauge"
                         "queue_depth{service=\"w\",worker=\"atlas\"} 2.0"
                         "# TYPE reconcile_pass_seconds summary"
                         "reconcile_pass_seconds_sum{service=\"w\",worker=\"atlas\"} 1.5"
                         "reconcile_pass_seconds_count{service=\"w\",worker=\"atlas\"} 3"
                         "# TYPE zz_writes_total counter"
                         "zz_writes_total{service=\"w\",worker=\"atlas\"} 4.0")
                  (+ line "\n"))))


(deftest test-the-metrics-text-keeps-its-lines
  ;; GET /metrics の本文は #2756 の前と 1 byte も違わない: 族の名の順・label の順・値の綴り(counter は _total と float・duration は
  ;; _seconds_sum の float と _seconds_count の int・gauge は float)。計器の名は送った順に依らず名の順に並ぶ。
  (val spec (| SPEC {"readiness" {"windowSeconds" 30}}))
  (var s (get (call (ClusterState :started-ms -1000000) "POST" "/resources/Service" {"name" "w" "spec" spec} :now 1000) 0))
  (:= s (beat s "atlas" 1000))
  (:= s (beat s "atlas" 1500 (running-row spec "1-a")))
  (:= s (report s (ready-report spec "1-a") 2000))
  (:= s (report s (| (ready-report spec "1-a")
                     {"metrics" {"counters" {"zz_writes" 4 "aa_reads" 2.5} "gauges" {"queue_depth" 2}
                                 "durations" {"reconcile_pass" {"sum" 1.5 "count" 3}}}})
                2500 :kind "metrics"))
  (val answered (call s "GET" "/metrics" :now 3000))
  (assert (= (get answered 1) 200) answered)
  (assert (= (. (get answered 2) text) METRICS-TEXT) (. (get answered 2) text)))


(deftest test-a-report-write-moves-neither-versions-nor-the-store-unless-the-verdict-moves
  ;; 失敗の形: 報告を書いただけで資源の版・出来事の記録・保存の行が動く。計器の報告と、Service の判定(status.ready)を変えない準備の
  ;; 報告は、観測の表の行を替えるだけ。
  (val spec (| SPEC {"readiness" {"windowSeconds" 30}}))
  (var s (get (call (ClusterState :started-ms -1000000) "POST" "/resources/Service" {"name" "w" "spec" spec} :now 1000) 0))
  (:= s (beat s "atlas" 1000))
  (:= s (beat s "atlas" 1500 (running-row spec "1-a")))
  (:= s (report s (ready-report spec "1-a") 2000))
  (assert (= (ready-of s 2000) "Ready"))
  ;; 同じ process の 2 度目の Ready(判定は Ready のまま)— 表の行は新しい報告に替わるが、版も記録も保存も動かない。
  (val again (report s (ready-report spec "1-a") 3000))
  (assert (= (. (get (.row again.observations.readiness "w") -1) origin at) 3000) (.row again.observations.readiness "w"))
  (assert (= #(again.revision again.audit-seq again.audit) #(s.revision s.audit-seq s.audit)))
  (<- again-delta dict (durable-delta s again))
  (assert (= again-delta {}) again-delta)
  ;; 計器の報告も同じ(計器は資源の状態を変えない)。
  (val metered (report again (| (ready-report spec "1-a") {"metrics" {"counters" {"writes" 1.0}}}) 3500 :kind "metrics"))
  (assert (= (.size metered.observations.metrics) 1))
  (assert (= #(metered.revision metered.audit-seq metered.audit) #(s.revision s.audit-seq s.audit)))
  (<- metered-delta dict (durable-delta again metered))
  (assert (= metered-delta {}) metered-delta)
  ;; 反例の対照: 判定を変える準備の報告(Ready → NotReady)は、同じ比べが Service の版と出来事を進める — 版の比べ(dirty-keys)は観測の表
  ;; readiness を読む(読まなければ status.ready の切り替わりを取りこぼす)。保存の差分に出るのは版の番号・版の記録・出来事だけ。
  (val refused (report metered (ready-report spec "1-a" :ready False) 4000))
  (assert (= (ready-of refused 4000) "NotReady"))
  (assert (> (rv refused "Service" "w") (rv s "Service" "w")))
  (assert (= (. (get refused.audit -1) changes) {"status.ready" ["Ready" "NotReady"]}) (get refused.audit -1))
  (<- stored dict (durable-delta metered refused))
  (assert (in "counter" stored) stored)
  (assert (all (gfor key stored (or (= key "counter") (.startswith key "meta/") (.startswith key "audit/")))) stored))
