;;; 区画が満ちて置けない task(unplacedKind = TASK-COMPARTMENT-FULL)の detail の検:
;;;   (1) CPU で置けない task の detail に、当たった machine の名・越えた申告の種類(CPU)・宣言の値と申告の和・置かれた task の組ごとの
;;;       本数と申告の和(多い順に 3 つまで)が入り、越えていない memory の和は混ぜない。
;;;   (2) memory で置けない task・memory と CPU の両方で置けない task・置かれた task が 0 本の区画・複数台が当たった時(再利用される 2 本目
;;;       を含む)・複数組を名乗る task と組無しの task のそれぞれで、detail が 1 行で正しく出る。
;;;   (3) detail の数が、置く前の check(compartment-misfit)が使う値と同じ所から出ており、別の数え方を足していない事(claim-mib-of /
;;;       claim-cpu-of を数え直さない・同じ拍に置いた task も反映される・starved-keeper の取っておき前後で compartment-misfit の引数と一致する・
;;;       detached-queue-of-state が detail をそのまま運ぶ)。
;;; 失敗ケース = 直す前の code(task-unplaced-text の固定の文だけ — machine 名・越えた種類・宣言と和・組ごとの内訳が出ない)。
(require doeff-hy.macros [defk deftest <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import unittest.mock [Mock])
(import hy)
(import pytest)
(import doeff_cluster [ClusterState TaskLimit GroupLimit DetachedQueueReason]
        doeff_cluster._coordinator.intent.cluster_model [TaskRecord TaskUnplacedKind]
        doeff_cluster._coordinator.core [cluster_policy :as cluster-policy]
        doeff_cluster._coordinator.core.cluster_policy [tasks-placed task-unplaced-text]
        doeff_cluster._coordinator.core.measured_room [KEEP-NOW-PRIORITY]
        doeff_cluster._coordinator.protocol.replies [task-summary]
        doeff_cluster._shared.protocol.detached [detached-queue-of-state])
(import doeff_hy._table [Table TableWrite table-of])
(import tests.program_rows [SAMPLE-TASK-PROGRAM])
(import tests.state_rows [table-from-rows])
(import tests.test_declared_sum_placement [T PY3 NOW NEEDS IDLE worker-of declared])

(val GROUPS
  (table-of #((TableWrite "g-top" (GroupLimit :group "g-top" :scope "worker" :limit 32))
              (TableWrite "g-second" (GroupLimit :group "g-second" :scope "worker" :limit 32))
              (TableWrite "g-third" (GroupLimit :group "g-third" :scope "worker" :limit 32 :cpu-millis 250))
              (TableWrite "g-fourth" (GroupLimit :group "g-fourth" :scope "worker" :limit 32 :cpu-millis 250))
              (TableWrite "cmd-session" (GroupLimit :group "cmd-session" :scope "cluster" :limit 32 :per-part True))
              (TableWrite "cmd-host" (GroupLimit :group "cmd-host" :scope "worker" :limit 32)))))


(defk queued-task [id [memory-mib 256] [limits #()] [priority 0] [submitted-ms 0]]
  {:pre [(: id str) (: memory-mib int) (: limits tuple) (: priority int) (: submitted-ms int)] :post [(: % TaskRecord)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "能力 NEEDS を要る待っている切り離した task を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 NEEDS 15000 (+ NOW 15000) submitted-ms :detached True :key id
              :memory-mib memory-mib :limits limits :priority priority))


(defk running-task [id worker [memory-mib 256] [limits #()]]
  {:pre [(: id str) (: worker str) (: memory-mib int) (: limits tuple)] :post [(: % TaskRecord)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker に置かれている切り離した task を作るため。"
  (replace (! (queued-task id :memory-mib memory-mib :limits limits)) :phase "assigned" :worker worker :started-ms 0))


(defk placed-on [sizes rows]
  {:pre [(: sizes dict) (: rows dict)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "区画 sizes(worker 名 → #(memoryMiB cpuMillis))の worker 群へ task rows を 1 拍置き、id → TaskRecord の dict を得るため。"
  (var worker-map {})
  (for [name (sorted (.keys sizes))]
    (<- w (worker-of name IDLE))
    (:= worker-map (| worker-map {name w})))
  (<- workers Table (table-from-rows worker-map))
  (<- tasks Table (table-from-rows rows))
  (<- compartments Table (declared sizes))
  (val state (replace (ClusterState :workers workers :tasks tasks :machine-compartments compartments) :group-limits GROUPS))
  (<- placed Table (tasks-placed NOW state state.placements T))
  (dfor t (.rows placed) t.id t))


(deftest test-cpu-full-detail-names-machine-resource-sums-and-top-groups
  ;; node-h の区画は memory 9216 MiB・CPU 4000m。4 組の task で CPU 3750m が埋まっている(memory は 1536 MiB で余裕)。
  ;; CPU 1000m の task が待つ時: machine 名・CPU・申告和 3750m・宣言 4000m・多い順 3 組まで(4 組目は省き「ほか 1 組」)が出る。
  (val rows
    {"p1" (! (running-task "p1" "node-h" :memory-mib 256 :limits #((TaskLimit :group "g-top"))))
     "p2" (! (running-task "p2" "node-h" :memory-mib 256 :limits #((TaskLimit :group "g-top"))))
     "p3" (! (running-task "p3" "node-h" :memory-mib 256 :limits #((TaskLimit :group "g-second"))))
     "p4" (! (running-task "p4" "node-h" :memory-mib 256 :limits #((TaskLimit :group "g-third"))))
     "p5" (! (running-task "p5" "node-h" :memory-mib 256 :limits #((TaskLimit :group "g-third"))))
     "p6" (! (running-task "p6" "node-h" :memory-mib 256 :limits #((TaskLimit :group "g-fourth"))))
     "q-wait" (! (queued-task "q-wait" :memory-mib 256 :limits #((TaskLimit :group "g-second"))))})
  (<- after dict (placed-on {"node-h" #(9216 4000)} rows))
  (val q (get after "q-wait"))
  (assert (= #(q.phase q.unplaced-kind) #("queued" TaskUnplacedKind.TASK-COMPARTMENT-FULL)) q)
  (<- base str (task-unplaced-text TaskUnplacedKind.TASK-COMPARTMENT-FULL))
  (assert (in base q.detail) q.detail)
  (assert (not-in "\n" q.detail) q.detail)
  (assert (in "node-h" q.detail) q.detail)
  (assert (in "CPU 申告和 3750m + この task 1000m > 宣言 4000m" q.detail) q.detail)
  (assert (not-in "memory 申告和" q.detail) q.detail)
  (for [piece #("g-top 2本 2000m" "g-second 1本 1000m" "g-third 2本 500m" "ほか 1 組")]
    (assert (in piece q.detail) #(piece q.detail)))
  (assert (not-in "g-fourth" q.detail) q.detail)
  (assert (< (.index q.detail "g-top") (.index q.detail "g-second") (.index q.detail "g-third")) q.detail))


(deftest test-memory-both-empty-and-multi-worker-details
  ;; (a) memory のみ超過: 複数組(part 違い)は組の名だけで 1 組にまとめ二重計上せず、組無しは「組なし」で数える。
  (val mem-rows
    {"m1" (! (running-task "m1" "node-z" :memory-mib 3072
                           :limits #((TaskLimit :group "cmd-session" :part "s-1") (TaskLimit :group "cmd-host"))))
     "m2" (! (running-task "m2" "node-z" :memory-mib 3072
                           :limits #((TaskLimit :group "cmd-session" :part "s-2") (TaskLimit :group "cmd-host"))))
     "m3" (! (running-task "m3" "node-z" :memory-mib 1024 :limits #()))
     "q-mem" (! (queued-task "q-mem" :memory-mib 2048 :limits #((TaskLimit :group "g-second"))))})
  (<- mem-after dict (placed-on {"node-z" #(8192 16000)} mem-rows))
  (val qm (get mem-after "q-mem"))
  (assert (= #(qm.phase qm.unplaced-kind) #("queued" TaskUnplacedKind.TASK-COMPARTMENT-FULL)) qm)
  (assert (in "node-z" qm.detail) qm.detail)
  (assert (in "memory 申告和 7168 MiB + この task 2048 MiB > 宣言 8192 MiB" qm.detail) qm.detail)
  (assert (not-in "CPU 申告和" qm.detail) qm.detail)
  (assert (in "cmd-host+cmd-session 2本 6144 MiB" qm.detail) qm.detail)
  (assert (in "組なし 1本 1024 MiB" qm.detail) qm.detail)
  (assert (< (.index qm.detail "cmd-host+cmd-session") (.index qm.detail "組なし")) qm.detail)
  ;; (b) memory と CPU の両方が超過: 両方の申告の種類・宣言と和・組内訳が出る。
  (val both-rows
    {"b1" (! (running-task "b1" "w-both" :memory-mib 1024 :limits #((TaskLimit :group "g-top"))))
     "b2" (! (running-task "b2" "w-both" :memory-mib 1024 :limits #((TaskLimit :group "g-top"))))
     "q-both" (! (queued-task "q-both" :memory-mib 1024 :limits #((TaskLimit :group "g-top"))))})
  (<- both-after dict (placed-on {"w-both" #(2048 2000)} both-rows))
  (val qb (get both-after "q-both"))
  (assert (in "memory 申告和 2048 MiB + この task 1024 MiB > 宣言 2048 MiB" qb.detail) qb.detail)
  (assert (in "CPU 申告和 2000m + この task 1000m > 宣言 2000m" qb.detail) qb.detail)
  ;; (c) 置かれた task が 0 本の区画: この task の申告だけで超過する事が分かる。
  (<- empty-after dict (placed-on {"w-empty" #(1024 4000)} {"q-big" (! (queued-task "q-big" :memory-mib 2048))}))
  (val qe (get empty-after "q-big"))
  (assert (in "w-empty" qe.detail) qe.detail)
  (assert (in "memory 申告和 0 MiB + この task 2048 MiB > 宣言 1024 MiB" qe.detail) qe.detail)
  (assert (in "置かれた task なし" qe.detail) qe.detail)
  ;; (d) 複数台が当たり、同じ形の 2 本目が判断を再利用しても両方の machine 名が同じ決定的な順で出る。
  (val two-rows
    {"a1" (! (running-task "a1" "w-a" :memory-mib 1024 :limits #((TaskLimit :group "g-top"))))
     "b1" (! (running-task "b1" "w-b" :memory-mib 1024 :limits #((TaskLimit :group "g-second"))))
     "q1" (! (queued-task "q1" :memory-mib 1024))
     "q2" (! (queued-task "q2" :memory-mib 1024))})
  (<- two-after dict (placed-on {"w-a" #(1024 4000) "w-b" #(1024 4000)} two-rows))
  (val q1 (get two-after "q1"))
  (val q2 (get two-after "q2"))
  (assert (= q1.detail q2.detail) #(q1.detail q2.detail))
  (assert (and (in "w-a" q1.detail) (in "w-b" q1.detail)) q1.detail)
  (assert (< (.index q1.detail "w-a") (.index q1.detail "w-b")) q1.detail))


(deftest test-detail-numbers-come-from-the-placement-check-without-recounting [#^ pytest.MonkeyPatch monkeypatch]
  ;; 受入 ccb81f438:
  ;; (1) 拍の初めに 3 本置かれ、同じ拍に 1 本置かれて 4 本目が埋まり、後ろの 4 本が待つ場面で、
  ;;     claim-mib-of / claim-cpu-of は拍の初めの 3 本に対して各 1 回だけ呼ばれ(detail 作成で数え直さない)、
  ;;     かつ待つ task の detail には同じ拍に置いた 4 本目を含めた 4 本・4000m が出る。
  (val mib-spy (Mock :wraps cluster-policy.claim-mib-of))
  (val cpu-spy (Mock :wraps cluster-policy.claim-cpu-of))
  (monkeypatch.setattr cluster-policy (hy.mangle "claim-mib-of") mib-spy)
  (monkeypatch.setattr cluster-policy (hy.mangle "claim-cpu-of") cpu-spy)
  (val same-tick-rows
    {"r1" (! (running-task "r1" "node-h" :memory-mib 256 :limits #((TaskLimit :group "g-top"))))
     "r2" (! (running-task "r2" "node-h" :memory-mib 256 :limits #((TaskLimit :group "g-top"))))
     "r3" (! (running-task "r3" "node-h" :memory-mib 256 :limits #((TaskLimit :group "g-top"))))
     "q1" (! (queued-task "q1" :memory-mib 256 :limits #((TaskLimit :group "g-top"))))
     "q2" (! (queued-task "q2" :memory-mib 256 :limits #((TaskLimit :group "g-top"))))
     "q3" (! (queued-task "q3" :memory-mib 256 :limits #((TaskLimit :group "g-top"))))
     "q4" (! (queued-task "q4" :memory-mib 256 :limits #((TaskLimit :group "g-top"))))
     "q5" (! (queued-task "q5" :memory-mib 256 :limits #((TaskLimit :group "g-top"))))})
  (<- same-tick-after dict (placed-on {"node-h" #(9216 4000)} same-tick-rows))
  (assert (= (. (get same-tick-after "q1") phase) "assigned") (get same-tick-after "q1"))
  (assert (= #(mib-spy.call-count cpu-spy.call-count) #(3 3)) #(mib-spy.call-count cpu-spy.call-count))
  (for [id #("q2" "q3" "q4" "q5")]
    (val t (get same-tick-after id))
    (assert (= t.unplaced-kind TaskUnplacedKind.TASK-COMPARTMENT-FULL) t)
    (assert (in "CPU 申告和 4000m + この task 1000m > 宣言 4000m" t.detail) t.detail)
    (assert (in "g-top 4本 4000m" t.detail) t.detail))
  ;; (2) starved-keeper の取っておき(kept)が絡む拍: compartment-misfit へ渡った引数 (held, used, kept, need, cpu) と
  ;;     detail に書かれた値が一致する(急ぎの task 自身の取っておきを自分の detail へ二重に足さず、後ろの task には取っておきが出る)。
  (val misfit-spy (Mock :wraps cluster-policy.compartment-misfit))
  (monkeypatch.setattr cluster-policy (hy.mangle "compartment-misfit") misfit-spy)
  (val keep-rows
    {"r1" (! (running-task "r1" "node-h" :memory-mib 2048 :limits #((TaskLimit :group "g-top"))))
     "q-urgent" (! (queued-task "q-urgent" :memory-mib 3072 :priority KEEP-NOW-PRIORITY :limits #((TaskLimit :group "g-second"))))
     "q-small" (! (queued-task "q-small" :memory-mib 1024 :priority 0 :limits #((TaskLimit :group "g-second"))))})
  (<- keep-after dict (placed-on {"node-h" #(4096 8000)} keep-rows))
  (val qu (get keep-after "q-urgent"))
  (val qs (get keep-after "q-small"))
  ;; q-urgent: 置いた 2048 + 取っておき 0 + 自身 3072 > 4096 → 取っておき 3072 を掛ける(自身の detail に取っておきは出ない)。
  (assert (in "memory 申告和 2048 MiB + この task 3072 MiB > 宣言 4096 MiB" qu.detail) qu.detail)
  (assert (not-in "取っておき 3072 MiB" qu.detail) qu.detail)
  ;; q-small: 置いた 2048 + 取っておき 3072 + 自身 1024 > 4096 → 後ろの task には取っておき 3072 MiB が出る。
  (assert (in "memory 申告和 2048 MiB + 取っておき 3072 MiB + この task 1024 MiB > 宣言 4096 MiB" qs.detail) qs.detail)
  ;; compartment-misfit の最後の呼び(q-small)の引数と一致する。
  (val last-args (. (get misfit-spy.call-args-list -1) args))
  (val last-held (get last-args 0))
  (val last-used (get last-args 1))
  (val last-kept (get last-args 2))
  (val last-need (get last-args 3))
  (assert (= #(last-used.memory-mib last-kept.memory-mib last-need last-held.memory-mib) #(2048 3072 1024 4096)) last-args)
  ;; (3) ReadDetachedQueue の読み(detached-queue-of-state)は coordinator の detail を数え直さずそのまま返す。
  (<- summary dict (task-summary qs))
  (<- reason DetachedQueueReason (detached-queue-of-state qs.key {"tasks" [summary] "groupLimits" []}))
  (assert (= reason.detail qs.detail) #(reason.detail qs.detail)))
