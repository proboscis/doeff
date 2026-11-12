;;; task の優先(priority)と同時数の上限(limits)を coordinator の置き方 tasks-placed が守る事の検(2026-10-11)。
;;; 実例: 大きな worker の task の枠を呼び手たちの長い背景の仕事が埋め、後から積んだ配備の組み立ての task が 30 分超 queued のまま
;;; 待った(置く順が id の順だけ — 先に積んだ背景の仕事が空いた枠を先に取った)。
(require doeff-hy.macros [deftest val <- defk])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff_cluster._coordinator.intent.request_bodies [MeasuredLoad])
(import doeff_cluster._coordinator.core.measured_room [CPU-FLOOR-CORES MEMORY-FLOOR-MIB RESERVE-SHARE TASK-CORES MEMORY-SLOT-MIB])

(import dataclasses [replace])
(import doeff_hy._table [Table])
(import doeff_cluster [JobSpec])
(import doeff_cluster [ClusterTiming])
(import doeff_cluster [ClusterJob WorkerInfo ClusterState]
        doeff_cluster._coordinator.intent.cluster_model [Placement TaskRecord ComponentVersion TaskUnplacedKind])
(import doeff_cluster._coordinator.intent.request_bodies [TaskBody])
(import doeff_cluster._coordinator.core.cluster_policy [tasks-placed task-unplaced-text])
(import doeff_cluster._coordinator.core.detached_policy [submit-detached])
(import doeff_cluster._coordinator.protocol.cluster_json [task-record-to-json task-record-from-json])
(import tests.program_rows [SAMPLE-TASK-PROGRAM program-placed task-body-of])
(import doeff_cluster [TaskLimit GroupLimit])
(import doeff_hy._table [TableWrite table-of])
(import tests.state_rows [table-from-rows])
(import tests.room_rows [compartments-like-rooms])

(setv T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(setv PY3 #((ComponentVersion "python" "3")))
;; 系が宣言する組の上限(2026-10-12 — 値と範囲は宣言が持ち、task は組の名だけを名乗る): 背景の仕事は worker ごとに 6 本・呼び手(区分)ごとに 2 本。
(setv GROUPS (table-of #((TableWrite "bg" (GroupLimit :group "bg" :scope "worker" :limit 6))
                         (TableWrite "bg-caller" (GroupLimit :group "bg-caller" :scope "cluster" :limit 2 :per-part True)))))

(defk node-h-like [room reserve]
  {:pre [(: room int) (: reserve int) (>= reserve 1) (<= room (- (* RESERVE-SHARE TASK-CORES reserve) CPU-FLOOR-CORES))]
   :post [(: % WorkerInfo)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "測った空きで task を room 本置け、予約(measured_room.reserve-of — 上限の 1/RESERVE-SHARE)が reserve の、能力 host-node-h の worker
   (最後の連絡 0・task と同じ版 python 3)を作るため: CPU は予約が reserve になる数(使い 0 — CPU では room より多く置ける)、memory の
   空きは床 + room 本分(空きを memory で room 本に絞る)。task の空きは常駐の job に依らない(常駐の job の使いは測った値に入っている)。
   下の検の数は、以前の手書きの枠(capacity − 常駐の job・task-reserve)の空きと予約を、同じ空きと予約として写した物。"
  (WorkerInfo "node-h" #("host-node-h") 0
              :measured (MeasuredLoad :at-ms 0 :cpus (* RESERVE-SHARE TASK-CORES reserve) :cpu-busy 0.0 :memory-total-MiB 262144
                                      :memory-available-MiB (+ MEMORY-FLOOR-MIB (* room MEMORY-SLOT-MIB))
                                      :cpu-some-avg10 0.0 :cpu-full-avg10 0.0 :memory-some-avg10 0.0 :memory-full-avg10 0.0)
              :versions PY3))

(defk resident [names]
  {:pre [(: names tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "node-h に置いた常駐の job の宣言と置き先の組 #(jobs placements) を作るため。"
  #((tuple (gfor n names (ClusterJob (JobSpec n "m" #() "rev") :peak-mib 200 :settle-mib 100)))
    (! (table-from-rows (dfor n names n (Placement n "node-h" 1 0))))))

(defk queued [id * [priority 0] [limits #()]]
  {:pre [(: id str) (: priority int) (: limits tuple)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "能力 host-node-h を要る待っている切り離した task(優先 priority・上限 limits)を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 #("host-node-h") 15000 20000 0 :detached True :key id
              :priority priority :limits limits))

(defk background [id caller]
  {:pre [(: id str) (: caller str)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "呼び手 caller の背景の仕事の形の task(優先 -1・呼び手ごとに 2 本・worker ごとに 6 本)を作るため。"
  (! (queued id :priority -1 :limits #((TaskLimit :group "bg-caller" :part caller) (TaskLimit :group "bg")))))

(defk phases [tasks ids]
  {:pre [(: tasks Table) (: ids list)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "task の id の列 → phase の列を読むため。"
  (lfor id ids (. (.row tasks id) phase)))

(setv LONG-IDS (lfor i (range 1 11) (.format "t{:02d}" i)))


(deftest test-a-build-task-is-placed-first-even-after-ten-queued-background-tasks
  ;; 失敗ケース(直す前は id の順に置き、先に積んだ背景の仕事 10 本が空き 10 を全部取って組み立ての task が待った): node-h の形の worker
  ;; (capacity 12・task-reserve 3・常駐の job 2)に背景の仕事 10 本(呼び手はみな別)を先に積み、後から組み立ての task(優先 0)を積む。
  ;; 1 拍の置きで組み立ての task が先に置かれ、背景の仕事はworker ごとの上限 6 本まで。残る 4 本は queued で、上限の固定の文で待つ。
  (setv #(jobs placements) (! (resident #("a" "b"))))
  (setv tasks {"t11" (! (queued "t11"))})
  (for [#(i id) (enumerate LONG-IDS)]
    (setv (get tasks id) (! (background id (.format "c-{}" i)))))
  (val state (ClusterState jobs (! (table-from-rows {"node-h" (! (node-h-like 10 3))})) placements (! (table-from-rows tasks)) :group-limits GROUPS
                     :machine-compartments (! (compartments-like-rooms {"node-h" (! (node-h-like 10 3))}))))
  (val placed (! (tasks-placed 1000 state placements T)))
  (assert (= (. (.row placed "t11") phase) "assigned") (.row placed "t11"))
  (val longs (! (phases placed LONG-IDS)))
  (assert (= (.count longs "assigned") 6) longs)
  (assert (= (.count longs "queued") 4) longs)
  (<- limited str (task-unplaced-text TaskUnplacedKind.TASK-LIMITED))
  (assert (all (gfor id LONG-IDS :if (= (. (.row placed id) phase) "queued") (= (. (.row placed id) detail) limited))) placed))

(deftest test-one-caller-runs-at-most-two-background-tasks-at-once
  ;; 同じ呼び手の背景の仕事 3 本: 呼び手ごとの上限 2(cluster の範囲)で 2 本だけ置かれ、3 本目は上限の文で待つ。別の呼び手の 1 本は置かれる。
  ;; 1 本が終われば 3 本目が置かれる。
  (val tasks {"t1" (! (background "t1" "c-a")) "t2" (! (background "t2" "c-a")) "t3" (! (background "t3" "c-a"))
              "t4" (! (background "t4" "c-b"))})
  (val state (ClusterState #() (! (table-from-rows {"node-h" (! (node-h-like 17 3))})) (table-of #()) (! (table-from-rows tasks)) :group-limits GROUPS
                     :machine-compartments (! (compartments-like-rooms {"node-h" (! (node-h-like 17 3))}))))
  (val placed (! (tasks-placed 1000 state (table-of #()) T)))
  (assert (= (! (phases placed ["t1" "t2" "t3" "t4"])) ["assigned" "assigned" "queued" "assigned"]) placed)
  (assert (= (. (.row placed "t3") detail) (! (task-unplaced-text TaskUnplacedKind.TASK-LIMITED))) (.row placed "t3"))
  (val ended (| (dict (.items placed)) {"t1" (replace (.row placed "t1") :phase "finished" :finished-ms 1500)}))
  (val next (! (tasks-placed 2000 (replace state :tasks (! (table-from-rows ended))) (table-of #()) T)))
  (assert (= (. (.row next "t3") phase) "assigned") (.row next "t3")))

(defk submitted [key body]
  {:pre [(: key str) (: body dict)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "詰めた Program を置き場に置いた状態へ、本文の JSON body を受け口と同じ解きで PUT /detached/<key> として送った #(status 答え 状態) を得るため。"
  (<- placed (get tuple #(ClusterState str)) (program-placed (ClusterState) {"python" "3"}))
  (setv #(placed-state sha) placed)
  (val state (replace placed-state :group-limits GROUPS))
  (<- parsed TaskBody (task-body-of (| {"program" sha "revision" "rev" "name" "bg" "needs" ["host-node-h"] "memoryMiB" 1024} body)))
  (val reply (submit-detached state key parsed 0))
  #(reply.status reply.body reply.state))

(deftest test-priority-and-limits-survive-the-store-and-come-from-the-detached-body
  ;; 送り手が PUT /detached の本文で名乗った優先と上限の組が行に入り、保存の形を往復しても変わらない。欄の無い本文は優先 0・上限なし。
  (setv #(status _ state) (! (submitted "bg/caller-a/x" {"priority" -1
                                                         "limits" [{"group" "bg-caller" "part" "caller-a"} {"group" "bg"}]})))
  (assert (= status 200) status)
  (val row (get (list (.rows state.tasks)) 0))
  (assert (= #(row.priority row.limits) #(-1 #((TaskLimit :group "bg-caller" :part "caller-a") (TaskLimit :group "bg")))) row)
  (<- back TaskRecord (task-record-from-json (task-record-to-json row)))
  (assert (= back row) back)
  (setv #(_ _ plain-state) (! (submitted "k" {})))
  (val plain (get (list (.rows plain-state.tasks)) 0))
  (assert (= #(plain.priority plain.limits) #(0 #())) plain))

(deftest test-a-malformed-limit-is-refused-by-name
  ;; 上限の組の名乗りの形の誤り(値や範囲を送る・空の組の名・列でない・整数でない優先)は 400 で、どの欄がどう違うかを名乗る。
  (for [#(body word) [#({"limits" [{"group" "bg" "scope" "worker"}]} "scope")
                      #({"limits" [{"group" "bg" "limit" 2}]} "limit")
                      #({"limits" [{"group" ""}]} "group")
                      #({"limits" {"group" "bg"}} "limits")
                      #({"priority" "high"} "priority")
                      #({"priority" True} "priority")]]
    (setv #(status answer _) (! (submitted "k" body)))
    (assert (= status 400) #(body status answer))
    (assert (in word answer.message) answer.message)))
