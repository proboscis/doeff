;;; task の CPU の申告を組の宣言の値で数える事(2026-11-18 — 外へ出すだけで CPU をほぼ使わない task の組が、区画の CPU を 1 core ずつ持ち、
;;; 区画の CPU が 4 core の機体では 5 本目から置けなかった)の検:
;;;   (1) 区画の CPU 4000m(memory は当たらない大きさ)で、CPU の申告 250m を宣言した組の task は 16 本まで置かれ、17 本目は CPU で待つ
;;;       (TASK-COMPARTMENT-FULL)。値を書いていない組の task は 4 本まで置かれ、5 本目が待つ。
;;;   (2) 置いた歩の申告の書き(claims-noted)と、申告の行の無い置いた task の数え(claim-cpu-of)と、積む時の断り(memory-need-refusal)が
;;;       置く判断と同じ値(task-cpu-of)で数える。
;;;   (3) 既定の動き: 組を名乗らない・宣言に無い組だけ・値を書いていない組だけの task は TASK-CPU-MILLIS。値を持つ組を幾つも名乗れば最小。
;;;   (4) 宣言の読み: cpuMillis が数でない・0 以下・真偽値の組は断られ(TASK-CPU-MILLIS へ黙って落ちない)、読めた値は spec へ綴り戻る。
;;; 失敗ケース = 直す前の形(全部の task が TASK-CPU-MILLIS — 宣言の組に値を書く欄が無い)。
(require doeff-hy.macros [defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import doeff_cluster [ClusterState TaskLimit GroupLimit]
        doeff_cluster._coordinator.intent.cluster_model [TaskRecord TaskUnplacedKind])
(import doeff_cluster._coordinator.core.cluster_policy [tasks-placed task-unplaced-text memory-need-refusal])
(import doeff_cluster._coordinator.core.task_claims [claims-noted claim-cpu-of task-cpu-of TASK-CPU-MILLIS])
(import doeff_cluster._shared.core.detached_rules [group-limit-refusal group-limit-of group-limit-spec])
(import doeff_hy._table [Table TableWrite table-of])
(import tests.program_rows [SAMPLE-TASK-PROGRAM])
(import tests.state_rows [table-from-rows])
(import tests.test_declared_sum_placement [T PY3 NOW NEEDS IDLE worker-of declared])

(val LIGHT "off-pod")
(val PLAIN "plain")
(val GROUPS (table-of #((TableWrite LIGHT (GroupLimit :group LIGHT :scope "worker" :limit 32 :cpu-millis 250))
                        (TableWrite PLAIN (GroupLimit :group PLAIN :scope "worker" :limit 32)))))
;; 区画の CPU 4000m・memory は当たらない大きさ。
(val SIZES {"w-a" #(1048576 4000)})


(defk named [id group]
  {:pre [(: id str) (: group str)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "能力 net を要る、組 group を名乗って待っている切り離した task を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 NEEDS 15000 (+ NOW 15000) 0 :detached True :key id :memory-mib 256
              :limits #((TaskLimit :group group))))


(defk ticked [group count]
  {:pre [(: group str) (: count int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "組 group の task count 本を区画 SIZES の 1 台に 1 拍で置き、拍の前と後の状態を得るため。"
  (<- w (worker-of "w-a" IDLE))
  (<- workers Table (table-from-rows {"w-a" w}))
  (var rows {})
  (for [i (range count)]
    (val id (.format "{}-{:02d}" group i))
    (<- t TaskRecord (named id group))
    (:= rows (| rows {id t})))
  (<- tasks Table (table-from-rows rows))
  (<- compartments Table (declared SIZES))
  (val before (replace (ClusterState :workers workers :tasks tasks :machine-compartments compartments) :group-limits GROUPS))
  (<- placed Table (tasks-placed NOW before before.placements T))
  #(before (replace before :tasks placed)))


(defk phases-of [state]
  {:pre [(: state ClusterState)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "状態の task の phase ごとの行(phase → 行の list)を得るため。"
  (var out {})
  (for [t (.rows state.tasks)]
    (:= out (| out {t.phase [#* (.get out t.phase []) t]})))
  out)


(deftest test-1-a-declared-light-group-fills-the-cpu-by-its-claim
  ;; (1): 250m の組は 16 本置かれ 17〜20 本目は CPU で待つ。値の無い組は 4 本置かれ 5 本目が待つ。
  (<- full str (task-unplaced-text TaskUnplacedKind.TASK-COMPARTMENT-FULL))
  (for [#(group count placed) #(#(LIGHT 20 16) #(PLAIN 5 4))]
    (<- pair tuple (ticked group count))
    (<- phases dict (phases-of (get pair 1)))
    (assert (= (len (.get phases "assigned" [])) placed) #(group (lfor #(k v) (.items phases) #(k (len v)))))
    (val waiting (.get phases "queued" []))
    (assert (= (len waiting) (- count placed)) #(group waiting))
    (for [t waiting]
      (assert (= t.unplaced-kind TaskUnplacedKind.TASK-COMPARTMENT-FULL) t)
      (assert (in full t.detail) t.detail))))


(deftest test-2-the-claim-the-sums-and-the-refusal-read-the-same-value
  ;; (2): 置いた歩の申告の行は 250m・行の無い置いた task の数えも 250m・積む時の断りは区画の CPU 500m の機体で値の無い組だけを断る。
  (<- pair tuple (ticked LIGHT 3))
  (<- noted ClusterState (claims-noted (get pair 0) (get pair 1)))
  (<- phases dict (phases-of noted))
  (for [t (get phases "assigned")]
    (assert (= (. (.row noted.claims t.id) cpu-millis) 250) (.row noted.claims t.id))
    (<- unclaimed int (claim-cpu-of t (table-of #()) noted.group-limits))
    (assert (= unclaimed 250) unclaimed))
  (<- w (worker-of "w-a" IDLE))
  (<- workers Table (table-from-rows {"w-a" w}))
  (<- small Table (declared {"w-a" #(1048576 500)}))
  (val state (replace (ClusterState :workers workers :machine-compartments small) :group-limits GROUPS))
  (<- light (| str None) (memory-need-refusal state NEEDS 256 "" False #((TaskLimit :group LIGHT))))
  (<- plain (| str None) (memory-need-refusal state NEEDS 256 "" False #((TaskLimit :group PLAIN))))
  (assert (is light None) light)
  (assert (and plain (in "1000 millicore" plain)) plain))


(deftest test-3-the-default-claim-and-the-smallest-declared-value
  ;; (3): 組を名乗らない・宣言に無い組・値を書いていない組は TASK-CPU-MILLIS。値を持つ組を幾つも名乗れば最小。
  (val tiny (table-of #((TableWrite LIGHT (GroupLimit :group LIGHT :scope "worker" :limit 32 :cpu-millis 250))
                        (TableWrite "tiny" (GroupLimit :group "tiny" :scope "worker" :limit 1 :cpu-millis 100))
                        (TableWrite PLAIN (GroupLimit :group PLAIN :scope "worker" :limit 32)))))
  (for [#(groups expected) #(#(#() TASK-CPU-MILLIS) #(#("undeclared") TASK-CPU-MILLIS) #(#(PLAIN) TASK-CPU-MILLIS)
                             #(#(PLAIN LIGHT) 250) #(#(LIGHT "tiny" "undeclared") 100))]
    (<- cpu int (task-cpu-of (tuple (gfor g groups (TaskLimit :group g))) tiny))
    (assert (= cpu expected) #(groups cpu))))


(deftest test-4-a-bad-cpu-claim-is-refused-at-the-declaration
  ;; (4): 数でない・0 以下・真偽値・小数の cpuMillis は宣言の読みで断る。読めた値は spec へ綴り戻り、欄の無い組は欄を綴らない。
  (for [bad #("250" 0 -1 True 2.5)]
    (val why (group-limit-refusal LIGHT {"scope" "worker" "limit" 32 "cpuMillis" bad}))
    (assert (and why (in "cpuMillis" why)) #(bad why)))
  (val spec {"scope" "worker" "limit" 32 "cpuMillis" 250})
  (assert (is (group-limit-refusal LIGHT spec) None))
  (val limit (group-limit-of LIGHT spec))
  (assert (= limit.cpu-millis 250) limit)
  (assert (= (group-limit-spec limit) spec) (group-limit-spec limit))
  (assert (not-in "cpuMillis" (group-limit-spec (group-limit-of PLAIN {"scope" "worker" "limit" 32})))))
