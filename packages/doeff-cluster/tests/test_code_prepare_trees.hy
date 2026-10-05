;; bytecode の準備の道具(worker/entry/code_prepare.hy)が全部の木を 1 回で焼く段取りの検 — 本物の file system(一時 dir)の上で。
;;   * 失敗ケース(a) 木をまたぐ閉包: 業務の木の入口が import する別の木(editable で入る依存の木)の module は焼かれ、どこからも import
;;     されない別の木の module は焼かれない — 閉包を木の中だけに戻すと、依存の木の module が焼かれずに赤。焼きは本物の焼きの道具
;;     (foundation/bytecode_pool.hy を子 process で起こす・並列 2)。
;;   * 失敗ケース(b) 大きい順: pool へ渡す焼く物の列は source の大きさの降順(同じ大きさは木と相対 path の順)— 名の順に戻すと赤。
;;     列を作る純粋な関数 bake-order と、準備の Program が焼きの効果へ渡す列(焼きの効果だけを記録の答え手に替える)の両方で確かめる。
;;   * 木ごとの引数の揃え方: --roots は木ごとに 1 つ・--from と --changed は無いか木ごとに 1 つ(空文字 = その木に無い)。
;;   * 入口は判断の module bake_plan を package の名でなく自分の位置から求めた path で読む(root の版の doeff に依らないため)。
;; 純粋な判断は package の名(doeff_cluster.worker.core.bake_plan)で import して検める。入口の Program へ渡す値と、Program の答えの型は
;; 入口が path で読んだ module(code_prepare.plan)の物を使う — 同じ file でも、path で読んだ module と package の module は別の module で、
;; record の class も別になる(isinstance の型の検めが通らない)。
(require doeff-hy.macros [defhandler defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import pathlib [Path])
(import doeff [with-handlers])
(import doeff_core_effects.handlers [slog-discard-handler])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_time [SimClock sim-time-handler])
(import doeff_cluster.worker.core.code_plan [MARKER cache-rel])
(import doeff_cluster.worker.protocol.tree_files [tree-files])
(import doeff_cluster.worker.core.bake_plan [BakeItem TreeArgs bake-order tree-arguments])
(import doeff_cluster.worker.entry.code_prepare [BAKE-PLAN BakeSources bake-trees plan pool-tool-baker prepare-trees])


(defk plant [root files]
  {:pre [(: root Path) (: files tuple)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "一時 dir の木に file を置くため(#(相対 path 中身) の列 — 親の dir も作る)。"
  (for [#(rel text) files]
    (val path (/ root rel))
    (.mkdir path.parent :parents True :exist-ok True)
    (.write-text path text))
  None)


(defk prepare-all [shaped jobs entries]
  {:pre [(: shaped tuple) (: jobs int) (: entries tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "道具の入口と同じ順で、揃えた木の組を焼く木にしてから全部を 1 回で準備し、木ごとの結果の列を返すため。"
  (<- trees tuple (bake-trees shaped))
  (<- outcomes tuple (prepare-trees trees "env" jobs entries))
  outcomes)


(deftest test-the-bake-scope-follows-imports-across-trees [#^ Path tmp-path]
  (val app (/ tmp-path "app"))
  (val lib (/ tmp-path "lib"))
  (<- (plant app #(#("app/__init__.py" "") #("app/main.hy" "(import libpkg.used [f])\n(setv V (f))\n") #("app/other.py" "X = 1\n"))))
  (<- (plant lib #(#("src/libpkg/__init__.py" "") #("src/libpkg/used.hy" "(defn f [] 1)\n") #("src/libpkg/unused.py" "Y = 2\n"))))
  (<- shaped (| tuple str) (plan.tree-arguments #((str app) (str lib)) #("." "src") #() #()))
  (assert (isinstance shaped tuple) shaped)
  (<- outcomes tuple
      (with-handlers [(sim-time-handler :clock (SimClock)) slog-discard-handler os-file-handler subprocess-handler tree-files
                      pool-tool-baker]
        (prepare-all shaped 2 #("app.main"))))
  (assert (= (tuple (gfor t outcomes #(t.named t.rebuilt t.reused t.failed t.problem))) #(#((str app) 2 0 0 None) #((str lib) 2 0 0 None)))
          outcomes)
  (assert (.exists (/ lib (cache-rel "src/libpkg/used.hy")))
          "業務の木の入口が import する依存の木の module が焼かれていない(閉包が木をまたいでいない)")
  (assert (.exists (/ lib (cache-rel "src/libpkg/__init__.py"))) "依存の木の package の __init__ も閉包に入る")
  (assert (.exists (/ app (cache-rel "app/main.hy"))))
  (assert (not (.exists (/ lib (cache-rel "src/libpkg/unused.py")))) "どこからも import されない依存の木の module まで焼いた")
  (assert (not (.exists (/ app (cache-rel "app/other.py")))) "どこからも import されない業務の木の module まで焼いた")
  (assert (and (.exists (/ app MARKER)) (.exists (/ lib MARKER))) "木ごとに完成の印を置く"))


(deftest test-the-pool-gets-the-largest-sources-first
  (<- order tuple (bake-order #((BakeItem :tree "/t" :rel "a/a.hy" :name "a.a" :size 10)
                                (BakeItem :tree "/t" :rel "a/b.hy" :name "a.b" :size 900)
                                (BakeItem :tree "/u" :rel "c.py" :name "c" :size 50)
                                (BakeItem :tree "/t" :rel "a/d.hy" :name "a.d" :size 50))))
  (assert (= order #(#("/t" "a/b.hy" "a.b") #("/t" "a/d.hy" "a.d") #("/u" "c.py" "c") #("/t" "a/a.hy" "a.a"))) order))


(defclass BakeLog []
  "焼きの効果の記録: items = 焼きの効果が受けた焼く物の列。"
  (defn #^ None __init__ [self]
    (setv #^ tuple self.items #())
    None))


(defhandler recorded-bakes [#^ BakeLog log]
  ;; 引数に残す理由: 検ごとに別の記録を持つ。焼きの効果(外の process を起こす答え手の代わり)が受けた焼く物を覚え、焼かずに「焼けなかった
  ;; 物も焼かずに残した物も無い」と答える。
  (BakeSources [items jobs paths]
    (setv log.items items)
    (resume (plan.BakeAnswer :failed #() :reused #()))))


(deftest test-the-prepare-program-hands-the-pool-the-largest-sources-first [#^ Path tmp-path]
  (val one (/ tmp-path "one"))
  (val two (/ tmp-path "two"))
  (<- (plant one #(#("p/__init__.py" "") #("p/a_small.py" "Z = 3\n") #("p/big.py" (* "X = 1\n" 200)))))
  (<- (plant two #(#("q/mid.py" (* "Y = 2\n" 20)))))
  (val log (BakeLog))
  (<- shaped (| tuple str) (plan.tree-arguments #((str one) (str two)) #("." ".") #() #()))
  (assert (isinstance shaped tuple) shaped)
  (<- (with-handlers [(sim-time-handler :clock (SimClock)) slog-discard-handler os-file-handler tree-files (recorded-bakes log)]
        (prepare-all shaped 2 #())))
  (assert (= (tuple (gfor i log.items (get i 1))) #("p/big.py" "q/mid.py" "p/a_small.py" "p/__init__.py")) log.items))


(deftest test-per-tree-arguments-line-up-by-one-rule
  (<- lined (| tuple str) (tree-arguments #("/a" "/b") #("." "src,vendor") #("" "/old/b") #()))
  (assert (= lined #((TreeArgs :named "/a" :roots #(".") :old None :changed None)
                     (TreeArgs :named "/b" :roots #("src" "vendor") :old "/old/b" :changed None)))
          lined)
  (<- short-roots (| tuple str) (tree-arguments #("/a" "/b") #(".") #() #()))
  (assert (isinstance short-roots str) short-roots)
  (<- short-from (| tuple str) (tree-arguments #("/a" "/b") #("." ".") #("/old/a") #()))
  (assert (isinstance short-from str) short-from)
  (<- empty-roots (| tuple str) (tree-arguments #("/a") #("") #() #()))
  (assert (isinstance empty-roots str) empty-roots))


(deftest test-the-entry-reads-the-bake-plan-by-its-own-path
  ;; 入口は判断の module を自分の位置の bake_plan.hy から、package と重ならない名で読む — package の名で import する形に戻すと、準備する
  ;; root の版の doeff の物を読み、この module を持たない古い版の root で落ちる。
  (val beside (/ (. (Path __file__) (resolve) parent parent) "src" "doeff_cluster" "worker" "core" "bake_plan.hy"))
  (assert (= #((Path plan.__file__) (Path BAKE-PLAN)) #(beside beside)) #(plan.__file__ BAKE-PLAN))
  (assert (= plan.__name__ "doeff_cluster_worker_bake_plan") plan.__name__))
