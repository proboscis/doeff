;; coordinator の資源の口: 資源ごとの compare-and-set・送り手と出来事の記録・所有者だけが消せる・旧い PUT /jobs の写し・
;; readiness・盤の行ごとの版。
(require doeff-hy.macros [deftest])
(import doeff_cluster.cluster_model [ClusterTiming ClusterState Request PlainText])
(import doeff_cluster.cluster_policy [state-to-json state-from-json board-changes job-from-json])
(import doeff_cluster.worker_model [spec-hash])
(import doeff_cluster.api_policy [respond])
(import doeff_cluster.resource_policy [LEGACY-OWNER adopt-legacy])

(setv T (ClusterTiming))
(setv V {"python" "3.14.0"})
(setv SPEC {"revision" "r1" "requires" {} "entry" "m" "args" []})

(defn req [method path [body None] [query None] [actor "c-me"]]
  (Request method path (or query {}) body :actor actor :peer "10.0.0.9"))

(defn call [state method path [body None] [query None] [actor "c-me"] [now 1000]]
  (respond state (req method path body query actor) now T))

(defn beat [state name now [statuses None]]
  (get (call state "POST" "/heartbeat" {"name" name "labels" {} "capacity" 10 "versions" V "statuses" (or statuses [])}
             :actor None :now now) 0))

(defn rv [state kind name]
  (get (. state meta) (+ kind "/" name) "resourceVersion"))


(deftest test-service-writes-are-compare-and-set-per-resource
  (setv #(s status _) (call (ClusterState) "POST" "/resources/Service" {"name" "a" "spec" SPEC}))
  (assert (= status 201))
  (setv v1 (rv s "Service" "a"))
  ;; 版の無い書きは断る(読んだ版を付けて書く)
  (setv #(_ status body) (call s "PUT" "/resources/Service/a" {"spec" (| SPEC {"revision" "r2"})}))
  (assert (= status 400) body)
  ;; 正しい版なら通り、generation が進む
  (setv #(s2 status body) (call s "PUT" "/resources/Service/a" {"spec" (| SPEC {"revision" "r2"}) "resourceVersion" v1}))
  (assert (= status 200) body)
  (assert (= #((get body "generation") (get body "spec" "revision")) #(2 "r2")))
  (assert (> (rv s2 "Service" "a") v1))
  ;; 古い版(同じ v1 を読んだ別の作業係)の書きは 409。いまの版を返す。
  (setv #(s3 status body) (call s2 "PUT" "/resources/Service/a" {"spec" (| SPEC {"revision" "r3"}) "resourceVersion" v1}
                                :actor "c-other"))
  (assert (= status 409) body)
  (assert (= (get body "current") (rv s2 "Service" "a")))
  (assert (is s3 s2))
  ;; もう在る名前は作れない
  (assert (= (get (call s2 "POST" "/resources/Service" {"name" "a" "spec" SPEC}) 1) 409)))


(deftest test-every-write-records-the-actor-and-the-versions
  (setv #(s _ _) (call (ClusterState) "POST" "/resources/Service" {"name" "a" "spec" SPEC} :actor "c-01ABC"))
  (setv #(s _ _) (call s "PUT" "/resources/Service/a" {"spec" (| SPEC {"replicas" 0}) "resourceVersion" (rv s "Service" "a")}
                       :actor "rollout-script"))
  (setv #(_ _ body) (call s "GET" "/events" None {"kind" "Service" "name" "a"}))
  (setv events (lfor e (get body "events") :if (in (get e "verb") #("create" "update")) e))
  (assert (= (lfor e events #((get e "verb") (get e "actor"))) [#("create" "c-01ABC") #("update" "rollout-script")]))
  (setv update (get events 1))
  (assert (= (get update "fromVersion") (get events 0 "toVersion")))
  (assert (= (get update "changes" "spec.replicas") [1 0]))
  ;; 送り手の無い資源の書きは断る
  (setv #(_ status body) (call s "POST" "/resources/Service" {"name" "b" "spec" SPEC} :actor None))
  (assert (= status 400) body)
  (assert (in "X-Actor" (get body "error")))
  ;; 盤と task は旧い client でも通し、送り元の番地で記録する
  (setv #(s status _) (call s "POST" "/tasks" {"env" "m:e" "blob" "B" "versions" V "revision" "r" "requires" {}} :actor None))
  (assert (= status 200))
  (assert (= (get (get s.audit -1) "actor") "coordinator"))   ; 置き先の決め(調停)
  (assert (in "anonymous@10.0.0.9" (lfor e s.audit (get e "actor")))))


(deftest test-only-the-owner-or-an-explicit-force-delete-removes-a-declaration
  (setv #(s _ _) (call (ClusterState) "POST" "/resources/Service" {"name" "shadow" "spec" SPEC} :actor "c-shadow-owner"))
  (setv #(s2 status body) (call s "DELETE" "/resources/Service/shadow" :actor "c-someone-else"))
  (assert (= status 403) body)
  (assert (is s2 s))
  (setv #(s3 status _) (call s "DELETE" "/resources/Service/shadow" None {"force" "true"} :actor "c-someone-else"))
  (assert (= status 200))
  (assert (= (len s3.jobs) 0))
  (assert (= (get (get s3.audit -1) "verb") "delete"))
  (setv #(s4 status _) (call s "DELETE" "/resources/Service/shadow" :actor "c-shadow-owner"))
  (assert (= status 200)))


(deftest test-legacy-put-jobs-never-deletes-and-refuses-stale-rows
  ;; 今夜の実弾: 一覧を丸ごと置き換える PUT /jobs が他の作業係の行を黙って消しうる。移行の間の写し:
  ;; 一覧に無い行は消さない・版の無い行で既存を変えない・古い版の行は 409(1 行でも競合すれば何も書かない)。
  (setv #(s _ _) (call (ClusterState) "POST" "/resources/Service" {"name" "shadow-a" "spec" SPEC} :actor "c-shadow"))
  (setv #(s _ _) (call s "POST" "/resources/Service" {"name" "shadow-b" "spec" SPEC} :actor "c-coord"))
  ;; shadow-b だけの一覧で PUT(shadow-a の行が無い)
  (setv #(_ _ view) (call s "GET" "/state"))
  (setv row (next (gfor j (get view "jobs") :if (= (get j "name") "shadow-b") j)))
  (setv #(s2 status body) (call s "PUT" "/jobs" {"jobs" [(| row {"revision" "r9"})]} :actor "c-coord"))
  (assert (= status 200) body)
  (assert (= (sorted (lfor j s2.jobs j.spec.name)) ["shadow-a" "shadow-b"]))
  (assert (= (get body "untouched") ["shadow-a"]))
  ;; 同じ古い行(版が古い)をもう一度送ると 409 で、何も変えない
  (setv #(s3 status body) (call s2 "PUT" "/jobs" {"jobs" [(| row {"revision" "r10"})]} :actor "c-coord"))
  (assert (= status 409) body)
  (assert (is s3 s2))
  ;; 版の無い行で既存を変えようとすると 409
  (setv #(_ status _) (call s2 "PUT" "/jobs" {"jobs" [{"name" "shadow-a" "revision" "rX" "entry" "m" "args" []}]}
                            :actor "c-coord"))
  (assert (= status 409))
  ;; 送り手の無い PUT /jobs は断る
  (assert (= (get (call s2 "PUT" "/jobs" {"jobs" []} :actor None) 1) 400)))


(deftest test-legacy-state-file-is-adopted-with-versions-and-a-legacy-owner
  (setv legacy {"jobs" [{"name" "turn-runner" "revision" "r" "requires" {} "pin" None "entry" "m" "args" []}]
                "assignments" {} "workers" [] "tasks" [] "nextTask" 1 "board" {"k" 1}}) ; 改名の前の file の形
  (setv s (adopt-legacy (state-from-json legacy 1000) 1000 T))
  (assert (= (. (get s.jobs 0) owner) LEGACY-OWNER))
  (assert (is-not (rv s "Service" "turn-runner") None))
  (assert (= (get (get s.audit -1) "actor") "migration"))
  (assert (= s.board-versions {"k" 1}))
  ;; 新しい形で書き直した物に盤は入らない(盤は行ごとの file)
  (assert (not-in "board" (state-to-json s)))
  ;; 誰でも 1 度だけ所有者を引き取れる。引き取った後は他の送り手が変えられない
  (setv #(s2 status _) (call s "PUT" "/resources/Service/turn-runner"
                             {"spec" {"revision" "r" "entry" "m" "args" [] "owner" "c-lab"} "resourceVersion" (rv s "Service" "turn-runner")}
                             :actor "c-lab"))
  (assert (= status 200))
  (setv #(_ status _) (call s2 "PUT" "/resources/Service/turn-runner"
                            {"spec" {"revision" "r" "entry" "m" "args" [] "owner" "c-thief"} "resourceVersion" (rv s2 "Service" "turn-runner")}
                            :actor "c-thief"))
  (assert (= status 403)))


(deftest test-board-rows-have-their-own-versions-and-only-written-rows-are-saved
  (setv big (dfor i (range 16) (.format "shadow-a/rows/{:02x}" i) (* "x" 1000)))
  (setv s (ClusterState :board big :board-versions (dfor k big k 1)))
  (setv #(s2 status body) (call s "PUT" "/board/writer-a/cycle" {"value" {"n" 1}} :actor None))
  (assert (= #(status (get body "resourceVersion")) #(200 1)))
  ;; 書き直しが要るのは書いた行だけ(大きな shadow の 16 区画は書き直さない)
  (assert (= (board-changes s s2) ["writer-a/cycle"]))
  ;; 行の版で compare-and-set
  (setv #(_ status body) (call s2 "PUT" "/board/writer-a/cycle" {"value" {"n" 2} "expectVersion" 0}))
  (assert (= #(status (get body "resourceVersion")) #(409 1)))
  (setv #(s3 status body) (call s2 "PUT" "/board/writer-a/cycle" {"value" {"n" 2} "expectVersion" 1}))
  (assert (= #(status (get body "resourceVersion")) #(200 2)))
  (setv #(_ _ rows) (call s3 "GET" "/board" None {"prefix" "writer-a/" "withVersions" "1"}))
  (assert (= rows {"writer-a/cycle" {"value" {"n" 2} "resourceVersion" 2}})))


;; --- readiness: 今の宣言で今動いている process の報告だけを数える -----------------------------------------------

(defn hash-of [spec [name "w"]]
  "coordinator が今の宣言から計算する spec の指紋(worker が process を起こした時に渡すのと同じ関数)。"
  (spec-hash (. (job-from-json (| spec {"name" name})) spec)))


(defn running-row [spec instance [placement 1] [attempts 1] [phase "running"] [name "w"]]
  "worker の heartbeat の状態の行(worker_policy.statuses → handlers.status-row と同じ欄)。"
  [{"name" name "phase" phase "runningRevision" (get spec "revision") "desiredRevision" (get spec "revision")
    "pid" 100 "attempts" attempts "detail" "" "instance" instance "specHash" (hash-of spec name) "placement" placement}])


(defn ready-report [spec instance [worker "atlas"] [placement 1] [attempt 1] [ready True] [name "w"]]
  "service の process の ReportReady の本文(report_client.hy と同じ欄 — 子 process が受け取った世代を載せる)。"
  {"worker" worker "pid" 7 "revision" (get spec "revision") "instance" instance "attempt" (str attempt)
   "specHash" (hash-of spec name) "placement" placement "ready" ready "reason" "拍を終えた"})


(defn ready-of [state now [name "w"]]
  (get (get (call state "GET" (+ "/resources/Service/" name) :now now) 2) "status" "ready"))


(defn report [state body now [name "w"] [kind "readiness"]]
  (setv #(s status reply) (call state "POST" (.format "/resources/Service/{}/{}" name kind) body :actor None :now now))
  (assert (= status 200) reply)
  s)


(deftest test-service-readiness-needs-a-recent-report-from-the-current-holder
  (setv spec (| SPEC {"readiness" {"windowSeconds" 10}}))
  (setv #(s _ _) (call (ClusterState) "POST" "/resources/Service" {"name" "w" "spec" spec}))
  (setv s (beat s "atlas" 1000))
  (setv running (running-row spec "1-aaa"))
  (setv s (beat s "atlas" 2000 running))
  ;; process は動いているが、まだ「準備できた」の報告が無い(起動直後の猶予の後は NotReady)
  (setv s (beat s "atlas" 20000 running))
  (assert (= (ready-of s 20000) "NotReady"))
  (setv s (report s (ready-report spec "1-aaa") 20000))
  (setv s (beat s "atlas" 20000 running))
  (assert (= (ready-of s 25000) "Ready"))
  ;; window(10 秒)を過ぎて報告が途絶えると NotReady
  (setv s (beat s "atlas" 30500 running))
  (assert (= (ready-of s 30500) "NotReady"))
  ;; 別の版からの報告は数えない
  (setv s (report s (| (ready-report spec "1-aaa") {"revision" "old" "specHash" (hash-of (| spec {"revision" "old"}))}) 31000))
  (assert (= (ready-of s 31000) "NotReady"))
  ;; 世代を持たない報告(以前の版の process)は数えない
  (setv s (report s {"worker" "atlas" "pid" 7 "revision" "r1" "ready" True} 31500))
  (assert (= (ready-of s 31500) "NotReady")))


(deftest test-a-config-only-change-is-not-ready-until-the-new-process-reports
  ;; 2026-09-24 05:11:12 の実弾の形: 版は同じで設定だけを変えた。止めた前の process の Ready の報告(同じ worker・同じ版)が window に
  ;; 残っていても数えない。新しい process が最初に報告するまで NotReady。
  (setv dry (| SPEC {"readiness" {"windowSeconds" 120} "run" {"kind" "service" "factory" "m:f" "env" "m:e" "config" {"apply" False}}})
        wet (| dry {"run" (| (get dry "run") {"config" {"apply" True}})}))
  (assert (!= (hash-of dry) (hash-of wet)))
  (setv #(s _ _) (call (ClusterState :started-ms -1000000) "POST" "/resources/Service" {"name" "w" "spec" dry} :now 1000))
  (setv s (beat s "atlas" 1000))                                   ; 割り当てを受ける(まだ何も動いていない)
  (setv s (beat s "atlas" 1500 (running-row dry "1-dry")))
  (setv s (report s (ready-report dry "1-dry") 2000))
  (setv s (beat s "atlas" 3000 (running-row dry "1-dry")))
  (assert (= (ready-of s 3000) "Ready"))
  ;; 設定だけを変える(版つきの PUT)。worker はまだ前の process を動かしている → 前の設定の process なので NotReady
  (setv #(s status body) (call s "PUT" "/resources/Service/w" {"spec" wet "resourceVersion" (rv s "Service" "w")} :now 4000))
  (assert (= status 200) body)
  (setv s (beat s "atlas" 4000 (running-row dry "1-dry")))
  (assert (= (ready-of s 4000) "NotReady"))
  (assert (in "前の宣言" (get (call s "GET" "/resources/Service/w" :now 4000) 2 "status" "readyReason")))
  ;; worker が前の process を止めている間も、前の process の(window の中の)報告は数えない
  (setv s (report s (ready-report dry "1-dry") 5000))
  (setv s (beat s "atlas" 5000 (running-row dry "1-dry" :phase "stopping")))
  (assert (= (ready-of s 5000) "NotReady"))
  ;; 新しい process が running になった(同じ worker・同じ版・試行 2)。まだ 1 拍も終えていない → NotReady(前の報告は 3 秒前で window の中)
  (setv s (beat s "atlas" 8000 (running-row wet "2-wet" :attempts 2)))
  (assert (= (ready-of s 8000) "NotReady"))
  (assert (in "2-wet" (get (call s "GET" "/resources/Service/w" :now 8000) 2 "status" "readyReason")))
  ;; 止め終えた前の process の報告が遅れて届いても数えない
  (setv s (report s (ready-report dry "1-dry") 9000))
  (assert (= (ready-of s 9000) "NotReady"))
  ;; 新しい process が最初の拍を終えて報告した → Ready
  (setv s (report s (ready-report wet "2-wet" :attempt 2) 28000))
  (setv s (beat s "atlas" 28000 (running-row wet "2-wet" :attempts 2)))
  (assert (= (ready-of s 28000) "Ready")))


(deftest test-a-report-that-arrives-before-the-heartbeat-counts-once-the-heartbeat-catches-up
  ;; 新しい process の最初の報告が、担い手の heartbeat(新しい process の世代を載せる)より先に届く順。報告は世代ごとに残すので、
  ;; heartbeat が追いついた時に数える。
  (setv spec (| SPEC {"readiness" {"windowSeconds" 30}}) new (| spec {"args" ["--x"]}))
  (setv #(s _ _) (call (ClusterState :started-ms -1000000) "POST" "/resources/Service" {"name" "w" "spec" spec} :now 1000))
  (setv s (beat s "atlas" 1000))
  (setv s (beat s "atlas" 1500 (running-row spec "1-a")))
  (setv s (report s (ready-report spec "1-a") 2000))
  (setv #(s _ _) (call s "PUT" "/resources/Service/w" {"spec" new "resourceVersion" (rv s "Service" "w")} :now 3000))
  (setv s (report s (ready-report new "2-b" :attempt 2) 6000))   ; 新しい process の報告が先
  (setv s (report s (ready-report spec "1-a") 6500))              ; 前の process の最後の報告が後から(残す列は世代ごと)
  (assert (= (ready-of s 6500) "NotReady"))                       ; heartbeat はまだ前の process を載せている
  (setv s (beat s "atlas" 7000 (running-row new "2-b" :attempts 2)))
  (assert (= (ready-of s 7000) "Ready")))


(deftest test-a-move-to-another-worker-is-not-ready-until-the-new-holder-process-reports
  ;; 担い手の worker が沈黙して別の worker へ移った。前の担い手の報告(window の中)も、戻ってきた前の担い手の遅れた報告も数えない。
  ;; 前の担い手へ戻った時も、前の process の報告は数えない(割り当ての世代と process の世代が違う)。
  (setv spec (| SPEC {"readiness" {"windowSeconds" 120}}))
  (setv #(s _ _) (call (ClusterState :started-ms -1000000) "POST" "/resources/Service" {"name" "w" "spec" spec} :now 1000))
  (setv s (beat s "atlas" 1000))
  (setv s (beat s "zeus" 1000))
  (setv s (beat s "atlas" 1500 (running-row spec "1-atlas")))
  (assert (= (. (get s.placements "w") worker) "atlas") s.placements)
  (setv s (report s (ready-report spec "1-atlas") 2000))
  (setv s (beat s "atlas" 2000 (running-row spec "1-atlas")))
  (assert (= (ready-of s 2000) "Ready"))
  ;; atlas が沈黙(45 秒)→ zeus へ移る(割り当ての世代 2)
  (setv s (beat s "zeus" 48000))
  (assert (= #((. (get s.placements "w") worker) (. (get s.placements "w") generation)) #("zeus" 2)) s.placements)
  (assert (= (ready-of s 48000) "NotReady"))
  (setv s (beat s "zeus" 50000 (running-row spec "1-zeus" :placement 2)))
  (assert (= (ready-of s 50000) "NotReady"))                           ; zeus の process はまだ報告していない
  (setv s (report s (ready-report spec "1-atlas") 51000))              ; 凍っていた atlas の process の遅れた報告
  (assert (= (ready-of s 51000) "NotReady"))
  (setv s (report s (ready-report spec "1-zeus" :worker "zeus" :placement 2) 60000))
  (setv s (beat s "zeus" 60000 (running-row spec "1-zeus" :placement 2)))
  (assert (= (ready-of s 60000) "Ready"))
  ;; zeus が沈黙し atlas へ戻る(割り当ての世代 3)。atlas の worker は起動し直して試行の番号が 1 に戻った — 名は新しい
  (setv s (beat s "atlas" 106000))
  (assert (= #((. (get s.placements "w") worker) (. (get s.placements "w") generation)) #("atlas" 3)) s.placements)
  (setv s (beat s "atlas" 107000 (running-row spec "1-atlas-again" :placement 3)))
  (setv s (report s (ready-report spec "1-atlas") 107500))             ; 最初の atlas の process の古い報告(同じ試行の番号 1)
  (assert (= (ready-of s 107500) "NotReady"))
  (setv s (report s (ready-report spec "1-atlas-again" :placement 3) 110000))
  (assert (= (ready-of s 110000) "Ready")))


(deftest test-metrics-are-exported-only-for-the-current-process-with-production-names
  (setv spec (| SPEC {"readiness" {"windowSeconds" 30}}) new (| spec {"args" ["--apply"]}))
  (setv #(s _ _) (call (ClusterState :started-ms -1000000) "POST" "/resources/Service" {"name" "w" "spec" spec} :now 1000))
  (setv s (beat s "atlas" 1000))
  (setv s (beat s "atlas" 1500 (running-row spec "1-a")))
  (defn metrics-body [instance spec writes [attempt 1]]
    (| (ready-report spec instance :attempt attempt)
       {"metrics" {"counters" {"app_condition_writes" writes} "gauges" {"queue_depth" 2.0}
                   "durations" {"reconcile_pass" {"sum" 1.5 "count" 3}}}}))
  (defn text-of [state now]
    (setv #(_ status body) (call state "GET" "/metrics" :now now))
    (assert (= status 200))
    (assert (isinstance body PlainText) body)
    body.text)
  (setv s (report s (metrics-body "1-a" spec 4.0) 2000 :kind "metrics"))
  (setv text (text-of s 2000))
  ;; 本番の Deployment と同じ名(counter は _total・duration は _seconds_sum / _count)・label は service と worker
  (assert (in "# TYPE app_condition_writes_total counter" text) text)
  (assert (in "app_condition_writes_total{service=\"w\",worker=\"atlas\"} 4.0" text) text)
  (assert (in "queue_depth{service=\"w\",worker=\"atlas\"} 2.0" text) text)
  (assert (in "reconcile_pass_seconds_count{service=\"w\",worker=\"atlas\"} 3" text) text)
  ;; 報告は資源の状態を変えない(版は進まない)
  (assert (= (rv s "Service" "w") (rv (report s (metrics-body "1-a" spec 5.0) 2500 :kind "metrics") "Service" "w")))
  ;; 設定を変えて新しい process が running になった後は、前の process の計器を出さない(新しい process の報告を待つ)
  (setv #(s _ _) (call s "PUT" "/resources/Service/w" {"spec" new "resourceVersion" (rv s "Service" "w")} :now 3000))
  (setv s (beat s "atlas" 4000 (running-row new "2-b" :attempts 2)))
  (setv s (report s (metrics-body "1-a" spec 9.0) 4500 :kind "metrics"))
  (assert (not-in "app_condition_writes_total" (text-of s 4500)))
  (setv s (report s (metrics-body "2-b" new 1.0 :attempt 2) 5000 :kind "metrics"))
  (assert (in "app_condition_writes_total{service=\"w\",worker=\"atlas\"} 1.0" (text-of s 5000)))
  ;; 古い報告(拍が止まった process)は出さない
  (setv s (beat s "atlas" 200000 (running-row new "2-b" :attempts 2)))
  (assert (not-in "app_condition_writes_total" (text-of s 200000)))
  ;; 形の正しくない報告は断る
  (setv #(_ status _) (call s "POST" "/resources/Service/w/metrics"
                            (| (ready-report new "2-b" :attempt 2) {"metrics" {"counters" {"bad name" 1.0}}}) :actor None :now 5000))
  (assert (= status 400)))


(deftest test-a-silent-carrier-is-unknown-until-its-jobs-move-away
  ;; 2026-09-25: 担い手の heartbeat が途絶えただけ(移し替えの 45 秒の内)は「分からない」— Rollout は失敗と数えない。
  ;; 移し替えの期限を過ぎたら NotReady(job は他へ移る)。
  (setv spec (| SPEC {"readiness" {"windowSeconds" 10}}))
  (setv #(s _ _) (call (ClusterState) "POST" "/resources/Service" {"name" "w" "spec" spec}))
  (setv s (beat s "atlas" 19000))
  (setv running (running-row spec "1-aaa"))
  (setv s (beat s "atlas" 20000 running))
  (setv s (report s (ready-report spec "1-aaa") 20000))
  (assert (= (ready-of s 21000) "Ready"))
  (assert (= (ready-of s 40000) "Unknown"))                ; 20 秒 heartbeat が無い
  (assert (= (ready-of s (+ 20000 (. T reassign-after-ms) 1)) "NotReady")))
