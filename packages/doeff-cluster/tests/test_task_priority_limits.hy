;;; task の優先(priority)と同時数の上限(limits)を coordinator の置き方 tasks-placed が守る事の検(2026-10-11)。
;;; 実例: 大きな worker の task の枠を呼び手たちの長い背景の仕事が埋め、後から積んだ配備の組み立ての task が 30 分超 queued のまま
;;; 待った(置く順が id の順だけ — 先に積んだ背景の仕事が空いた枠を先に取った)。
(require doeff-hy.macros [deftest val <- defk])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

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

(setv T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(setv PY3 #((ComponentVersion "python" "3")))
;; 系が宣言する組の上限(2026-10-12 — 値と範囲は宣言が持ち、task は組の名だけを名乗る): 背景の仕事は worker ごとに 6 本・呼び手(区分)ごとに 2 本。
(setv GROUPS (table-of #((TableWrite "bg" (GroupLimit :group "bg" :scope "worker" :limit 6))
                         (TableWrite "bg-caller" (GroupLimit :group "bg-caller" :scope "cluster" :limit 2 :per-part True)))))

(defk zeus-like [room reserve]
  {:pre [(: room int) (: reserve int)] :post [(: % WorkerInfo)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "task の空き room のうち reserve を task のために空けておく(背景の枠 = room − reserve — card ki-2acb63aec0fd で task の空きは常駐の job に
   依らない。下の検の数は直す前の capacity − 常駐の job の空きを同じ空きとして写した物)、能力 host-zeus の worker(最後の連絡 0・task と
   同じ版 python 3)を作るため。"
  (WorkerInfo "zeus" #("host-zeus") 0 :task-reserve reserve :versions PY3 :background-room (- room reserve)))

(defk resident [names]
  {:pre [(: names tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "zeus に置いた常駐の job の宣言と置き先の組 #(jobs placements) を作るため。"
  #((tuple (gfor n names (ClusterJob (JobSpec n "m" #() "rev") :peak-mib 200 :settle-mib 100)))
    (! (table-from-rows (dfor n names n (Placement n "zeus" 1 0))))))

(defk queued [id * [priority 0] [limits #()]]
  {:pre [(: id str) (: priority int) (: limits tuple)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "能力 host-zeus を要る待っている切り離した task(優先 priority・上限 limits)を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 #("host-zeus") 15000 20000 0 :detached True :key id
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
  ;; 失敗ケース(直す前は id の順に置き、先に積んだ背景の仕事 10 本が空き 10 を全部取って組み立ての task が待った): zeus の形の worker
  ;; (capacity 12・task-reserve 3・常駐の job 2)に背景の仕事 10 本(呼び手はみな別)を先に積み、後から組み立ての task(優先 0)を積む。
  ;; 1 拍の置きで組み立ての task が先に置かれ、背景の仕事はworker ごとの上限 6 本まで。残る 4 本は queued で、上限の固定の文で待つ。
  (setv #(jobs placements) (! (resident #("a" "b"))))
  (setv tasks {"t11" (! (queued "t11"))})
  (for [#(i id) (enumerate LONG-IDS)]
    (setv (get tasks id) (! (background id (.format "c-{}" i)))))
  (val state (ClusterState jobs (! (table-from-rows {"zeus" (! (zeus-like 10 3))})) placements (! (table-from-rows tasks)) :group-limits GROUPS))
  (val placed (! (tasks-placed 1000 state placements T)))
  (assert (= (. (.row placed "t11") phase) "assigned") (.row placed "t11"))
  (val longs (! (phases placed LONG-IDS)))
  (assert (= (.count longs "assigned") 6) longs)
  (assert (= (.count longs "queued") 4) longs)
  (<- limited str (task-unplaced-text TaskUnplacedKind.TASK-LIMITED))
  (assert (all (gfor id LONG-IDS :if (= (. (.row placed id) phase) "queued") (= (. (.row placed id) detail) limited))) placed))

(deftest test-background-tasks-leave-half-the-task-reserve-for-the-build-that-comes-later
  ;; 背景の仕事(優先 < 0)は task-reserve の半分(切り上げ)を優先 0 以上の task に残す(2026-10-14 — 以前は予約を全部残した):
  ;; worker ごとの上限が無くても、背景の仕事 10 本は空き 10 のうち予約 3 の 2 を残した 8 本までしか置かれない。後の拍で積んだ組み立ての
  ;; task は、背景の仕事が走っている間でも残した予約へ置かれる。
  (setv #(jobs placements) (! (resident #("a" "b"))))
  (setv tasks {})
  (for [id LONG-IDS]
    (setv (get tasks id) (! (queued id :priority -1))))
  (val first (! (tasks-placed 1000 (ClusterState jobs (! (table-from-rows {"zeus" (! (zeus-like 10 3))})) placements (! (table-from-rows tasks))) placements T)))
  (val longs (! (phases first LONG-IDS)))
  (assert (= (.count longs "assigned") 8) longs)
  (assert (= (. (.row first "t10") detail) (! (task-unplaced-text TaskUnplacedKind.TASK-RESERVED))) (.row first "t10"))
  (val later (! (tasks-placed 2000 (ClusterState jobs (! (table-from-rows {"zeus" (! (zeus-like 10 3))})) placements
                                                (! (table-from-rows (| (dict (.items first)) {"t11" (! (queued "t11"))}))))
                             placements T)))
  (assert (= (. (.row later "t11") phase) "assigned") (.row later "t11"))
  ;; 優先 0 の task は今までどおり予約の最後まで使える(上の組み立ての後も残した 1 本に置け、その次は空きが無い)。
  (val more (! (tasks-placed 3000 (ClusterState jobs (! (table-from-rows {"zeus" (! (zeus-like 10 3))})) placements
                                               (! (table-from-rows (| (dict (.items later)) {"t12" (! (queued "t12")) "t13" (! (queued "t13"))}))))
                            placements T)))
  (assert (= (! (phases more ["t12" "t13"])) ["assigned" "queued"]) more)
  (assert (= (. (.row more "t13") detail) (! (task-unplaced-text TaskUnplacedKind.TASK-NO-ROOM))) (.row more "t13")))

(deftest test-background-tasks-run-while-resident-jobs-fill-the-job-side
  ;; 失敗ケース(card ki-f6d3da302cf0・本番 2026-10-14 17:05〜): zeus の形(capacity 33・task-reserve 6)で常駐の job が 27(= capacity −
  ;; task-reserve・job の側の空き 0)になると、残る空きは予約の 6 だけになり、予約を使わない背景の仕事は 1 本も置かれなかった(会話の
  ;; long-command 8 本が 76〜230 分 queued)。背景の仕事は予約の半分(切り捨て — 6 なら 3)までは使い、残りの半分(切り上げ)を優先 0 以上の
  ;; task に残す: 背景の仕事 10 本(呼び手はみな別)のうち 3 本が 1 拍で置かれ、残りは予約の文で待つ。後から積んだ組み立ての task 3 本は、
  ;; 背景の仕事が走っている間でも残した予約へ置かれる。
  (setv #(jobs placements) (! (resident (tuple (gfor i (range 27) (.format "j{:02d}" i))))))
  (setv tasks {})
  (for [#(i id) (enumerate LONG-IDS)]
    (setv (get tasks id) (! (background id (.format "c-{}" i)))))
  (val state (ClusterState jobs (! (table-from-rows {"zeus" (! (zeus-like 6 6))})) placements (! (table-from-rows tasks)) :group-limits GROUPS))
  (val first (! (tasks-placed 1000 state placements T)))
  (val longs (! (phases first LONG-IDS)))
  (assert (= (.count longs "assigned") 3) longs)
  (<- reserved str (task-unplaced-text TaskUnplacedKind.TASK-RESERVED))
  (assert (all (gfor id LONG-IDS :if (= (. (.row first id) phase) "queued") (= (. (.row first id) detail) reserved))) first)
  (val builds ["b1" "b2" "b3"])
  (setv more (dict (.items first)))
  (for [id builds]
    (setv (get more id) (! (queued id))))
  (val later (! (tasks-placed 2000 (replace state :tasks (! (table-from-rows more))) placements T)))
  (assert (= (! (phases later builds)) ["assigned" "assigned" "assigned"]) later))

(deftest test-background-tasks-keep-their-half-of-the-reserve-while-foreground-groups-run
  ;; 失敗ケース(card ki-66ebb596d7d6・本番 2026-10-29 17:09〜21:11 JST): zeus の形(capacity 40・task-reserve 6・常駐の job 33 → task の
  ;; 空き 7)で優先 0 の task 4 本(日次の検・着地の後の検 2 組・着地の宣言し直し)が走ると、残る空き 3 は予約の半分 3 を越えず、背景の仕事は
  ;; 1 本も置かれなかった(long-command 最大 27 本が 3〜4 時間 queued)。背景の仕事は予約の残りの半分(切り捨て — 6 なら 3)を自分の分として
  ;; 持つ: 優先 0 の 4 本と背景の仕事 10 本(呼び手はみな別)を積むと、1 拍で優先 0 の 4 本と背景の仕事 3 本が置かれる。同じ拍に積んだ
  ;; 優先 0 の 5 本目は、背景の仕事が待っている間は背景の仕事の分を使わず、その旨の文で待つ。
  (setv #(jobs placements) (! (resident (tuple (gfor i (range 33) (.format "j{:02d}" i))))))
  (val fronts ["f1" "f2" "f3" "f4" "f5"])
  (setv tasks {})
  (for [id fronts]
    (setv (get tasks id) (! (queued id))))
  (for [#(i id) (enumerate LONG-IDS)]
    (setv (get tasks id) (! (background id (.format "c-{}" i)))))
  (val state (ClusterState jobs (! (table-from-rows {"zeus" (! (zeus-like 7 6))})) placements (! (table-from-rows tasks)) :group-limits GROUPS))
  (val placed (! (tasks-placed 1000 state placements T)))
  (assert (= (! (phases placed fronts)) ["assigned" "assigned" "assigned" "assigned" "queued"]) placed)
  (assert (= (. (.row placed "f5") detail) (! (task-unplaced-text TaskUnplacedKind.TASK-BACKGROUND-RESERVED))) (.row placed "f5"))
  (val longs (! (phases placed LONG-IDS)))
  (assert (= (.count longs "assigned") 3) longs))

(deftest test-foreground-tasks-use-the-whole-reserve-when-no-background-task-waits
  ;; 背景の仕事の分は、背景の仕事が待っている時だけ残す: 同じ zeus の形(task の空き 7)で優先 0 の task 7 本だけを積むと 7 本とも置かれる
  ;; (待つ者の無い分を空けたままにしない)。背景の仕事が予約の残りの半分を既に持っていれば、待っている背景の仕事が在っても優先 0 の
  ;; task は残りの空きを全部使える。
  (setv #(jobs placements) (! (resident (tuple (gfor i (range 33) (.format "j{:02d}" i))))))
  (val fronts (lfor i (range 7) (.format "f{}" i)))
  (setv only {})
  (for [id fronts]
    (setv (get only id) (! (queued id))))
  (val alone (! (tasks-placed 1000 (ClusterState jobs (! (table-from-rows {"zeus" (! (zeus-like 7 6))})) placements
                                                 (! (table-from-rows only)) :group-limits GROUPS)
                              placements T)))
  (assert (= (.count (! (phases alone fronts)) "assigned") 7) alone)
  ;; 背景の仕事 10 本だけを先に積む: 空き 7 のうち優先 0 以上の task に残す 3 を除いた 4 本が置かれる(優先 0 の側の守りは今までどおり)。
  (setv tasks {})
  (for [#(i id) (enumerate LONG-IDS)]
    (setv (get tasks id) (! (background id (.format "c-{}" i)))))
  (val state (ClusterState jobs (! (table-from-rows {"zeus" (! (zeus-like 7 6))})) placements (! (table-from-rows tasks)) :group-limits GROUPS))
  (val first (! (tasks-placed 1000 state placements T)))
  (assert (= (.count (! (phases first LONG-IDS)) "assigned") 4) first)
  ;; 後から積んだ優先 0 の task 4 本: 背景の仕事は自分の分 3 を越えて持っているので、優先 0 の側は残りの 3 を全部使い、4 本目は空きを待つ。
  (setv more (dict (.items first)))
  (for [id (cut fronts 4)]
    (setv (get more id) (! (queued id))))
  (val later (! (tasks-placed 2000 (replace state :tasks (! (table-from-rows more))) placements T)))
  (assert (= (! (phases later (cut fronts 4))) ["assigned" "assigned" "assigned" "queued"]) later)
  (assert (= (. (.row later "f3") detail) (! (task-unplaced-text TaskUnplacedKind.TASK-NO-ROOM))) (.row later "f3")))

(deftest test-one-caller-runs-at-most-two-background-tasks-at-once
  ;; 同じ呼び手の背景の仕事 3 本: 呼び手ごとの上限 2(cluster の範囲)で 2 本だけ置かれ、3 本目は上限の文で待つ。別の呼び手の 1 本は置かれる。
  ;; 1 本が終われば 3 本目が置かれる。
  (val tasks {"t1" (! (background "t1" "c-a")) "t2" (! (background "t2" "c-a")) "t3" (! (background "t3" "c-a"))
              "t4" (! (background "t4" "c-b"))})
  (val state (ClusterState #() (! (table-from-rows {"zeus" (! (zeus-like 30 3))})) (table-of #()) (! (table-from-rows tasks)) :group-limits GROUPS))
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
  (<- parsed TaskBody (task-body-of (| {"program" sha "revision" "rev" "name" "bg" "needs" ["host-zeus"]} body)))
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
