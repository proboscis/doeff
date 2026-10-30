;; 静かな heartbeat の早道の失敗ケース(#2655): coordinator の 1 歩の中(状態が同じ now で調停済み)で、変化の無い
;; heartbeat は調停を繰り返さない(api_policy.quiet-heartbeat)。早道は状態の変化を落とさない — 静かな heartbeat の答えは調停を
;; 回した答えと同じで、変化のある heartbeat(報告の行が変わる・沈黙から戻る)は早道に入らない。
(require doeff-hy.macros [defk deftest <- val])
(import doeff [run])
(import doeff_cluster [ClusterTiming])
(import doeff_cluster [http-request])
(import doeff_cluster [ClusterState])
(import doeff_cluster [tick]
        doeff_cluster._coordinator.core.api_policy [respond quiet-heartbeat])
(import doeff_cluster._coordinator.core.cluster_policy [register-heartbeat])
(import doeff_cluster [responded]
        doeff_cluster._coordinator.protocol.request_bodies [body-of])
(import tests.program_rows [SAMPLE-RUN])

(setv T (ClusterTiming))
(setv V {"python" "3.14.0" "doeff" "1"})


(defk beat-body [name statuses]
  {:pre [(: name str) (: statuses list)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "worker name の heartbeat の本文(報告の行 statuses)を組むため。"
  {"name" name "provides" ["net"] "backgroundRoom" 10 "taskReserve" 0 "versions" V "statuses" statuses})


(defk answered [state body now settled]
  {:pre [(: state ClusterState) (: body dict) (: now int) (: settled bool)] :post [(: % tuple)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "heartbeat 1 つを、本文を型に解いてから判断へ渡した答え(settled = 状態が同じ now で調停済みと呼び手が保証するか)。"
  (val request (! (http-request "POST" "/heartbeat" {} body)))
  (<- read (body-of request))
  (<- answer tuple (respond state request now T read :settled settled))
  answer)


(defk placed []
  {:pre [] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "worker w が名乗り、Service s0 が w に置かれ、w が s0 を running と報告した状態(時刻 1000)を、検の出発点として組むため。"
  (<- named dict (beat-body "w" []))
  (val first (responded (ClusterState) (! (http-request "POST" "/heartbeat" {} named)) 0 T))
  (val second (responded (get first 0) (! (http-request "PUT" "/jobs" {} {"jobs" [{"name" "s0" "run" SAMPLE-RUN "peakMiB" 200 "settleMiB" 100 "revision" "r" "needs" ["net"]}]}
                                                     :actor "test"))
                         0 T))
  (<- reporting dict (beat-body "w" [{"name" "s0" "phase" "running" "revision" "r"}]))
  (val third (responded (get second 0) (! (http-request "POST" "/heartbeat" {} reporting)) 1000 T))
  (get third 0))


(deftest test-a-quiet-heartbeat-answers-the-same-as-the-settled-path
  ;; 同じ now で調停済みの状態に、報告の変わらない heartbeat — 早道の答え(状態・status・返事)は、調停を回した答えと同じ。
  (val now 3500)
  (val ready (! (tick (! (placed)) now T)))
  (val body (! (beat-body "w" [{"name" "s0" "phase" "running" "revision" "r"}])))
  (val heard (! (register-heartbeat ready (run (body-of (! (http-request "POST" "/heartbeat" {} body)))) now)))
  (assert (quiet-heartbeat ready heard "w" now T) "報告の変わらない heartbeat は静か")
  (val fast (! (answered ready body now True)))
  (val slow (! (answered ready body now False)))
  (assert (= (get fast 0) (get slow 0)) "早道の状態は調停を回した状態と同じ")
  (assert (= (get fast 1) (get slow 1)))
  (assert (= (get fast 2) (get slow 2)) "早道の返事は調停を回した返事と同じ"))


(deftest test-a-heartbeat-that-changes-the-report-or-revives-takes-the-settled-path
  ;; 変化のある heartbeat は早道に入らない: 報告の行が変わる(running → exited)・沈黙の列から戻る worker。
  (val now 3500)
  (val ready (! (tick (! (placed)) now T)))
  (val changed-body (! (beat-body "w" [{"name" "s0" "phase" "exited" "revision" "r"}])))
  (val changed (! (register-heartbeat ready (run (body-of (! (http-request "POST" "/heartbeat" {} changed-body)))) now)))
  (assert (not (quiet-heartbeat ready changed "w" now T)) "報告の行が変わった heartbeat は静かでない")
  (assert (= (get (! (answered ready changed-body now True)) 0) (get (! (answered ready changed-body now False)) 0))
          "静かでない heartbeat は settled でも調停を回す")
  ;; 沈黙した worker の heartbeat(lease の窓を越えて連絡が無かった)は、生きているへ戻る変化 — 静かでない。
  (val late (+ 1000 T.lease-ms 5000))
  (val silent (! (tick (! (placed)) late T)))
  (assert (in "w" silent.silent) "lease の窓を越えた worker は沈黙の列に入る")
  (val body (! (beat-body "w" [{"name" "s0" "phase" "running" "revision" "r"}])))
  (val revived (! (register-heartbeat silent (run (body-of (! (http-request "POST" "/heartbeat" {} body)))) late)))
  (assert (not (quiet-heartbeat silent revived "w" late T)) "沈黙から戻る heartbeat は静かでない")
  (assert (not-in "w" (. (get (! (answered silent body late True)) 0) silent)) "戻った worker は調停で沈黙の列から外れる"))


;; --- task の比べを行の同一性で絞る(Q1): 同じ object の行は replace を通さず、違う行だけ lease-until-ms を外して比べる ---
(import dataclasses [replace])
(import unittest.mock [patch])
(import doeff_cluster._coordinator.core [api_policy])
(import doeff_cluster._coordinator.intent.cluster_model [TaskRecord])
(import tests.state_rows [table-from-rows])


(defn task-row [id [phase "assigned"] [worker "w"] [lease-until 20000]]
  (TaskRecord id "check" "sha" "rev" #() #("local") 15000 lease-until 0 :phase phase :worker worker))


(defk quiet-pair [before-tasks heard-tasks]
  {:pre [(: before-tasks dict) (: heard-tasks dict)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "静かな heartbeat の組(調停済みの前の状態と、task の欄だけ差し替えた写し)と now を返すため。"
  (val now 3500)
  (val ready (! (tick (! (placed)) now T)))
  (val body (! (beat-body "w" [{"name" "s0" "phase" "running" "revision" "r"}])))
  (val heard (! (register-heartbeat ready (run (body-of (! (http-request "POST" "/heartbeat" {} body)))) now)))
  #((replace ready :tasks (! (table-from-rows before-tasks))) (replace heard :tasks (! (table-from-rows heard-tasks))) now))


(deftest test-a-task-rows-differing-only-in-lease-until-stay-quiet
  (setv before {"a" (task-row "a") "b" (task-row "b")})
  (setv heard {"a" (replace (get before "a") :lease-until-ms 99000) "b" (get before "b")})
  (val pr_b (! (quiet-pair before heard)))
  (setv [b h now] pr_b)
  (assert (quiet-heartbeat b h "w" now T)))


(deftest test-a-task-row-with-another-phase-or-worker-is-not-quiet
  (setv before {"a" (task-row "a") "b" (task-row "b")})
  (for [changed [(replace (get before "a") :phase "finished") (replace (get before "a") :worker "w2")]]
    (val pr_b (! (quiet-pair before {"a" changed "b" (get before "b")})))
    (setv [b h now] pr_b)
    (assert (not (quiet-heartbeat b h "w" now T)))))


(deftest test-a-different-number-of-task-rows-is-not-quiet
  (setv before {"a" (task-row "a") "b" (task-row "b")})
  (val pr_b1 (! (quiet-pair before {"a" (get before "a")})))
  (setv [b1 h1 now] pr_b1)
  (assert (not (quiet-heartbeat b1 h1 "w" now T)) "行が減った")
  (val pr_b2 (! (quiet-pair before {"a" (get before "a") "b" (get before "b") "c" (task-row "c")})))
  (setv [b2 h2 _] pr_b2)
  (assert (not (quiet-heartbeat b2 h2 "w" now T)) "行が増えた")
  (val pr_b3 (! (quiet-pair before {"a" (get before "a") "c" (get before "b")})))
  (setv [b3 h3 _] pr_b3)
  (assert (not (quiet-heartbeat b3 h3 "w" now T)) "数が同じで鍵が違う"))


(deftest test-a-same-object-rows-skip-replace
  (setv before (dfor i (range 387) (str i) (task-row (str i))))
  (setv heard (| before {"7" (replace (get before "7") :lease-until-ms 99000)}))
  (val pr_b (! (quiet-pair before heard)))
  (setv [b h now] pr_b)
  (setv calls [])
  (defn counting [row #** kw] (.append calls 1) (replace row #** kw))
  (with [(patch.object api_policy "replace" counting)]
    (assert (quiet-heartbeat b h "w" now T)))
  (assert (<= (len calls) 12) f"replace {(len calls)} 回(同じ object の行は飛ばす)")
  (setv calls [])
  (with [(patch.object api_policy "replace" counting)]
    (assert (quiet-heartbeat b (replace b :tasks (! (table-from-rows before))) "w" now T)))
  (assert (<= (len calls) 6) "task の行がすべて同じ object なら task には replace を使わない"))
