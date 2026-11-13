;;; 置けない訳の種類(TaskRecord.unplaced-kind・GET /state の task 行の unplacedKind — 2026-11-13)の検: 外の読み手が detail の文を照らさず、
;;; 種類の名で待ちの訳を分けられるようにする。
;;;   (1) 綴りの表 TASK-UNPLACED-KIND-NAMES は TaskUnplacedKind の全ての値を、値の名のまま綴る(種類が増えたらこの検が落ちる)
;;;   (2) place-tasks は detail の文と同じ判断の種類を行に書く(区画の無さ・区画の和・組の上限 — 同じ入力の後ろの task が判断を借りる時も)
;;;   (3) GET /state の task 行は種類を名乗る行と queued の行に unplacedKind を出す(種類の無い queued の行は null・置いた行・終えた行は
;;;       欄を出さない — 外の読み手は欄の無い queued の行を種類を出さない前の版の coordinator と読む)
;;;   (4) 種類は保存しない — 読み直した行は種類を持たず、次の拍の place-tasks が detail の文が同じでも書き直す
;;; 失敗ケース = 直す前の形(種類は detail の文の中だけに在り、文を言い替えるたびに外の写しが黙って数え損ねた)。
(require doeff-hy.macros [defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import doeff_cluster [ClusterState ClusterTiming WorkerInfo TaskLimit GroupLimit]
        doeff_cluster._coordinator.intent.cluster_model [TaskRecord ComponentVersion TaskUnplacedKind])
(import doeff_cluster._coordinator.core.cluster_policy [tasks-placed task-unplaced-text end-detached])
(import doeff_cluster._coordinator.protocol.cluster_json [task-record-to-json task-record-from-json])
(import doeff_cluster._coordinator.protocol.replies [task-summary TASK-UNPLACED-KIND-NAMES])
(import doeff_cluster._shared.intent.compartment_model [Compartment MachineCompartments])
(import doeff_hy._table [Table TableWrite table-of])
(import tests.program_rows [SAMPLE-TASK-PROGRAM])
(import tests.state_rows [table-from-rows])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(val PY3 #((ComponentVersion "python" "3")))
(val NOW 100000)
(val NEEDS #("net"))
(val LLM "llm")
(val GROUPS (table-of #((TableWrite LLM (GroupLimit :group LLM :scope "cluster" :limit 1)))))


(defk worker-of [name]
  {:pre [(: name str)] :post [(: % WorkerInfo)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "能力 net を名乗る生きた worker name を作るため。"
  (WorkerInfo name NEEDS NOW :versions PY3))


(defk declared [sizes]
  {:pre [(: sizes dict)] :post [(: % Table)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker の名 → 区画の memory(MiB)から、worker ごとに tasks 区画 1 つ(層 jobs・CPU 4 core)の machine の行を持つ表を作るため。"
  (table-of (tuple (gfor #(name memory) (sorted (.items sizes))
                         (TableWrite (+ "m-" name)
                                     (MachineCompartments :machine (+ "m-" name)
                                                          :compartments #((Compartment :use "tasks" :worker name :layer "jobs"
                                                                                       :memory-mib memory :memory-limit-mib memory
                                                                                       :cpu-millis 4000))))))))


(defk queued [id * [limits #()]]
  {:pre [(: id str) (: limits tuple)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "能力 net を要る、1024 MiB を名乗って待っている切り離した task(組 limits を名乗る)を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 NEEDS 15000 (+ NOW 15000) 0 :detached True :key id
              :memory-mib 1024 :limits limits))


(defk running [id worker * [limits #()]]
  {:pre [(: id str) (: worker str) (: limits tuple)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker に置かれて走っている、1024 MiB を名乗る切り離した task(組 limits を名乗る)を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 NEEDS 15000 (+ NOW 15000) 0 :detached True :key id
              :memory-mib 1024 :limits limits :phase "assigned" :worker worker :started-ms 1000))


(defk placed-rows [sizes rows]
  {:pre [(: sizes dict) (: rows dict)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker w-a 1 台(区画の大きさ sizes)と task の行 rows と組の宣言 GROUPS の状態を置き先に 1 拍回し、拍の後の全部の行(id → 行)を得るため。"
  (<- w WorkerInfo (worker-of "w-a"))
  (<- workers Table (table-from-rows {"w-a" w}))
  (<- tasks Table (table-from-rows rows))
  (<- compartments Table (declared sizes))
  (val state (replace (ClusterState :workers workers :tasks tasks :machine-compartments compartments) :group-limits GROUPS))
  (<- placed (tasks-placed NOW state state.placements T))
  (dfor t (.rows placed) t.id t))


(defk kind-agrees [row kind]
  {:pre [(: row TaskRecord) (: kind TaskUnplacedKind)] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "待っている行 row が種類 kind を持ち、detail がその種類の文を含み、GET /state の task 行が同じ種類の名を出すかを判じるため。"
  (<- text str (task-unplaced-text kind))
  (<- spelled dict (task-summary row))
  (and (= row.phase "queued") (is row.unplaced-kind kind) (in text row.detail)
       (= (.get spelled "unplacedKind") (get TASK-UNPLACED-KIND-NAMES kind))))


(deftest test-every-unplaced-kind-is-spelled-by-its-name
  ;; 表は enum の全ての値をちょうど持ち、名は値の名のまま(TASK-COMPARTMENT-FULL など)で重ならない。
  (assert (= (set TASK-UNPLACED-KIND-NAMES) (set TaskUnplacedKind)) TASK-UNPLACED-KIND-NAMES)
  (for [kind TaskUnplacedKind]
    (assert (= (get TASK-UNPLACED-KIND-NAMES kind) (.replace kind.name "_" "-")) kind))
  (assert (= (len (set (.values TASK-UNPLACED-KIND-NAMES))) (len TaskUnplacedKind))))


(deftest test-place-tasks-writes-the-kind-with-the-detail
  ;; 区画を持つ worker が無い: TASK-NO-COMPARTMENT。
  (<- q1 TaskRecord (queued "q1"))
  (<- bare dict (placed-rows {} {"q1" q1}))
  (<- no-compartment bool (kind-agrees (get bare "q1") TaskUnplacedKind.TASK-NO-COMPARTMENT))
  (assert no-compartment (get bare "q1"))
  ;; 区画 1 本分が置いた task で埋まっている: 待っている 2 本とも TASK-COMPARTMENT-FULL(2 本目は 1 本目の判断を借りる)。
  (<- held TaskRecord (running "held" "w-a"))
  (<- q2 TaskRecord (queued "q2"))
  (<- full dict (placed-rows {"w-a" 1024} {"held" held "q1" q1 "q2" q2}))
  (for [id #("q1" "q2")]
    (<- compartment-full bool (kind-agrees (get full id) TaskUnplacedKind.TASK-COMPARTMENT-FULL))
    (assert compartment-full (get full id)))
  ;; 組 llm(上限 1)の 1 本が走っている: 同じ組を名乗る task は TASK-LIMITED(区画は空いている)。
  (<- holder TaskRecord (running "holder" "w-a" :limits #((TaskLimit :group LLM))))
  (<- waiter TaskRecord (queued "waiter" :limits #((TaskLimit :group LLM))))
  (<- limited dict (placed-rows {"w-a" 8192} {"holder" holder "waiter" waiter}))
  (<- by-limit bool (kind-agrees (get limited "waiter") TaskUnplacedKind.TASK-LIMITED))
  (assert by-limit (get limited "waiter")))


(deftest test-placed-and-ended-rows-carry-no-kind
  ;; 種類を持って待っていた task は、区画が空いた拍に置かれ、種類を落とす(GET /state の行も欄を出さない)。
  (<- q TaskRecord (queued "q"))
  (val waiting (replace q :detail (task-unplaced-text TaskUnplacedKind.TASK-COMPARTMENT-FULL)
                          :unplaced-kind TaskUnplacedKind.TASK-COMPARTMENT-FULL))
  (<- rows dict (placed-rows {"w-a" 4096} {"q" waiting}))
  (val placed (get rows "q"))
  (assert (= #(placed.phase placed.unplaced-kind) #("assigned" None)) placed)
  (<- placed-row dict (task-summary placed))
  (assert (not-in "unplacedKind" placed-row) placed-row)
  ;; 待ったまま取り消された行も種類を落とす(detail は終わりの訳に替わる)。
  (val cancelled (end-detached waiting "cancelled" NOW "取り消された"))
  (assert (is cancelled.unplaced-kind None) cancelled)
  (<- cancelled-row dict (task-summary cancelled))
  (assert (not-in "unplacedKind" cancelled-row) cancelled-row))


(deftest test-the-kind-is-not-stored-and-is-written-again
  ;; 保存の綴りは種類を持たず、読み直した行は種類を持たない。
  (<- held TaskRecord (running "held" "w-a"))
  (<- q TaskRecord (queued "q"))
  (<- before dict (placed-rows {"w-a" 1024} {"held" held "q" q}))
  (val waiting (get before "q"))
  (assert (is waiting.unplaced-kind TaskUnplacedKind.TASK-COMPARTMENT-FULL) waiting)
  (val stored (task-record-to-json waiting))
  (assert (not-in "unplaced_kind" stored) stored)
  (<- back TaskRecord (task-record-from-json stored))
  (assert (= #(back.detail back.unplaced-kind) #(waiting.detail None)) back)
  ;; 次の拍の place-tasks は、detail の文が同じでも種類を書き直す(失敗ケース: 文だけを比べると読み直した行が種類を持たないまま残る)。
  (<- again dict (placed-rows {"w-a" 1024} {"held" held "q" back}))
  (<- rewritten bool (kind-agrees (get again "q") TaskUnplacedKind.TASK-COMPARTMENT-FULL))
  (assert rewritten (get again "q")))


(deftest test-a-queued-row-without-a-kind-spells-the-field-as-null
  ;; 種類の無い待ち(走らせ直しの次の試行・準備の失敗の後の置き直しなど — detail だけを書く)も、queued の行は欄を null で出す。
  ;; 失敗ケース = 直す前の形(欄を出さず、外の読み手が種類を出さない前の版の coordinator と見分けられなかった)。
  (<- q TaskRecord (queued "q"))
  (val waiting (replace q :detail "走らせ直しの次の試行を待つ"))
  (assert (is waiting.unplaced-kind None) waiting)
  (<- row dict (task-summary waiting))
  (assert (in "unplacedKind" row) row)
  (assert (is (get row "unplacedKind") None) row)
  ;; 種類を持つ queued の行は名を出す。
  (<- named dict (task-summary (replace waiting :unplaced-kind TaskUnplacedKind.TASK-LIMITED)))
  (assert (= (get named "unplacedKind") "TASK-LIMITED") named))
