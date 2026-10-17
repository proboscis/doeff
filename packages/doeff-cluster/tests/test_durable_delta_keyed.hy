;;; durable-delta が変わった鍵だけ作る検(ki — T7)。
;;;
;;; 時間では測らない。数える物は、組の鍵を絞る関数(SourceGroup.keyed)に渡った表の鍵と、全部を作る関数(build)の呼ばれた回数。
;;; 固定すること:
;;;   - 失敗ケース: 乱数の書き・消し・基の作り直し(基を分け合わない表への入れ替え)の列で、絞った durable-delta が、全部の組を作った
;;;     差分(durable-kv の前後の比べ)と 1 字も違わない。消えた鍵は None で出る。盤(board)の扱いも同じ。
;;;   - 1 行だけ書いた歩は、その行の鍵 1 つだけを前後で作り、全鍵を作る build は呼ばない。
(require doeff-hy.macros [deftest val var defk <-])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

(import random)
(import dataclasses [replace])
(import unittest.mock [Mock])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState TaskRecord ResourceMeta BoardRow])
(import doeff_cluster.coordinator.protocol.durable_kv :as dk)
(import doeff_hy.table [TableWrite])
(import tests.state_rows [table-from-rows])
(import tests.program_rows [SAMPLE-TASK-PROGRAM])

(val BASE-ROWS 200)


(defn #^ TaskRecord task-row [#^ str id #^ int salt]
  "task 1 本の行(salt で中身を変える)。"
  (TaskRecord id "" SAMPLE-TASK-PROGRAM "" #() #() 60000 (+ 1000000 salt) 0 :phase "assigned" :worker "w" :detached True))


(defn #^ ResourceMeta meta-row [#^ int salt]
  "資源の版の記録 1 つ(salt で中身を変える)。"
  (ResourceMeta :resource-version salt :generation 1 :created-by "a" :created-ms 0 :updated-by "b" :updated-ms salt))


(defk big-state []
  {:pre [] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "tasks・meta・board を BASE-ROWS 行ずつ持つ状態(基が 64 行を越えるので、書きは差分の層に載る)。"
  (<- tasks (table-from-rows (dfor i (range BASE-ROWS) (.format "t{:03d}" i) (task-row (.format "t{:03d}" i) i))))
  (<- meta (table-from-rows (dfor i (range BASE-ROWS) (.format "Service/s{:03d}" i) (meta-row i))))
  (<- board (table-from-rows (dfor i (range BASE-ROWS) (.format "k{:03d}" i) (BoardRow :value i :version 1 :expires-ms None :size 1))))
  (ClusterState :tasks tasks :meta meta :board board))


(defk by-full [before after]
  {:pre [(: before ClusterState) (: after ClusterState)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "全部の組を作って比べた差分(盤を含む) — 絞った差分の正解。"
  (<- old (dk.full-kv before))
  (<- new (dk.full-kv after))
  (| (dfor #(k v) (.items new) :if (!= (.get old k) v) k v)
     (dfor k old :if (not-in k new) k None)))


(defk random-step [rng state n]
  {:pre [(: rng random.Random) (: state ClusterState) (: n int)] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "乱数の 1 歩 — 表への書き・消し(基を分け合う)か、表を作り直して入れ替える(基を分け合わない)。n は新しい鍵の連番。"
  (val field (.choice rng ["tasks" "meta" "board"]))
  (val table (getattr state field))
  (val keys (list (.keys table)))
  (val kind (.choice rng ["write" "write" "delete" "add" "rebuild" "same"]))
  (val key (.choice rng keys))
  (val make (fn [k salt]
              (cond (= field "tasks") (task-row k salt)
                    (= field "meta") (meta-row salt)
                    True (BoardRow :value salt :version 1 :expires-ms None :size 1))))
  (cond (= kind "write") (replace state #** {field (.with-writes table #((TableWrite key (make key (.randrange rng 5)))))})
        (= kind "delete") (replace state #** {field (.with-writes table #((TableWrite key None)))})
        (= kind "add") (replace state #** {field (.with-writes table #((TableWrite (.format "n{}" n) (make (.format "n{}" n) n))))})
        (= kind "rebuild") (do (<- rebuilt (table-from-rows (dict (.items table))))
                               (replace state #** {field rebuilt}))
        True state))


(deftest test-counterexample-random-writes-deletes-and-rebuilds-match-the-full-delta
  ;; 失敗ケース: 乱数の書き・消し・基の作り直しの列で、絞った差分が全部を作った差分と同じ(消えた鍵は None・盤も同じ)。
  (val rng (random.Random 20261017))
  (var state (! (big-state)))
  (for [n (range 400)]
    (val nxt (! (random-step rng state n)))
    (val got (! (dk.durable-delta state nxt)))
    (val want (! (by-full state nxt)))
    (assert (= got want) #(n got want))
    (:= state nxt)))


(deftest test-a-step-that-writes-one-task-builds-only-that-key [monkeypatch]
  ;; 1 行だけ書いた歩: tasks の組は絞りの関数に鍵 1 つだけが前後で渡り、全鍵を作る build は呼ばれない。
  (val s (! (big-state)))
  (val changed (replace s :tasks (.with-writes s.tasks #((TableWrite "t007" (task-row "t007" 99))))))
  (val spies (tuple (gfor g dk.SOURCE-GROUPS
                          #(g.fields (Mock :wraps g.build) (if (is g.keyed None) None (Mock :wraps g.keyed))))))
  (.setattr monkeypatch dk "SOURCE_GROUPS" (tuple (gfor #(fields b k) spies (dk.SourceGroup :fields fields :build b :keyed k))))
  (val got (! (dk.durable-delta s changed)))
  (assert (= (sorted got) ["task/t007"]) got)
  (val tasks-pair (next (gfor #(fields b k) spies :if (= fields #("tasks")) #(b k))))
  (assert (= (. (get tasks-pair 0) call-count) 0))
  (assert (= (list (gfor c (. (get tasks-pair 1) call-args-list) (get c.args 1))) [#("t007") #("t007")]))
  (assert (= (sum (gfor #(_f b _k) spies b.call-count)) 0)))
