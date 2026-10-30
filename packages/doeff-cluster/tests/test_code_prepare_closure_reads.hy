;; bytecode の準備の道具(worker/entry/code_prepare.hy)の閉包の歩みが、一回りの読みをまとめて並べ、進みの印を触る事の検
;; (card ki-d3e585481618)— 本物の file system(一時 dir)の上で。
;;   * 失敗ケース(2026-10-26 の本番): 機体の USB の SSD の trim が読み 1 回を 50〜290 ms にした間、閉包の歩みは module ごとに source と
;;     保存先の entry を 1 つずつ順に読み、202 秒・600 秒超かかった。進みの印は bytecode の処理ステージの前と後にしか触られないので、呼び手
;;     の準備は進んでいる歩みを停滞と判じて止めた。
;;   * (a) 一回りの読み: 閉包の一回り(closure-step の答え)の source は 1 回の ReadSources、entry は 1 回の ReadStoreEntries で読む — module
;;     ごとの読みに戻すと、読みの効果の数が module の数になって赤。
;;   * (b) 進みの印: 走査の後・閉包の一回りごと・焼きの前と後に MarkProgress を出す — 処理ステージの前と後だけに戻すと赤。
;;   * (c) 印の答え手: 印の file の時刻を今にし(中身は変えない)、印の無い起動と消えた印の file では何もしない。
;;   * (d) 並べた読み: 答え手 closure-reads は path の順に答え、読めない source は None で返す(UTF-8 でない byte は前の読み ReadText と同じく置き換える — 中身の鍵が揃う)。
(require doeff-hy.macros [defhandler defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import os)
(import pathlib [Path])
(import doeff [with-handlers])
(import doeff_core_effects [slog-discard-handler])
(import doeff_core_effects [os-file-handler])
(import doeff_time [SimClock sim-time-handler])
(import doeff_cluster [tree-files])
(import doeff_cluster._worker.entry.code_prepare [BakeSources MarkProgress ReadSources ReadStoreEntries bake-trees closure-reads plan
                                               prepare-trees progress-marks])


(defk plant [root files]
  {:pre [(: root Path) (: files tuple)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "一時 dir の木に file を置くため(#(相対 path 中身) の列 — 親の dir も作る)。"
  (for [#(rel text) files]
    (val path (/ root rel))
    (.mkdir path.parent :parents True :exist-ok True)
    (.write-text path text))
  None)


(defk prepare-from-entry [shaped]
  {:pre [(: shaped tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "道具の入口と同じ順で、揃えた木の組を焼く木にしてから入口 app.main の閉包だけを準備するため(保存先は使わない設定)。"
  (<- trees tuple (bake-trees shaped))
  (<- outcomes tuple (prepare-trees trees "env" 2 #("app.main") None "test"))
  outcomes)


(defclass ReadLog []
  "閉包の歩みの効果の記録: sources = ReadSources が受けた path の列の列・entries = ReadStoreEntries が受けた列の列・
   marks = MarkProgress の数・bakes = 焼きの効果の数。"
  (defn #^ None __init__ [self]
    (setv #^ tuple self.sources #())
    (setv #^ tuple self.entries #())
    (setv #^ int self.marks 0)
    (setv #^ int self.bakes 0)
    None))


(defhandler recorded-reads [#^ ReadLog log]
  ;; 引数に残す理由: 検ごとに別の記録を持つ。読みの効果を覚えて外側の答え手(本物の closure-reads)へ出し直し、進みの印と焼きは数える
  ;; だけ(焼かずに「焼けなかった物も焼かずに残した物も無い」と答える)。
  (ReadSources [paths]
    (setv log.sources (+ log.sources #(paths)))
    (<- texts tuple (ReadSources paths))
    (resume texts))
  (ReadStoreEntries [paths]
    (setv log.entries (+ log.entries #(paths)))
    (<- datas tuple (ReadStoreEntries paths))
    (resume datas))
  (MarkProgress []
    (setv log.marks (+ log.marks 1))
    (resume None))
  (BakeSources [items jobs paths code-store]
    (setv log.bakes (+ log.bakes 1))
    (resume (plan.BakeAnswer :failed #() :stored #() :reused #() :unstored #()))))


(deftest test-the-closure-reads-each-round-at-once-and-marks-progress [#^ Path tmp-path]
  ;; 入口 app.main → app.a・app.b(一回り目の 2 つは 1 回の読み)→ app.c。一回り目は入口 1 つ。
  (val app (/ tmp-path "app"))
  (<- (plant app #(#("app/__init__.py" "")
                   #("app/main.py" "import app.a\nimport app.b\n")
                   #("app/a.py" "import app.c\n")
                   #("app/b.hy" "(import app.c)\n")
                   #("app/c.py" "X = 1\n")
                   #("app/unused.py" "Y = 2\n"))))
  (<- shaped (| tuple str) (plan.tree-arguments #((str app)) #(".")))
  (assert (isinstance shaped tuple) shaped)
  (val log (ReadLog))
  (<- outcomes tuple
      (with-handlers [(sim-time-handler :clock (SimClock)) slog-discard-handler os-file-handler tree-files closure-reads (recorded-reads log)]
        (prepare-from-entry shaped)))
  (val rounds (tuple (gfor paths log.sources (tuple (sorted (gfor p paths (. (Path p) name)))))))
  (assert (in #("a.py" "b.hy") rounds) rounds)
  (assert (= (len log.sources) (len log.entries)) #(log.sources log.entries))
  (assert (all (gfor #(s e) (zip log.sources log.entries) (= (len s) (len e)))) #(log.sources log.entries))
  (assert (not-in "unused.py" (gfor r rounds n r n)) rounds)
  ;; 印 = 走査の後 1・一回りごと・焼きの前と後 2。
  (assert (= log.marks (+ 1 (len log.sources) 2)) #(log.marks (len log.sources)))
  (assert (= log.bakes 1) log.bakes)
  ;; 焼きは記録の答え手が焼かずに答えるので、検めは .pyc の無さを理由に載せる — 見るのは木 1 つの結果が返る事だけ。
  (assert (= (len outcomes) 1) outcomes))


(deftest test-the-progress-mark-moves-the-mark-time-and-keeps-its-content [#^ Path tmp-path]
  (val mark (/ tmp-path "env.progress"))
  (.write-text mark "bytecode\n")
  (os.utime mark #(1000000000 1000000000))
  (<- (with-handlers [(progress-marks (str mark))] (MarkProgress)))
  (assert (> (. (.stat mark) st-mtime) 1000000000) (. (.stat mark) st-mtime))
  (assert (= (.read-text mark) "bytecode\n") (.read-text mark))
  ;; 印の無い起動と、消えた印の file では何もしない(準備を止めない)。
  (<- (with-handlers [(progress-marks None)] (MarkProgress)))
  (<- (with-handlers [(progress-marks (str (/ tmp-path "gone.progress")))] (MarkProgress)))
  (assert (not (.exists (/ tmp-path "gone.progress")))))


(deftest test-the-closure-reads-answer-in-order-and-name-unreadable-sources-none [#^ Path tmp-path]
  (val paths (tuple (gfor i (range 40) (str (/ tmp-path f"s{i}.py")))))
  (for [#(i p) (enumerate paths)]
    (when (!= i 7)
      (.write-text (Path p) f"V = {i}\n")))
  (.write-bytes (Path (get paths 9)) b"\xff\xfe not utf-8")
  (<- texts tuple (with-handlers [closure-reads] (ReadSources paths)))
  (assert (= (len texts) 40) texts)
  (assert (is (get texts 7) None) "無い file は None")
  (assert (= (get texts 9) (.decode b"\xff\xfe not utf-8" "utf-8" :errors "replace")) "UTF-8 でない byte は置き換えて読む(前の読みと同じ)")
  (assert (= (get texts 12) "V = 12\n") texts)
  (<- datas tuple (with-handlers [closure-reads] (ReadStoreEntries #(None None))))
  (assert (= datas #(None None)) datas))
