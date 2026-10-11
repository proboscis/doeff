;;; task の優先(priority)と同時数の上限(limits)を coordinator の置き方 place-tasks が守る事の検(2026-10-11)。
;;; 実例: 大きな worker の task の枠を呼び手たちの長い背景の仕事が埋め、後から積んだ配備の組み立ての task が 30 分超 queued のまま
;;; 待った(置く順が id の順だけ — 先に積んだ背景の仕事が空いた枠を先に取った)。
(require doeff-hy.macros [deftest val <- defk])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

(import dataclasses [replace])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterJob WorkerInfo Placement ClusterState TaskRecord ComponentVersion TaskUnplacedKind])
(import doeff_cluster.coordinator.intent.request_bodies [TaskBody])
(import doeff_cluster.coordinator.core.cluster_policy [place-tasks task-unplaced-text])
(import doeff_cluster.coordinator.core.detached_policy [submit-detached])
(import doeff_cluster.coordinator.protocol.cluster_json [task-record-to-json task-record-from-json])
(import tests.program_rows [SAMPLE-TASK-PROGRAM program-placed task-body-of])
(import doeff_cluster.shared.intent.detached_model [TaskLimit])

(setv T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(setv PY3 #((ComponentVersion "python" "3")))

(defk zeus-like [capacity reserve]
  {:pre [(: capacity int) (: reserve int)] :post [(: % WorkerInfo)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "capacity のうち reserve を task のために空けておく、能力 host-zeus の worker(最後の連絡 0・task と同じ版 python 3)を作るため。"
  (WorkerInfo "zeus" #("host-zeus") capacity 0 :task-reserve reserve :versions PY3))

(defk resident [names]
  {:pre [(: names tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "zeus に置いた常駐の job の宣言と置き先の組 #(jobs placements) を作るため。"
  #((tuple (gfor n names (ClusterJob (JobSpec n "m" #() "rev"))))
    (dfor n names n (Placement n "zeus" 1 0))))

(defk queued [id * [priority 0] [limits #()]]
  {:pre [(: id str) (: priority int) (: limits tuple)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "能力 host-zeus を要る待っている切り離した task(優先 priority・上限 limits)を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 #("host-zeus") 15000 20000 0 :detached True :key id
              :priority priority :limits limits))

(defk background [id caller]
  {:pre [(: id str) (: caller str)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "呼び手 caller の背景の仕事の形の task(優先 -1・呼び手ごとに 2 本・worker ごとに 6 本)を作るため。"
  (! (queued id :priority -1 :limits #((TaskLimit :group (+ "bg/" caller) :limit 2 :scope "cluster")
                                       (TaskLimit :group "bg" :limit 6 :scope "worker")))))

(defk phases [tasks ids]
  {:pre [(: tasks dict) (: ids list)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "task の id の列 → phase の列を読むため。"
  (lfor id ids (. (get tasks id) phase)))

(setv LONG-IDS (lfor i (range 1 11) (.format "t{:02d}" i)))


(deftest test-a-build-task-is-placed-first-even-after-ten-queued-background-tasks
  ;; 失敗ケース(直す前は id の順に置き、先に積んだ背景の仕事 10 本が空き 10 を全部取って組み立ての task が待った): zeus の形の worker
  ;; (capacity 12・task-reserve 3・常駐の job 2)に背景の仕事 10 本(呼び手はみな別)を先に積み、後から組み立ての task(優先 0)を積む。
  ;; 1 拍の置きで組み立ての task が先に置かれ、背景の仕事はworker ごとの上限 6 本まで。残る 4 本は queued で、上限の固定の文で待つ。
  (setv #(jobs placements) (! (resident #("a" "b"))))
  (setv tasks {"t11" (! (queued "t11"))})
  (for [#(i id) (enumerate LONG-IDS)]
    (setv (get tasks id) (! (background id (.format "c-{}" i)))))
  (val state (ClusterState jobs {"zeus" (! (zeus-like 12 3))} placements tasks))
  (val placed (! (place-tasks 1000 state placements T)))
  (assert (= (. (get placed "t11") phase) "assigned") (get placed "t11"))
  (val longs (! (phases placed LONG-IDS)))
  (assert (= (.count longs "assigned") 6) longs)
  (assert (= (.count longs "queued") 4) longs)
  (<- limited str (task-unplaced-text TaskUnplacedKind.TASK-LIMITED))
  (assert (all (gfor id LONG-IDS :if (= (. (get placed id) phase) "queued") (= (. (get placed id) detail) limited))) placed))

(deftest test-background-tasks-leave-the-task-reserve-for-the-build-that-comes-later
  ;; 背景の仕事(優先 < 0)は task-reserve を使わない: worker ごとの上限が無くても、背景の仕事 10 本は空き 10 のうち予約 3 を残した 7 本
  ;; までしか置かれない。後の拍で積んだ組み立ての task は、背景の仕事が走っている間でも予約へ置かれる。
  (setv #(jobs placements) (! (resident #("a" "b"))))
  (setv tasks {})
  (for [id LONG-IDS]
    (setv (get tasks id) (! (queued id :priority -1))))
  (val first (! (place-tasks 1000 (ClusterState jobs {"zeus" (! (zeus-like 12 3))} placements tasks) placements T)))
  (val longs (! (phases first LONG-IDS)))
  (assert (= (.count longs "assigned") 7) longs)
  (assert (= (. (get first "t10") detail) (! (task-unplaced-text TaskUnplacedKind.TASK-RESERVED))) (get first "t10"))
  (val later (! (place-tasks 2000 (ClusterState jobs {"zeus" (! (zeus-like 12 3))} placements
                                                (| first {"t11" (! (queued "t11"))}))
                             placements T)))
  (assert (= (. (get later "t11") phase) "assigned") (get later "t11"))
  ;; 優先 0 の task は今までどおり予約まで使える(上の組み立ての後も 2 本置ける)。
  (val more (! (place-tasks 3000 (ClusterState jobs {"zeus" (! (zeus-like 12 3))} placements
                                               (| later {"t12" (! (queued "t12")) "t13" (! (queued "t13"))}))
                            placements T)))
  (assert (= (! (phases more ["t12" "t13"])) ["assigned" "assigned"]) more))

(deftest test-one-caller-runs-at-most-two-background-tasks-at-once
  ;; 同じ呼び手の背景の仕事 3 本: 呼び手ごとの上限 2(cluster の範囲)で 2 本だけ置かれ、3 本目は上限の文で待つ。別の呼び手の 1 本は置かれる。
  ;; 1 本が終われば 3 本目が置かれる。
  (val tasks {"t1" (! (background "t1" "c-a")) "t2" (! (background "t2" "c-a")) "t3" (! (background "t3" "c-a"))
              "t4" (! (background "t4" "c-b"))})
  (val state (ClusterState #() {"zeus" (! (zeus-like 30 3))} {} tasks))
  (val placed (! (place-tasks 1000 state {} T)))
  (assert (= (! (phases placed ["t1" "t2" "t3" "t4"])) ["assigned" "assigned" "queued" "assigned"]) placed)
  (assert (= (. (get placed "t3") detail) (! (task-unplaced-text TaskUnplacedKind.TASK-LIMITED))) (get placed "t3"))
  (val ended (| placed {"t1" (replace (get placed "t1") :phase "finished" :finished-ms 1500)}))
  (val next (! (place-tasks 2000 (replace state :tasks ended) {} T)))
  (assert (= (. (get next "t3") phase) "assigned") (get next "t3")))

(defk submitted [key body]
  {:pre [(: key str) (: body dict)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "詰めた Program を置き場に置いた状態へ、本文の JSON body を受け口と同じ解きで PUT /detached/<key> として送った #(status 答え 状態) を得るため。"
  (<- placed (get tuple #(ClusterState str)) (program-placed (ClusterState) {"python" "3"}))
  (setv #(state sha) placed)
  (<- parsed TaskBody (task-body-of (| {"program" sha "revision" "rev" "name" "bg" "needs" ["host-zeus"]} body)))
  (val reply (submit-detached state key parsed 0))
  #(reply.status reply.body reply.state))

(deftest test-priority-and-limits-survive-the-store-and-come-from-the-detached-body
  ;; 送り手が PUT /detached の本文で名乗った優先と上限が行に入り、保存の形を往復しても変わらない。欄の無い本文は優先 0・上限なし。
  (setv #(status _ state) (! (submitted "bg/c-a/x" {"priority" -1
                                                    "limits" [{"group" "bg/c-a" "limit" 2 "scope" "cluster"}
                                                              {"group" "bg" "limit" 6 "scope" "worker"}]})))
  (assert (= status 200) status)
  (val row (get (list (.values state.tasks)) 0))
  (assert (= #(row.priority row.limits) #(-1 #((TaskLimit :group "bg/c-a" :limit 2 :scope "cluster") (TaskLimit :group "bg" :limit 6 :scope "worker")))) row)
  (<- back TaskRecord (task-record-from-json (task-record-to-json row)))
  (assert (= back row) back)
  (setv #(_ _ plain-state) (! (submitted "k" {})))
  (val plain (get (list (.values plain-state.tasks)) 0))
  (assert (= #(plain.priority plain.limits) #(0 #())) plain))

(deftest test-a-malformed-limit-is-refused-by-name
  ;; 上限の形の誤り(範囲の綴り・0 以下の数・空の組の名・整数でない優先)は 400 で、どの欄がどう違うかを名乗る。
  (for [#(body word) [#({"limits" [{"group" "g" "limit" 2 "scope" "node"}]} "scope")
                      #({"limits" [{"group" "g" "limit" 0 "scope" "worker"}]} "limit")
                      #({"limits" [{"group" "" "limit" 1 "scope" "worker"}]} "group")
                      #({"limits" {"group" "g"}} "limits")
                      #({"priority" "high"} "priority")
                      #({"priority" True} "priority")]]
    (setv #(status answer _) (! (submitted "k" body)))
    (assert (= status 400) #(body status answer))
    (assert (in word answer.message) answer.message)))
