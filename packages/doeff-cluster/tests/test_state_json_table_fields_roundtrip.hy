;;; ClusterState の Table の欄が、保存の形(state-to-json → state-from-json)で往復することの検(ki-aa9ec1f5e1d8 — #2708 の G)。
;;;
;;; state-to-json は test だけが呼ぶ。state-from-json は置き場(durable の KV)が無い時の旧い file の移しが呼ぶ。
;;; 欄ごとの往復は T の検(placements・drains・rollouts・programs ほか)が見ている。ここは workers・tasks・meta と、
;;; 全部の欄を 1 つの状態に入れた往復(欄の取りこぼしが無いこと)と、欄の無い旧い file が空の表で読めることを見る。
(require doeff-hy.macros [deftest val <- defk])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

(import json)
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState Drain HandoffWatch Placement ProgramRow ResourceMeta TaskRecord WarmEntry WorkerInfo])
(import doeff_cluster.coordinator.protocol.state_json [state-to-json state-from-json])
(import doeff_hy._table [Table TableWrite table-of])
(import tests.program_rows [SAMPLE-TASK-PROGRAM])

(val TABLE-FIELDS ["workers" "placements" "tasks" "meta" "rollouts" "refused" "programs" "drains" "surges" "warms" "handoffs" "group_limits"])


(defn #^ WorkerInfo worker [#^ str name]
  (WorkerInfo name #("net") 10 0 :task-reserve 2))


(defn #^ TaskRecord task [#^ str id]
  (TaskRecord id "" SAMPLE-TASK-PROGRAM "" #() #() 60000 1000 0 :phase "assigned" :worker "w1" :detached True))


(defn #^ ResourceMeta meta-row [#^ int salt]
  (ResourceMeta :resource-version salt :generation 1 :created-by "a" :created-ms 0 :updated-by "b" :updated-ms salt))


(defn #^ ClusterState filled []
  "取り出せる欄すべてに 1 行ずつ入れた状態(往復で取りこぼすと、その欄の比べが赤になる)。"
  (ClusterState
    :workers (table-of #((TableWrite "w1" (worker "w1")) (TableWrite "w2" (worker "w2"))))
    :placements (table-of #((TableWrite "svc" (Placement "svc" "w1" 1 5))))
    :surges (table-of #((TableWrite "svc" (Placement "svc" "w2" 2 6))))
    :tasks (table-of #((TableWrite "t1" (task "t1")) (TableWrite "t2" (task "t2"))))
    :meta (table-of #((TableWrite "Service/svc" (meta-row 3))))
    :drains (table-of #((TableWrite "w1" (Drain :worker "w1" :since-ms 0 :until-ms 9000))))
    :programs (table-of #((TableWrite (* "a" 64) (ProgramRow :blob "a" :versions {} :put-ms 1))))
    :warms (table-of #((TableWrite "live" (WarmEntry :key "live" :runtime-env {} :needs #("net") :until-ms 5000 :holder "h"))))
    :handoffs (table-of #((TableWrite "svc" (HandoffWatch :declaration "x" :since-ms 7))))
    :next-task 3))


(deftest test-every-table-field-survives-the-state-file
  (val state (filled))
  ;; 盤は入らない(行ごとの file)ので、JSON を経由して読み直しても欄の形は同じ表。
  (<- data dict (state-to-json state))
  (<- from-file ClusterState (state-from-json (json.loads (json.dumps data)) 0))
  (for [name ["workers" "placements" "tasks" "meta" "drains" "surges" "programs" "warms" "handoffs"]]
    (val got (getattr from-file name))
    (assert (isinstance got Table) #(name got))
    (assert (= (.keys got) (.keys (getattr state name))) #(name))
    (assert (> (.size got) 0) #(name "空の表を往復した")))
  (assert (= (.row from-file.tasks "t2") (.row state.tasks "t2")))
  (assert (= (.row from-file.meta "Service/svc") (.row state.meta "Service/svc")))
  (assert (= (.row from-file.placements "svc") (.row state.placements "svc")))
  (assert (= (.row from-file.surges "svc") (.row state.surges "svc")))
  (assert (= from-file.next-task 3))
  ;; 読み直した表を書き直すと同じ JSON(往復で形が変わらない)。
  (<- again dict (state-to-json from-file))
  (assert (= again data)))


(deftest test-a-worker-row-keeps-its-stored-fields-through-the-state-file
  (val state (ClusterState :workers (table-of #((TableWrite "w1" (WorkerInfo "w1" #("net") 10 0 :task-reserve 2 :node "n1"))))))
  (<- data dict (state-to-json state))
  (<- from-file ClusterState (state-from-json data 777))
  (val got (.row from-file.workers "w1"))
  (assert (= #(got.name got.provides got.capacity got.task-reserve got.node) #("w1" #("net") 10 2 "n1")) got)
  ;; 知っていた worker は「いま生きていた」とみなす(読み直した時刻が最後の連絡)。
  (assert (= got.last-seen-ms 777) got))


(deftest test-a-state-file-without-the-table-fields-reads-as-empty-tables
  (val empty (! (state-from-json {"jobs" [] "nextTask" 1} 0)))
  (for [name TABLE-FIELDS]
    (val got (getattr empty name))
    (assert (isinstance got Table) #(name got))
    (assert (= (.size got) 0) #(name got))))
